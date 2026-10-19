"""Access to the bundled synthetic catalog and transport timetable (the 'mock partner API')."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Dict, List

from .transport_gen import generate_route

from .. import config
from ..config import DATA_DIR


@lru_cache(maxsize=1)
def load_catalog() -> dict:
    with open(DATA_DIR / "catalog.json", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_transport() -> dict:
    with open(DATA_DIR / "transport.json", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- generated places
# Destinations/origins outside the bundled catalog can be created at runtime by tripguard.generator
# (Ollama). They are kept in memory and persisted as JSON under cache/generated/.
GENERATED_DIR = config.GENERATED_DIR
_gen: Dict[str, Dict[str, dict]] = {"destinations": {}, "origins": {}}
_loaded = False
_route_cache: Dict[tuple, dict] = {}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "place"


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        files = sorted(GENERATED_DIR.glob("*.json"))
    except OSError:
        return
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if f.name.startswith("dest_"):
                _gen["destinations"][data["name"]] = data["profile"]
            elif f.name.startswith("origin_"):
                _gen["origins"][data["name"]] = {"lat": data["lat"], "lon": data["lon"], "generated": True}
        except (OSError, ValueError, KeyError):
            continue            # a damaged cache file must never break the app


def reset_generated() -> None:
    """Forget in-memory generated places (the next call re-reads the folder). Used by tests."""
    global _loaded
    _gen["destinations"].clear()
    _gen["origins"].clear()
    _route_cache.clear()
    _loaded = False


def register_destination(name: str, profile: dict, persist: bool = True, meta: dict | None = None) -> None:
    _ensure_loaded()
    profile = {**profile, "generated": True}
    _gen["destinations"][name] = profile
    _route_cache.clear()
    if persist:
        _write(f"dest_{slug(name)}.json", {"name": name, "profile": profile, **(meta or {})})


def register_origin(name: str, lat: float, lon: float, persist: bool = True) -> None:
    _ensure_loaded()
    _gen["origins"][name] = {"lat": lat, "lon": lon, "generated": True}
    _route_cache.clear()
    if persist:
        _write(f"origin_{slug(name)}.json", {"name": name, "lat": lat, "lon": lon})


def _write(fname: str, payload: dict) -> None:
    try:
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)
        (GENERATED_DIR / fname).write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass                    # persistence is best-effort; the in-memory copy still works


def clear_generated() -> int:
    """Delete saved generated places. Returns how many files were removed."""
    n = 0
    try:
        for f in GENERATED_DIR.glob("*.json"):
            f.unlink()
            n += 1
    except OSError:
        pass
    reset_generated()
    return n


def is_generated(name: str) -> bool:
    _ensure_loaded()
    return name in _gen["destinations"]


def generated_destinations() -> List[str]:
    _ensure_loaded()
    return sorted(_gen["destinations"])


def builtin_destinations() -> List[str]:
    return sorted(load_catalog()["destinations"])


def destinations() -> List[str]:
    _ensure_loaded()
    return sorted(set(load_catalog()["destinations"]) | set(_gen["destinations"]))


def origins() -> List[str]:
    _ensure_loaded()
    return sorted(set(load_catalog()["origins"]) | set(_gen["origins"]))


def destination(name: str) -> dict:
    _ensure_loaded()
    if name in _gen["destinations"]:
        return _gen["destinations"][name]
    return load_catalog()["destinations"][name]


def origin_coord(name: str) -> Dict[str, float]:
    _ensure_loaded()
    if name in load_catalog()["origins"]:
        return load_catalog()["origins"][name]
    if name in _gen["origins"]:
        return _gen["origins"][name]
    d = destination(name)        # a known destination can also be a starting point
    return {"lat": d["lat"], "lon": d["lon"]}


def transport_options(origin: str, dest: str) -> dict:
    """Return {'distance_km': int, 'options': [...]} for the route (same list is used both ways)."""
    _ensure_loaded()
    routes = load_transport()["routes"]
    if origin in routes and dest in routes[origin]:
        return routes[origin][dest]
    key = (origin, dest)
    if key not in _route_cache:
        a, b = origin_coord(origin), destination(dest)
        _route_cache[key] = generate_route((a["lat"], a["lon"]), (b["lat"], b["lon"]))
    return _route_cache[key]


def place_index(dest: str) -> Dict[str, dict]:
    """id -> place dict for attractions, stays and food of a destination."""
    d = destination(dest)
    idx: Dict[str, dict] = {}
    for a in d["attractions"]:
        idx[a["id"]] = {**a, "kind": "attraction"}
    for s in d["stays"]:
        idx[s["id"]] = {**s, "kind": "stay"}
    for f in d["food"]:
        idx[f["id"]] = {**f, "kind": "food"}
    return idx
