"""Critic Agent: independent, rule-based validation of the plan.

The Critic does not trust the Executor. It recomputes prices from the catalog, re-checks opening
hours, weather, travel times, overlaps and totals from the raw plan, and returns machine-readable
issues the Planner can act on. It also validates LLM-written text (grounding check).
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set

from ..config import (LIVE_MAX_AGE_MIN, LOCAL_TRANSFER_MIN, MAX_ACTIVE_HOURS_PER_DAY,
                      MAX_ACTIVITIES_PER_DAY, RAIN_BLOCK_PROB, RESERVE_PCT)
from ..tools.catalog import destination, place_index
from ..tools.routing import travel_minutes
from ..trace import Tracer, set_task
from ..utils import at, from_iso, inr, round_to

HARD, SOFT = "hard", "soft"


class _Report:
    def __init__(self):
        self.checks: List[dict] = []
        self.issues: List[dict] = []
        self.warnings: List[dict] = []

    def add(self, cid: str, name: str, severity: str, ok: bool, detail: str,
            code: str = "", data: Optional[dict] = None, extra_issues: Optional[List[dict]] = None):
        self.checks.append({"id": cid, "name": name, "severity": severity, "passed": ok, "detail": detail})
        if ok:
            return
        bucket = self.issues if severity == HARD else self.warnings
        if extra_issues:
            for it in extra_issues:
                bucket.append({"check_id": cid, "severity": severity, **it})
        else:
            bucket.append({"check_id": cid, "severity": severity, "code": code or cid,
                           "message": detail, "data": data or {}})


def _rain(research: dict, cons: dict, iso: str) -> int:
    ov = cons.get("rain_override", {})
    if iso in ov:
        return ov[iso]
    return research["weather"]["days"].get(iso, {}).get("rain_prob", 0)


def run_checks(plan: Optional[dict], req: dict, research: dict, cons: dict) -> dict:
    R = _Report()
    if plan is None:
        R.add("transport_feasible", "A feasible transport combination exists", HARD, False,
              "No outbound/return pair leaves enough time at the destination for these dates",
              code="no_feasible_transport")
        return {"checks": R.checks, "issues": R.issues, "warnings": [], "passed": False, "unfixable": True,
                "summary": "0 checks passed"}

    dest = req["destination"]
    dcfg = destination(dest)
    idx = place_index(dest)
    t = plan["travelers"]
    costs = plan["costs"]
    out, ret = plan["transport"]["outbound"], plan["transport"]["return"]
    stay = plan["stay"]
    matrix = research.get("matrix")

    # 1 ---- budget
    total, budget = costs["grand_total"], req["budget"]
    R.add("budget", "Total cost within budget", HARD, total <= budget,
          f"{inr(total)} of {inr(budget)} budget" + ("" if total <= budget else f" - over by {inr(total - budget)}"),
          code="over_budget", data={"by": total - budget, "total": total})

    # 2 ---- price integrity + arithmetic
    problems: List[str] = []
    veh = math.ceil(t / dcfg["local"]["capacity"])
    transfer_cost = veh * dcfg["local"]["station_transfer"]
    food = act = local = 0
    for d in plan["days"]:
        local += d["local_hop_cost"]
        for it in d["items"]:
            pid = it.get("place_id")
            if it["type"] in ("activity", "meal"):
                cat = idx.get(pid)
                if not cat:
                    problems.append(f"unknown place {pid}")
                    continue
                if it["cost_total"] != cat["cost_pp"] * t:
                    problems.append(f"{it['name']}: {it['cost_total']} != {cat['cost_pp']}x{t}")
                if it["type"] == "meal":
                    food += it["cost_total"]
                else:
                    act += it["cost_total"]
            elif it["type"] in ("arrival", "transfer"):
                if it["cost_total"] != transfer_cost:
                    problems.append(f"{it['type']} transfer cost {it['cost_total']} != {transfer_cost}")
                local += it["cost_total"]
    if stay:
        cat = idx[stay["id"]]
        units = t if cat["unit"] == "bed" else math.ceil(t / cat["capacity"])
        if stay["cost_total"] != units * cat["price"] * stay["nights"]:
            problems.append("stay cost does not match catalog price x units x nights")
        breakfast = 0 if cat["breakfast_included"] else stay["nights"] * t * dcfg["local"]["breakfast_pp"]
    else:
        breakfast = 0
    expected = {
        "transport_out": out["fare_pp"] * t, "transport_return": ret["fare_pp"] * t,
        "stay": stay["cost_total"] if stay else 0, "food": food + breakfast,
        "activities": act, "local_transport": local,
    }
    for k, v in expected.items():
        if costs[k] != v:
            problems.append(f"{k}: reported {costs[k]} vs recomputed {v}")
    sub = sum(expected.values())
    if costs["subtotal"] != sub or costs["reserve"] != round_to(sub * RESERVE_PCT, 10) \
            or costs["grand_total"] != sub + round_to(sub * RESERVE_PCT, 10):
        problems.append("subtotal / reserve / grand total do not reconcile")
    R.add("arithmetic", "Prices match the catalog and totals reconcile", HARD, not problems,
          "all line items recomputed from catalog prices" if not problems else "; ".join(problems[:3]),
          code="arithmetic_mismatch", data={"problems": problems})

    # 3 ---- transport timing
    o_arr, o_dep = from_iso(out["arrive"]), from_iso(out["depart"])
    r_dep, r_arr = from_iso(ret["depart"]), from_iso(ret["arrive"])
    start = date.fromisoformat(plan["start_date"])
    trip_end = at(start + timedelta(days=plan["total_days"] - 1), "23:59")
    t_problems = []
    if o_arr != o_dep + timedelta(minutes=out["duration_min"]):
        t_problems.append("outbound arrival does not equal departure + duration")
    if r_arr != r_dep + timedelta(minutes=ret["duration_min"]):
        t_problems.append("return arrival does not equal departure + duration")
    if o_arr >= r_dep:
        t_problems.append("outbound arrives after the return departs")
    if r_arr > trip_end:
        t_problems.append("return journey ends after the last day of the trip")
    last_end: Optional[datetime] = None
    for d in plan["days"]:
        for it in d["items"]:
            if it["type"] in ("activity", "meal"):
                e = from_iso(it["end"])
                if last_end is None or e > last_end:
                    last_end = e
    if last_end and last_end > r_dep - timedelta(minutes=ret["arrive_before_min"] + LOCAL_TRANSFER_MIN):
        t_problems.append("activities run into the time needed to reach the station/airport")
    R.add("transport_timing", "Transport timings are consistent with the schedule", HARD, not t_problems,
          "arrivals, departures and buffers are consistent" if not t_problems else "; ".join(t_problems),
          code="transport_timing", data={"problems": t_problems})

    # 4 ---- overlaps
    all_items = sorted((it for d in plan["days"] for it in d["items"]), key=lambda x: (x["start"], x["end"]))
    overlaps = []
    for a, b in zip(all_items, all_items[1:]):
        if b["start"] < a["end"]:
            overlaps.append(f"{a['name']} / {b['name']}")
    R.add("overlap", "No overlapping items", HARD, not overlaps,
          "no item overlaps another" if not overlaps else f"overlap: {overlaps[0]}", code="overlap",
          data={"pairs": overlaps})

    # 5 ---- opening hours (recomputed from catalog)
    bad_hours: List[dict] = []
    for d in plan["days"]:
        day = date.fromisoformat(d["date"])
        for it in d["items"]:
            if it["type"] not in ("activity", "meal"):
                continue
            cat = idx.get(it["place_id"])
            if not cat:
                continue
            s, e = from_iso(it["start"]), from_iso(it["end"])
            closed_days = cat.get("closed_days", [])
            if day.weekday() in closed_days or s < at(day, cat["open"]) or e > at(day, cat["close"]) + timedelta(minutes=15):
                bad_hours.append({"code": "hours_conflict", "message": f"{it['name']} is closed at {s:%H:%M}-{e:%H:%M} on {day:%a}",
                                  "data": {"place_id": it["place_id"], "name": it["name"], "day": d["day_no"]}})
    R.add("opening_hours", "Everything is open when we visit", HARD, not bad_hours,
          "all places open at the scheduled times" if not bad_hours else bad_hours[0]["message"],
          extra_issues=bad_hours)

    # 6 ---- travel feasibility between consecutive stops
    infeasible: List[str] = []
    for d in plan["days"]:
        seq = [it for it in d["items"] if it["lat"] is not None]
        for a, b in zip(seq, seq[1:]):
            pa = {"id": a["place_id"], "lat": a["lat"], "lon": a["lon"]}
            pb = {"id": b["place_id"], "lat": b["lat"], "lon": b["lon"]}
            need = travel_minutes(dest, pa, pb, matrix)
            have = (from_iso(b["start"]) - from_iso(a["end"])).total_seconds() / 60
            if have + 2 < need:
                infeasible.append(f"{a['name']} -> {b['name']}: need {need} min, have {int(have)}")
    R.add("travel_time", "Enough time to travel between stops", HARD, not infeasible,
          "every hop has enough travel time" if not infeasible else infeasible[0],
          code="travel_conflict", data={"hops": infeasible})

    # 7 ---- weather
    wet: List[dict] = []
    for d in plan["days"]:
        rain = _rain(research, cons, d["date"])
        for it in d["items"]:
            if it["type"] == "activity":
                cat = idx.get(it["place_id"])
                if cat and cat["outdoor"] and rain >= RAIN_BLOCK_PROB:
                    wet.append({"code": "weather_conflict",
                                "message": f"Outdoor '{it['name']}' on Day {d['day_no']} with {rain}% rain",
                                "data": {"place_id": it["place_id"], "name": it["name"], "day": d["day_no"]}})
    R.add("weather", "No outdoor plans on heavy-rain days", HARD, not wet,
          f"rain threshold {RAIN_BLOCK_PROB}% respected on every day" if not wet else wet[0]["message"],
          extra_issues=wet)

    # 8 ---- stay coverage
    s_problems = []
    nights = (r_dep.date() - o_arr.date()).days
    if plan["nights"] != nights:
        s_problems.append(f"plan says {plan['nights']} nights but dates imply {nights}")
    if nights > 0 and not stay:
        s_problems.append("nights need accommodation but no stay is booked")
    if stay and stay["units"] * stay["capacity"] < t:
        s_problems.append("stay capacity is smaller than the group")
    if stay and stay["nights"] != nights:
        s_problems.append("stay nights differ from nights at destination")
    R.add("stay", "Accommodation covers every night and the whole group", HARD, not s_problems,
          (f"{nights} night(s) covered for {t} traveller(s)" if not s_problems else "; ".join(s_problems)),
          code="stay_mismatch", data={"problems": s_problems})

    # 9 ---- disruption constraints respected
    c_problems: List[dict] = []
    closed = set(cons.get("closed_attractions", []))
    for d in plan["days"]:
        for it in d["items"]:
            if it["type"] == "activity" and it["place_id"] in closed:
                c_problems.append({"code": "closed_place", "message": f"{it['name']} is marked closed but is still planned",
                                   "data": {"place_id": it["place_id"], "name": it["name"]}})
    excl = set(cons.get("excluded_transport", []))
    if f"out:{out['id']}" in excl or f"ret:{ret['id']}" in excl:
        c_problems.append({"code": "excluded_transport", "message": "a cancelled transport option is still in use", "data": {}})
    R.add("constraints", "Closed or cancelled items are not used", HARD, not c_problems,
          "closed places and cancelled services are excluded" if not c_problems else c_problems[0]["message"],
          extra_issues=c_problems)

    # ---------------------------------------------------------------- soft checks
    load_problems = []
    for d in plan["days"]:
        acts = [it for it in d["items"] if it["type"] == "activity"]
        hours = sum((from_iso(i["end"]) - from_iso(i["start"])).total_seconds() / 3600 for i in acts + [m for m in d["items"] if m["type"] == "meal"])
        if len(acts) > MAX_ACTIVITIES_PER_DAY or hours > MAX_ACTIVE_HOURS_PER_DAY:
            load_problems.append(f"Day {d['day_no']} is overloaded ({len(acts)} activities, {hours:.1f} h)")
        if d["window"] and not acts:
            w0, w1 = (datetime.strptime(x, "%H:%M") for x in d["window"])
            if (w1 - w0).total_seconds() / 3600 >= 5:
                load_problems.append(f"Day {d['day_no']} has no activities")
    R.add("day_load", "Days are neither overloaded nor empty", SOFT, not load_problems,
          "balanced days" if not load_problems else "; ".join(load_problems[:2]))

    missing_meals = []
    for d in plan["days"]:
        if not d["window"]:
            continue
        w0 = datetime.strptime(d["window"][0], "%H:%M").time()
        w1 = datetime.strptime(d["window"][1], "%H:%M").time()
        kinds = {it["name"].split(":")[0] for it in d["items"] if it["type"] == "meal"}
        if w0 <= datetime.strptime("13:30", "%H:%M").time() and w1 >= datetime.strptime("14:15", "%H:%M").time() and "Lunch" not in kinds:
            missing_meals.append(f"Day {d['day_no']} lunch")
        if w0 <= datetime.strptime("20:30", "%H:%M").time() and w1 >= datetime.strptime("21:15", "%H:%M").time() and "Dinner" not in kinds:
            missing_meals.append(f"Day {d['day_no']} dinner")
    R.add("meals", "Lunch and dinner are planned on full days", SOFT, not missing_meals,
          "meals planned" if not missing_meals else "missing: " + ", ".join(missing_meals[:3]))

    covered: Set[str] = set()
    for d in plan["days"]:
        for it in d["items"]:
            if it["type"] == "activity":
                covered |= set(it["tags"])
    uncovered = [i for i in req["interests"] if i not in covered]
    R.add("interests", "Every stated interest is covered", SOFT, not uncovered,
          "all interests covered" if not uncovered else "not covered: " + ", ".join(uncovered))

    stale = []
    notes = []
    for s in research.get("sources", []):
        if s["kind"] == "live" and s["age_s"] > LIVE_MAX_AGE_MIN * 60:
            stale.append(s["what"])
        if s["kind"] in ("cache", "bundled"):
            notes.append(f"{s['what']}: {s['kind']}")
    R.add("freshness", "Data is fresh enough (live data < 30 min old)", SOFT, not stale,
          ("sources: " + "; ".join(notes)) if not stale else "stale: " + ", ".join(stale))

    if dcfg.get("generated"):
        R.add("generated_data", "Destination data is verified", SOFT, False,
              "places, prices and hours for this destination are AI-estimated by a local model - verify before booking",
              code="generated_data")
    passed = not R.issues
    n_ok = sum(1 for c in R.checks if c["passed"])
    return {"checks": R.checks, "issues": R.issues, "warnings": R.warnings, "passed": passed,
            "unfixable": False, "summary": f"{n_ok}/{len(R.checks)} checks passed"}


# --------------------------------------------------------------------------- narrative grounding
_AMOUNT = re.compile(r"(?:₹|Rs\.?|INR)\s*([\d][\d,]*)", re.IGNORECASE)


def allowed_amounts(plan: dict, req: dict) -> Set[int]:
    c = plan["costs"]
    vals: Set[int] = {int(v) for v in c.values() if isinstance(v, (int, float))}
    vals |= {int(req["budget"]), int(req["budget"] - c["grand_total"]), int(c["grand_total"] - req["budget"])}
    for leg in plan["transport"].values():
        vals.add(int(leg["fare_pp"]))
    if plan["stay"]:
        vals |= {int(plan["stay"]["price"]), int(plan["stay"]["cost_total"])}
    for d in plan["days"]:
        vals.add(int(d["local_hop_cost"]))
        for it in d["items"]:
            vals.add(int(it["cost_total"]))
            if it["place_id"]:
                p = place_index(plan["destination"]).get(it["place_id"])
                if p and "cost_pp" in p:
                    vals.add(int(p["cost_pp"]))
    vals.add(int(round(c["grand_total"] / max(1, plan["travelers"]))))
    return vals


def check_narrative(text: str, plan: dict, req: dict) -> dict:
    """Reject LLM text that mentions a rupee amount which does not exist in the plan."""
    allowed = allowed_amounts(plan, req)
    bad = []
    for m in _AMOUNT.finditer(text):
        n = int(m.group(1).replace(",", "") or 0)
        if n not in allowed:
            bad.append(n)
    words = len(text.split())
    ok = not bad and 15 <= words <= 220
    return {"ok": ok, "bad_amounts": bad, "words": words}


# --------------------------------------------------------------------------- graph node
def critic_node(state: dict) -> dict:
    T = Tracer(state, "Critic")
    plan, req, research, cons = state.get("plan"), state["request"], state["research"], state["constraints"]
    report = run_checks(plan, req, research, cons)
    for c in report["checks"]:
        T.log(c["name"], c["detail"], "ok" if c["passed"] else ("fail" if c["severity"] == HARD else "warn"))
    verdict = "PASSED" if report["passed"] else "REJECTED"
    T.log(f"Verdict: {verdict}", report["summary"] + ("" if report["passed"] else
          f"; {len(report['issues'])} blocking issue(s)"), "ok" if report["passed"] else "fail")

    strat = state["strategy"]
    entry = {
        "attempt": state.get("attempt", 1), "mode": state.get("mode", "plan"),
        "level": strat["level"], "label": strat["label"],
        "total": plan["costs"]["grand_total"] if plan else None, "budget": req["budget"],
        "passed": report["passed"],
        "failed": [c["name"] for c in report["checks"] if not c["passed"] and c["severity"] == HARD],
        "outbound": plan["transport"]["outbound"]["label"] if plan else None,
        "stay": (plan["stay"]["name"] if plan and plan["stay"] else None),
    }
    return {"critic": report, "attempts": list(state.get("attempts", [])) + [entry],
            "tasks": set_task(state.get("tasks", []), "T6", "done" if report["passed"] else "failed"),
            "trace": T.merged(state), "last_node": "critic"}
