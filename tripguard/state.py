"""Shared workflow state (a plain, JSON-serialisable dict) used by every agent."""
from __future__ import annotations

import copy
from typing import Any, Dict, Optional

from typing_extensions import TypedDict

from .config import DEFAULT_SETTINGS


class TripState(TypedDict, total=False):
    mode: str                 # "plan" | "disrupt"
    text: str                 # the user's natural-language request
    settings: dict            # llm / live / simulate_down / max_retries / ...
    defaults: dict            # sidebar defaults used when the text omits a field
    request: dict             # structured request produced by the Planner
    provenance: dict          # field -> "text" | "llm" | "default"
    strategy: dict            # current planning strategy (relaxation level etc.)
    tasks: list               # task list produced by the Planner
    research: dict            # data gathered by the Research agent
    plan: dict                # itinerary + costs produced by the Executor
    prior_plan: dict          # previous plan (kept when repairing after a disruption)
    critic: dict              # validation report
    attempt: int
    retries_used: int
    constraints: dict         # disruption-driven constraints
    disruption: dict          # last disruption event + impact analysis
    affected_days: list
    full_replan: bool
    trace: list               # agent activity log shown in the UI
    attempts: list            # per-attempt history (strategy, total, failing checks)
    narrative: str
    narrative_source: str
    diff: dict
    status: str               # ok | warnings | infeasible | needs_input
    message: str
    halt: bool
    engine: str
    last_node: str


def empty_constraints() -> Dict[str, Any]:
    return {
        "delay_out_min": 0,
        "excluded_transport": [],
        "closed_attractions": [],
        "rain_override": {},
        "extra_days": 0,
    }


def new_state(text: str, settings: Optional[dict] = None, defaults: Optional[dict] = None) -> TripState:
    s = {**DEFAULT_SETTINGS, **(settings or {})}
    return TripState(
        mode="plan",
        text=text,
        settings=s,
        defaults=copy.deepcopy(defaults or {}),
        attempt=0,
        retries_used=0,
        constraints=empty_constraints(),
        trace=[],
        attempts=[],
        tasks=[],
        halt=False,
        full_replan=False,
        affected_days=[],
        last_node="start",
    )
