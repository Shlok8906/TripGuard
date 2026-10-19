"""TripGuard - a student trip planner whose plans are checked before you see them (Streamlit UI).

Run:  streamlit run app.py     (needs Ollama running with qwen3:4b for free-text and new destinations)
"""
from __future__ import annotations

import html
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from tripguard.api import stream_plan
from tripguard.config import DEFAULT_OLLAMA_MODEL
from tripguard.export import email_preview, plan_to_html, plan_to_json, plan_to_markdown
from tripguard.llm import list_ollama_models
from tripguard.tools.catalog import builtin_destinations, clear_generated, destination, generated_destinations
from tripguard.utils import from_iso, inr

st.set_page_config(page_title="TripGuard", page_icon="🧭", layout="wide")

SAMPLES = {
    "Goa, 3 friends": "Plan a 4-day Goa trip from Pune for 3 friends, budget ₹18,000, we love beaches, street food and nightlife",
    "Jaipur, 2 people": "3 day Jaipur trip from Delhi for 2 people, budget ₹14,000, heritage and food, prefer train",
    "Hampi, any place": "3 day Hampi trip from Bengaluru for 2 friends, budget ₹9,000, heritage and nature",
    "Manali, tight budget": "Manali from Bengaluru 3 days solo budget ₹3,000",
}

# ------------------------------------------------------------------ look
# Idea: the plan is a route. Each day is a timetable line with a stop per item (railway-board feel).
# One accent (signal marigold) on neutral tints, so it holds up in both light and dark themes.
CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&display=swap');
:root{--tg-accent:#e8a200;--tg-ink:#1d3557;--tg-line:rgba(127,127,127,.32);--tg-soft:rgba(127,127,127,.11)}
h1,h2,h3,.tg-title,.tg-dayno{font-family:'Bricolage Grotesque',system-ui,sans-serif!important;letter-spacing:-.015em}
.tg-route{font:700 clamp(1.9rem,4.5vw,3rem)/1.05 'Bricolage Grotesque',system-ui,sans-serif;letter-spacing:-.025em;margin:4px 0 2px}
.tg-route i{font-style:normal;font-weight:500;opacity:.45;padding:0 .25em}
.tg-sub{opacity:.75;margin-bottom:6px}
.tg-card{border:1px solid var(--tg-line);border-radius:10px;padding:12px 14px;height:100%}
.tg-card b{display:block;margin-bottom:2px}
.tg-card .tg-small{font-size:.86rem;opacity:.75}
.tg-day{margin:6px 0 30px}
.tg-dayhead{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:0 0 10px}
.tg-dayno{font-weight:700;font-size:1.4rem}
.tg-date{opacity:.7}
.tg-chip{display:inline-block;padding:1px 10px;border-radius:999px;background:var(--tg-soft);font-size:.82rem;margin-right:4px}
.tg-chip.warn{background:rgba(232,162,0,.22)}
.tg-line{border-left:3px solid var(--tg-accent);margin-left:6px}
.tg-row{display:grid;grid-template-columns:104px 1fr auto;gap:10px;padding:7px 0 7px 20px;position:relative}
.tg-row:before{content:"";position:absolute;left:-8px;top:14px;width:13px;height:13px;border-radius:50%;background:var(--tg-accent)}
.tg-row.tg-move:before{border-radius:3px;background:var(--tg-ink);outline:2px solid var(--tg-accent)}
.tg-row.tg-quiet{opacity:.72;padding-top:3px;padding-bottom:3px}
.tg-row.tg-quiet:before{width:9px;height:9px;left:-6px;top:11px;background:var(--tg-line)}
.tg-time{font-variant-numeric:tabular-nums;font-weight:600;white-space:nowrap}
.tg-name{line-height:1.3}
.tg-note{font-size:.84rem;opacity:.68}
.tg-cost{font-variant-numeric:tabular-nums;white-space:nowrap;opacity:.9}
.tg-bar{height:8px;border-radius:4px;background:var(--tg-soft);overflow:hidden;margin:3px 0 0}
.tg-bar i{display:block;height:100%;background:var(--tg-accent)}
.tg-bar.over i{background:#d64545}
.tg-brow{display:grid;grid-template-columns:1fr auto;gap:2px 12px;padding:6px 0}
.tg-brow .tg-bar{grid-column:1/3}
.tg-check{display:grid;grid-template-columns:26px 1fr;padding:7px 0;border-bottom:1px solid var(--tg-line)}
.tg-src{display:inline-block;margin:0 6px 6px 0;padding:3px 11px;border-radius:999px;border:1px solid var(--tg-line);font-size:.84rem}
.tg-src:before{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px;background:var(--tg-line)}
.tg-src.live:before{background:#2e9e5b}.tg-src.cache:before{background:#d99100}.tg-src.generated:before{background:#d64545}
.tg-trace{font-size:.86rem;padding:1px 0}
@media (max-width:640px){.tg-row{grid-template-columns:1fr;gap:1px}.tg-time{font-size:.85rem;opacity:.8}}
</style>"""
st.markdown(CSS, unsafe_allow_html=True)

E = html.escape
SRC_NOTE = {"live": "live", "cache": "saved copy", "bundled": "built-in", "generated": "AI-estimated"}
TAG_ICON = {"beach": "🏖️", "heritage": "🏛️", "nature": "⛰️", "spiritual": "🛕", "food": "🍜", "nightlife": "🌙",
            "adventure": "🧗", "shopping": "🛍️", "culture": "🎭", "relax": "🌿"}


# ------------------------------------------------------------------ helpers
def hhmm(iso: str) -> str:
    return from_iso(iso).strftime("%H:%M")


def mode_icon(label: str) -> str:
    low = label.lower()
    return "✈️" if "flight" in low else ("🚌" if "bus" in low else "🚆")


def item_icon(it: dict) -> str:
    t = it["type"]
    if t == "transit":
        return mode_icon(it["name"])
    if t == "meal":
        return "🍽️"
    if t == "arrival":
        return "🏨"
    if t == "transfer":
        return "🚕"
    if t == "checkout":
        return "🧳"
    return next((TAG_ICON[g] for g in it.get("tags", []) if g in TAG_ICON), "📍")


def row_html(it: dict) -> str:
    cls = "tg-row" + (" tg-move" if it["type"] == "transit" else " tg-quiet" if it["type"] in ("transfer", "checkout") else "")
    note = f"<div class='tg-note'>{E(it['notes'])}</div>" if it.get("notes") and it["type"] in ("activity", "meal") else ""
    cost = inr(it["cost_total"]) if it.get("cost_total") else ""
    return (f"<div class='{cls}'><div class='tg-time'>{hhmm(it['start'])}-{hhmm(it['end'])}</div>"
            f"<div class='tg-name'>{item_icon(it)} {E(it['name'])}{note}</div><div class='tg-cost'>{cost}</div></div>")


def day_html(d: dict) -> str:
    w = d.get("weather") or {}
    rain = w.get("rain_prob", 0)
    icon = "🌧️" if rain >= 60 else ("🌦️" if rain >= 40 else "☀️")
    spend = sum(it.get("cost_total") or 0 for it in d["items"]) + (d.get("local_hop_cost") or 0)
    chips = (f"<span class='tg-chip{' warn' if rain >= 40 else ''}'>{icon} {w.get('tmin', '?')}-{w.get('tmax', '?')}°C, rain {rain}%</span>"
             f"<span class='tg-chip'>{inr(spend)} today</span>")
    local = (f"<div class='tg-row tg-quiet'><div class='tg-time'></div><div class='tg-name'>🛺 Local rides between stops</div>"
             f"<div class='tg-cost'>{inr(d['local_hop_cost'])}</div></div>" if d.get("local_hop_cost") else "")
    head = (f"<div class='tg-dayhead'><span class='tg-dayno'>Day {d['day_no']}</span>"
            f"<span class='tg-date'>{E(d['weekday'])} {from_iso(d['date'] + 'T00:00').strftime('%d %b')}</span>{chips}</div>")
    return f"<div class='tg-day'>{head}<div class='tg-line'>{''.join(row_html(it) for it in d['items'])}{local}</div></div>"


def render_trace(trace: list) -> str:
    rows, current = [], None
    for e in trace:
        if e["attempt"] != current:
            current = e["attempt"]
            rows.append(f"<div style='margin:10px 0 2px;font-weight:600'>{'Attempt ' + str(current) if current else 'Getting started'}</div>")
        mark = {"ok": "✅", "warn": "⚠️", "fail": "❌"}.get(e["status"], "▫️")
        rows.append(f"<div class='tg-trace'>{mark} <b>{E(e['agent'])}</b> {E(e['action'])}"
                    f"<span style='opacity:.7'> - {E(str(e['detail']))}</span></div>")
    return "<div style='max-height:380px;overflow-y:auto'>" + "".join(rows) + "</div>"


def run_stream(stream, box, banner):
    final = None
    for node, s in stream:
        final = s
        banner.info(f"{node.replace('_agent', '').title()} agent is working...")
        box.markdown(render_trace(s["trace"]), unsafe_allow_html=True)
    banner.empty()
    box.empty()
    return final


def model_ready(model: str, installed: list) -> bool:
    return any(m == model or m.startswith(model + ":") for m in installed)


def current_settings() -> dict:
    return {"llm": "ollama", "ollama_model": st.session_state.get("ollama_model") or DEFAULT_OLLAMA_MODEL,
            "live": bool(st.session_state.get("live_data", True)), "simulate_down": bool(st.session_state.get("sim_down", False)),
            "max_retries": int(st.session_state.get("max_retries", 4)), "engine": st.session_state.get("engine_choice", "auto")}


def set_prompt(text: str):
    st.session_state["prompt"] = text


# ------------------------------------------------------------------ sidebar
st.session_state.setdefault("prompt", SAMPLES["Goa, 3 friends"])
with st.sidebar:
    st.header("TripGuard")
    model = st.session_state.get("ollama_model") or DEFAULT_OLLAMA_MODEL
    installed = list_ollama_models()
    if not installed:
        st.warning("Ollama is not running. Start it with `ollama serve`. Until then the built-in places still plan, "
                   "but new destinations and free-form wording need the model.")
    elif model_ready(model, installed):
        st.success(f"Model ready: {model}")
    else:
        st.warning(f"{model} is not installed yet. Run `ollama pull {model}`.")
    st.checkbox("Use live weather and travel times", key="live_data", value=True,
                help="Falls back to saved copies, then built-in estimates, when you are offline.")
    saved = generated_destinations()
    if saved:
        st.caption("Saved places: " + ", ".join(saved))
    with st.expander("Advanced"):
        st.text_input("Ollama model", value=DEFAULT_OLLAMA_MODEL, key="ollama_model")
        st.selectbox("Agent engine", ["auto", "builtin", "langgraph"], key="engine_choice",
                     help="auto uses LangGraph when installed, otherwise the built-in runner.")
        st.slider("Re-plan attempts", 0, 6, 4, key="max_retries")
        st.checkbox("Pretend live sources are down", key="sim_down", help="Shows the fallback: live, then saved copy, then built-in.")
        if saved and st.button("Delete saved places"):
            clear_generated()
            st.rerun()
    st.caption("Fares, timetables and venues are sample data. Places outside the built-in five are AI-estimated.")

# ------------------------------------------------------------------ input (the only place trip details are entered)
st.title("Plan a trip that checks itself")
st.text_area("Where to, how long, who with, and how much?", key="prompt", height=84,
             placeholder="e.g. 4 days in Goa from Pune for 3 friends, ₹18,000, beaches and street food")
st.caption("Anything you leave out is assumed and shown below the plan. Built-in places: " + ", ".join(builtin_destinations())
           + ". Any other place in India works too; the first time takes a few minutes.")
cols = st.columns(len(SAMPLES))
for col, (label, text) in zip(cols, SAMPLES.items()):
    col.button(label, key=f"sample_{label}", on_click=set_prompt, args=(text,))
go = st.button("Plan my trip", type="primary")
banner = st.empty()
trace_box = st.empty()

if go:
    st.session_state["state"] = run_stream(stream_plan(st.session_state["prompt"], current_settings(), {}), trace_box, banner)
    st.session_state["approved"] = False
state = st.session_state.get("state")

# ------------------------------------------------------------------ results
if not state:
    st.info("Pick an example or describe your trip, then choose Plan my trip.")
    st.stop()

if state["status"] == "needs_input":
    st.warning(state["message"])
    with st.expander("What the agents did"):
        st.markdown(render_trace(state["trace"]), unsafe_allow_html=True)
    st.stop()

plan, req, critic = state["plan"], state["request"], state["critic"]
if plan is None:
    st.error(state["message"])
    st.stop()

c = plan["costs"]
start = date.fromisoformat(plan["start_date"])
end = start + timedelta(days=plan["total_days"] - 1)
left = req["budget"] - c["grand_total"]

st.markdown(f"<div class='tg-route'>{E(plan['origin'])}<i>to</i>{E(plan['destination'])}</div>"
            f"<div class='tg-sub'>{start:%a %d %b} to {end:%a %d %b %Y}, {plan['travelers']} traveller{'s' if plan['travelers'] > 1 else ''}"
            f"</div>", unsafe_allow_html=True)
if state["status"] == "infeasible":
    st.error(state["message"])
elif state["status"] == "warnings":
    st.warning(state["message"])
else:
    st.success(state["message"])
m1, m2, m3 = st.columns(3)
m1.metric("Total cost", inr(c["grand_total"]))
m2.metric("Per person", inr(round(c["grand_total"] / plan["travelers"])))
m3.metric("Under budget by" if left >= 0 else "Over budget by", inr(abs(left)))
if state.get("narrative"):
    st.write(state["narrative"])
if req.get("generated"):
    st.warning("This destination was written by a local language model, so place names, prices and hours are estimates. "
               "The checks confirm the plan fits that profile, not that the profile is true. Verify before booking.")
assumed = [k for k, v in state.get("provenance", {}).items() if v == "default" and k not in ("transport_pref", "stay_pref", "interests")]
if assumed:
    names = {"origin": f"starting from {req['origin']}", "days": f"{req['days']} days", "travelers": f"{req['travelers']} travellers",
             "budget": f"a budget of {inr(req['budget'])}", "start_date": f"starting {start:%a %d %b}"}
    st.caption("Assumed because you didn't say: " + ", ".join(names[k] for k in assumed if k in names)
               + ". Put them in your request to change them.")

t_trip, t_budget, t_map, t_checks, t_export = st.tabs(["Itinerary", "Budget", "Map", "Checks", "Export"])

# ---- Itinerary
with t_trip:
    o, r = plan["transport"]["outbound"], plan["transport"]["return"]
    g1, g2, g3 = st.columns(3)
    delay = f" (running {o['delay_min']} min late)" if o["delay_min"] else ""
    g1.markdown(f"<div class='tg-card'><b>{mode_icon(o['label'])} Going</b>{E(o['label'].capitalize())}, {E(from_iso(o['depart']).strftime('%a'))} "
                f"{hhmm(o['depart'])} to {hhmm(o['arrive'])}{delay}<div class='tg-small'>{inr(o['fare_pp'])} per person</div></div>",
                unsafe_allow_html=True)
    g2.markdown(f"<div class='tg-card'><b>{mode_icon(r['label'])} Coming back</b>{E(r['label'].capitalize())}, {E(from_iso(r['depart']).strftime('%a'))} "
                f"{hhmm(r['depart'])} to {hhmm(r['arrive'])}<div class='tg-small'>{inr(r['fare_pp'])} per person</div></div>",
                unsafe_allow_html=True)
    if plan["stay"]:
        s = plan["stay"]
        g3.markdown(f"<div class='tg-card'><b>🏨 Staying</b>{E(s['name'])}<div class='tg-small'>{plan['nights']} night(s), "
                    f"{s['units']} {s['unit']}(s), {inr(s['cost_total'])}</div></div>", unsafe_allow_html=True)
    st.write("")
    for d in plan["days"]:
        st.markdown(day_html(d), unsafe_allow_html=True)

# ---- Budget
with t_budget:
    pct = min(100, round(c["grand_total"] / max(1, req["budget"]) * 100))
    st.markdown(f"<b>{inr(c['grand_total'])}</b> of your <b>{inr(req['budget'])}</b> budget"
                f"<div class='tg-bar{' over' if left < 0 else ''}'><i style='width:{pct}%'></i></div>", unsafe_allow_html=True)
    st.write("")
    parts = [("Train, bus or flight", c["transport_out"] + c["transport_return"]), ("Stay", c["stay"]), ("Food", c["food"] + c["breakfast_est"]),
             ("Activities and entry fees", c["activities"]), ("Local rides", c["local_transport"]), ("Safety reserve (5%)", c["reserve"])]
    for label, val in sorted(parts, key=lambda x: -x[1]):
        share = round(val / max(1, c["grand_total"]) * 100)
        st.markdown(f"<div class='tg-brow'><span>{label}</span><span class='tg-cost'>{inr(val)} <span style='opacity:.6'>({share}%)</span></span>"
                    f"<div class='tg-bar'><i style='width:{share}%'></i></div></div>", unsafe_allow_html=True)
    tries = state.get("attempts") or []
    if len(tries) > 1:
        st.subheader("How the plan was made to fit")
        for a in tries:
            verdict = "fits" if a["passed"] else ("over budget" if "budget" in a["failed"] else "failed " + ", ".join(a["failed"]))
            st.markdown(f"- Try {a['attempt']}, {a['label'].lower()}: {inr(a['total'])}, {verdict}")

# ---- Map
with t_map:
    pts, stops = [], []
    for d in plan["days"]:
        for it in d["items"]:
            if it["type"] in ("activity", "meal") and it.get("lat") is not None:
                pts.append({"lat": it["lat"], "lon": it["lon"]})
                stops.append(f"<div class='tg-row tg-quiet' style='grid-template-columns:70px 1fr'><div class='tg-time'>Day {d['day_no']}</div>"
                             f"<div class='tg-name'>{item_icon(it)} {E(it['name'])}</div></div>")
    if plan["stay"]:
        pts.append({"lat": plan["stay"]["lat"], "lon": plan["stay"]["lon"]})
    if pts:
        left_col, right_col = st.columns([3, 2])
        left_col.map(pd.DataFrame(pts))
        right_col.markdown("<div class='tg-line'>" + "".join(stops) + "</div>", unsafe_allow_html=True)
        if req.get("generated") or destination(plan["destination"]).get("coords_source") == "model":
            st.caption("Positions are approximate for AI-estimated places.")
    else:
        st.info("No located stops to show.")

# ---- Checks
with t_checks:
    n_pass = sum(1 for ck in critic["checks"] if ck["passed"])
    st.markdown(f"#### {n_pass} of {len(critic['checks'])} checks passed")
    st.caption("A separate checker agent recomputes every price from the catalog and re-tests times, opening hours, weather and travel "
               "time. It never trusts the planner's numbers.")
    for group, title in (("hard", "Must pass"), ("soft", "Worth a look")):
        rows = [ck for ck in critic["checks"] if ck["severity"] == group]
        if not rows:
            continue
        st.markdown(f"**{title}**")
        st.markdown("".join(
            f"<div class='tg-check'><span>{'✅' if ck['passed'] else ('❌' if group == 'hard' else '⚠️')}</span>"
            f"<span>{E(ck['name'])}<div class='tg-note'>{E(ck['detail'])}</div></span></div>" for ck in rows), unsafe_allow_html=True)
    st.write("")
    st.markdown("**Where the data came from**")
    st.markdown("".join(f"<span class='tg-src {E(s['kind'])}'>{E(s['what'])}: {E(s['name'])} ({SRC_NOTE.get(s['kind'], s['kind'])})</span>"
                        for s in state["research"]["sources"]), unsafe_allow_html=True)
    with st.expander("What each agent did"):
        st.markdown(render_trace(state["trace"]), unsafe_allow_html=True)

# ---- Export
with t_export:
    if state["status"] == "infeasible":
        st.error("This plan is over budget, so export is switched off. Raise the budget or change the request.")
    else:
        ok = st.checkbox("I have read this plan and approve it", key="approved")
        if ok:
            d1, d2, d3 = st.columns(3)
            d1.download_button("Itinerary (.md)", plan_to_markdown(state), "tripguard_itinerary.md")
            d2.download_button("Printable page (.html)", plan_to_html(state), "tripguard_itinerary.html")
            d3.download_button("Data (.json)", plan_to_json(state), "tripguard_plan.json")
            st.caption("Open the .html file in a browser and print it to save a PDF.")
            with st.expander("Preview of the message you could send (nothing is sent)"):
                st.code(email_preview(state))
        else:
            st.caption("Tick the box to unlock the downloads.")
