"""Core tests: parser, data tools, planning, Critic fault-injection, workflow, disruptions, Ollama adapter."""
import copy
import os
import sys
import unittest
from datetime import date
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tripguard import llm as llm_mod  # noqa: E402
from tripguard.agents.critic import check_narrative, run_checks  # noqa: E402
from tripguard.api import apply_disruption, plan_trip  # noqa: E402
from tripguard.export import email_preview, plan_to_json, plan_to_markdown  # noqa: E402
from tripguard.graph import run_workflow  # noqa: E402
from tripguard.parser import parse_rules  # noqa: E402
from tripguard.state import new_state  # noqa: E402
from tripguard.tools.routing import get_matrix  # noqa: E402
from tripguard.tools.weather import get_weather  # noqa: E402

TODAY = date(2026, 10, 19)
S = {"today": "2026-10-19"}
D = {"start_date": "2026-10-23"}
GOA = "Plan a 4-day Goa trip from Pune for 3 friends, budget ₹18,000, we love beaches, street food and nightlife"


def plan(text=GOA, **settings):
    return plan_trip(text, {**S, **settings}, D)


class Parser(unittest.TestCase):
    def test_fields(self):
        r = parse_rules(GOA, TODAY)
        self.assertEqual((r["destination"], r["origin"], r["days"], r["travelers"], r["budget"]),
                         ("Goa", "Pune", 4, 3, 18000))
        self.assertIn("food", r["interests"])

    def test_budget_forms(self):
        self.assertEqual(parse_rules("Goa from Pune 25k for 2", TODAY)["budget"], 25000)
        self.assertEqual(parse_rules("Goa from Pune 1.5 lakh", TODAY)["budget"], 150000)

    def test_per_person_budget(self):
        self.assertEqual(parse_rules("Goa from Pune 3 people 5000 per person", TODAY)["budget"], 15000)

    def test_clamping_and_missing_destination(self):
        st = plan("Trip from Pune for 2")
        self.assertEqual(st["status"], "needs_input")
        st = plan("Goa from Pune 30 days 2 people 90000")
        self.assertLessEqual(st["request"]["days"], 7)

    def test_same_origin_destination(self):
        self.assertEqual(plan("Goa from Goa 3 days")["status"], "needs_input")


class Tools(unittest.TestCase):
    def test_weather_fallback_when_down(self):
        w = get_weather("Goa", [date(2026, 10, 23), date(2026, 10, 24)], True, True, TODAY)
        self.assertEqual(len(w["days"]), 2)
        self.assertTrue(all("rain_prob" in v for v in w["days"].values()))
        self.assertIn(w["source"]["kind"], ("cache", "bundled"))

    def test_weather_never_touches_network_when_not_live(self):
        with mock.patch("requests.get", side_effect=AssertionError("network used")):
            get_weather("Goa", [date(2026, 10, 23)], False, False, TODAY)
            get_matrix("Goa", False, False)

    def test_live_failure_falls_back(self):
        with mock.patch("requests.get", side_effect=ConnectionError("down")):
            w = get_weather("Goa", [date(2026, 10, 23)], True, False, TODAY)
            m = get_matrix("Goa", True, False)
        self.assertIn(w["source"]["kind"], ("cache", "bundled"))
        self.assertIn(m["source"]["kind"], ("cache", "bundled"))

    def test_simulate_down_ok_end_to_end(self):
        st = plan(live=True, simulate_down=True)
        self.assertIn(st["status"], ("ok", "warnings"))


class Workflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.st = plan()

    def test_passes_after_retries(self):
        self.assertEqual(self.st["status"], "ok")
        self.assertGreaterEqual(self.st["attempt"], 2, "sample should force at least one Critic rejection")
        self.assertLessEqual(self.st["plan"]["costs"]["grand_total"], 18000)
        self.assertTrue(self.st["attempts"][0]["failed"])

    def test_agents_all_appear_in_trace(self):
        agents = {e["agent"] for e in self.st["trace"]}
        self.assertTrue({"Planner", "Research", "Executor", "Critic", "Finalize"} <= agents)

    def test_deterministic(self):
        again = plan()
        self.assertEqual(again["plan"]["costs"], self.st["plan"]["costs"])

    def test_infeasible_is_honest(self):
        st = plan("Manali from Bengaluru 3 days solo budget 3000")
        self.assertEqual(st["status"], "infeasible")
        self.assertIn("short by", st["message"])

    def test_json_serialisable_and_exports(self):
        import json
        json.dumps(self.st)
        self.assertIn("Day 1", plan_to_markdown(self.st))
        json.loads(plan_to_json(self.st))
        self.assertIn("MOCK EMAIL", email_preview(self.st))

    def test_builtin_engine_equals_auto(self):
        a = run_workflow(new_state(GOA, {**S, "engine": "builtin"}, D))
        self.assertEqual(a["plan"]["costs"], self.st["plan"]["costs"])
        self.assertEqual(a["engine"] if self.st["engine"] == "builtin" else "builtin", "builtin")

    def test_every_sample_prompt(self):
        for t in ("3 day Jaipur trip from Delhi for 2 people, budget 14000, heritage and food, prefer train",
                  "Udaipur from Ahmedabad 3 days 2 travellers budget 9000 lakes and culture",
                  "Rishikesh from Delhi 3 days 4 friends budget 20000 adventure"):
            st = plan(t)
            self.assertIn(st["status"], ("ok", "warnings", "infeasible"), t)
            if st["status"] != "infeasible":
                self.assertTrue(st["critic"]["passed"], t)


class CriticFaultInjection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.st = plan()

    def check(self, mutate):
        p = copy.deepcopy(self.st["plan"])
        mutate(p)
        res = run_checks(p, self.st["request"], self.st["research"], self.st["constraints"])
        return {i["code"] for i in res["issues"]}, res

    def test_clean_plan_passes(self):
        codes, res = self.check(lambda p: None)
        self.assertTrue(res["passed"], res["issues"])

    def test_over_budget_detected(self):
        def m(p):
            p["costs"]["grand_total"] += 5000
        codes, _ = self.check(m)
        self.assertTrue(codes & {"over_budget", "arithmetic"})

    def test_tampered_price_detected(self):
        def m(p):
            for d in p["days"]:
                for it in d["items"]:
                    if it["type"] == "activity" and it["cost_total"]:
                        it["cost_total"] = 1
                        return
            p["days"][0]["items"][0]["cost_total"] += 777
        codes, _ = self.check(m)
        self.assertTrue(codes)

    def test_overlap_detected(self):
        def m(p):
            items = p["days"][1]["items"]
            items[1]["start"] = items[0]["start"]
        codes, _ = self.check(m)
        self.assertTrue(codes & {"overlap", "travel_time", "hours_conflict"})

    def test_opening_hours_detected(self):
        def m(p):
            for it in p["days"][1]["items"]:
                if it["type"] == "activity":
                    it["start"] = it["start"][:11] + "03:00"
                    it["end"] = it["end"][:11] + "04:00"
                    return
        codes, _ = self.check(m)
        self.assertTrue(codes)

    def test_narrative_grounding(self):
        good = f"Total ₹{self.st['plan']['costs']['grand_total']:,} within budget. " + "Enjoy the beaches and food. " * 6
        self.assertTrue(check_narrative(good, self.st["plan"], self.st["request"])["ok"])
        bad = "This trip costs only ₹1,234 which is a steal. " + "Enjoy the beaches and food. " * 6
        r = check_narrative(bad, self.st["plan"], self.st["request"])
        self.assertFalse(r["ok"])
        self.assertTrue(r["bad_amounts"])


class Disruptions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = plan()

    def run_event(self, ev):
        st = apply_disruption(self.base, ev)
        self.assertIn(st["status"], ("ok", "warnings"), ev)
        self.assertTrue(st["critic"]["passed"], ev)
        self.assertTrue(st["diff"], ev)
        return st

    def test_outbound_delay_repairs_only_day1(self):
        st = self.run_event({"type": "outbound_delay", "minutes": 180})
        self.assertEqual(st["diff"]["unchanged_days"], [2, 3, 4])

    def test_return_cancelled(self):
        st = self.run_event({"type": "return_cancelled"})
        self.assertNotEqual(st["plan"]["transport"]["return"]["id"], self.base["plan"]["transport"]["return"]["id"])

    def test_return_cancelled_twice(self):
        st = self.run_event({"type": "return_cancelled"})
        st2 = apply_disruption(st, {"type": "return_cancelled"})
        self.assertIn(st2["status"], ("ok", "warnings"))

    def test_rain_day_removes_outdoor(self):
        st = self.run_event({"type": "rain_day", "day": 2})
        self.assertEqual(st["diff"]["unchanged_days"], [1, 3, 4])

    def test_attraction_closed_excluded(self):
        st = self.run_event({"type": "attraction_closed", "attraction_id": "goa-aguada"})
        names = [it["place_id"] for d in st["plan"]["days"] for it in d["items"]]
        self.assertNotIn("goa-aguada", names)

    def test_original_state_not_mutated(self):
        before = copy.deepcopy(self.base["plan"])
        apply_disruption(self.base, {"type": "return_cancelled"})
        self.assertEqual(before, self.base["plan"])


class OllamaAdapter(unittest.TestCase):
    def _resp(self, content):
        m = mock.Mock()
        m.raise_for_status.return_value = None
        m.json.return_value = {"message": {"content": content}}
        return m

    def test_extract_filters_bad_values(self):
        raw = '{"destination":"goa","origin":"Atlantis","days":99,"travelers":3,"budget_inr":20000,"interests":["beach","skydiving"]}'
        with mock.patch("requests.post", return_value=self._resp(raw)):
            out = llm_mod.OllamaLLM("m").extract_request("x")
        self.assertEqual(out, {"destination": "Goa", "origin_free": "Atlantis", "travelers": 3, "budget": 20000,
                               "interests": ["beach"]})

    def test_workflow_with_ollama_that_invents_prices(self):
        def fake_post(url, json=None, **k):
            if json.get("format") == "json":
                return self._resp('{"destination":"Goa","days":4}')
            return self._resp("Great trip for only ₹999! " + "word " * 40)
        with mock.patch("requests.post", side_effect=fake_post):
            st = plan(llm="ollama")
        self.assertEqual(st["narrative_source"], "template")      # invented amount rejected
        self.assertTrue(any(e["action"] == "Critic rejected LLM summary" for e in st["trace"]))

    def test_workflow_survives_ollama_down(self):
        with mock.patch("requests.post", side_effect=ConnectionError("refused")):
            st = plan(llm="ollama")
        self.assertEqual(st["status"], "ok")
        self.assertTrue(any(e["action"] == "LLM unavailable" for e in st["trace"]))

    def test_reasoning_model_think_tags(self):
        raw = '<think>The user wants {a plan}. Let me think... {"x": 1}</think>\n{"destination":"Goa","days":4}'
        with mock.patch("requests.post", return_value=self._resp(raw)) as post:
            out = llm_mod.OllamaLLM("deepseek-r1:8b").extract_request("x")
        self.assertEqual(out["destination"], "Goa")
        self.assertIs(post.call_args.kwargs["json"]["think"], False)
        self.assertEqual(llm_mod._strip_thinking("reasoning...</think>Hello"), "Hello")

    def test_list_models_unreachable(self):
        with mock.patch("requests.get", side_effect=ConnectionError()):
            self.assertEqual(llm_mod.list_ollama_models(), [])


if __name__ == "__main__":
    unittest.main()
