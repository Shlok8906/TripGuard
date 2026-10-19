"""Workflow orchestration.

The agents are plain functions `state -> partial update`. They are wired into a LangGraph
StateGraph when `langgraph` is installed, and into an equivalent built-in runner otherwise
(or if LangGraph raises for any reason), so the app always works.

        START --> planner --> research --> executor --> critic --+--> finalize --> END
          |          ^                                           |
          |          +------------- rejected (retries left) ----+
          +--> disruption --> research  (targeted repair loop re-enters here)
"""
from __future__ import annotations

import copy
from functools import lru_cache
from typing import Callable, Dict, Iterator, Tuple

from .agents.critic import critic_node
from .agents.disruption import disruption_node
from .agents.executor import executor_node
from .agents.finalize import finalize_node
from .agents.planner import MAX_LEVEL, planner_node
from .agents.research import research_node
from .state import TripState

END = "__end__"

NODE_FUNCS: Dict[str, Callable[[dict], dict]] = {
    "planner_agent": planner_node,
    "research_agent": research_node,
    "executor_agent": executor_node,
    "critic_agent": critic_node,
    "disruption_agent": disruption_node,
    "finalize_agent": finalize_node,
}


# --------------------------------------------------------------------------- routing (shared by both engines)
def route_entry(state: dict) -> str:
    return "disruption_agent" if state.get("mode") == "disrupt" else "planner_agent"


def route_after_planner(state: dict) -> str:
    return "finalize_agent" if state.get("halt") else "research_agent"


def route_after_critic(state: dict) -> str:
    critic = state.get("critic") or {}
    if critic.get("passed") or critic.get("unfixable"):
        return "finalize_agent"
    codes = {i["code"] for i in critic.get("issues", [])}
    if codes <= {"over_budget"} and state["strategy"]["level"] >= MAX_LEVEL:
        return "finalize_agent"          # strategy ladder exhausted, nothing cheaper to try
    if state.get("retries_used", 0) >= state["settings"].get("max_retries", 4):
        return "finalize_agent"
    fixable = {"over_budget", "hours_conflict", "weather_conflict", "closed_place"}
    if not (codes & fixable) and state.get("retries_used", 0) >= 1:
        return "finalize_agent"          # relaxing cannot fix this kind of rule failure
    return "planner_agent"


def next_node(node: str, state: dict) -> str:
    if node == "planner_agent" or node == "disruption_agent":
        return route_after_planner(state)
    if node == "research_agent":
        return "executor_agent"
    if node == "executor_agent":
        return "critic_agent"
    if node == "critic_agent":
        return route_after_critic(state)
    return END


# --------------------------------------------------------------------------- built-in engine
def _stream_builtin(state: dict, note: str = "") -> Iterator[Tuple[str, dict]]:
    state = copy.deepcopy(state)
    state["engine"] = "builtin"
    if note:
        state["trace"] = list(state.get("trace", [])) + [{
            "i": len(state.get("trace", [])) + 1, "ts": "", "agent": "Orchestrator", "attempt": 0,
            "action": "Engine", "detail": note, "status": "warn"}]
    node = route_entry(state)
    for _ in range(80):
        if node == END:
            break
        state.update(NODE_FUNCS[node](state))
        state["last_node"] = node
        yield node, state
        node = next_node(node, state)


# --------------------------------------------------------------------------- LangGraph engine
def langgraph_available() -> bool:
    try:
        import langgraph.graph  # noqa: F401
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def build_langgraph():
    from langgraph.graph import END as LG_END
    from langgraph.graph import START, StateGraph

    g = StateGraph(TripState)
    for name, fn in NODE_FUNCS.items():
        g.add_node(name, fn)
    g.add_conditional_edges(START, route_entry,
                            {"planner_agent": "planner_agent", "disruption_agent": "disruption_agent"})
    after = {"research_agent": "research_agent", "finalize_agent": "finalize_agent"}
    g.add_conditional_edges("planner_agent", route_after_planner, after)
    g.add_conditional_edges("disruption_agent", route_after_planner, after)
    g.add_edge("research_agent", "executor_agent")
    g.add_edge("executor_agent", "critic_agent")
    g.add_conditional_edges("critic_agent", route_after_critic,
                            {"finalize_agent": "finalize_agent", "planner_agent": "planner_agent"})
    g.add_edge("finalize_agent", LG_END)
    return g.compile()


def _stream_langgraph(state: dict) -> Iterator[Tuple[str, dict]]:
    app = build_langgraph()
    last = None
    for chunk in app.stream(copy.deepcopy(state), config={"recursion_limit": 100}, stream_mode="values"):
        chunk = dict(chunk)
        chunk["engine"] = "langgraph"
        last = chunk
        yield chunk.get("last_node", "?"), chunk
    if not last or last.get("last_node") != "finalize_agent":
        raise RuntimeError("LangGraph run did not reach the finalize step")


# --------------------------------------------------------------------------- public API
def stream_workflow(state: TripState, engine: str = "") -> Iterator[Tuple[str, dict]]:
    """Yield (node_name, state) after every agent step so a UI can update live."""
    engine = engine or state.get("settings", {}).get("engine", "auto")
    note = ""
    if engine in ("auto", "langgraph"):
        if langgraph_available():
            produced = False
            try:
                for node, st in _stream_langgraph(state):
                    produced = True
                    yield node, st
                return
            except Exception as e:  # any LangGraph/API problem: fall back to the built-in engine
                note = f"LangGraph failed ({type(e).__name__}: {e}) - switched to the built-in engine"
        elif engine == "langgraph":
            note = "LangGraph is not installed - using the built-in engine"
    yield from _stream_builtin(state, note)


def run_workflow(state: TripState, engine: str = "") -> dict:
    final = None
    for _, st in stream_workflow(state, engine):
        final = st
    return final or dict(state)
