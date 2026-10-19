"""Export helpers: Markdown, JSON and a *mock* e-mail preview (nothing is ever sent)."""
from __future__ import annotations

import html as _html
import json

from .utils import from_iso, inr


def _t(iso: str) -> str:
    return from_iso(iso).strftime("%H:%M")


def plan_to_markdown(state: dict) -> str:
    plan, req = state["plan"], state["request"]
    c = plan["costs"]
    o, r = plan["transport"]["outbound"], plan["transport"]["return"]
    out = [f"# {plan['destination']} trip from {plan['origin']} ({plan['total_days']} days)",
           "", f"_{state.get('narrative', '')}_", "",
           f"- **Travellers:** {plan['travelers']}  |  **Start:** {plan['start_date']}  |  **Budget:** {inr(req['budget'])}",
           f"- **Outbound:** {o['label']} {_t(o['depart'])} -> {_t(o['arrive'])}",
           f"- **Return:** {r['label']} {_t(r['depart'])} -> {_t(r['arrive'])}"]
    if plan["stay"]:
        out.append(f"- **Stay:** {plan['stay']['name']} ({plan['nights']} night(s), {inr(plan['stay']['cost_total'])})")
    for d in plan["days"]:
        w = d.get("weather") or {}
        out += ["", f"## Day {d['day_no']} - {d['weekday']} {d['date']}",
                f"Weather: {w.get('tmin', '?')}-{w.get('tmax', '?')} °C, rain chance {w.get('rain_prob', '?')}%", ""]
        for it in d["items"]:
            cost = f" - {inr(it['cost_total'])}" if it.get("cost_total") else ""
            out.append(f"- {_t(it['start'])}-{_t(it['end'])}  {it['name']}{cost}")
    out += ["", "## Budget", "", "| Item | Amount |", "|---|---|"]
    labels = [("transport_out", "Outbound fare"), ("transport_return", "Return fare"), ("stay", "Stay"),
              ("food", "Food"), ("breakfast_est", "Breakfast"), ("activities", "Activities"),
              ("local_transport", "Local transport"), ("reserve", "5% reserve"), ("grand_total", "**Total**")]
    out += [f"| {n} | {inr(c[k])} |" for k, n in labels]
    crit = state.get("critic", {})
    out += ["", f"Validation: {crit.get('summary', 'n/a')}", "",
            ("> Destination data was AI-estimated by a local language model (names, prices, hours) and is NOT verified. "
             "Transport is synthetic sample data. Classroom proof of concept."
             if state["request"].get("generated") else
             "> Prices, timetables and places are synthetic sample data for a classroom proof of concept.")]
    return "\n".join(out)


def plan_to_json(state: dict) -> str:
    keep = {k: state.get(k) for k in ("request", "plan", "critic", "narrative", "status", "diff", "attempts")}
    return json.dumps(keep, indent=2, ensure_ascii=False)


def email_preview(state: dict, to: str = "friends@example.com") -> str:
    plan = state["plan"]
    return (f"To: {to}\nSubject: {plan['destination']} trip plan - {plan['start_date']} "
            f"({inr(plan['costs']['grand_total'])})\n\n{state.get('narrative', '')}\n\n"
            f"Full itinerary attached.\n\n[MOCK EMAIL - nothing was sent]")


def plan_to_html(state: dict) -> str:
    """Standalone, print-friendly page (open in a browser, then Print -> Save as PDF)."""
    e = _html.escape
    plan, req = state["plan"], state["request"]
    c = plan["costs"]
    o, r = plan["transport"]["outbound"], plan["transport"]["return"]
    rows = []
    for d in plan["days"]:
        w = d.get("weather") or {}
        rows.append(f"<h2>Day {d['day_no']} - {e(d['weekday'])} {e(d['date'])}"
                    f"<small> {w.get('tmin', '?')}-{w.get('tmax', '?')} &deg;C, rain {w.get('rain_prob', '?')}%</small></h2><table>")
        for it in d["items"]:
            cost = inr(it["cost_total"]) if it.get("cost_total") else ""
            rows.append(f"<tr><td class=t>{_t(it['start'])}-{_t(it['end'])}</td><td>{e(it['name'])}</td><td class=c>{cost}</td></tr>")
        rows.append("</table>")
    labels = [("transport_out", "Outbound fare"), ("transport_return", "Return fare"), ("stay", "Stay"), ("food", "Food"),
              ("breakfast_est", "Breakfast"), ("activities", "Activities"), ("local_transport", "Local transport"),
              ("reserve", "5% reserve"), ("grand_total", "Total")]
    budget = "".join(f"<tr><td>{n}</td><td class=c>{inr(c[k])}</td></tr>" for k, n in labels)
    note = ("Destination data was AI-estimated by a local language model and is not verified. Transport is synthetic sample data."
            if req.get("generated") else "Prices, timetables and places are synthetic sample data (classroom proof of concept).")
    stay = f"<p><b>Stay:</b> {e(plan['stay']['name'])}, {plan['nights']} night(s)</p>" if plan["stay"] else ""
    return (f"<!doctype html><meta charset=utf-8><title>{e(plan['destination'])} trip</title><style>"
            "body{font:15px/1.5 system-ui,sans-serif;max-width:760px;margin:32px auto;padding:0 16px;color:#1d2433}"
            "h1{margin-bottom:0}h2{margin:26px 0 6px;border-bottom:3px solid #e8a200;padding-bottom:4px}h2 small{font-weight:400;color:#667;font-size:.7em;margin-left:8px}"
            "table{border-collapse:collapse;width:100%}td{padding:4px 8px;border-bottom:1px solid #e6e8ee}.t{white-space:nowrap;width:110px;font-variant-numeric:tabular-nums}"
            ".c{text-align:right;white-space:nowrap}.note{color:#667;font-size:.85em;margin-top:28px}</style>"
            f"<h1>{e(plan['origin'])} to {e(plan['destination'])}</h1><p>{e(state.get('narrative', ''))}</p>"
            f"<p><b>Outbound:</b> {e(o['label'])} {_t(o['depart'])}-{_t(o['arrive'])} &nbsp; <b>Return:</b> {e(r['label'])} {_t(r['depart'])}-{_t(r['arrive'])}</p>{stay}"
            f"{''.join(rows)}<h2>Budget (limit {inr(req['budget'])})</h2><table>{budget}</table><p class=note>{note}</p>")
