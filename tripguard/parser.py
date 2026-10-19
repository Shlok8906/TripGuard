"""Deterministic natural-language -> structured trip request parser.

This is the baseline the Planner always runs. A local LLM (Ollama) may *fill gaps* the rules
could not resolve (e.g. free-form interests), but numeric facts found by the rules win,
which keeps the system reliable even with small models.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from .config import INTERESTS
from .tools.catalog import destinations, origins
from .utils import next_weekday

NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "a": 1}
NUM_RE = r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten)"

ORIGIN_ALIASES = {
    "pune": "Pune", "mumbai": "Mumbai", "bombay": "Mumbai", "delhi": "Delhi",
    "new delhi": "Delhi", "bengaluru": "Bengaluru", "bangalore": "Bengaluru",
    "hyderabad": "Hyderabad", "ahmedabad": "Ahmedabad",
}


def _dest_aliases() -> Dict[str, str]:
    return {d.lower(): d for d in destinations()}


def _origin_aliases() -> Dict[str, str]:
    return {**{o.lower(): o for o in origins()}, **ORIGIN_ALIASES}

INTEREST_KEYWORDS = {
    "beach": [r"beach", r"sea\b", r"coast", r"surf"],
    "food": [r"food", r"foodie", r"eat", r"cuisine", r"street food", r"cafes?", r"restaurant"],
    "nightlife": [r"night\s?life", r"party", r"parties", r"club", r"pubs?", r"night market", r"music"],
    "heritage": [r"heritage", r"history", r"historic", r"forts?", r"palaces?", r"monuments?", r"architecture"],
    "adventure": [r"adventure", r"trek", r"rafting", r"thrill", r"paraglid", r"water ?sports", r"bungee", r"scuba", r"sports"],
    "nature": [r"nature", r"hills?", r"mountains?", r"scenic", r"lakes?", r"waterfalls?", r"sunset", r"sunrise", r"views?"],
    "spiritual": [r"spiritual", r"temples?", r"yoga", r"aarti", r"ashram", r"meditation", r"pilgrim"],
    "shopping": [r"shopping", r"bazaars?", r"markets?", r"souvenirs?", r"handicrafts?"],
    "culture": [r"culture", r"cultural", r"museums?", r"art\b", r"folk"],
    "relax": [r"relax", r"chill", r"calm", r"peaceful", r"slow"],
}

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
          "sep": 9, "oct": 10, "nov": 11, "dec": 12}
WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
            "saturday": 5, "sunday": 6, "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _num(tok: str) -> int:
    tok = tok.lower()
    return int(tok) if tok.isdigit() else NUM_WORDS[tok]


def _find_destination(t: str) -> Optional[str]:
    aliases = _dest_aliases()
    hits = [(m.start(), aliases[k]) for k in aliases for m in re.finditer(r"\b" + k + r"\b", t)]
    if not hits:
        return None
    # prefer a destination that is not the object of "from"
    for pos, name in sorted(hits):
        if not re.search(r"from\s+$", t[max(0, pos - 6):pos]):
            return name
    return sorted(hits)[0][1]


def _find_origin(t: str, dest: Optional[str]) -> Optional[str]:
    aliases = _origin_aliases()
    m = re.search(r"\bfrom\s+([a-z ]+?)(?=\s+(?:to|for|with|and|by|on|in|starting|under|within)\b|\s+\d|[,.;!?]|$)", t)
    if m:
        phrase = m.group(1).strip()
        same = _find_destination(phrase)
        if same:                      # "from Goa" where Goa is a destination, not an origin: keep it so the Planner can flag it
            return same
        for k in sorted(aliases, key=len, reverse=True):
            if re.search(r"\b" + k + r"\b", phrase):
                return aliases[k]
    for k in sorted(aliases, key=len, reverse=True):
        for mm in re.finditer(r"\b" + k + r"\b", t):
            val = aliases[k]
            if val != dest and not re.search(r"(?:to|in|at)\s+$", t[max(0, mm.start() - 4):mm.start()]):
                return val
    return None


def _find_days(t: str) -> Optional[int]:
    m = re.search(r"\b(\d+)\s*-?\s*days?\b", t)
    if m:
        return int(m.group(1))
    m = re.search(r"\b" + NUM_RE + r"[\s-]*days?\b", t)
    if m:
        return _num(m.group(1))
    m = re.search(r"\b(\d+)\s*nights?\b", t)
    if m:
        return int(m.group(1)) + 1
    if "long weekend" in t:
        return 4
    if "weekend" in t:
        return 3
    return None


def _find_travelers(t: str) -> Optional[int]:
    if re.search(r"\b(solo|alone|by myself|just me|only me)\b", t):
        return 1
    m = re.search(r"\b(?:me|i)\s+and\s+(?:my\s+)?" + NUM_RE + r"\s+(?:friends|buddies|colleagues|classmates|cousins)", t)
    if m:
        return _num(m.group(1)) + 1
    m = re.search(r"\bme\s+and\s+(?:my\s+)?(?:friend|buddy|colleague|classmate|cousin|partner|wife|husband)\b", t)
    if m:
        return 2
    m = re.search(r"\bwith\s+" + NUM_RE + r"\s+(?:friends|buddies|colleagues|classmates|cousins)", t)
    if m:
        return _num(m.group(1)) + 1
    m = re.search(r"\bfamily of\s+" + NUM_RE, t)
    if m:
        return _num(m.group(1))
    m = re.search(r"\b" + NUM_RE + r"\s+(?:of us|people|persons|person|pax|travell?ers|adults|students|members|guys|girls|friends|buddies|colleagues|classmates)", t)
    if m:
        return _num(m.group(1))
    if re.search(r"\b(couple|honeymoon)\b", t):
        return 2
    return None


def _scale(num: str, suffix: Optional[str]) -> float:
    v = float(num.replace(",", ""))
    s = (suffix or "").lower()
    if s in ("k", "thousand"):
        v *= 1_000
    elif s in ("lakh", "lac", "l"):
        v *= 100_000
    return v


def _find_budget(t: str, travelers: Optional[int]) -> Tuple[Optional[int], bool]:
    patterns = [
        r"budget\s*(?:of|is|around|about|:|-)?\s*(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|lakh|lac)?",
        r"(?:₹|rs\.?|inr)\s*(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|lakh|lac)?",
        r"(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|lakh|lac)?\s*(?:rs\b\.?|rupees|inr|₹)",
        r"under\s*(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|lakh|lac)?",
        r"\b(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|lakh|lac)?\s*(?:rs\.?|rupees|inr|₹)?\s*(?=per\s*(?:person|head|pax)|each\b|/\s*(?:person|head)|pp\b)",
        r"\b(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|lakh|lac)\b",
    ]
    for p in patterns:
        m = re.search(p, t)
        if m:
            amount = _scale(m.group(1), m.group(2))
            tail = t[m.end(): m.end() + 22]
            per_person = bool(re.search(r"per\s*(?:person|head|pax)|/\s*(?:person|head)|\bpp\b|\beach\b", tail))
            if per_person:
                amount *= max(1, travelers or 1)
            return int(amount), per_person
    return None, False


def _find_date(t: str, today: date) -> Optional[date]:
    m = re.search(r"\b(20\d\d)-(\d\d)-(\d\d)\b", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    mon = "|".join(MONTHS)
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + mon + r")[a-z]*\b", t) or None
    if m:
        day, month = int(m.group(1)), MONTHS[m.group(2)]
    else:
        m2 = re.search(r"\b(" + mon + r")[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?\b", t)
        if not m2:
            m2 = None
        if m2:
            month, day = MONTHS[m2.group(1)], int(m2.group(2))
        else:
            day = month = 0
    if day:
        try:
            d = date(today.year, month, day)
            if d <= today:
                d = date(today.year + 1, month, day)
            return d
        except ValueError:
            return None
    if "tomorrow" in t:
        return today + timedelta(days=1)
    m = re.search(r"\b(?:next|this|on)\s+(" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")\b", t)
    if m:
        return next_weekday(today, WEEKDAYS[m.group(1)], 1)
    if "weekend" in t:
        return next_weekday(today, 4, 1)
    return None


def _find_interests(t: str) -> List[str]:
    found = []
    for interest, pats in INTEREST_KEYWORDS.items():
        if any(re.search(p, t) for p in pats):
            found.append(interest)
    return [i for i in INTERESTS if i in found]


def _find_transport_pref(t: str) -> Optional[str]:
    if re.search(r"\b(cheap|cheapest|low[- ]cost|economical|save money|backpack\w*)\b", t):
        return "cheapest"
    if re.search(r"\b(fastest|quickest|fly|flight|flying|by air|plane)\b", t):
        return "fastest"
    return None


def _find_stay_pref(t: str) -> Optional[str]:
    if re.search(r"\bhostels?\b|\bdorms?\b", t):
        return "hostel"
    if re.search(r"\bhotels?\b", t):
        return "hotel"
    return None


_PLACE_STOP = {"a", "an", "the", "my", "our", "some", "any", "india", "somewhere", "anywhere", "beach", "hills", "hill", "mountains",
               "friends", "family", "weekend", "day", "days", "night", "nights", "budget", "trip", "me", "us", "you", "it", "there",
               "i", "we", "plan", "relax", "chill", "unwind", "rest", "party", "explore", "see", "eat", "enjoy", "celebrate", "get", "go", "travel", "stay", "have", "do", "adventure", "nature", "food", "culture", "heritage", "shopping", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february",
               "march", "april", "may", "june", "july", "august", "september", "october", "november", "december", "next", "this"}
_DELIM = r"(?=\s+(?:from|for|with|by|on|in|under|budget|and|starting|next|this|within|during|around|near|at|to|via|we|i|our|my)\b|[,.;!?]|\s+\d|\s*$)"


def _clean_place(p: str) -> Optional[str]:
    words = [w for w in re.split(r"\s+", p.strip()) if w]
    while words and words[0].lower() in _PLACE_STOP:
        words.pop(0)
    while words and words[-1].lower() in _PLACE_STOP:
        words.pop()
    name = " ".join(words)
    return name.title() if 2 <= len(name) <= 40 and re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", name) else None


def free_destination(text: str) -> Optional[str]:
    """A place name the catalog does not know ('Hampi', 'Mount Abu'), found by rule. None if nothing plausible."""
    for pat in (r"\b(?:to|visit|visiting|explore|exploring)\s+((?:[A-Z][A-Za-z'-]+)(?:\s+[A-Z][A-Za-z'-]+){0,2})",
                r"\b((?:[A-Z][A-Za-z'-]+)(?:\s+[A-Z][A-Za-z'-]+){0,2})\s+(?:trip|tour|getaway|holiday|vacation)\b"):
        for m in re.finditer(pat, text):
            c = _clean_place(m.group(1))
            if c and c.lower() not in _dest_aliases() and c.lower() not in _origin_aliases():
                return c
    low = " " + text.lower() + " "
    m = re.search(r"\b(?:trip|tour|holiday|vacation|getaway|visit|travel|travelling|traveling)\s+to\s+([a-z][a-z ]{1,30}?)" + _DELIM, low)
    if m:
        c = _clean_place(m.group(1))
        if c and c.lower() not in _dest_aliases() and c.lower() not in _origin_aliases():
            return c
    return None


def free_origin(text: str) -> Optional[str]:
    """'from <town>' where the town is not a known origin."""
    low = " " + text.lower() + " "
    m = re.search(r"\bfrom\s+([a-z][a-z ]{1,30}?)" + _DELIM, low)
    if not m:
        return None
    c = _clean_place(m.group(1))
    if c and c.lower() not in _origin_aliases() and c.lower() not in _dest_aliases():
        return c
    return None


def parse_rules(text: str, today: date) -> Dict[str, object]:
    """Extract whatever the text states. Missing fields are simply absent."""
    t = " " + text.lower().strip() + " "
    found: Dict[str, object] = {}
    dest = _find_destination(t)
    if dest:
        found["destination"] = dest
    org = _find_origin(t, dest)
    if org:
        found["origin"] = org
    days = _find_days(t)
    if days:
        found["days"] = days
    trav = _find_travelers(t)
    if trav:
        found["travelers"] = trav
    budget, _ = _find_budget(t, trav)
    if budget:
        found["budget"] = budget
    d = _find_date(t, today)
    if d:
        found["start_date"] = d.isoformat()
    interests = _find_interests(t)
    if interests:
        found["interests"] = interests
    pref = _find_transport_pref(t)
    if pref:
        found["transport_pref"] = pref
    stay = _find_stay_pref(t)
    if stay:
        found["stay_pref"] = stay
    return found


def build_request(text: str, defaults: dict, today: date,
                  llm_fields: Optional[dict] = None) -> Tuple[dict, dict, List[str]]:
    """Merge rule-parsed fields, optional LLM-extracted fields and sidebar defaults.

    Returns (request, provenance, notes). Provenance maps each field to text | llm | default.
    """
    notes: List[str] = []
    rules = parse_rules(text, today)
    llm_fields = llm_fields or {}
    req: Dict[str, object] = {}
    prov: Dict[str, str] = {}

    fallback = {
        "origin": defaults.get("origin", "Pune"),
        "destination": defaults.get("destination"),
        "days": defaults.get("days", 4),
        "travelers": defaults.get("travelers", 2),
        "budget": defaults.get("budget", 15000),
        "start_date": defaults.get("start_date") or next_weekday(today, 4, 2).isoformat(),
        "interests": defaults.get("interests", []),
        "transport_pref": None,
        "stay_pref": "any",
    }
    for field, dflt in fallback.items():
        if field in rules:
            req[field], prov[field] = rules[field], "text"
        elif llm_fields.get(field) not in (None, "", []):
            req[field], prov[field] = llm_fields[field], "llm"
        else:
            req[field], prov[field] = dflt, "default"

    # places the catalog does not know: remembered so the Planner can ask the generator (Ollama) to resolve them
    if "destination" not in rules and req["destination"] in (None, ""):
        cand = llm_fields.get("destination_free") or free_destination(text)
        if cand:
            req["destination_unknown"] = cand
            prov["destination"] = "text"
    if "origin" not in rules and llm_fields.get("origin") in (None, ""):
        cand = llm_fields.get("origin_free") or free_origin(text)
        if cand:
            req["origin_unknown"] = cand
            prov["origin"] = "text"

    # interests: union of rule and LLM hits (both are validated against the vocabulary)
    merged = list(dict.fromkeys(list(rules.get("interests", [])) + [i for i in llm_fields.get("interests", []) if i in INTERESTS]))
    if merged:
        req["interests"] = [i for i in INTERESTS if i in merged]
        prov["interests"] = "text" if rules.get("interests") else "llm"

    # clamp to sane ranges and say so
    def clamp(field: str, lo: int, hi: int):
        try:
            v = int(req[field])
        except (TypeError, ValueError):
            v = lo
        nv = min(hi, max(lo, v))
        if nv != v:
            notes.append(f"{field} {v} adjusted to {nv} (supported range {lo}-{hi})")
        req[field] = nv

    clamp("days", 2, 7)
    clamp("travelers", 1, 8)
    clamp("budget", 500, 1_000_000)
    if req["origin"] not in origins() and req["origin"] != req["destination"] and not req.get("origin_unknown"):
        notes.append(f"Unknown origin {req['origin']!r}; using Pune")
        req["origin"] = "Pune"
    if req["destination"] and req["destination"] not in destinations():
        notes.append(f"Unknown destination {req['destination']!r}")
        req["destination"] = None
    if req["destination"] == req["origin"]:
        notes.append("Origin and destination are the same")
    if not req["interests"]:
        req["interests"] = ["heritage", "food"]
        prov["interests"] = "default"
    req["raw_text"] = text
    return req, prov, notes
