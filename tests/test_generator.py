"""Any-destination support: Ollama-generated profiles (HTTP mocked), validation and persistence."""
import copy
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tripguard import generator as gen  # noqa: E402
from tripguard.agents.critic import run_checks  # noqa: E402
from tripguard.api import apply_disruption, plan_trip  # noqa: E402
from tripguard.tools import catalog  # noqa: E402

S = {"today": "2026-10-19", "llm": "ollama"}
D = {"start_date": "2026-10-23"}
HAMPI = "3 day Hampi trip from Bengaluru for 2 friends budget 9000 heritage and nature"

HAMPI_META = {"canonical_name": "Hampi", "state": "Karnataka", "country": "India", "lat": 15.335, "lon": 76.46,
              "tagline": "Ruined empire among boulders",
              "climate": {"rain_prob": [2, 2, 3, 8, 15, 30, 40, 38, 35, 25, 8, 3],
                          "tmax": [31, 34, 37, 38, 37, 32, 30, 30, 31, 31, 30, 30], "tmin": [16, 18, 21, 24, 24, 23, 22, 22, 22, 21, 18, 15]},
              "local": {"mode": "auto-rickshaw", "cost_per_km": 14, "speed_kmh": 22, "station_transfer": 250, "breakfast_pp": 70}}
HAMPI_ATTR = {"attractions": [
    {"name": "Virupaksha Temple", "tags": ["spiritual", "heritage"], "cost_pp": 0, "duration_min": 60, "open": "06:00", "close": "20:00",
     "outdoor": False, "best_time": "morning", "must_see": True, "desc": "Living 7th-century temple.", "lat": 15.335, "lon": 76.462},
    {"name": "Vittala Temple & Stone Chariot", "tags": ["heritage", "culture"], "cost_pp": 40, "duration_min": 120, "open": "08:30", "close": "17:30",
     "outdoor": True, "best_time": "morning", "must_see": True, "desc": "Iconic stone chariot.", "lat": 15.342, "lon": 76.475},
    {"name": "Hampi Bazaar walk", "tags": ["shopping", "culture"], "cost_pp": 0, "duration_min": 60, "open": "08:00", "close": "20:00",
     "outdoor": True, "best_time": "any", "desc": "Old market street.", "lat": 15.336, "lon": 76.461},
    {"name": "Hemakuta Hill sunset", "tags": ["nature"], "cost_pp": 0, "duration_min": 60, "open": "05:00", "close": "19:30",
     "outdoor": True, "best_time": "evening", "desc": "Sunset over ruins.", "lat": 15.333, "lon": 76.459},
    {"name": "Lotus Mahal & Elephant Stables", "tags": ["heritage"], "cost_pp": 40, "duration_min": 90, "open": "08:30", "close": "17:30",
     "outdoor": True, "best_time": "morning", "desc": "Zenana enclosure.", "lat": 15.3, "lon": 76.5},
    # deliberately bad items the validator must repair:
    {"name": "Matanga Hill sunrise", "tags": ["nature", "skydiving"], "cost_pp": "free", "duration_min": 9999, "open": "25:99", "close": "xx",
     "lat": 19.07, "lon": 72.87, "best_time": "dawn"},          # coordinates in Mumbai
    {"name": "Virupaksha Temple", "tags": ["spiritual"]},      # duplicate
    "not an object", {"name": ""},
]}
HAMPI_SF = {"stays": [{"tier": "dorm", "name": "Riverside backpackers", "price_inr": 450},
                      {"tier": "private", "name": "Boulder guesthouse", "price_inr": 1300},
                      {"tier": "budget_hotel", "name": "Heritage view hotel", "price_inr": 999999}],   # absurd price, mid_hotel missing
            "food": [{"name": "Mango Tree style rooftop", "kind": "restaurant", "cost_pp": 350, "tags": ["food", "relax"]},
                     {"name": "Hampi thali house", "kind": "thali", "cost_pp": 150, "tags": ["food"]}]}   # no street / cafe


def resp(payload):
    m = mock.Mock()
    m.raise_for_status.return_value = None
    m.json.return_value = payload
    return m


def fake_post(meta=HAMPI_META, attr=HAMPI_ATTR, sf=HAMPI_SF, calls=None):
    def _post(url, json=None, **k):
        sys_prompt = json["messages"][0]["content"]
        if calls is not None:
            calls.append(sys_prompt[:40])
        if "You extract structured trip requests" in sys_prompt:
            origin = "Mysore" if "Mysore" in json["messages"][1]["content"] else "Bengaluru"
            return resp({"message": {"content": '{"destination": "hampi", "origin": "%s", "days": 3, "travelers": 2, "budget_inr": 9000, "interests": ["heritage"]}' % origin}})
        if '"canonical_name"' in sys_prompt:
            import json as j
            return resp({"message": {"content": j.dumps(meta)}})
        if '"attractions"' in sys_prompt:
            import json as j
            return resp({"message": {"content": j.dumps(attr)}})
        if '"stays"' in sys_prompt:
            import json as j
            return resp({"message": {"content": j.dumps(sf)}})
        if "ONE JSON object {\"lat\"" in sys_prompt:
            return resp({"message": {"content": '{"lat": 12.30, "lon": 76.64, "country": "India"}'}})
        return resp({"message": {"content": "A short friendly summary " * 3}})
    return _post


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = catalog.GENERATED_DIR
        catalog.GENERATED_DIR = __import__("pathlib").Path(self.tmp.name)
        catalog.reset_generated()

    def tearDown(self):
        catalog.GENERATED_DIR = self._old
        catalog.reset_generated()
        self.tmp.cleanup()


class Normalisers(unittest.TestCase):
    C = (15.335, 76.46)

    def test_attractions_repaired(self):
        out, notes = gen.normalise_attractions(HAMPI_ATTR["attractions"], self.C, "hampi")
        names = [a["name"] for a in out]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(out), 6)                                  # duplicate, junk and empty rows dropped
        bad = next(a for a in out if a["name"].startswith("Matanga"))
        self.assertEqual((bad["cost_pp"], bad["open"], bad["close"]), (0, "09:00", "18:00"))
        self.assertEqual(bad["tags"], ["nature"])
        self.assertEqual(bad["best_time"], "any")
        self.assertLess(gen.haversine_km(bad["lat"], bad["lon"], *self.C), gen.MAX_CENTER_DIST_KM)   # Mumbai coords replaced
        self.assertTrue(notes)
        for a in out:
            self.assertLessEqual(a["duration_min"], 300)
            self.assertGreaterEqual(gen._mins(a["close"]) - gen._mins(a["open"]), a["duration_min"])

    def test_stays_always_cover_every_tier(self):
        out, notes = gen.normalise_stays(HAMPI_SF["stays"], self.C, "hampi")
        self.assertEqual([s["type"] for s in out], ["hostel_dorm", "hostel_private", "hotel_budget", "hotel_mid"])
        self.assertEqual(out[2]["price"], 5000)                        # absurd price clamped
        self.assertTrue(all("AI-estimated" in s["name"] for s in out))
        self.assertTrue(notes)

    def test_food_fills_missing_kinds(self):
        out, _ = gen.normalise_food(HAMPI_SF["food"], self.C, "hampi")
        self.assertEqual({f["kind"] for f in out}, {"street", "thali", "cafe", "restaurant"})
        self.assertEqual(len({f["id"] for f in out}), len(out))

    def test_meta_defaults_on_garbage(self):
        meta, notes = gen.normalise_meta({"climate": {"rain_prob": [1, 2]}, "local": {"cost_per_km": "lots"}})
        self.assertEqual(len(meta["climate"]["rain_prob"]), 12)
        self.assertEqual(meta["local"]["cost_per_km"], 14)
        self.assertGreaterEqual(len(notes), 3)

    def test_everything_is_garbage(self):
        self.assertEqual(gen.normalise_attractions(None, self.C, "x")[0], [])
        self.assertEqual(len(gen.normalise_stays("nope", self.C, "x")[0]), 4)
        self.assertEqual(len(gen.normalise_food(None, self.C, "x")[0]), 4)


class AnyDestination(Base):
    def test_full_plan_for_unknown_destination(self):
        calls = []
        with mock.patch("requests.post", side_effect=fake_post(calls=calls)):
            st = plan_trip(HAMPI, S, D)
        self.assertEqual(st["request"]["destination"], "Hampi")
        self.assertTrue(st["request"].get("generated"))
        self.assertIn(st["status"], ("ok", "warnings"), st["message"])
        self.assertTrue(st["critic"]["passed"], st["critic"]["issues"])
        self.assertTrue(any(w["code"] == "generated_data" for w in st["critic"]["warnings"]))
        self.assertIn("AI-estimated", st["message"])
        self.assertTrue(any(e["action"] == "Profile ready" for e in st["trace"]))
        self.assertTrue(any(s["kind"] == "generated" for s in st["research"]["sources"]))
        self.assertLessEqual(st["plan"]["costs"]["grand_total"], 9000)
        self.assertGreaterEqual(sum(1 for c in calls if "travel data assistant" in c), 3)

    def test_profile_is_saved_and_reused_without_ollama(self):
        with mock.patch("requests.post", side_effect=fake_post()):
            plan_trip(HAMPI, S, D)
        self.assertTrue(list(catalog.GENERATED_DIR.glob("dest_hampi.json")))
        catalog.reset_generated()                                     # simulate a fresh app start
        with mock.patch("requests.post", side_effect=AssertionError("Ollama must not be called")):
            st = plan_trip("3 day Hampi trip from Bengaluru for 2 friends budget 9000 heritage", {"today": "2026-10-19", "llm": "mock"}, D)
        self.assertIn(st["status"], ("ok", "warnings"), st["message"])
        self.assertEqual(st["request"]["destination"], "Hampi")

    def test_mock_model_explains_how_to_unlock(self):
        st = plan_trip(HAMPI, {"today": "2026-10-19", "llm": "mock"}, D)
        self.assertEqual(st["status"], "needs_input")
        self.assertIn("Ollama", st["message"])

    def test_ollama_down(self):
        with mock.patch("requests.post", side_effect=ConnectionError("refused")):
            st = plan_trip(HAMPI, S, D)
        self.assertEqual(st["status"], "needs_input")
        self.assertIn("ollama serve", st["message"])
        self.assertEqual(catalog.generated_destinations(), [])

    def test_foreign_place_rejected(self):
        meta = dict(HAMPI_META, canonical_name="Paris", country="France", lat=48.85, lon=2.35)
        with mock.patch("requests.post", side_effect=fake_post(meta=meta)):
            st = plan_trip("3 day trip to Paris from Delhi budget 20000", S, D)
        self.assertEqual(st["status"], "needs_input")
        self.assertIn("not an Indian place", st["message"])
        self.assertEqual(catalog.generated_destinations(), [])

    def test_unlocatable_place_rejected(self):
        meta = dict(HAMPI_META, lat=0, lon=0)                       # not in India
        with mock.patch("requests.post", side_effect=fake_post(meta=meta)):
            st = plan_trip(HAMPI, S, D)
        self.assertEqual(st["status"], "needs_input")
        self.assertIn("Could not locate", st["message"])

    def test_too_few_attractions_rejected(self):
        with mock.patch("requests.post", side_effect=fake_post(attr={"attractions": HAMPI_ATTR["attractions"][:2]})):
            st = plan_trip(HAMPI, S, D)
        self.assertEqual(st["status"], "needs_input")
        self.assertIn("enough real attractions", st["message"])

    def test_geocoder_used_when_live(self):
        def fake_get(url, **k):
            if gen.GEO_URL in url:
                return resp({"results": [{"name": "Hampi", "latitude": 15.3350, "longitude": 76.4600, "admin1": "Karnataka",
                                          "country_code": "IN"}]})
            raise ConnectionError("offline")
        with mock.patch("requests.post", side_effect=fake_post()), mock.patch("requests.get", side_effect=fake_get):
            st = plan_trip(HAMPI, {**S, "live": True}, D)
        self.assertIn(st["status"], ("ok", "warnings"), st["message"])
        self.assertEqual(catalog.destination("Hampi")["coords_source"], "geocoder")

    def test_geocoder_rejects_foreign_match(self):
        def fake_get(url, **k):
            return resp({"results": [{"name": "Paris", "latitude": 48.85, "longitude": 2.35, "admin1": "Ile-de-France", "country_code": "FR"}]})
        with mock.patch("requests.post", side_effect=fake_post()), mock.patch("requests.get", side_effect=fake_get):
            st = plan_trip("3 day trip to Paris from Delhi budget 20000", {**S, "live": True}, D)
        self.assertEqual(st["status"], "needs_input")
        self.assertIn("outside India", st["message"])

    def test_unknown_origin_resolved(self):
        text = "3 day Hampi trip from Mysore for 2 friends budget 9000 heritage"
        with mock.patch("requests.post", side_effect=fake_post()):
            st = plan_trip(text, S, D)
        self.assertEqual(st["request"]["origin"], "Mysore")
        self.assertIn("Mysore", catalog.origins())
        self.assertIn(st["status"], ("ok", "warnings"), st["message"])

    def test_unknown_origin_without_help_falls_back(self):
        st = plan_trip("3 day Goa trip from Mysore for 2 budget 12000", {"today": "2026-10-19", "llm": "mock"}, D)
        self.assertEqual(st["request"]["origin"], "Pune")
        self.assertTrue(any(e["action"] == "Origin" and e["status"] == "warn" for e in st["trace"]))

    def test_builtin_destination_never_calls_generator(self):
        with mock.patch("requests.post", side_effect=fake_post()) as post:
            st = plan_trip("Plan a 4-day Goa trip from Pune for 3 friends, budget ₹18,000, beaches and street food", S, D)
        self.assertEqual(st["status"], "ok")
        sys_prompts = [c.kwargs["json"]["messages"][0]["content"] for c in post.call_args_list]
        self.assertFalse(any("canonical_name" in p for p in sys_prompts))

    def test_disruptions_work_on_generated_destination(self):
        with mock.patch("requests.post", side_effect=fake_post()):
            base = plan_trip(HAMPI, S, D)
        for ev in ({"type": "outbound_delay", "minutes": 120}, {"type": "return_cancelled"}, {"type": "rain_day", "day": 2}):
            st = apply_disruption(base, ev)
            self.assertIn(st["status"], ("ok", "warnings"), (ev, st["message"]))

    def test_critic_still_catches_tampering_on_generated_data(self):
        with mock.patch("requests.post", side_effect=fake_post()):
            st = plan_trip(HAMPI, S, D)
        p = copy.deepcopy(st["plan"])
        p["costs"]["grand_total"] += 4000
        res = run_checks(p, st["request"], st["research"], st["constraints"])
        self.assertFalse(res["passed"])

    def test_clear_generated(self):
        with mock.patch("requests.post", side_effect=fake_post()):
            plan_trip(HAMPI, S, D)
        self.assertGreaterEqual(catalog.clear_generated(), 1)
        self.assertEqual(catalog.generated_destinations(), [])

    def test_damaged_cache_file_is_ignored(self):
        (catalog.GENERATED_DIR / "dest_broken.json").write_text("{not json", encoding="utf-8")
        catalog.reset_generated()
        self.assertIn("Goa", catalog.destinations())


class ParserFreeText(unittest.TestCase):
    def test_candidates(self):
        from tripguard.parser import free_destination, free_origin
        self.assertEqual(free_destination("Plan a trip to Mount Abu from Ahmedabad, 3 days"), "Mount Abu")
        self.assertEqual(free_destination("3 day Hampi trip from Bengaluru"), "Hampi")
        self.assertEqual(free_destination("a holiday to coorg from mysore for 4 days"), "Coorg")
        self.assertIsNone(free_destination("I want a trip to relax"))
        self.assertIsNone(free_destination("Goa from Pune 4 days"))
        self.assertEqual(free_origin("trip from nashik to Goa"), "Nashik")
        self.assertIsNone(free_origin("trip from Pune to Goa"))


if __name__ == "__main__":
    unittest.main()
