"""Weather adapter: Open-Meteo (live, no API key) -> disk cache -> bundled climatology.

Every result carries provenance (`source`) so the Critic can check freshness and the UI can
show which data came from where.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List

import requests

from ..config import HTTP_TIMEOUT_S
from . import cache
from .catalog import destination

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
FORECAST_HORIZON_DAYS = 15


def _climatology(dest: str, dates: List[date]) -> Dict[str, dict]:
    clim = destination(dest)["climate"]
    out = {}
    for d in dates:
        m = d.month - 1
        out[d.isoformat()] = {
            "rain_prob": clim["rain_prob"][m],
            "tmax": clim["tmax"][m],
            "tmin": clim["tmin"][m],
        }
    return out


def _fetch_open_meteo(dest: str, dates: List[date]) -> Dict[str, dict]:
    cfg = destination(dest)
    params = {
        "latitude": cfg["lat"], "longitude": cfg["lon"],
        "daily": "precipitation_probability_max,temperature_2m_max,temperature_2m_min",
        "timezone": "Asia/Kolkata",
        "start_date": dates[0].isoformat(), "end_date": dates[-1].isoformat(),
    }
    r = requests.get(OPEN_METEO, params=params, timeout=HTTP_TIMEOUT_S)
    r.raise_for_status()
    daily = r.json()["daily"]
    out = {}
    for i, day in enumerate(daily["time"]):
        prob = daily["precipitation_probability_max"][i]
        out[day] = {
            "rain_prob": int(prob) if prob is not None else 0,
            "tmax": daily["temperature_2m_max"][i],
            "tmin": daily["temperature_2m_min"][i],
        }
    if len(out) < len(dates):
        raise ValueError("incomplete forecast")
    return out


def get_weather(dest: str, dates: List[date], live: bool, simulate_down: bool, today: date) -> dict:
    """Return {'days': {iso: {...}}, 'source': {...}, 'notes': [(status, text), ...]}."""
    notes = []
    key = f"weather_{dest}_{dates[0]}_{dates[-1]}"

    def src(kind: str, name: str, detail: str, age_s: float = 0.0) -> dict:
        return {"name": name, "kind": kind, "detail": detail, "age_s": round(age_s),
                "fetched_at": datetime.now().isoformat(timespec="seconds")}

    if live:
        horizon_ok = (dates[-1] - today).days <= FORECAST_HORIZON_DAYS and dates[0] >= today
        if simulate_down:
            notes.append(("warn", "API outage simulated - falling back to cache / climatology"))
        elif not horizon_ok:
            notes.append(("info", "trip dates are outside the 15-day forecast window - using climatology"))
        else:
            try:
                days = _fetch_open_meteo(dest, dates)
                cache.save(key, days)
                notes.append(("ok", "live Open-Meteo forecast fetched"))
                return {"days": days, "source": src("live", "Open-Meteo", "live forecast"), "notes": notes}
            except Exception as e:  # network, HTTP, JSON - anything: fall back
                notes.append(("warn", f"live API failed ({type(e).__name__}) - falling back"))
        hit = cache.load(key)
        if hit:
            days, age = hit
            notes.append(("warn", f"using disk cache ({int(age // 60)} min old)"))
            return {"days": days, "source": src("cache", "Open-Meteo (cached)", "disk cache", age), "notes": notes}

    notes.append(("info", "bundled monthly climatology (offline-safe mode)"))
    return {"days": _climatology(dest, dates),
            "source": src("bundled", "Climatology table", "monthly averages shipped with the app"),
            "notes": notes}
