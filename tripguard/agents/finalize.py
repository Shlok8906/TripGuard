"""Finalize step: status, summary text (LLM text is validated by the Critic) and plan diff."""
from __future__ import annotations

from datetime import date
from typing import Dict, List

from ..llm import LLMError, get_llm
from ..trace import Tracer, set_task
from ..utils import from_iso, inr
from .critic import check_narrative


# ----------------------------------------------------------------------------- diff
def _key_items(plan: dict) -> Dict[tuple, dict]:
    out = {}
    for d in plan["days"]:
        for it in d["items"]:
            if it["type"] in ("activity", "meal"):
                out[(d["day_no"], it["type"], it["name"])] = {**it, "day_no": d["day_no"]}
    return out


def diff_plans(old: dict, new: dict) -> dict:
    a, b = _key_items(old), _key_items(new)
    added = [{"day": k[0], "name": v["name"], "time": v["start"][11:16]} for k, v in b.items() if k not in a]
    removed = [{"day": k[0], "name": v["name"], "time": v["start"][11:16]} for k, v in a.items() if k not in b]
    moved = [{"day": k[0], "name": v["name"], "from": a[k]["start"][11:16], "to": v["start"][11:16]}
             for k, v in b.items() if k in a and a[k]["start"] != v["start"]]
    changes: List[str] = []
    for side in ("outbound", "return"):
        o, n = old["transport"][side], new["transport"][side]
        f = lambda iso: from_iso(iso).strftime("%a %H:%M")
        if o["id"] != n["id"]:
            changes.append(f"{side.title()}: {o['label']} {f(o['depart'])} -> {n['label']} {f(n['depart'])} (arrives {f(n['arrive'])})")
        elif o["arrive"] != n["arrive"] or o["depart"] != n["depart"]:
            changes.append(f"{side.title()} {n['label']}: arrival moved {f(o['arrive'])} -> {f(n['arrive'])}")
    os_, ns = old["stay"], new["stay"]
    if (os_ and os_["id"]) != (ns and ns["id"]) or (os_ and ns and os_["nights"] != ns["nights"]):
        changes.append(f"Stay: {os_['name'] if os_ else 'none'} ({os_['nights'] if os_ else 0} nights) -> "
                       f"{ns['name'] if ns else 'none'} ({ns['nights'] if ns else 0} nights)")
    if new["total_days"] != old["total_days"]:
        changes.append(f"Trip length: {old['total_days']} -> {new['total_days']} days")
    unchanged = []
    for d_new in new["days"]:
        d_old = next((x for x in old["days"] if x["date"] == d_new["date"]), None)
        so = [(i["name"], i["start"]) for i in (d_old or {"items": []})["items"] if i["type"] in ("activity", "meal")]
        sn = [(i["name"], i["start"]) for i in d_new["items"] if i["type"] in ("activity", "meal")]
        if d_old and so == sn:
            unchanged.append(d_new["day_no"])
    before, after = old["costs"]["grand_total"], new["costs"]["grand_total"]
    return {"added": added, "removed": removed, "moved": moved, "other": changes,
            "unchanged_days": unchanged, "cost_before": before, "cost_after": after, "cost_delta": after - before}


# ----------------------------------------------------------------------------- narrative
def build_facts(plan: dict, req: dict, status: str) -> dict:
    acts = [it["name"] for d in plan["days"] for it in d["items"] if it["type"] == "activity"]
    c = plan["costs"]
    o, r = plan["transport"]["outbound"], plan["transport"]["return"]
    return {
        "destination": plan["destination"], "origin": plan["origin"], "travellers": plan["travelers"],
        "start_date": plan["start_date"], "days": plan["total_days"], "nights": plan["nights"],
        "budget_inr": f"₹{req['budget']:,}", "total_inr": f"₹{c['grand_total']:,}",
        "outbound": f"{o['label']} departing {o['depart'][11:16]}",
        "return": f"{r['label']} departing {r['depart'][11:16]}",
        "stay": plan["stay"]["name"] if plan["stay"] else "none",
        "highlights": acts[:6], "status": status,
    }


def template_narrative(plan: dict, req: dict, status: str) -> str:
    c = plan["costs"]
    o, r = plan["transport"]["outbound"], plan["transport"]["return"]
    start = date.fromisoformat(plan["start_date"])
    acts = [it["name"] for d in plan["days"] for it in d["items"] if it["type"] == "activity"]
    hi = ", ".join(acts[:4]) if acts else "relaxed local exploring"
    stay = f" You'll stay at {plan['stay']['name']} for {plan['nights']} night(s)." if plan["stay"] else ""
    if status == "infeasible":
        short = c["grand_total"] - req["budget"]
        head = (f"This is the cheapest plan I could build for {plan['destination']}, but it still needs "
                f"{inr(c['grand_total'])} against your {inr(req['budget'])} budget (short by {inr(short)}).")
    else:
        head = (f"Your {plan['total_days']}-day {plan['destination']} trip for {plan['travelers']} from {plan['origin']} "
                f"starting {start:%a %d %b} comes to {inr(c['grand_total'])}, within your {inr(req['budget'])} budget.")
    return (f"{head} You'll travel out by {o['label'].lower()} and return by {r['label'].lower()}.{stay} "
            f"Highlights include {hi}.")


# ----------------------------------------------------------------------------- node
def finalize_node(state: dict) -> dict:
    T = Tracer(state, "Finalize")
    if state.get("halt"):
        T.log("Stopped", state.get("message", "needs more input"), "warn")
        return {"status": state.get("status", "needs_input"), "trace": T.merged(state), "last_node": "finalize"}

    plan, critic, req = state.get("plan"), state.get("critic", {}), state["request"]
    updates: dict = {"last_node": "finalize"}

    if plan is None:
        updates.update(status="infeasible",
                       message="No transport combination fits these dates. Try more days or a different start date.")
        T.log("No plan", updates["message"], "fail")
        updates["trace"] = T.merged(state)
        return updates

    if critic.get("passed"):
        status = "warnings" if critic.get("warnings") else "ok"
        msg = ("Plan validated. " + (f"{len(critic['warnings'])} soft warning(s) to review." if critic.get("warnings") else "All checks passed.")
               + " Review it and approve to export."
               + (" Destination data was AI-estimated by a local model - verify prices and hours before booking." if req.get("generated") else ""))
    else:
        status = "infeasible"
        over = next((i for i in critic.get("issues", []) if i["code"] == "over_budget"), None)
        if over:
            msg = (f"I could not fit this trip into {inr(req['budget'])}. The cheapest plan I could build costs "
                   f"{inr(plan['costs']['grand_total'])} - short by {inr(over['data']['by'])}. "
                   "Increase the budget, add travellers to share costs, or choose another destination.")
        else:
            msg = "The plan still breaks a rule after all retries: " + "; ".join(i["message"] for i in critic.get("issues", [])[:2])
    updates.update(status=status, message=msg)
    T.log("Status", msg, {"ok": "ok", "warnings": "warn", "infeasible": "fail"}[status])

    # ---- narrative: LLM text must pass the Critic's grounding check, else use the template
    settings = state["settings"]
    llm = get_llm(settings)
    text, source = None, "template"
    if llm.is_real:
        try:
            cand = llm.write_narrative(build_facts(plan, req, status))
            if cand:
                chk = check_narrative(cand, plan, req)
                if chk["ok"]:
                    text, source = cand, llm.name
                    T.log("Summary written", f"{llm.name} text passed the Critic's grounding check", "ok")
                else:
                    why = (f"invented amount(s) {chk['bad_amounts']}" if chk["bad_amounts"]
                           else f"length {chk['words']} words out of range")
                    T.log("Critic rejected LLM summary", f"{why} - using the verified template instead", "warn")
        except LLMError as e:
            T.log("LLM unavailable", f"{e} - using the template summary", "warn")
    if text is None:
        text = template_narrative(plan, req, status)
        T.log("Summary written", "template (facts taken directly from the plan)", "info")
    updates.update(narrative=text, narrative_source=source)

    # ---- diff after a disruption
    if state.get("prior_plan"):
        updates["diff"] = diff_plans(state["prior_plan"], plan)
        d = updates["diff"]
        T.log("Repair summary", f"{len(d['added'])} added, {len(d['removed'])} removed, {len(d['moved'])} moved; "
              f"unchanged day(s): {d['unchanged_days'] or 'none'}; cost {inr(d['cost_before'])} -> {inr(d['cost_after'])}", "ok")
    updates["tasks"] = set_task(state.get("tasks", []), "T7", "done")
    updates["trace"] = T.merged(state)
    return updates
