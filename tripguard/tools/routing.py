"""Local travel-time adapter: OSRM table service (live) -> disk cache -> haversine estimate."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Dict, List, Optional

import requests

from ..config import HTTP_TIMEOUT_S
from ..utils import haversine_km
from . import cache
from .catalog import destination

OSRM_TABLE = "https://router.project-osrm.org/table/v1/driving/"


def build_points(dest: str) -> List[dict]:
    d = destination(dest)
    pts = []
    for coll in ("stays", "attractions", "food"):
        for p in d[coll]:
            pts.append({"id": p["id"], "lat": p["lat"], "lon": p["lon"]})
    return pts


def _fetch_osrm(points: List[dict]) -> List[List[Optional[float]]]:
    coords = ";".join(f"{p['lon']},{p['lat']}" for p in points)
    r = requests.get(OSRM_TABLE + coords, params={"annotations": "duration"}, timeout=HTTP_TIMEOUT_S)
    r.raise_for_status()
    body = r.json()
    if body.get("code") != "Ok":
        raise ValueError(f"OSRM code {body.get('code')}")
    return body["durations"]


def get_matrix(dest: str, live: bool, simulate_down: bool) -> dict:
    """Return {'index': {id: i}, 'minutes': [[...]] | None, 'source': {...}, 'notes': [...]}."""
    points = build_points(dest)
    index = {p["id"]: i for i, p in enumerate(points)}
    notes = []
    key = f"osrm_{dest}"

    def src(kind: str, name: str, detail: str, age_s: float = 0.0) -> dict:
        return {"name": name, "kind": kind, "detail": detail, "age_s": round(age_s),
                "fetched_at": datetime.now().isoformat(timespec="seconds")}

    def to_minutes(durations):
        # driving seconds -> realistic local minutes (traffic, parking, walking) with a floor
        return [[None if s is None else round(s / 60 * 1.25 + 5, 1) for s in row] for row in durations]

    if live:
        if simulate_down:
            notes.append(("warn", "API outage simulated - falling back to cache / estimate"))
        else:
            try:
                minutes = to_minutes(_fetch_osrm(points))
                cache.save(key, minutes)
                notes.append(("ok", f"live OSRM travel-time matrix fetched ({len(points)} places)"))
                return {"index": index, "minutes": minutes,
                        "source": src("live", "OSRM", "live travel-time matrix"), "notes": notes}
            except Exception as e:
                notes.append(("warn", f"live API failed ({type(e).__name__}) - falling back"))
        hit = cache.load(key)
        if hit:
            minutes, age = hit
            notes.append(("warn", f"using disk cache ({int(age // 60)} min old)"))
            return {"index": index, "minutes": minutes,
                    "source": src("cache", "OSRM (cached)", "disk cache", age), "notes": notes}

    notes.append(("info", "straight-line distance estimate (offline-safe mode)"))
    return {"index": index, "minutes": None,
            "source": src("bundled", "Haversine estimate", "straight-line x road factor / local speed"),
            "notes": notes}


def road_km(a: dict, b: dict) -> float:
    return haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) * 1.35


def travel_minutes(dest: str, a: dict, b: dict, matrix: Optional[dict]) -> int:
    """Minutes to get from place a to place b (dicts with id/lat/lon)."""
    if a.get("id") == b.get("id") and a.get("id") is not None:
        return 0
    if matrix and matrix.get("minutes"):
        i = matrix["index"].get(a.get("id"))
        j = matrix["index"].get(b.get("id"))
        if i is not None and j is not None:
            m = matrix["minutes"][i][j]
            if m is not None:
                return max(10, int(math.ceil(m)))
    speed = destination(dest)["local"]["speed_kmh"]
    km = road_km(a, b)
    return max(10, int(math.ceil(km / speed * 60 + 5)))
