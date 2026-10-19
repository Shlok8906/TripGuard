"""Destination generator: lets the planner handle ANY place in India, not just the bundled catalog.

How it stays safe with a small local model:
  * the model only *proposes* (names, rough prices, hours, tags); every field is parsed, clamped and
    replaced by a sane default when invalid - the Critic then validates plans against that profile;
  * coordinates come from the Open-Meteo geocoder when Live mode is on, otherwise from the model and are
    bounds-checked; places the model puts far from the town centre are re-placed on a ring around it;
  * stays and food always cover every price tier (templates fill gaps) so the planner's ladder keeps working;
  * everything generated is labelled "AI-estimated" and flagged so the UI and Critic warn the user.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

import requests

from .config import GEN_TIMEOUT_S, HTTP_TIMEOUT_S, INTERESTS
from .llm import LLMError, get_llm
from .tools.catalog import (builtin_destinations, destinations, is_generated, origins, register_destination,
                            register_origin, slug)
from .utils import haversine_km

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
INDIA_BBOX = (6.0, 37.5, 68.0, 98.5)          # lat_min, lat_max, lon_min, lon_max
MAX_CENTER_DIST_KM = 40
LabelFn = Callable[[str, str, str], None]


class GenerationError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- small validators
def _num(v, lo: float, hi: float, default=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(x) or math.isinf(x):
        return default
    return min(hi, max(lo, x))


def _round(x: float, base: int) -> int:
    return int(round(x / base) * base)


def _hhmm(v, default: str) -> str:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(v or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return default
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _mins(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def _text(v, lo: int = 3, hi: int = 60) -> Optional[str]:
    if not isinstance(v, str):
        return None
    t = re.sub(r"\s+", " ", re.sub(r"[^\w .,'&()/-]", "", v)).strip()
    return t[:hi] if len(t) >= lo else None


def in_india(lat: float, lon: float) -> bool:
    return INDIA_BBOX[0] <= lat <= INDIA_BBOX[1] and INDIA_BBOX[2] <= lon <= INDIA_BBOX[3]


def _ring(center: Tuple[float, float], i: int, n: int, r_min: float = 1.5, r_step: float = 1.2, spread: int = 4) -> Tuple[float, float]:
    """Deterministic spot near the centre: used when the model's coordinates cannot be trusted."""
    ang = 2 * math.pi * (i + 0.5) / max(1, n)
    r = r_min + (i % spread) * r_step
    dlat = r * math.cos(ang) / 111.0
    dlon = r * math.sin(ang) / (111.0 * max(0.2, math.cos(math.radians(center[0]))))
    return round(center[0] + dlat, 4), round(center[1] + dlon, 4)


# ----------------------------------------------------------------------------- normalisers (pure, unit-tested)
_DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _closed_days(v) -> List[int]:
    out = []
    for x in v if isinstance(v, list) else []:
        if isinstance(x, str) and x[:3].lower() in _DAYS:
            out.append(_DAYS[x[:3].lower()])
        elif isinstance(x, int) and 0 <= x <= 6:
            out.append(x)
    out = sorted(set(out))
    return out if len(out) <= 2 else []


def normalise_attractions(raw, center: Tuple[float, float], key: str) -> Tuple[List[dict], List[str]]:
    notes: List[str] = []
    items = raw if isinstance(raw, list) else []
    seen, cand = set(), []
    for r in items:
        if not isinstance(r, dict):
            continue
        name = _text(r.get("name"))
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        cand.append((name, r))
    cand = cand[:12]
    out = []
    moved = 0
    for i, (name, r) in enumerate(cand):
        tags = [t for t in (r.get("tags") or []) if t in INTERESTS] if isinstance(r.get("tags"), list) else []
        tags = tags[:3] or ["culture"]
        cost = _round(_num(r.get("cost_pp"), 0, 3000, 0), 10)
        dur = _round(_num(r.get("duration_min"), 30, 300, 90), 15)
        o, c = _hhmm(r.get("open"), "09:00"), _hhmm(r.get("close"), "18:00")
        if _mins(c) - _mins(o) < dur + 30:            # window too short for the visit: fall back to daytime hours
            o, c = "09:00", "18:00"
            dur = min(dur, 150)
        best = r.get("best_time") if r.get("best_time") in ("morning", "evening", "any") else "any"
        outdoor = r["outdoor"] if isinstance(r.get("outdoor"), bool) else bool(set(tags) & {"nature", "beach", "adventure"})
        lat, lon = _num(r.get("lat"), -90, 90), _num(r.get("lon"), -180, 180)
        if lat is None or lon is None or haversine_km(lat, lon, center[0], center[1]) > MAX_CENTER_DIST_KM:
            lat, lon = _ring(center, i, len(cand))
            moved += 1
        out.append({
            "id": f"{key}-a{i + 1}", "name": name, "lat": round(lat, 4), "lon": round(lon, 4), "tags": tags,
            "cost_pp": cost, "duration_min": dur, "open": o, "close": c, "outdoor": outdoor, "best_time": best,
            "closed_days": _closed_days(r.get("closed_days")),
            "event": bool(r.get("event")) and best == "evening",
            "must_see": bool(r.get("must_see")) if i < 6 else False,
            "desc": (_text(r.get("desc"), 3, 140) or name),
        })
    if moved:
        notes.append(f"{moved} attraction location(s) were unreliable and placed near the centre (map positions approximate)")
    return out, notes


STAY_TIERS = {
    # tier: (type, unit, capacity, comfort, breakfast_included, price_lo, price_hi, default_price, default_name)
    "dorm": ("hostel_dorm", "bed", 1, 2, False, 300, 1500, 600, "Budget hostel - dorm bed"),
    "private": ("hostel_private", "room", 2, 3, True, 600, 3500, 1400, "Guesthouse - private room"),
    "budget_hotel": ("hotel_budget", "room", 2, 4, True, 900, 5000, 2200, "Budget hotel - room"),
    "mid_hotel": ("hotel_mid", "room", 3, 5, True, 1500, 9000, 3800, "Mid-range hotel - family room"),
}


def normalise_stays(raw, center: Tuple[float, float], key: str) -> Tuple[List[dict], List[str]]:
    notes: List[str] = []
    by_tier: Dict[str, dict] = {}
    for r in raw if isinstance(raw, list) else []:
        if isinstance(r, dict) and r.get("tier") in STAY_TIERS and r["tier"] not in by_tier:
            by_tier[r["tier"]] = r
    out = []
    for i, (tier, (typ, unit, cap, comfort, bf, lo, hi, dp, dn)) in enumerate(STAY_TIERS.items()):
        r = by_tier.get(tier, {})
        price = _num(r.get("price_inr"), lo, hi)
        name = _text(r.get("name"))
        if price is None or not name:
            notes.append(f"stay tier '{tier}' not usable from the model - template used")
        lat, lon = _ring(center, i, 4, r_min=0.8, r_step=0.7, spread=4)
        out.append({"id": f"{key}-s{i + 1}", "name": f"{name or dn} (AI-estimated)", "type": typ, "unit": unit,
                    "capacity": cap, "price": _round(price if price is not None else dp, 50), "comfort": comfort,
                    "breakfast_included": bf, "lat": lat, "lon": lon})
    return out, notes


FOOD_KINDS = {
    # kind: (default name, default cost, default tags, open, close)
    "street": ("Local street-food stalls", 90, ["food", "street"], "07:00", "21:30"),
    "thali": ("Local thali meals", 180, ["food"], "08:00", "22:00"),
    "cafe": ("Cafe", 250, ["food", "relax"], "08:00", "22:30"),
    "restaurant": ("Restaurant", 450, ["food"], "11:00", "23:00"),
}


def normalise_food(raw, center: Tuple[float, float], key: str) -> Tuple[List[dict], List[str]]:
    notes: List[str] = []
    out, kinds, seen = [], set(), set()
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict) or r.get("kind") not in FOOD_KINDS:
            continue
        name, cost = _text(r.get("name")), _num(r.get("cost_pp"), 30, 1500)
        if not name or cost is None or name.lower() in seen:
            continue
        seen.add(name.lower())
        kind = r["kind"]
        _, _, dtags, do, dc = FOOD_KINDS[kind]
        tags = list(dict.fromkeys(["food"] + [t for t in (r.get("tags") or []) if t in INTERESTS or t in ("street", "seafood")]
                                  + (["street"] if kind == "street" else [])))[:4]
        o, c = _hhmm(r.get("open"), do), _hhmm(r.get("close"), dc)
        if _mins(c) - _mins(o) < 180:
            o, c = do, dc
        out.append({"kind": kind, "name": f"{name} (AI-estimated)", "cost_pp": _round(cost, 10), "tags": tags,
                    "open": o, "close": c})
        kinds.add(kind)
        if len(out) >= 8:
            break
    for kind, (dn, dcost, dtags, do, dc) in FOOD_KINDS.items():
        if kind not in kinds:
            out.append({"kind": kind, "name": f"{dn} (AI-estimated)", "cost_pp": dcost, "tags": list(dtags), "open": do, "close": dc})
            notes.append(f"no usable '{kind}' food place from the model - template used")
    for i, f in enumerate(out):
        f["id"] = f"{key}-f{i + 1}"
        f["lat"], f["lon"] = _ring(center, i, len(out), r_min=1.0, r_step=0.9, spread=4)
    return out, notes


def normalise_meta(raw: dict) -> Tuple[dict, List[str]]:
    notes: List[str] = []
    clim_in = raw.get("climate") if isinstance(raw.get("climate"), dict) else {}

    def arr(name: str, lo: float, hi: float, default: float) -> List[int]:
        v = clim_in.get(name)
        if isinstance(v, list) and len(v) == 12:
            nums = [_num(x, lo, hi) for x in v]
            if all(x is not None for x in nums):
                return [int(round(x)) for x in nums]
        notes.append(f"climate '{name}' missing/invalid - generic value used")
        return [int(default)] * 12

    loc_in = raw.get("local") if isinstance(raw.get("local"), dict) else {}
    local = {
        "mode": _text(loc_in.get("mode"), 3, 30) or "auto / taxi",
        "cost_per_km": int(_num(loc_in.get("cost_per_km"), 8, 30, 14)),
        "capacity": 3,
        "speed_kmh": int(_num(loc_in.get("speed_kmh"), 15, 35, 25)),
        "station_transfer": _round(_num(loc_in.get("station_transfer"), 100, 700, 300), 10),
        "breakfast_pp": _round(_num(loc_in.get("breakfast_pp"), 30, 250, 80), 10),
    }
    meta = {"state": _text(raw.get("state"), 2, 40) or "India", "tagline": _text(raw.get("tagline"), 3, 90) or "AI-estimated destination profile",
            "local": local, "climate": {"rain_prob": arr("rain_prob", 0, 100, 25), "tmax": arr("tmax", -10, 50, 30),
                                        "tmin": arr("tmin", -20, 40, 20)}}
    return meta, notes


# ----------------------------------------------------------------------------- geocoding
def geocode(name: str) -> Optional[dict]:
    """Open-Meteo geocoder. Returns the best India match (or the first hit so callers can reject foreign places)."""
    try:
        r = requests.get(GEO_URL, params={"name": name, "count": 5, "language": "en", "format": "json"}, timeout=HTTP_TIMEOUT_S)
        r.raise_for_status()
        results = r.json().get("results") or []
    except Exception:
        return None
    if not results:
        return None
    pick = next((x for x in results if x.get("country_code") == "IN"), results[0])
    try:
        return {"name": pick["name"], "lat": float(pick["latitude"]), "lon": float(pick["longitude"]),
                "state": pick.get("admin1") or "India", "country_code": pick.get("country_code", "")}
    except (KeyError, TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------- prompts
SYS_META = (
    "You are a travel data assistant for India. Reply with ONE JSON object only. Describe the Indian place named by the user. "
    'Keys: "canonical_name" (string), "state" (string), "country" (string), "lat" (number), "lon" (number), '
    '"tagline" (max 10 words), "climate" ({"rain_prob": 12 monthly values 0-100 for Jan..Dec, "tmax": 12 monthly values in C, '
    '"tmin": 12 monthly values in C}), "local" ({"mode": string, "cost_per_km": rupees per km for an auto/taxi, '
    '"speed_kmh": number, "station_transfer": rupees for a taxi between the station/bus stand and town centre, '
    '"breakfast_pp": rupees for a simple breakfast per person}). If you do not know the place, set "country" to "unknown".'
)
SYS_ATTR = (
    "You are a travel data assistant for India. Reply with ONE JSON object: "
    '{"attractions": [ ... ]} with 8 to 10 real, well-known sights/activities in or near the given place, including at least '
    "3 free ones. Each item: "
    '{"name": string, "tags": 1-3 values from ' + str(INTERESTS) + ', "cost_pp": entry/activity price in rupees per person (0 if free), '
    '"duration_min": number, "open": "HH:MM", "close": "HH:MM", "outdoor": true|false, "best_time": "morning"|"evening"|"any", '
    '"closed_days": list of weekday names the place is closed (usually empty), "must_see": true|false, "desc": one short sentence, '
    '"lat": number, "lon": number}. Only include places you are confident exist. Do not invent names.'
)
SYS_STAYFOOD = (
    "You are a travel data assistant for India. Reply with ONE JSON object: "
    '{"stays": [4 items, one each for tier "dorm" (price per bed), "private" (price per room), "budget_hotel" (price per room), '
    '"mid_hotel" (price per room)], "food": [6 to 8 items]} for the given place. Stay item: {"tier": string, "name": string, "price_inr": '
    'typical rupees per night}. Food item: {"name": string, "kind": "street"|"thali"|"cafe"|"restaurant", "cost_pp": rupees per person, '
    '"tags": values from ["food","street","seafood","relax","nightlife","culture"], "open": "HH:MM", "close": "HH:MM"}. '
    "Use realistic budget-traveller prices for 2026 and generic descriptive names (for example 'Riverside guesthouse') unless you "
    "are certain a specific business exists."
)


# ----------------------------------------------------------------------------- orchestration
def _match_known(name: str) -> Optional[str]:
    low = name.strip().lower()
    return next((d for d in destinations() if d.lower() == low), None)


def _match_origin(name: str) -> Optional[str]:
    low = name.strip().lower()
    return next((o for o in origins() if o.lower() == low), None)


def generate_destination(raw_name: str, settings: dict, log: LabelFn) -> str:
    """Create (and persist) a destination profile. Returns its canonical name or raises GenerationError."""
    known = _match_known(raw_name)
    if known:
        return known
    llm = get_llm(settings)
    if not llm.is_real:
        raise GenerationError(f"'{raw_name}' is not in the built-in catalog. Switch the sidebar to Ollama (local) to plan any destination, "
                              f"or choose one of: {', '.join(builtin_destinations())}.")
    live = bool(settings.get("live")) and not settings.get("simulate_down")

    geo = geocode(raw_name) if live else None
    if geo:
        if geo["country_code"] and geo["country_code"] != "IN":
            raise GenerationError(f"'{raw_name}' resolves to a place outside India - this prototype supports Indian destinations only.")
        log("Geocoder", f"{geo['name']}, {geo['state']} at {geo['lat']:.3f}, {geo['lon']:.3f} (Open-Meteo)", "ok")
    elif live:
        log("Geocoder", "no result / unreachable - will use the model's coordinates", "warn")
    else:
        log("Geocoder", "Live data is off - using the model's coordinates (turn Live data on for exact positions)", "info")

    try:
        log("Generating profile", f"asking {llm.name} about {raw_name} (this can take a minute or two)", "info")
        meta_raw = llm.chat_json(SYS_META, raw_name, timeout=GEN_TIMEOUT_S)
    except LLMError as e:
        raise GenerationError(f"Ollama could not be reached or answered badly ({e}). Is `ollama serve` running and the model pulled?")
    country = str(meta_raw.get("country", "")).strip().lower()
    if country and country not in ("india", "in", "bharat"):
        raise GenerationError(f"The model says '{raw_name}' is not an Indian place (country: {meta_raw.get('country')}). "
                              "Check the spelling, or pick a destination in India.")
    if geo:
        center = (geo["lat"], geo["lon"])
        coords_source = "geocoder"
    else:
        lat, lon = _num(meta_raw.get("lat"), -90, 90), _num(meta_raw.get("lon"), -180, 180)
        if lat is None or lon is None or not in_india(lat, lon):
            raise GenerationError(f"Could not locate '{raw_name}'. Check the spelling, or turn on Live data so the geocoder can find it.")
        center, coords_source = (lat, lon), "model"
    canonical = (geo["name"] if geo else _text(meta_raw.get("canonical_name"), 2, 40)) or raw_name.title()
    canonical = _match_known(canonical) or canonical
    if _match_known(canonical):
        return canonical
    key = slug(canonical)[:12]

    attr: List[dict] = []
    notes: List[str] = []
    for attempt in (1, 2):
        try:
            if attempt == 1:
                log("Generating profile", "attractions", "info")
            raw = llm.chat_json(SYS_ATTR, f"{canonical}, {meta_raw.get('state') or geo and geo['state'] or 'India'}", timeout=GEN_TIMEOUT_S)
        except LLMError as e:
            raise GenerationError(f"Ollama failed while listing attractions ({e}).")
        attr, n = normalise_attractions(raw.get("attractions"), center, key)
        if len(attr) >= 4:
            notes += n
            break
        log("Generating profile", f"only {len(attr)} usable attractions - retrying once", "warn")
    if len(attr) < 4:
        raise GenerationError(f"The model could not list enough real attractions for '{raw_name}' (got {len(attr)}). "
                              "Try a larger model (for example qwen2.5:7b) or a more well-known destination.")
    try:
        log("Generating profile", "stays and food", "info")
        sf = llm.chat_json(SYS_STAYFOOD, canonical, timeout=GEN_TIMEOUT_S)
    except LLMError as e:
        sf = {}
        log("Generating profile", f"stays/food call failed ({e}) - templates will be used", "warn")
    stays, n1 = normalise_stays(sf.get("stays"), center, key)
    food, n2 = normalise_food(sf.get("food"), center, key)
    meta, n3 = normalise_meta(meta_raw)
    notes += n1 + n2 + n3
    if geo:
        meta["state"] = geo["state"]
    profile = {"lat": round(center[0], 4), "lon": round(center[1], 4), "country": "India", **meta,
               "stays": stays, "attractions": attr, "food": food, "coords_source": coords_source, "profile_notes": notes}
    register_destination(canonical, profile, meta={"model": llm.name, "generated_at": datetime.now().isoformat(timespec="seconds"),
                                                  "asked_as": raw_name})
    log("Profile ready", f"{canonical}: {len(attr)} attractions, {len(stays)} stays, {len(food)} food places - AI-estimated, not verified", "ok")
    for n in notes:
        log("Profile note", n, "info")
    return canonical


def resolve_origin(raw_name: str, settings: dict, log: LabelFn) -> Optional[str]:
    """Locate any Indian town as a starting point (needs only coordinates). None if it cannot be located."""
    known = _match_origin(raw_name) or _match_known(raw_name)
    if known:
        return known
    live = bool(settings.get("live")) and not settings.get("simulate_down")
    lat = lon = None
    name = raw_name.strip().title()
    geo = geocode(raw_name) if live else None
    if geo and geo["country_code"] == "IN":
        lat, lon, name = geo["lat"], geo["lon"], geo["name"]
        log("Geocoder", f"origin {name} located at {lat:.3f}, {lon:.3f}", "ok")
    else:
        llm = get_llm(settings)
        if llm.is_real:
            try:
                d = llm.chat_json('Reply with ONE JSON object {"lat": number, "lon": number, "country": string} giving the centre of the '
                                  "Indian town or city named by the user.", raw_name, timeout=GEN_TIMEOUT_S)
                la, lo = _num(d.get("lat"), -90, 90), _num(d.get("lon"), -180, 180)
                if la is not None and lo is not None and in_india(la, lo):
                    lat, lon = la, lo
                    log("Origin", f"{name} located by the model at {la:.2f}, {lo:.2f} (approximate)", "warn")
            except LLMError:
                pass
    if lat is None:
        return None
    register_origin(name, round(lat, 4), round(lon, 4))
    return name


def resolve_places(req: dict, settings: dict, log: LabelFn) -> Optional[str]:
    """Resolve unknown destination/origin names in a parsed request. Returns an error message to show the user, or None."""
    org_raw = req.pop("origin_unknown", None)
    if org_raw:
        found = resolve_origin(org_raw, settings, log)
        if found:
            req["origin"] = found
        else:
            log("Origin", f"could not locate '{org_raw}' - using {req['origin']} instead "
                "(Ollama or Live data is needed for towns outside the built-in list)", "warn")
    dest_raw = req.pop("destination_unknown", None)
    if dest_raw and not req.get("destination"):
        try:
            req["destination"] = generate_destination(dest_raw, settings, log)
        except GenerationError as e:
            log("Destination", str(e), "fail")
            return str(e)
    if req.get("destination") and is_generated(req["destination"]):
        req["generated"] = True
    return None
