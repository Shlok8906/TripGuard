"""Deterministic planning engine used by the Executor agent.

Pure Python, no network, no model calls: given a request, a strategy and the Research agent's
data it selects transport and a stay, schedules each day and prices everything.
"""
from __future__ import annotations

import copy
import math
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from .config import (ARRIVAL_SETTLE_MIN, LOCAL_TRANSFER_MIN, MAX_ACTIVITIES_PER_DAY,
                     MIN_USABLE_HOURS, RAIN_BLOCK_PROB, RAIN_WARN_PROB, RESERVE_PCT)
from .tools.catalog import destination, transport_options
from .tools.routing import road_km, travel_minutes
from .utils import at, cap1, from_iso, hm, round_to, to_iso

MEALS = {"lunch": ("12:30", "14:30"), "dinner": ("19:30", "21:30")}
MEAL_MIN = 60
CORE_TYPES = ("transit", "arrival", "transfer", "checkout")
GREEDY_TYPES = ("activity", "meal")


# =============================================================================== transport
def leg_from_option(opt: dict, depart_date: date, delay_min: int = 0) -> dict:
    dep = at(depart_date, opt["depart"])
    arr = dep + timedelta(minutes=opt["duration_min"] + delay_min)
    return {
        "id": opt["id"], "mode": opt["mode"], "label": opt["label"], "operator": opt["operator"],
        "depart": to_iso(dep), "arrive": to_iso(arr), "delay_min": delay_min,
        "duration_min": opt["duration_min"] + delay_min,
        "scheduled_duration_min": opt["duration_min"],
        "fare_pp": opt["fare_pp"], "comfort": opt["comfort"],
        "arrive_before_min": opt["arrive_before_min"],
    }


def day_windows(arrival: datetime, ret_dep: datetime, ret_buffer: int, nights: int,
                start: date, total_days: int) -> Dict[str, Optional[Tuple[datetime, datetime]]]:
    """Usable sightseeing window per calendar day (None when the day is pure travel)."""
    out: Dict[str, Optional[Tuple[datetime, datetime]]] = {}
    for i in range(total_days):
        d = start + timedelta(days=i)
        if d < arrival.date() or d > ret_dep.date():
            out[d.isoformat()] = None
            continue
        s, e = at(d, "08:30"), at(d, "22:30")
        if d == arrival.date():
            s = max(s, arrival + timedelta(minutes=ARRIVAL_SETTLE_MIN))
        if d == ret_dep.date():
            e = min(e, ret_dep - timedelta(minutes=ret_buffer + LOCAL_TRANSFER_MIN))
            if nights > 0 and d != arrival.date():
                s = max(s, at(d, "09:30"))
        out[d.isoformat()] = (s, e) if (e - s) >= timedelta(minutes=45) else None
    return out


def usable_hours(wins: dict) -> float:
    return round(sum((w[1] - w[0]).total_seconds() / 3600 for w in wins.values() if w), 2)


def _pair_score(pref: str, o: dict, r: dict, usable: float, arrive: datetime) -> float:
    fares = o["fare_pp"] + r["fare_pp"]
    hours = (o["duration_min"] + r["duration_min"]) / 60
    comfort = o["comfort"] + r["comfort"]
    if pref == "cheapest":
        return fares + 4 * hours - usable
    if pref == "fastest":
        return 1000 * hours + 0.2 * fares - 5 * usable
    odd = 400 if arrive.hour < 5 else 0           # arriving in the small hours is unpleasant
    if pref == "comfort":
        return fares + 70 * hours - 90 * usable - 400 * comfort + odd
    return fares + 70 * hours - 90 * usable - 60 * comfort + odd


def pick_transport(req: dict, strat: dict, cons: dict, total_days: int,
                   allow_extension: bool = False, unlock_return: bool = False) -> Optional[dict]:
    """Choose the (outbound, return) pair that best fits the strategy, or None if none is feasible."""
    route = transport_options(req["origin"], req["destination"])
    start = date.fromisoformat(req["start_date"])
    excluded = set(cons.get("excluded_transport", []))
    lock_out = cons.get("locked_out_id")
    lock_ret = None if unlock_return else cons.get("locked_ret_id")
    delay = cons.get("delay_out_min", 0)
    first = cons.get("extra_days", 0)
    cap = strat.get("transport_share", 0.5) * req["budget"]
    for extra, capped in [(e, c_) for e in [first] + ([first + 1] if allow_extension else [])
                          for c_ in (True, False)]:
        td = total_days + extra
        ret_date = start + timedelta(days=td - 1)
        best = None
        for o in route["options"]:
            if f"out:{o['id']}" in excluded or (lock_out and o["id"] != lock_out):
                continue
            out_leg = leg_from_option(o, start, delay)
            arr = from_iso(out_leg["arrive"])
            if (arr.date() - start).days > 2:
                continue
            for r in route["options"]:
                if f"ret:{r['id']}" in excluded or (lock_ret and r["id"] != lock_ret):
                    continue
                ret_leg = leg_from_option(r, ret_date)
                rdep, rarr = from_iso(ret_leg["depart"]), from_iso(ret_leg["arrive"])
                if rarr > at(ret_date, "23:59"):
                    continue
                if rdep <= arr + timedelta(minutes=ARRIVAL_SETTLE_MIN + r["arrive_before_min"] + LOCAL_TRANSFER_MIN):
                    continue
                if capped and (o["fare_pp"] + r["fare_pp"]) * req["travelers"] > cap:
                    continue
                nights = (rdep.date() - arr.date()).days
                wins = day_windows(arr, rdep, r["arrive_before_min"], nights, start, td)
                usable = usable_hours(wins)
                if usable < MIN_USABLE_HOURS:
                    continue
                key = (_pair_score(strat["transport_pref"], out_leg, ret_leg, usable, arr), o["id"], r["id"])
                if best is None or key < best[0]:
                    best = (key, out_leg, ret_leg, usable)
        if best:
            return {"outbound": best[1], "return": best[2], "usable_hours": best[3],
                    "total_days": td, "extra_days": extra}
    return None


# =============================================================================== stay
def pick_stay(req: dict, strat: dict, cons: dict, nights: int) -> Optional[dict]:
    if nights <= 0:
        return None
    dcfg = destination(req["destination"])
    travelers = req["travelers"]
    stays = dcfg["stays"]
    pref = req.get("stay_pref", "any")
    if pref in ("hostel", "hotel"):
        filtered = [s for s in stays if s["type"].startswith(pref)]
        stays = filtered or stays
    locked = cons.get("locked_stay_id")
    if locked:
        stays = [s for s in dcfg["stays"] if s["id"] == locked] or stays
    best = None
    for s in stays:
        units = travelers if s["unit"] == "bed" else math.ceil(travelers / s["capacity"])
        total = units * s["price"] * nights
        score = total if strat["stay_policy"] == "cheapest" else total / (s["comfort"] ** 1.4)
        key = (score, s["id"])
        if best is None or key < best[0]:
            best = (key, s, units, total)
    _, s, units, total = best
    return {"id": s["id"], "name": s["name"], "type": s["type"], "unit": s["unit"], "units": units,
            "price": s["price"], "nights": nights, "cost_total": total, "comfort": s["comfort"],
            "breakfast_included": s["breakfast_included"], "lat": s["lat"], "lon": s["lon"],
            "capacity": s["capacity"]}


# =============================================================================== scheduling
def _item(type_: str, name: str, start: datetime, end: datetime, *, category: str = "",
          cost: int = 0, place: Optional[dict] = None, notes: str = "", travel_min: int = 0,
          tags: Optional[list] = None, pid: Optional[str] = None) -> dict:
    return {
        "type": type_, "name": name, "start": to_iso(start), "end": to_iso(end),
        "category": category, "cost_total": int(cost), "place_id": pid,
        "lat": place["lat"] if place else None, "lon": place["lon"] if place else None,
        "notes": notes, "travel_min": int(travel_min), "tags": tags or [],
    }


def _vehicles(req: dict, dcfg: dict) -> int:
    return math.ceil(req["travelers"] / dcfg["local"]["capacity"])


def _rain(c: dict, d: date) -> int:
    ov = c["cons"].get("rain_override", {})
    if d.isoformat() in ov:
        return ov[d.isoformat()]
    return c["weather"].get(d.isoformat(), {}).get("rain_prob", 0)


def _tod(h: int) -> str:
    return "morning" if h < 12 else "afternoon" if h < 17 else "evening"


def _pick_food(c: dict, meal: str, cur_t: datetime, cur_loc: dict, earliest: datetime,
               latest: datetime, used_food: Set[str]):
    tier = c["strat"]["food_tier"]
    interests = set(c["req"]["interests"])
    best = None
    for f in c["dcfg"]["food"]:
        kind = f["kind"]
        if tier == "street" and kind not in ("street", "thali"):
            continue
        if tier == "budget" and kind == "restaurant":
            continue
        trav = travel_minutes(c["dest"], cur_loc, f, c["matrix"])
        start = max(cur_t + timedelta(minutes=trav), earliest)
        if start > latest:
            continue
        d = start.date()
        if start < at(d, f["open"]) or start + timedelta(minutes=45) > at(d, f["close"]):
            continue
        score = -f["cost_pp"] / 90 - trav / 20 + len(set(f["tags"]) & interests)
        if "food" in interests and kind in ("street", "thali"):
            score += 1.5
        if tier == "mixed" and meal == "dinner" and kind in ("cafe", "restaurant"):
            score += 1.5
        if f["id"] in used_food:
            score -= 5
        key = (-score, f["id"])
        if best is None or key < best[0]:
            best = (key, f, trav, start)
    return None if best is None else best[1:]


def _score_attraction(c: dict, a: dict, trav: int, start: datetime, rain: int,
                      covered: Set[str]) -> float:
    interests = set(c["req"]["interests"])
    overlap = set(a["tags"]) & interests
    s = 4.0 * len(overlap) + 2.5 * len(set(a["tags"]) & (interests - covered))
    if not overlap:
        s += 0.5 if a["must_see"] else -3.0
    if a["must_see"]:
        s += 1.0
    if a["outdoor"] and rain >= RAIN_WARN_PROB:
        s -= 1.5
    if not a["outdoor"] and rain >= RAIN_WARN_PROB:
        s += 1.0
    s -= a["cost_pp"] / (900 if c["strat"]["activity_policy"] == "all_interest" else 200)
    s -= trav / 25
    if a["best_time"] == _tod(start.hour):
        s += 1.0
    return s


def plan_day(c: dict, d: date, win: Tuple[datetime, datetime], used_att: Set[str],
             used_food: Set[str], covered: Set[str], base: dict) -> List[dict]:
    """Schedule one day. If strict interest-matching leaves the day without activities, retry
    once with a relaxed relevance threshold so long days are not left empty."""
    ua, uf, cv = set(used_att), set(used_food), set(covered)
    items = _plan_day_pass(c, d, win, ua, uf, cv, base, min_score=-2.5)
    if not any(it["type"] == "activity" for it in items) and (win[1] - win[0]) >= timedelta(minutes=150):
        ua, uf, cv = set(used_att), set(used_food), set(covered)
        items = _plan_day_pass(c, d, win, ua, uf, cv, base, min_score=-6.0)
    used_att |= ua
    used_food |= uf
    covered |= cv
    return items


def _plan_day_pass(c: dict, d: date, win: Tuple[datetime, datetime], used_att: Set[str],
                   used_food: Set[str], covered: Set[str], base: dict, min_score: float) -> List[dict]:
    """Greedy, constraint-respecting schedule for one day. Returns activity + meal items."""
    s, e = win
    req, strat, dcfg, dest = c["req"], c["strat"], c["dcfg"], c["dest"]
    policy = strat["activity_policy"]
    closed = set(c["cons"].get("closed_attractions", []))
    rain = _rain(c, d)
    max_act = MAX_ACTIVITIES_PER_DAY if (e - s) >= timedelta(hours=6) else 2
    cur_t, cur_loc = s, base
    items: List[dict] = []
    done: Set[str] = set()
    n_act = paid = 0

    for _ in range(16):
        pending = None
        for meal in ("lunch", "dinner"):
            if meal in done:
                continue
            earliest = at(d, MEALS[meal][0])
            latest = min(at(d, MEALS[meal][1]), e - timedelta(minutes=MEAL_MIN))
            if latest < earliest or s > latest:
                done.add(meal)
                continue
            if cur_t > latest:
                done.add(meal)
                continue
            pending = (meal, earliest, latest)
            break

        meal_now = False
        if pending:
            meal, earliest, latest = pending
            food = _pick_food(c, meal, cur_t, cur_loc, earliest, latest, used_food)
            if food is None:
                done.add(meal)
                continue
            f, f_trav, f_start = food
            meal_now = cur_t + timedelta(minutes=f_trav) >= earliest - timedelta(minutes=20)

        if not meal_now and n_act < max_act:
            deadline = (pending[2] - timedelta(minutes=10)) if pending else e
            best = None
            for a in dcfg["attractions"]:
                if a["id"] in used_att or a["id"] in closed or d.weekday() in a["closed_days"]:
                    continue
                if a["outdoor"] and rain >= RAIN_BLOCK_PROB:
                    continue
                if policy == "free_only" and a["cost_pp"] > 50:
                    continue
                if policy == "mostly_free" and (a["cost_pp"] > 300 or (a["cost_pp"] > 50 and paid >= 1)):
                    continue
                trav = travel_minutes(dest, cur_loc, a, c["matrix"])
                start = cur_t + timedelta(minutes=trav)
                opens, closes = at(d, a["open"]), at(d, a["close"])
                if start < opens:
                    if (opens - start) > timedelta(minutes=150 if a["event"] else 45):
                        continue
                    start = opens
                end = start + timedelta(minutes=a["duration_min"])
                if end > closes or end > deadline or end > e:
                    continue
                sc = _score_attraction(c, a, trav, start, rain, covered)
                if sc < min_score:
                    continue
                key = (-sc, trav, a["id"])
                if best is None or key < best[0]:
                    best = (key, a, trav, start, end)
            if best:
                _, a, trav, start, end = best
                note = a["desc"]
                if start > cur_t + timedelta(minutes=trav):
                    note += f" (arrive early, opens {a['open']})"
                items.append(_item("activity", a["name"], start, end, category="activity",
                                   cost=a["cost_pp"] * req["travelers"], place=a, notes=note,
                                   travel_min=trav, tags=a["tags"], pid=a["id"]))
                used_att.add(a["id"])
                covered |= set(a["tags"]) & set(req["interests"])
                n_act += 1
                paid += 1 if a["cost_pp"] > 50 else 0
                cur_t, cur_loc = end, a
                continue

        if pending:
            meal, earliest, latest = pending
            f, f_trav, f_start = food
            f_end = f_start + timedelta(minutes=MEAL_MIN)
            items.append(_item("meal", f"{meal.title()}: {f['name']}", f_start, f_end, category="food",
                               cost=f["cost_pp"] * req["travelers"], place=f,
                               notes=f"{f['kind'].title()} - about Rs {f['cost_pp']} per person",
                               travel_min=f_trav, tags=f["tags"], pid=f["id"]))
            used_food.add(f["id"])
            done.add(meal)
            cur_t, cur_loc = f_end, f
            continue
        break
    return items


def day_local_cost(c: dict, items: List[dict], base: dict, is_last: bool) -> Tuple[int, float]:
    """Local transport cost + km for the day's hops (stay -> stops -> stay)."""
    vehicles = _vehicles(c["req"], c["dcfg"])
    rate = c["dcfg"]["local"]["cost_per_km"]
    km, prev = 0.0, base
    for it in sorted(items, key=lambda x: x["start"]):
        if it["lat"] is None:
            continue
        cur = {"id": it["place_id"], "lat": it["lat"], "lon": it["lon"]}
        km += road_km(prev, cur)
        prev = cur
    if items and not is_last:
        km += road_km(prev, base)
    return round_to(km * rate * vehicles, 10), round(km, 1)


# =============================================================================== plan assembly
def build_plan(req: dict, strat: dict, cons: dict, research: dict, mode: str,
               prior_plan: Optional[dict] = None, affected_days: Optional[List[int]] = None,
               full_replan: bool = True) -> Tuple[Optional[dict], List[Tuple[str, str]]]:
    """Return (plan | None, notes). `notes` are (status, text) pairs for the trace."""
    notes: List[Tuple[str, str]] = []
    dest = req["destination"]
    dcfg = destination(dest)
    start = date.fromisoformat(req["start_date"])
    n_days = req["days"]
    travelers = req["travelers"]

    # ---- transport (with graceful degradation when locks make the pair infeasible)
    tp = pick_transport(req, strat, cons, n_days)
    if tp is None and cons.get("locked_ret_id"):
        notes.append(("warn", "Locked return leg no longer fits - searching other return options"))
        tp = pick_transport(req, strat, cons, n_days, unlock_return=True)
    if tp is None and mode == "disrupt":
        notes.append(("warn", "No same-day option fits - extending the trip by one day"))
        tp = pick_transport(req, strat, cons, n_days, allow_extension=True, unlock_return=True)
    if tp is None:
        return None, [("fail", "No outbound/return combination leaves enough time at the destination")]
    cons["extra_days"] = tp["extra_days"]
    total_days = tp["total_days"]
    out_leg, ret_leg = tp["outbound"], tp["return"]
    arr, rdep = from_iso(out_leg["arrive"]), from_iso(ret_leg["depart"])
    nights = (rdep.date() - arr.date()).days

    stay = pick_stay(req, strat, cons, nights)
    base = ({"id": stay["id"], "lat": stay["lat"], "lon": stay["lon"], "name": stay["name"]} if stay
            else {"id": None, "lat": dcfg["lat"], "lon": dcfg["lon"], "name": dest})
    wins = day_windows(arr, rdep, ret_leg["arrive_before_min"], nights, start, total_days)

    c = {"req": req, "strat": strat, "cons": cons, "dcfg": dcfg, "dest": dest,
         "weather": research["weather"]["days"], "matrix": research.get("matrix")}

    # ---- which days can be reused from the previous plan (targeted repair)
    reuse: Dict[int, List[dict]] = {}
    if prior_plan and not full_replan:
        aff = set(affected_days or [])
        for i, pd_ in enumerate(prior_plan["days"]):
            if i in aff or i >= total_days:
                continue
            w = wins.get(pd_["date"])
            pw = pd_.get("window")
            same = (w is None and pw is None) or (w and pw and [hm(w[0]), hm(w[1])] == pw)
            if same:
                reuse[i] = [copy.deepcopy(it) for it in pd_["items"] if it["type"] in GREEDY_TYPES]
    used_att: Set[str] = set()
    used_food: Set[str] = set()
    covered: Set[str] = set()
    for its in reuse.values():
        for it in its:
            if it["type"] == "activity":
                used_att.add(it["place_id"])
                covered |= set(it["tags"]) & set(req["interests"])
            else:
                used_food.add(it["place_id"])

    transfer_cost = _vehicles(req, dcfg) * dcfg["local"]["station_transfer"]
    days: List[dict] = []
    for i in range(total_days):
        d = start + timedelta(days=i)
        iso = d.isoformat()
        win = wins[iso]
        w = research["weather"]["days"].get(iso, {})
        rain = _rain(c, d)
        items: List[dict] = []
        # core items
        if d == from_iso(out_leg["depart"]).date():
            dly = out_leg["delay_min"]
            note = f"{out_leg['operator']} - Rs {out_leg['fare_pp']} per person" + (f" - DELAYED {dly} min" if dly else "")
            items.append(_item("transit", f"{cap1(out_leg['label'])}: {req['origin']} to {dest}",
                               from_iso(out_leg["depart"]), arr, category="", notes=note))
        if d == arr.date():
            a_end = arr + timedelta(minutes=ARRIVAL_SETTLE_MIN)
            nm = f"Arrive in {dest}, transfer to {stay['name']} and freshen up" if stay else f"Arrive in {dest}"
            items.append(_item("arrival", nm, arr, a_end, category="local", cost=transfer_cost,
                               place=base if stay else None, pid=base["id"] if stay else None,
                               notes="Station transfer (shared by the group)"))
        if d == rdep.date():
            t_end = rdep - timedelta(minutes=ret_leg["arrive_before_min"])
            t_start = t_end - timedelta(minutes=LOCAL_TRANSFER_MIN)
            if nights > 0 and d != arr.date():
                c_end = min(at(d, "09:30"), t_start)      # leave earlier when the departure is early
                items.append(_item("checkout", f"Check out of {stay['name']}, store luggage",
                                   c_end - timedelta(minutes=30), c_end, place=base, pid=base["id"]))
            items.append(_item("transfer", "Transfer to station / airport", t_start,
                               t_end, category="local", cost=transfer_cost, notes="Station transfer (shared by the group)"))
            items.append(_item("transit", f"{cap1(ret_leg['label'])}: {dest} to {req['origin']}", rdep,
                               from_iso(ret_leg["arrive"]),
                               notes=f"{ret_leg['operator']} - Rs {ret_leg['fare_pp']} per person"))
        # greedy items
        if win is not None:
            if i in reuse:
                items.extend(reuse[i])
            else:
                items.extend(plan_day(c, d, win, used_att, used_food, covered, base))
        items.sort(key=lambda x: x["start"])
        greedy = [x for x in items if x["type"] in GREEDY_TYPES]
        hop_cost, km = day_local_cost(c, greedy, base, d == rdep.date())
        days.append({
            "day_no": i + 1, "date": iso, "weekday": d.strftime("%a"),
            "window": [hm(win[0]), hm(win[1])] if win else None,
            "weather": {"rain_prob": rain, "tmax": w.get("tmax"), "tmin": w.get("tmin")},
            "items": items, "local_hop_cost": hop_cost, "local_km": km,
        })

    plan = {
        "origin": req["origin"], "destination": dest, "start_date": req["start_date"],
        "requested_days": n_days, "total_days": total_days, "extra_days": tp["extra_days"],
        "nights": nights, "travelers": travelers,
        "transport": {"outbound": out_leg, "return": ret_leg},
        "stay": stay, "days": days, "usable_hours": tp["usable_hours"],
    }
    plan["costs"] = compute_costs(plan, dcfg)
    return plan, notes


def compute_costs(plan: dict, dcfg: dict) -> dict:
    t = plan["travelers"]
    out_leg, ret_leg = plan["transport"]["outbound"], plan["transport"]["return"]
    stay = plan["stay"]
    food = activities = local = 0
    for d in plan["days"]:
        local += d["local_hop_cost"]
        for it in d["items"]:
            if it["category"] == "food":
                food += it["cost_total"]
            elif it["category"] == "activity":
                activities += it["cost_total"]
            elif it["category"] == "local":
                local += it["cost_total"]
    breakfast = 0
    if stay and not stay["breakfast_included"]:
        breakfast = plan["nights"] * t * dcfg["local"]["breakfast_pp"]
    costs = {
        "transport_out": out_leg["fare_pp"] * t,
        "transport_return": ret_leg["fare_pp"] * t,
        "stay": stay["cost_total"] if stay else 0,
        "food": food + breakfast,
        "activities": activities,
        "local_transport": local,
    }
    subtotal = sum(costs.values())
    reserve = round_to(subtotal * RESERVE_PCT, 10)
    costs.update({"breakfast_est": breakfast, "subtotal": subtotal, "reserve": reserve,
                  "grand_total": subtotal + reserve})
    return costs
