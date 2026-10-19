"""Disruption Agent: turns a real-world event into constraints and a targeted repair request.

Supported events
    outbound_delay     {"minutes": 180}
    return_cancelled   {}
    rain_day           {"day": 2}              (1-based day number)
    attraction_closed  {"attraction_id": "goa-aguada"}

The agent never edits the itinerary itself. It (1) analyses the impact, (2) locks everything that
is still valid (booked legs, stay), (3) marks the affected days and (4) hands over to the usual
Research -> Executor -> Critic loop, which repairs only what is needed.
"""
from __future__ import annotations

import copy
from datetime import date, timedelta

from ..tools.catalog import destination, transport_options
from ..trace import Tracer, set_task
from ..utils import from_iso, inr

EVENT_TYPES = {
    "outbound_delay": "Outbound journey delayed",
    "return_cancelled": "Return journey cancelled",
    "rain_day": "Heavy rain on a day",
    "attraction_closed": "Attraction closed",
}


def describe_event(plan: dict, ev: dict) -> str:
    t = ev["type"]
    if t == "outbound_delay":
        return f"Outbound {plan['transport']['outbound']['label']} delayed by {int(ev['minutes'])} min"
    if t == "return_cancelled":
        return f"Return {plan['transport']['return']['label']} cancelled"
    if t == "rain_day":
        return f"Heavy rain forecast on Day {int(ev['day'])}"
    if t == "attraction_closed":
        names = {a["id"]: a["name"] for a in destination(plan["destination"])["attractions"]}
        return f"{names.get(ev['attraction_id'], ev['attraction_id'])} is closed"
    return t


def disruption_node(state: dict) -> dict:
    view = {**state, "attempt": 1}
    T = Tracer(view, "Disruption")
    ev = copy.deepcopy(state["disruption"])
    plan = state.get("plan")
    if plan is None or ev.get("type") not in EVENT_TYPES:
        T.log("Cannot apply event", "no plan to repair or unknown event type", "fail")
        return {"halt": True, "status": "needs_input", "message": "There is no plan to repair yet.",
                "trace": T.merged(state), "last_node": "disruption"}

    req = state["request"]
    cons = copy.deepcopy(state["constraints"])
    prior = copy.deepcopy(plan)
    start = date.fromisoformat(prior["start_date"])
    out, ret = prior["transport"]["outbound"], prior["transport"]["return"]
    cons["locked_out_id"] = out["id"]
    cons["locked_ret_id"] = ret["id"]
    cons["locked_stay_id"] = prior["stay"]["id"] if prior["stay"] else None
    affected = set()
    impact = []

    t = ev["type"]
    ev["label"] = describe_event(prior, ev)
    T.log("Event received", ev["label"], "warn")

    if t == "outbound_delay":
        minutes = int(ev["minutes"])
        old = from_iso(out["arrive"])
        new = old + timedelta(minutes=minutes)
        cons["delay_out_min"] = cons.get("delay_out_min", 0) + minutes
        i0, i1 = (old.date() - start).days, (new.date() - start).days
        affected = set(range(min(i0, i1), max(i0, i1) + 1))
        impact.append(f"Arrival moves {old:%a %H:%M} -> {new:%a %H:%M}")
        impact.append(f"Day {i0 + 1} schedule must start later" + (f"; arrival now on Day {i1 + 1}" if i1 != i0 else ""))
    elif t == "return_cancelled":
        cons["excluded_transport"] = list(cons.get("excluded_transport", [])) + [f"ret:{ret['id']}"]
        cons["locked_ret_id"] = None
        ret_idx = (from_iso(ret["depart"]).date() - start).days
        affected = {ret_idx}
        remaining = [o for o in transport_options(req["origin"], req["destination"])["options"]
                     if f"ret:{o['id']}" not in cons["excluded_transport"]]
        impact.append(f"Return of {from_iso(ret['depart']):%a %H:%M} is gone; {len(remaining)} alternatives remain")
        impact.append(f"Day {ret_idx + 1} schedule depends on the new departure time")
    elif t == "rain_day":
        day = int(ev["day"])
        day = max(1, min(day, len(prior["days"])))
        iso = prior["days"][day - 1]["date"]
        cons["rain_override"] = {**cons.get("rain_override", {}), iso: 90}
        affected = {day - 1}
        outdoor = [i["name"] for i in prior["days"][day - 1]["items"] if i["type"] == "activity"]
        impact.append(f"Day {day} rain risk set to 90%; outdoor activities are not allowed")
        if outdoor:
            impact.append("Activities to re-check: " + ", ".join(outdoor))
    elif t == "attraction_closed":
        pid = ev["attraction_id"]
        cons["closed_attractions"] = list(dict.fromkeys(list(cons.get("closed_attractions", [])) + [pid]))
        for i, d in enumerate(prior["days"]):
            if any(it["place_id"] == pid for it in d["items"]):
                affected.add(i)
        impact.append("Planned on Day(s) " + (", ".join(str(i + 1) for i in sorted(affected)) or "none (not in the current plan)"))

    for line in impact:
        T.log("Impact analysis", line, "info")
    kept = [f"Day {i + 1}" for i in range(len(prior["days"])) if i not in affected]
    T.log("Repair plan",
          f"locked: outbound, return{' (unless cancelled)' if t == 'return_cancelled' else ''}, stay; "
          f"re-plan: {', '.join(f'Day {i + 1}' for i in sorted(affected)) or 'nothing'}; keep: {', '.join(kept) or 'none'}", "info")

    ev["impact"] = impact
    ev["affected_days"] = sorted(affected)
    tasks = state.get("tasks", [])
    for tid in ("T2", "T3", "T4", "T5", "T6", "T7"):
        tasks = set_task(tasks, tid, "pending")
    return {"mode": "disrupt", "prior_plan": prior, "constraints": cons, "disruption": ev,
            "affected_days": sorted(affected), "full_replan": False, "attempt": 1, "retries_used": 0,
            "critic": {}, "halt": False, "tasks": tasks, "narrative": "", "diff": {},
            "trace": T.merged(state), "last_node": "disruption"}
