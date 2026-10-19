"""High-level helpers used by the Streamlit app, the CLI and the tests."""
from __future__ import annotations

import copy
from typing import Iterator, Optional, Tuple

from .agents.disruption import EVENT_TYPES
from .graph import run_workflow, stream_workflow
from .state import new_state

__all__ = ["plan_trip", "stream_plan", "disrupt_state", "stream_disruption", "apply_disruption", "EVENT_TYPES"]


def plan_trip(text: str, settings: Optional[dict] = None, defaults: Optional[dict] = None) -> dict:
    return run_workflow(new_state(text, settings, defaults))


def stream_plan(text: str, settings: Optional[dict] = None,
                defaults: Optional[dict] = None) -> Iterator[Tuple[str, dict]]:
    return stream_workflow(new_state(text, settings, defaults))


def disrupt_state(state: dict, event: dict) -> dict:
    """Return a copy of a finished state, ready to be re-run as a disruption repair."""
    st = copy.deepcopy(state)
    st["mode"] = "disrupt"
    st["disruption"] = dict(event)
    st["halt"] = False
    return st


def stream_disruption(state: dict, event: dict) -> Iterator[Tuple[str, dict]]:
    return stream_workflow(disrupt_state(state, event))


def apply_disruption(state: dict, event: dict) -> dict:
    return run_workflow(disrupt_state(state, event))
