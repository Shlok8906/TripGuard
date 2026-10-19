#!/usr/bin/env python3
"""Command-line runner for TripGuard (no Streamlit needed).

  python run_cli.py --text "4 day Goa trip from Pune for 3 friends budget 18000"
  python run_cli.py --text "..." --disrupt return_cancelled
  python run_cli.py --llm ollama --model qwen3:4b --text "..."
"""
import argparse
import sys

from tripguard.api import apply_disruption, stream_plan
from tripguard.config import DEFAULT_OLLAMA_MODEL
from tripguard.export import plan_to_markdown
from tripguard.utils import inr

DEFAULT_TEXT = "Plan a 4-day Goa trip from Pune for 3 friends, budget ₹18,000, we love beaches, street food and nightlife"
ICON = {"ok": "[ok]  ", "warn": "[warn]", "fail": "[FAIL]", "info": "[..]  "}


def print_trace(trace, start=0):
    for e in trace[start:]:
        print(f"  {ICON.get(e['status'], '[..]')} {e['agent']:<11} {e['action']}: {e['detail']}")


def parse_event(spec: str) -> dict:
    kind, _, arg = spec.partition(":")
    if kind == "outbound_delay":
        return {"type": kind, "minutes": int(arg or 180)}
    if kind == "rain_day":
        return {"type": kind, "day": int(arg or 2)}
    if kind == "attraction_closed":
        return {"type": kind, "attraction_id": arg or "goa-aguada"}
    if kind == "return_cancelled":
        return {"type": kind}
    sys.exit(f"unknown disruption '{kind}'")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", default=DEFAULT_TEXT)
    ap.add_argument("--llm", choices=["mock", "ollama"], default="mock")
    ap.add_argument("--model", default=DEFAULT_OLLAMA_MODEL)
    ap.add_argument("--live", action="store_true", help="try live Open-Meteo / OSRM (falls back to cache/bundled)")
    ap.add_argument("--simulate-down", action="store_true", help="pretend live sources are down")
    ap.add_argument("--start-date", default=None, help="YYYY-MM-DD default if the text has no date")
    ap.add_argument("--origin", default=None)
    ap.add_argument("--disrupt", default=None,
                    help="outbound_delay[:min] | return_cancelled | rain_day[:day] | attraction_closed[:id]")
    ap.add_argument("--export", default=None, help="write the itinerary to this .md file")
    a = ap.parse_args()

    settings = {"llm": a.llm, "ollama_model": a.model, "live": a.live, "simulate_down": a.simulate_down}
    defaults = {k: v for k, v in (("start_date", a.start_date), ("origin", a.origin)) if v}
    state = None
    for _, st in stream_plan(a.text, settings, defaults):
        state = st
    print(f"\nEngine: {state['engine']}   Status: {state['status']}\n")
    print_trace(state["trace"])
    if state.get("plan"):
        print("\n" + plan_to_markdown(state))
    else:
        print("\n" + state.get("message", ""))
    if a.disrupt and state.get("plan"):
        n = len(state["trace"])
        state = apply_disruption(state, parse_event(a.disrupt))
        print(f"\n=== DISRUPTION: {a.disrupt} -> {state['status']} ===")
        print_trace(state["trace"], n)
        if state.get("diff"):
            d = state["diff"]
            print(f"\nChanged: +{len(d['added'])} -{len(d['removed'])} moved {len(d['moved'])}; "
                  f"{d['other']}; cost {inr(d['cost_before'])} -> {inr(d['cost_after'])}")
    if a.export and state.get("plan"):
        with open(a.export, "w", encoding="utf-8") as f:
            f.write(plan_to_markdown(state))
        print(f"\nSaved {a.export}")
    return 0 if state["status"] in ("ok", "warnings") else 1


if __name__ == "__main__":
    sys.exit(main())
