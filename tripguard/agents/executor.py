"""Executor Agent: runs the Python tools that turn research data into a priced itinerary."""
from __future__ import annotations

from ..planning import build_plan
from ..trace import Tracer, set_task
from ..utils import cap1, from_iso, hm, inr


def executor_node(state: dict) -> dict:
    T = Tracer(state, "Executor")
    req, strat, research = state["request"], state["strategy"], state["research"]
    import copy
    cons = copy.deepcopy(state["constraints"])
    prior = state.get("prior_plan")
    full = bool(state.get("full_replan", True)) or prior is None
    repair = state.get("mode") == "disrupt" and not full

    if repair:
        T.log("Targeted repair", f"re-planning only day(s) {[i + 1 for i in state.get('affected_days', [])]}; "
              "other days are kept as they are", "info")
    plan, notes = build_plan(req, strat, cons, research, state.get("mode", "plan"),
                             prior, state.get("affected_days"), full)
    for status, text in notes:
        T.log("Transport", text, status)

    if plan is None:
        T.log("Build failed", "no feasible transport combination for these dates", "fail")
        return {"plan": None, "constraints": cons, "tasks": set_task(state.get("tasks", []), "T4", "failed"),
                "trace": T.merged(state), "last_node": "executor"}

    o, r = plan["transport"]["outbound"], plan["transport"]["return"]
    delay = f" (delayed {o['delay_min']} min)" if o["delay_min"] else ""
    fmt = lambda iso: from_iso(iso).strftime("%a %H:%M")
    T.log("Transport selected",
          f"out: {cap1(o['label'])} {fmt(o['depart'])} -> {fmt(o['arrive'])}{delay} | "
          f"return: {cap1(r['label'])} {fmt(r['depart'])} -> {fmt(r['arrive'])} | fares {inr(o['fare_pp'] + r['fare_pp'])} per person", "ok")
    st = plan["stay"]
    if st:
        T.log("Stay selected", f"{st['name']} - {st['units']} {st['unit']}(s) x {st['nights']} night(s) = {inr(st['cost_total'])}", "ok")
    else:
        T.log("Stay", "no overnight stay needed", "info")
    for d in plan["days"]:
        acts = [it["name"] for it in d["items"] if it["type"] == "activity"]
        w = f"{d['window'][0]}-{d['window'][1]}" if d["window"] else "travel day"
        T.log(f"Day {d['day_no']} scheduled", f"{w}; {len(acts)} activities" + (f": {', '.join(acts)}" if acts else ""), "info")
    c = plan["costs"]
    T.log("Costing", f"subtotal {inr(c['subtotal'])} + reserve {inr(c['reserve'])} = {inr(c['grand_total'])} "
          f"(budget {inr(req['budget'])})", "info")
    tasks = set_task(set_task(state.get("tasks", []), "T4", "done"), "T5", "done")
    return {"plan": plan, "constraints": cons, "tasks": tasks, "trace": T.merged(state), "last_node": "executor"}
