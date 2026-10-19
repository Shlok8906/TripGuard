"""Small shared helpers (time, distance, money formatting)."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Optional


def hhmm_to_min(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def at(d: date, hhmm: str) -> datetime:
    h, m = hhmm.split(":")
    return datetime(d.year, d.month, d.day, int(h), int(m))


def to_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


def from_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    dp = p2 - p1
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def round_to(x: float, base: int = 10) -> int:
    return int(round(x / base) * base)


def inr(n: float) -> str:
    """Format a number as Indian-grouped rupees, e.g. 123456 -> ₹1,23,456."""
    n = int(round(n))
    sign = "-" if n < 0 else ""
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return f"{sign}₹{s}"


def next_weekday(today: date, weekday: int, min_days_ahead: int = 1) -> date:
    """Next date with the given weekday (Mon=0) at least `min_days_ahead` days after today."""
    d = today + timedelta(days=min_days_ahead)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


def parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    return date.fromisoformat(s[:10])


def cap1(s: str) -> str:
    """Capitalise only the first letter (keeps acronyms such as 3AC / AC intact)."""
    return s[:1].upper() + s[1:] if s else s
