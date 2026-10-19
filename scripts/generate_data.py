"""Generate the synthetic datasets used by TripGuard.

Run:  python scripts/generate_data.py

Outputs
    data/catalog.json    destinations, attractions, stays, food, climate
    data/transport.json  bus / train / flight options for every origin -> destination

IMPORTANT: everything here is SYNTHETIC sample data for a student proof of concept.
Landmark names are real public places (coordinates are approximate); stays, food places,
operators, prices, opening hours and timetables are invented for demonstration only.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


# ----------------------------------------------------------------------------- helpers
def A(id, name, lat, lon, tags, cost, dur, open_, close, outdoor, best="any",
      closed=(), event=False, must=False, desc=""):
    return {
        "id": id, "name": name, "lat": lat, "lon": lon, "tags": list(tags),
        "cost_pp": cost, "duration_min": dur, "open": open_, "close": close,
        "outdoor": outdoor, "best_time": best, "closed_days": list(closed),
        "event": event, "must_see": must, "desc": desc,
    }


def S(id, name, kind, unit, cap, price, comfort, breakfast, lat, lon):
    return {
        "id": id, "name": name, "type": kind, "unit": unit, "capacity": cap,
        "price": price, "comfort": comfort, "breakfast_included": breakfast,
        "lat": lat, "lon": lon,
    }


def F(id, name, kind, cost, tags, lat, lon, open_="08:00", close="23:00"):
    return {
        "id": id, "name": name, "kind": kind, "cost_pp": cost, "tags": list(tags),
        "lat": lat, "lon": lon, "open": open_, "close": close,
    }


# ----------------------------------------------------------------------------- origins
ORIGINS = {
    "Pune": (18.5204, 73.8567),
    "Mumbai": (19.0760, 72.8777),
    "Delhi": (28.6139, 77.2090),
    "Bengaluru": (12.9716, 77.5946),
    "Hyderabad": (17.3850, 78.4867),
    "Ahmedabad": (23.0225, 72.5714),
}

# ----------------------------------------------------------------------------- destinations
DESTINATIONS = {}

DESTINATIONS["Goa"] = {
    "lat": 15.4909, "lon": 73.8278, "state": "Goa",
    "tagline": "Beaches, forts and seafood shacks",
    "local": {"mode": "scooter-taxi / auto", "cost_per_km": 16, "capacity": 3,
              "speed_kmh": 26, "station_transfer": 350, "breakfast_pp": 90},
    "climate": {
        "rain_prob": [2, 1, 2, 5, 20, 85, 95, 92, 75, 40, 12, 4],
        "tmax": [32, 33, 34, 34, 34, 31, 29, 29, 30, 32, 33, 32],
        "tmin": [21, 22, 24, 26, 27, 25, 24, 24, 24, 24, 23, 21],
    },
    "stays": [
        S("goa-dorm", "Coconut Grove Backpackers (sample) - dorm bed", "hostel_dorm", "bed", 1, 650, 2, False, 15.5420, 73.7660),
        S("goa-priv", "Palm Lane Hostel (sample) - private room", "hostel_private", "room", 2, 1500, 3, True, 15.5380, 73.7680),
        S("goa-hotel", "Sunset Cove Budget Hotel (sample)", "hotel_budget", "room", 2, 2200, 4, True, 15.5450, 73.7620),
        S("goa-mid", "Lagoon View Residency (sample) - family room", "hotel_mid", "room", 3, 3600, 5, True, 15.5400, 73.7640),
    ],
    "attractions": [
        A("goa-calangute", "Calangute Beach", 15.5439, 73.7553, ["beach", "relax"], 0, 120, "06:00", "19:00", True, "morning", must=True, desc="Long, lively beach with shacks and sunbeds."),
        A("goa-baga-sports", "Baga Beach water-sports pack", 15.5553, 73.7517, ["beach", "adventure"], 600, 150, "09:00", "17:30", True, "morning", desc="Parasailing / banana-boat bundle (sample price)."),
        A("goa-baga-evening", "Baga Beach shacks (evening)", 15.5553, 73.7517, ["nightlife", "beach", "food"], 0, 120, "17:00", "23:59", True, "evening", event=True, desc="Sunset drinks, live music and shack dining."),
        A("goa-aguada", "Fort Aguada & lighthouse", 15.4925, 73.7737, ["heritage", "nature"], 30, 90, "09:30", "17:30", True, "morning", must=True, desc="17th-century Portuguese fort above the sea."),
        A("goa-basilica", "Basilica of Bom Jesus, Old Goa", 15.5009, 73.9116, ["heritage", "spiritual", "culture"], 0, 60, "09:00", "18:30", False, "any", must=True, desc="UNESCO-listed baroque church."),
        A("goa-fontainhas", "Fontainhas Latin Quarter walk, Panjim", 15.4968, 73.8326, ["heritage", "culture", "food"], 0, 90, "08:00", "19:00", True, "morning", desc="Colourful Portuguese-era lanes and bakeries."),
        A("goa-museum", "Goa State Museum, Panaji", 15.4989, 73.8270, ["culture", "heritage"], 20, 75, "09:30", "17:30", False, "any", closed=[0], desc="Indoor museum - good rainy-day option (closed Mondays, sample)."),
        A("goa-arpora-night", "Saturday Night Market, Arpora", 15.5740, 73.7650, ["shopping", "nightlife", "food"], 0, 120, "18:00", "23:30", True, "evening", closed=[0, 1, 2, 3, 4, 6], event=True, desc="Open-air night market (Saturdays only, sample)."),
        A("goa-anjuna-flea", "Anjuna Flea Market", 15.5736, 73.7406, ["shopping", "food"], 0, 120, "08:30", "18:30", True, "any", closed=[0, 1, 3, 4, 5, 6], desc="Wednesday flea market (sample)."),
        A("goa-chapora", "Chapora Fort and coastal viewpoint", 15.6049, 73.7348, ["heritage", "nature", "relax"], 0, 75, "09:00", "18:30", True, "evening", desc="Hilltop fort with sweeping coastal views."),
        A("goa-vagator", "Vagator Beach sunset", 15.5994, 73.7347, ["beach", "nature", "relax"], 0, 90, "15:30", "19:30", True, "evening", event=True, desc="Cliff-backed beach, best near sunset."),
        A("goa-scuba", "Grande Island scuba trip", 15.4000, 73.8000, ["adventure", "beach"], 1800, 240, "09:00", "15:00", True, "morning", desc="Boat trip + intro dive (sample price)."),
    ],
    "food": [
        F("goa-f1", "Beach Shack Fish Thali (sample)", "thali", 220, ["food", "seafood"], 15.5430, 73.7570),
        F("goa-f2", "Panjim Poi & Chai Stall (sample)", "street", 90, ["food", "street"], 15.4975, 73.8320, "07:00", "21:00"),
        F("goa-f3", "Baga Grill House (sample)", "restaurant", 520, ["food", "seafood", "nightlife"], 15.5545, 73.7525, "12:00", "23:30"),
        F("goa-f4", "Calangute Rolls & Chaat Corner (sample)", "street", 110, ["food", "street"], 15.5415, 73.7585, "10:00", "23:00"),
        F("goa-f5", "Old Goa Cafe (sample)", "cafe", 260, ["food", "relax"], 15.5000, 73.9100, "08:00", "19:00"),
        F("goa-f6", "Vagator Cliff Cafe (sample)", "cafe", 340, ["food", "relax", "beach"], 15.5990, 73.7350, "09:00", "23:00"),
    ],
}

DESTINATIONS["Jaipur"] = {
    "lat": 26.9124, "lon": 75.7873, "state": "Rajasthan",
    "tagline": "Forts, palaces and bazaars of the Pink City",
    "local": {"mode": "auto-rickshaw / cab", "cost_per_km": 15, "capacity": 3,
              "speed_kmh": 22, "station_transfer": 250, "breakfast_pp": 80},
    "climate": {
        "rain_prob": [3, 4, 3, 3, 8, 25, 70, 75, 40, 5, 2, 2],
        "tmax": [22, 26, 32, 38, 42, 40, 34, 32, 34, 35, 29, 24],
        "tmin": [8, 11, 17, 23, 28, 29, 26, 25, 24, 19, 13, 9],
    },
    "stays": [
        S("jai-dorm", "Pink Lane Backpackers (sample) - dorm bed", "hostel_dorm", "bed", 1, 450, 2, False, 26.9200, 75.7900),
        S("jai-priv", "Haveli Hostel (sample) - private room", "hostel_private", "room", 2, 1100, 3, True, 26.9210, 75.8000),
        S("jai-hotel", "Rajputana Inn (sample)", "hotel_budget", "room", 2, 1800, 4, True, 26.9180, 75.8050),
        S("jai-mid", "Amer Gate Residency (sample) - family room", "hotel_mid", "room", 3, 3000, 5, True, 26.9250, 75.8100),
    ],
    "attractions": [
        A("jai-amber", "Amber Fort", 26.9855, 75.8513, ["heritage", "culture"], 100, 150, "08:00", "17:30", True, "morning", must=True, desc="Hilltop fort-palace with mirror work."),
        A("jai-citypalace", "City Palace", 26.9258, 75.8237, ["heritage", "culture"], 200, 120, "09:30", "17:00", False, "any", must=True, desc="Royal residence and museum complex."),
        A("jai-hawamahal", "Hawa Mahal", 26.9239, 75.8267, ["heritage"], 50, 45, "09:00", "16:30", True, "morning", must=True, desc="Iconic pink sandstone facade."),
        A("jai-jantar", "Jantar Mantar", 26.9246, 75.8246, ["heritage", "culture"], 50, 60, "09:00", "16:30", True, "any", desc="18th-century astronomical observatory."),
        A("jai-nahargarh", "Nahargarh Fort viewpoint", 26.9372, 75.8155, ["nature", "heritage", "relax"], 50, 120, "10:00", "19:00", True, "evening", event=True, desc="Sunset views over the city."),
        A("jai-jalmahal", "Jal Mahal viewpoint", 26.9535, 75.8462, ["nature", "relax"], 0, 30, "06:00", "19:00", True, "any", desc="Palace in the middle of Man Sagar lake."),
        A("jai-johari", "Johari Bazaar stroll", 26.9190, 75.8280, ["shopping", "food"], 0, 120, "10:00", "20:30", True, "evening", closed=[6], desc="Jewellery, textiles and street snacks (closed Sundays, sample)."),
        A("jai-albert", "Albert Hall Museum", 26.9117, 75.8197, ["culture", "heritage"], 40, 75, "09:00", "17:00", False, "any", desc="Indoor museum - good rainy-day option."),
        A("jai-birla", "Birla Mandir", 26.8924, 75.8156, ["spiritual"], 0, 45, "06:00", "20:00", False, "evening", desc="White marble temple."),
        A("jai-village", "Rajasthani village dinner & folk show", 26.8800, 75.8000, ["food", "culture", "nightlife"], 800, 180, "18:30", "23:00", False, "evening", event=True, desc="Thali dinner with folk performances (sample price)."),
    ],
    "food": [
        F("jai-f1", "Lassi & Kachori Stall (sample)", "street", 90, ["food", "street"], 26.9195, 75.8275, "07:00", "21:00"),
        F("jai-f2", "Dal Baati Thali House (sample)", "thali", 230, ["food"], 26.9230, 75.8200),
        F("jai-f3", "Rooftop Haveli Cafe (sample)", "cafe", 380, ["food", "relax"], 26.9250, 75.8250, "09:00", "22:30"),
        F("jai-f4", "Royal Rasoi Restaurant (sample)", "restaurant", 650, ["food", "culture"], 26.9150, 75.8100, "12:00", "23:00"),
        F("jai-f5", "Chaat Gali Corner (sample)", "street", 100, ["food", "street"], 26.9180, 75.8260, "10:00", "22:00"),
        F("jai-f6", "Amber Road Veg Dhaba (sample)", "thali", 200, ["food"], 26.9700, 75.8400),
    ],
}

DESTINATIONS["Manali"] = {
    "lat": 32.2396, "lon": 77.1887, "state": "Himachal Pradesh",
    "tagline": "Mountain valleys, cafes and adventure",
    "local": {"mode": "local taxi", "cost_per_km": 22, "capacity": 4,
              "speed_kmh": 24, "station_transfer": 600, "breakfast_pp": 100},
    "climate": {
        "rain_prob": [20, 25, 30, 25, 25, 45, 80, 85, 50, 12, 8, 15],
        "tmax": [8, 10, 15, 20, 24, 26, 24, 23, 23, 20, 14, 10],
        "tmin": [-2, 0, 4, 8, 11, 14, 15, 15, 11, 6, 1, -1],
    },
    "stays": [
        S("man-dorm", "Beas Backpackers (sample) - dorm bed", "hostel_dorm", "bed", 1, 600, 2, False, 32.2570, 77.1850),
        S("man-priv", "Cedar Hostel (sample) - private room", "hostel_private", "room", 2, 1400, 3, True, 32.2550, 77.1860),
        S("man-hotel", "Pine View Hotel (sample)", "hotel_budget", "room", 2, 2200, 4, True, 32.2450, 77.1880),
        S("man-mid", "Snow Peak Residency (sample) - family room", "hotel_mid", "room", 3, 3800, 5, True, 32.2420, 77.1890),
    ],
    "attractions": [
        A("man-hadimba", "Hadimba Devi Temple", 32.2482, 77.1800, ["spiritual", "nature", "heritage"], 0, 60, "08:00", "18:00", True, "morning", must=True, desc="Wooden temple in a deodar forest."),
        A("man-mall", "Mall Road walk", 32.2396, 77.1887, ["shopping", "food"], 0, 90, "10:00", "22:00", True, "evening", desc="Shops, snacks and Tibetan market."),
        A("man-oldmanali", "Old Manali cafes", 32.2590, 77.1840, ["food", "nightlife", "relax"], 0, 120, "09:00", "23:00", True, "evening", must=True, desc="Riverside cafes and live music."),
        A("man-vashisht", "Vashisht hot springs & temple", 32.2646, 77.1888, ["spiritual", "relax"], 0, 75, "06:00", "21:00", True, "morning", desc="Natural hot-spring baths."),
        A("man-solang", "Solang Valley (ropeway & views)", 32.3160, 77.1580, ["adventure", "nature"], 150, 180, "09:00", "17:00", True, "morning", must=True, desc="Meadow valley with ropeway and activities."),
        A("man-paraglide", "Paragliding at Solang (optional)", 32.3160, 77.1580, ["adventure"], 2500, 120, "10:00", "16:00", True, "morning", desc="Tandem flight (sample price)."),
        A("man-jogini", "Jogini Waterfall trek", 32.2777, 77.2025, ["nature", "adventure"], 0, 180, "08:00", "16:30", True, "morning", desc="Easy forest trek to a waterfall."),
        A("man-tibetan", "Tibetan Monastery & Gompa", 32.2398, 77.1881, ["spiritual", "culture"], 0, 60, "08:00", "18:00", False, "any", desc="Quiet monastery - good rainy-day option."),
        A("man-naggar", "Naggar Castle & Roerich gallery", 32.1228, 77.1770, ["heritage", "culture"], 100, 120, "09:00", "17:00", False, "any", desc="Medieval castle and art gallery."),
    ],
    "food": [
        F("man-f1", "Siddu & Chai Stall (sample)", "street", 100, ["food", "street"], 32.2400, 77.1890, "08:00", "21:00"),
        F("man-f2", "Himachali Thali Dhaba (sample)", "thali", 240, ["food"], 32.2430, 77.1850),
        F("man-f3", "Riverside Cafe, Old Manali (sample)", "cafe", 360, ["food", "relax", "nightlife"], 32.2595, 77.1845, "09:00", "23:00"),
        F("man-f4", "Mountain Grill Restaurant (sample)", "restaurant", 600, ["food"], 32.2410, 77.1880, "12:00", "22:30"),
        F("man-f5", "Momo Corner (sample)", "street", 120, ["food", "street"], 32.2390, 77.1880, "10:00", "22:00"),
        F("man-f6", "Vashisht Rooftop Cafe (sample)", "cafe", 300, ["food", "relax"], 32.2640, 77.1890, "08:30", "21:30"),
    ],
}

DESTINATIONS["Rishikesh"] = {
    "lat": 30.0869, "lon": 78.2676, "state": "Uttarakhand",
    "tagline": "Yoga capital, Ganga aarti and white-water rafting",
    "local": {"mode": "shared auto / taxi", "cost_per_km": 18, "capacity": 3,
              "speed_kmh": 24, "station_transfer": 400, "breakfast_pp": 80},
    "climate": {
        "rain_prob": [12, 15, 12, 6, 10, 35, 85, 88, 60, 10, 3, 6],
        "tmax": [22, 25, 31, 37, 40, 38, 33, 32, 33, 32, 28, 23],
        "tmin": [6, 8, 13, 18, 23, 25, 25, 24, 22, 16, 10, 7],
    },
    "stays": [
        S("rish-dorm", "Ganga Backpackers (sample) - dorm bed", "hostel_dorm", "bed", 1, 500, 2, False, 30.1250, 78.3250),
        S("rish-priv", "Yogi Hostel (sample) - private room", "hostel_private", "room", 2, 1200, 3, True, 30.1230, 78.3230),
        S("rish-hotel", "Riverside Stay (sample)", "hotel_budget", "room", 2, 1900, 4, True, 30.1200, 78.3150),
        S("rish-mid", "Ganges View Residency (sample) - family room", "hotel_mid", "room", 3, 3200, 5, True, 30.1100, 78.3000),
    ],
    "attractions": [
        A("rish-laxman", "Laxman Jhula", 30.1267, 78.3292, ["spiritual", "nature", "shopping"], 0, 60, "05:00", "21:00", True, "morning", must=True, desc="Iconic suspension bridge over the Ganga."),
        A("rish-ramjhula", "Ram Jhula & Parmarth Niketan", 30.1195, 78.3076, ["spiritual", "culture"], 0, 75, "05:00", "21:00", True, "morning", desc="Ashram complex by the river."),
        A("rish-aarti", "Ganga Aarti at Triveni Ghat", 30.1068, 78.2955, ["spiritual", "culture"], 0, 75, "17:30", "19:30", True, "evening", event=True, must=True, desc="Evening river-side ceremony."),
        A("rish-beatles", "Beatles Ashram (Chaurasi Kutia)", 30.1228, 78.3203, ["culture", "heritage", "nature"], 150, 90, "09:00", "16:00", True, "any", desc="Graffiti-covered ruins in the forest."),
        A("rish-rafting", "White-water rafting (16 km)", 30.0891, 78.4262, ["adventure"], 700, 210, "08:00", "15:00", True, "morning", must=True, desc="Grade II-III rapids (sample price)."),
        A("rish-kunjapuri", "Kunjapuri sunrise trek", 30.2013, 78.3317, ["nature", "spiritual", "adventure"], 0, 150, "05:00", "10:00", True, "morning", event=True, desc="Sunrise viewpoint at a hilltop temple."),
        A("rish-neergarh", "Neer Garh waterfall trek", 30.1121, 78.3319, ["nature", "adventure"], 20, 120, "08:00", "17:00", True, "morning", desc="Short trek to a waterfall."),
        A("rish-tapovan", "Tapovan cafe hopping", 30.1330, 78.3270, ["food", "relax", "shopping"], 0, 90, "08:00", "22:00", True, "evening", desc="Yoga-town cafes and bookshops."),
        A("rish-bungee", "Bungee jump (optional)", 30.1270, 78.4000, ["adventure"], 3550, 120, "09:00", "16:00", True, "morning", desc="83 m jump (sample price)."),
    ],
    "food": [
        F("rish-f1", "Ghat-side Poori Sabzi Stall (sample)", "street", 80, ["food", "street"], 30.1070, 78.2960, "06:30", "20:00"),
        F("rish-f2", "Satvik Thali House (sample)", "thali", 180, ["food", "spiritual"], 30.1200, 78.3150),
        F("rish-f3", "German Bakery-style Cafe (sample)", "cafe", 330, ["food", "relax"], 30.1330, 78.3270, "08:00", "22:00"),
        F("rish-f4", "Rooftop Ganga Restaurant (sample)", "restaurant", 480, ["food", "relax"], 30.1260, 78.3290, "11:30", "22:30"),
        F("rish-f5", "Chaat & Lassi Corner (sample)", "street", 90, ["food", "street"], 30.1180, 78.3100, "09:00", "21:30"),
        F("rish-f6", "Tapovan Smoothie Bar (sample)", "cafe", 280, ["food", "relax"], 30.1335, 78.3265, "08:00", "21:30"),
    ],
}

DESTINATIONS["Udaipur"] = {
    "lat": 24.5854, "lon": 73.7125, "state": "Rajasthan",
    "tagline": "City of Lakes - palaces, boats and sunsets",
    "local": {"mode": "auto-rickshaw / cab", "cost_per_km": 15, "capacity": 3,
              "speed_kmh": 22, "station_transfer": 300, "breakfast_pp": 85},
    "climate": {
        "rain_prob": [2, 2, 2, 2, 6, 30, 75, 80, 45, 5, 2, 2],
        "tmax": [26, 29, 34, 39, 41, 38, 31, 29, 32, 35, 31, 27],
        "tmin": [9, 12, 17, 22, 26, 26, 24, 23, 22, 18, 13, 10],
    },
    "stays": [
        S("udr-dorm", "Lakeview Backpackers (sample) - dorm bed", "hostel_dorm", "bed", 1, 500, 2, False, 24.5780, 73.6850),
        S("udr-priv", "Haveli Hostel Udaipur (sample) - private room", "hostel_private", "room", 2, 1300, 3, True, 24.5790, 73.6830),
        S("udr-hotel", "Pichola Heritage Inn (sample)", "hotel_budget", "room", 2, 2100, 4, True, 24.5770, 73.6810),
        S("udr-mid", "Sunrise Palace Residency (sample) - family room", "hotel_mid", "room", 3, 3500, 5, True, 24.5750, 73.6800),
    ],
    "attractions": [
        A("udr-citypalace", "City Palace Udaipur", 24.5764, 73.6835, ["heritage", "culture"], 300, 120, "09:30", "17:30", False, "any", must=True, desc="Sprawling palace on the lake."),
        A("udr-boat", "Lake Pichola boat ride", 24.5700, 73.6800, ["nature", "relax", "heritage"], 400, 60, "10:00", "17:30", True, "evening", must=True, desc="Boat past Jag Mandir."),
        A("udr-jagdish", "Jagdish Temple", 24.5800, 73.6840, ["spiritual", "heritage"], 0, 45, "05:00", "21:00", False, "morning", desc="Indo-Aryan temple near the palace."),
        A("udr-saheliyon", "Saheliyon-ki-Bari gardens", 24.5994, 73.6812, ["nature", "heritage", "relax"], 30, 60, "09:00", "17:00", True, "morning", desc="Fountain gardens."),
        A("udr-fatehsagar", "Fateh Sagar Lake promenade", 24.6008, 73.6770, ["nature", "relax", "food"], 0, 90, "06:00", "22:00", True, "evening", event=True, desc="Lakeside promenade and snacks."),
        A("udr-sajjangarh", "Sajjangarh Monsoon Palace", 24.5944, 73.6358, ["nature", "heritage"], 300, 120, "09:00", "17:30", True, "evening", desc="Hilltop palace with panoramic views."),
        A("udr-bagore", "Bagore Ki Haveli museum", 24.5788, 73.6806, ["culture", "heritage"], 100, 75, "09:30", "17:30", False, "any", desc="Haveli museum - good rainy-day option."),
        A("udr-folkshow", "Folk dance show at Bagore Ki Haveli", 24.5788, 73.6806, ["culture", "nightlife"], 150, 60, "18:45", "20:30", False, "evening", event=True, desc="Evening dance performance."),
        A("udr-hathipol", "Hathi Pol bazaar", 24.5810, 73.6810, ["shopping", "food"], 0, 90, "10:00", "21:00", True, "evening", desc="Handicrafts, miniature paintings, snacks."),
    ],
    "food": [
        F("udr-f1", "Kachori & Jalebi Stall (sample)", "street", 80, ["food", "street"], 24.5800, 73.6830, "07:00", "21:00"),
        F("udr-f2", "Mewari Thali House (sample)", "thali", 210, ["food"], 24.5790, 73.6820),
        F("udr-f3", "Lakeview Rooftop Cafe (sample)", "cafe", 360, ["food", "relax"], 24.5740, 73.6810, "08:30", "23:00"),
        F("udr-f4", "Royal Mewar Restaurant (sample)", "restaurant", 620, ["food", "culture"], 24.5760, 73.6830, "12:00", "23:00"),
        F("udr-f5", "Street Chaat Lane (sample)", "street", 100, ["food", "street"], 24.5815, 73.6805, "10:00", "22:00"),
        F("udr-f6", "Fateh Sagar Snack Stalls (sample)", "street", 130, ["food", "street", "relax"], 24.6005, 73.6775, "15:00", "22:30"),
    ],
}


# ----------------------------------------------------------------------------- transport
def haversine(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    d = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(d))


MODES = {
    # mode: (label, operator, slots, base fare, per-km fare, comfort, arrive_before_min)
    "bus_seater": ("seater bus", "Sample Intercity Bus", ["07:00", "15:00", "19:30"], 120, 0.95, 2, 20),
    "bus_sleeper": ("AC sleeper bus", "Sample Night Coach", ["19:00", "20:30", "21:45"], 250, 1.45, 3, 20),
    "train_sleeper": ("sleeper-class train", "Sample Rail Express", ["06:15", "13:40", "19:50", "22:30"], 80, 0.55, 2, 30),
    "train_3ac": ("3AC train", "Sample Rail Express", ["07:05", "14:20", "20:35", "23:10"], 240, 1.55, 4, 30),
    "flight": ("flight", "Sample Air", ["06:05", "11:20", "17:45"], 2000, 3.0, 5, 120),
}


def round_to(x, base):
    return int(round(x / base) * base)


def build_transport():
    routes = {}
    for o, ocoord in ORIGINS.items():
        routes[o] = {}
        for d, dcfg in DESTINATIONS.items():
            air_km = haversine(ocoord, (dcfg["lat"], dcfg["lon"]))
            road_km = air_km * 1.25
            opts = []
            for mode, (label, operator, slots, base, perkm, comfort, arrive_before) in MODES.items():
                if mode.startswith("bus") and road_km > 900:
                    continue
                if mode == "flight" and air_km < 250:
                    continue
                if mode == "flight":
                    fare = round_to(base + perkm * air_km, 50)
                    duration = round_to(45 + air_km / 720 * 60, 5)
                else:
                    fare = round_to(base + perkm * road_km, 10)
                    if mode.startswith("train"):
                        speed = 45 if road_km < 600 else 58
                    else:
                        speed = 42
                    duration = round_to(road_km / speed * 60, 5)
                for s in slots:
                    opts.append({
                        "id": f"{mode}-{s.replace(':', '')}",
                        "mode": mode, "label": label, "operator": operator,
                        "depart": s, "duration_min": duration, "fare_pp": fare,
                        "comfort": comfort, "arrive_before_min": arrive_before,
                    })
            routes[o][d] = {"distance_km": round(road_km), "options": opts}
    return routes


def main():
    DATA.mkdir(exist_ok=True)
    catalog = {
        "_note": "SYNTHETIC sample data for a student PoC. Landmark names are real; coordinates approximate; "
                 "stays, food places, prices and hours are invented.",
        "origins": {k: {"lat": v[0], "lon": v[1]} for k, v in ORIGINS.items()},
        "destinations": DESTINATIONS,
    }
    (DATA / "catalog.json").write_text(json.dumps(catalog, indent=1, ensure_ascii=False), encoding="utf-8")
    transport = {
        "_note": "SYNTHETIC timetable and fares, generated from distance formulas. Not real operators.",
        "routes": build_transport(),
    }
    (DATA / "transport.json").write_text(json.dumps(transport, indent=1), encoding="utf-8")
    n_opts = sum(len(v["options"]) for o in transport["routes"].values() for v in o.values())
    print(f"catalog.json: {len(DESTINATIONS)} destinations | transport.json: {n_opts} options")


if __name__ == "__main__":
    main()
