"""Synthetic transport options for ANY origin/destination pair (same formulas as the bundled timetable)."""
from __future__ import annotations

from ..utils import haversine_km, round_to

MODES = {
    # mode: (label, operator, departure slots, base fare, per-km fare, comfort, arrive_before_min)
    "bus_seater": ("seater bus", "Sample Intercity Bus", ["07:00", "15:00", "19:30"], 120, 0.95, 2, 20),
    "bus_sleeper": ("AC sleeper bus", "Sample Night Coach", ["19:00", "20:30", "21:45"], 250, 1.45, 3, 20),
    "train_sleeper": ("sleeper-class train", "Sample Rail Express", ["06:15", "13:40", "19:50", "22:30"], 80, 0.55, 2, 30),
    "train_3ac": ("3AC train", "Sample Rail Express", ["07:05", "14:20", "20:35", "23:10"], 240, 1.55, 4, 30),
    "flight": ("flight", "Sample Air", ["06:05", "11:20", "17:45"], 2000, 3.0, 5, 120),
}


def generate_route(a: tuple, b: tuple) -> dict:
    """a, b = (lat, lon). Returns {'distance_km', 'options'} shaped like data/transport.json routes."""
    air_km = haversine_km(a[0], a[1], b[0], b[1])
    road_km = air_km * 1.25
    opts = []
    for mode, (label, operator, slots, base, perkm, comfort, before) in MODES.items():
        if mode.startswith("bus") and road_km > 900:
            continue
        if mode == "flight" and air_km < 250:
            continue
        if mode == "flight":
            fare = round_to(base + perkm * air_km, 50)
            duration = round_to(45 + air_km / 720 * 60, 5)
        else:
            fare = round_to(base + perkm * road_km, 10)
            speed = (45 if road_km < 600 else 58) if mode.startswith("train") else 42
            duration = round_to(road_km / speed * 60, 5)
        for sl in slots:
            opts.append({"id": f"{mode}-{sl.replace(':', '')}", "mode": mode, "label": label, "operator": operator,
                         "depart": sl, "duration_min": duration, "fare_pp": fare, "comfort": comfort,
                         "arrive_before_min": before})
    return {"distance_km": round(road_km), "options": opts}
