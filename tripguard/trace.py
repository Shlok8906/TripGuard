"""Agent activity log. Each node collects events with a Tracer and returns them in its update."""
from __future__ import annotations

import time
from typing import List


class Tracer:
    def __init__(self, state: dict, agent: str):
        self.agent = agent
        self.attempt = state.get("attempt", 0)
        self.base = len(state.get("trace", []))
        self.events: List[dict] = []

    def log(self, action: str, detail: str = "", status: str = "info") -> None:
        self.events.append({
            "i": self.base + len(self.events) + 1,
            "ts": time.strftime("%H:%M:%S"),
            "agent": self.agent,
            "attempt": self.attempt,
            "action": action,
            "detail": detail,
            "status": status,      # info | ok | warn | fail
        })

    def merged(self, state: dict) -> List[dict]:
        return list(state.get("trace", [])) + self.events


def set_task(tasks: list, task_id: str, status: str) -> list:
    out = []
    for t in tasks:
        t = dict(t)
        if t["id"] == task_id:
            t["status"] = status
        out.append(t)
    return out
