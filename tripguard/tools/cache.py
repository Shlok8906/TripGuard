"""Tiny JSON disk cache used by the live adapters (live -> disk cache -> bundled data)."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional, Tuple

from ..config import CACHE_DIR


def _path(key: str):
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
    return CACHE_DIR / f"{safe}.json"


def save(key: str, payload: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        body = {"fetched_at": datetime.now().isoformat(timespec="seconds"), "payload": payload}
        _path(key).write_text(json.dumps(body), encoding="utf-8")
    except OSError:
        pass  # caching is best-effort


def load(key: str) -> Optional[Tuple[dict, float]]:
    """Return (payload, age_seconds) or None."""
    p = _path(key)
    try:
        body = json.loads(p.read_text(encoding="utf-8"))
        age = (datetime.now() - datetime.fromisoformat(body["fetched_at"])).total_seconds()
        return body["payload"], max(0.0, age)
    except (OSError, ValueError, KeyError):
        return None
