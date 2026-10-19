"""Planner Agent: understands the request, makes the task plan and chooses/relaxes the strategy.

Decision logic (the 'brain' of the retry loop):
  * First pass: parse the request (rules, optionally gap-filled by a local LLM), build the task
    list, run a quick feasibility pre-check and start with the most comfortable strategy.
  * After a Critic rejection: read the machine-readable issues, relax the strategy one or two
    levels depending on how far over budget the plan is, and exclude places that broke rules.
"""
from __future__ import annotations

import copy
import math
from datetime import date

from ..generator import resolve_places
from ..llm import LLMError, get_llm
from ..parser import build_request
from ..tools.catalog import builtin_destinations, destination, is_generated, transport_options
from ..trace import Tracer, set_task
from ..utils import inr

# Relaxation ladder: each level trades comfort for cost.
LEVELS = [
    {"label": "Comfort-first", "transport_pref": "comfort", "stay_policy": "best_value",
     "activity_policy": "all_interest", "food_tier": "mixed", "transport_share": 0.50},
    {"label": "Value transport", "transport_pref": "balanced", "stay_policy": "best_value",
     "activity_policy": "all_interest", "food_tier": "mixed", "transport_share": 0.40},
    {"label": "Cheapest transport + stay", "transport_pref": "cheapest", "stay_policy": "cheapest",
     "activity_policy": "all_interest", "food_tier": "mixed", "transport_share": 0.35},
    {"label": "Mostly free activities, budget food", "transport_pref": "cheapest", "stay_policy": "cheapest",
     "activity_policy": "mostly_free", "food_tier": "budget", "transport_share": 0.35},
    {"label": "Free activities, street food", "transport_pref": "cheapest", "stay_policy": "cheapest",
     "activity_policy": "free_only", "food_tier": "street", "transport_share": 0.35},
]
MAX_LEVEL = len(LEVELS) - 1

TASKS = [
    ("T1", "Planner", "Understand the request and set the strategy"),
    ("T2", "Research", "Collect transport, stays, places and food options"),
    ("T3", "Research", "Fetch weather and local travel times (live, cache or bundled)"),
    ("T4", "Executor", "Select transport and a stay"),
    ("T5", "Executor", "Schedule each day and price the plan"),
    ("T6", "Critic", "Validate the plan against the rules"),
    ("T7", "Finalize", "Write the summary and wait for approval"),
]


def strategy_for(level: int, req: dict) -> dict:
    level = max(0, min(MAX_LEVEL, level))
    s = copy.deepcopy(LEVELS[level])
    s["level"] = level
    pref = req.get("transport_pref")
    if pref == "cheapest":
        s["transport_pref"] = "cheapest"
    elif pref == "fastest":
        s["transport_pref"] = "fastest" if level <= 1 else ("balanced" if level == 2 else "cheapest")
    return s


def rough_minimum(req: dict) -> int:
    """Optimistic lower bound on the trip cost (cheapest fares, dorm beds, street food)."""
    dest = req["destination"]
    d = destination(dest)
    t = req["travelers"]
    opts = transport_options(req["origin"], dest)["options"]
    fare = min(o["fare_pp"] for o in opts) * 2 * t
    nights = max(0, req["days"] - 2)
    stay = min((t if s["unit"] == "bed" else math.ceil(t / s["capacity"])) * s["price"] for s in d["stays"]) * nights
    meals = min(f["cost_pp"] for f in d["food"]) * 2 * max(1, req["days"] - 1) * t
    local = d["local"]["station_transfer"] * 2
    return int((fare + stay + meals + local) * 1.05)


def _tasks():
    return [{"id": i, "agent": a, "desc": d, "status": "pending"} for i, a, d in TASKS]


def planner_node(state: dict) -> dict:
    T = Tracer(state, "Planner")
    settings = state["settings"]

    # ------------------------------------------------------------------ first pass
    if not state.get("request"):
        today = date.fromisoformat(settings["today"]) if settings.get("today") else date.today()
        llm_fields = {}
        llm = get_llm(settings)
        if llm.is_real:
            try:
                llm_fields = llm.extract_request(state["text"]) or {}
                T.log("LLM extraction", f"{llm.name} proposed: {', '.join(sorted(llm_fields)) or 'nothing'}", "ok")
            except LLMError as e:
                T.log("LLM unavailable", f"{e} - using the rule-based parser only", "warn")
        else:
            T.log("Parsing request", "rule-based parser (mock model)", "info")
        req, prov, notes = build_request(state["text"], state.get("defaults", {}), today, llm_fields)
        for n in notes:
            T.log("Adjusted input", n, "warn")

        if req.get("destination_unknown") or req.get("origin_unknown"):
            err = resolve_places(req, settings, T.log)
            if err:
                return {"request": req, "provenance": prov, "halt": True, "status": "needs_input", "message": err,
                        "tasks": set_task(_tasks(), "T1", "failed"), "trace": T.merged(state), "last_node": "planner"}
        if req.get("destination") and is_generated(req["destination"]):
            req["generated"] = True
            prov["destination"] = "generated"

        if not req["destination"]:
            hint = ", ".join(builtin_destinations())
            T.log("Need more information", f"No destination found. Name a place in India (built-in: {hint}).", "fail")
            return {"request": req, "provenance": prov, "halt": True, "status": "needs_input",
                    "message": "I couldn't tell where you want to go. Name a destination in India - built-in: "
                               + hint + ". Other places need the Ollama model (sidebar).",
                    "tasks": set_task(_tasks(), "T1", "failed"), "trace": T.merged(state), "last_node": "planner"}

        if req["origin"] == req["destination"]:
            T.log("Invalid request", "Origin and destination are the same", "fail")
            return {"request": req, "provenance": prov, "halt": True, "status": "needs_input",
                    "message": "Origin and destination are the same city - pick a different destination.",
                    "tasks": set_task(_tasks(), "T1", "failed"), "trace": T.merged(state), "last_node": "planner"}

        shown = ", ".join(f"{k}={req[k]}" for k in ("origin", "destination", "days", "travelers", "budget"))
        T.log("Understood request", f"{shown}; interests: {', '.join(req['interests'])}", "ok")
        defaulted = [k for k, v in prov.items() if v == "default" and k not in ("transport_pref", "stay_pref")]
        if defaulted:
            T.log("Used defaults", ", ".join(defaulted), "info")

        lo = rough_minimum(req)
        if lo > req["budget"]:
            T.log("Feasibility pre-check",
                  f"Rough minimum ~{inr(lo)} exceeds budget {inr(req['budget'])} - will try, but expect rejection", "warn")
        else:
            T.log("Feasibility pre-check", f"Rough minimum ~{inr(lo)} vs budget {inr(req['budget'])} - plausible", "ok")

        strat = strategy_for(0, req)
        T.log("Strategy chosen", f"Level 0 - {strat['label']} (transport: {strat['transport_pref']}, "
              f"stay: {strat['stay_policy']}, activities: {strat['activity_policy']}, food: {strat['food_tier']})", "info")
        T.log("Task plan", "T1 parse -> T2/T3 research -> T4/T5 build -> T6 validate -> T7 finalize", "info")
        return {"request": req, "provenance": prov, "strategy": strat, "attempt": 1, "full_replan": True,
                "tasks": set_task(_tasks(), "T1", "done"), "trace": T.merged(state), "last_node": "planner"}

    # ------------------------------------------------------------------ replanning after a rejection
    critic = state.get("critic", {})
    issues = critic.get("issues", [])
    req = state["request"]
    strat = state["strategy"]
    cons = copy.deepcopy(state["constraints"])
    level = strat["level"]

    over = next((i for i in issues if i["code"] == "over_budget"), None)
    jump = 0
    if over:
        gap = over["data"]["by"] / max(1, req["budget"])
        jump = 2 if gap > 0.50 else 1
        T.log("Reading Critic feedback", f"over budget by {inr(over['data']['by'])} ({gap:.0%}) -> relax {jump} level(s)", "warn")
    excluded_any = False
    for i in issues:
        if i["code"] in ("hours_conflict", "weather_conflict", "closed_place") and i["data"].get("place_id"):
            pid = i["data"]["place_id"]
            if pid not in cons["closed_attractions"]:
                cons["closed_attractions"].append(pid)
                excluded_any = True
                T.log("Excluding place", f"{i['data'].get('name', pid)} broke a rule ({i['code']})", "warn")
    if jump == 0 and not excluded_any:
        jump = 1   # unknown rule failure: still try a different strategy
        T.log("Reading Critic feedback", "no budget issue - trying the next strategy anyway", "warn")

    new_level = min(MAX_LEVEL, level + jump)
    new = strategy_for(new_level, req)
    if new_level != level:
        T.log("Relaxing strategy",
              f"Level {level} -> {new_level}: {new['label']} (transport: {new['transport_pref']}, stay: {new['stay_policy']}, "
              f"activities: {new['activity_policy']}, food: {new['food_tier']})", "info")
        if req.get("transport_pref") == "fastest" and new["transport_pref"] != "fastest":
            T.log("Preference relaxed", "dropping the 'fastest' transport preference to save money", "warn")
    # after a disruption the booked outbound leg stays locked, but the stay may change when relaxing
    if state.get("mode") == "disrupt":
        cons["locked_stay_id"] = None
    tasks = [dict(t, status="pending") if t["id"] in ("T4", "T5", "T6") else t for t in state.get("tasks", [])]
    return {"strategy": new, "constraints": cons, "attempt": state.get("attempt", 1) + 1,
            "retries_used": state.get("retries_used", 0) + 1, "full_replan": True,
            "tasks": tasks, "trace": T.merged(state), "last_node": "planner"}
