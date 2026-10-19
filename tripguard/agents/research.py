"""Research Agent: gathers data from several sources and records where each item came from.

Sources (each with a fallback chain, so the demo never depends on the network):
  * Transport timetable / fares  - bundled 'partner API' (synthetic JSON)
  * Places, stays, food          - bundled catalog (synthetic JSON)
  * Weather                      - Open-Meteo (live) -> disk cache -> monthly climatology
  * Local travel times           - OSRM (live)       -> disk cache -> straight-line estimate
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from ..tools.catalog import destination, transport_options
from ..tools.routing import get_matrix
from ..tools.weather import get_weather
from ..trace import Tracer, set_task


def _dates(req: dict, cons: dict, extra: int = 1):
    start = date.fromisoformat(req["start_date"])
    n = req["days"] + cons.get("extra_days", 0) + extra   # +1 spare day in case a disruption extends the trip
    return [start + timedelta(days=i) for i in range(n)]


def research_node(state: dict) -> dict:
    T = Tracer(state, "Research")
    req, settings, cons = state["request"], state["settings"], state["constraints"]
    today = date.fromisoformat(settings["today"]) if settings.get("today") else date.today()
    live, down = bool(settings.get("live")), bool(settings.get("simulate_down"))
    prev = state.get("research") or {}
    dest = req["destination"]
    dates = _dates(req, cons)

    wanted = {x.isoformat() for x in dates}
    reuse_weather = bool(prev.get("weather")) and wanted <= set(prev["weather"]["days"])
    reuse_matrix = bool(prev.get("matrix"))
    n_excl = len(cons.get("excluded_transport", []))
    route = transport_options(req["origin"], dest)
    d = destination(dest)

    if prev and reuse_weather and reuse_matrix:
        # retry / repair loop: nothing new to fetch
        extra = ""
        if n_excl:
            extra = f"; partner API re-queried: {len(route['options']) - n_excl} transport options remain"
        T.log("Data re-used", "places, stays, weather and travel times from the earlier fetch" + extra, "info")
        weather, matrix = prev["weather"], prev["matrix"]
    else:
        T.log("Transport options", f"{req['origin']} <-> {dest}: {len(route['options']) - n_excl} options "
              f"({route['distance_km']} km; partner API is synthetic data)", "ok")
        T.log("Places & stays", f"{len(d['attractions'])} attractions, {len(d['stays'])} stays, "
              f"{len(d['food'])} food places in {dest}", "ok")
        if reuse_weather:
            weather = prev["weather"]
            T.log("Weather", "re-using the forecast fetched earlier", "info")
        else:
            weather = get_weather(dest, dates, live, down, today)
            for status, text in weather["notes"]:
                T.log("Weather", text, status)
        if reuse_matrix:
            matrix = prev["matrix"]
            T.log("Routing", "re-using travel-time matrix fetched earlier", "info")
        else:
            matrix = get_matrix(dest, live, down)
            for status, text in matrix["notes"]:
                T.log("Routing", text, status)

    sources = [
        {"what": "Transport timetable", "name": "Sample partner API", "kind": "bundled",
         "detail": "synthetic JSON", "age_s": 0},
        ({"what": "Places, stays, food", "name": "Ollama-generated profile (unverified)", "kind": "generated",
          "detail": f"AI-estimated; coordinates from {d.get('coords_source', 'model')}", "age_s": 0}
         if d.get("generated") else
         {"what": "Places, stays, food", "name": "Sample catalog", "kind": "bundled",
          "detail": "synthetic JSON", "age_s": 0}),
        {"what": "Weather", **weather["source"]},
        {"what": "Travel times", **matrix["source"]},
    ]
    research = {"weather": weather, "matrix": matrix, "sources": sources,
                "fetched_at": datetime.now().isoformat(timespec="seconds")}
    tasks = set_task(set_task(state.get("tasks", []), "T2", "done"), "T3", "done")
    return {"research": research, "tasks": tasks, "trace": T.merged(state), "last_node": "research"}
