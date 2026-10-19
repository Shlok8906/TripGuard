"""Executes app.py against the strict Streamlit stub (real Streamlit is not required)."""
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import fake_streamlit  # noqa: E402
import test_generator as tg  # noqa: E402
from tripguard.tools import catalog  # noqa: E402


def run_app(stub):
    """One script run, like Streamlit: st.stop() ends it quietly, st.rerun() runs it again."""
    try:
        runpy.run_path(os.path.join(ROOT, "app.py"), run_name="__main__")
    except fake_streamlit.AppStopped:
        pass
    except fake_streamlit.RerunRequested:
        run_app(stub)


def no_ollama():
    """Ollama not running: every HTTP call fails (the app must still work for built-in places)."""
    return mock.patch.multiple("requests", post=mock.Mock(side_effect=ConnectionError("down")),
                               get=mock.Mock(side_effect=ConnectionError("down")))


class UISmoke(unittest.TestCase):
    def setUp(self):
        self.st = fake_streamlit.install()
        self.tmp = tempfile.TemporaryDirectory()
        self._old = catalog.GENERATED_DIR
        catalog.GENERATED_DIR = Path(self.tmp.name)
        catalog.reset_generated()

    def tearDown(self):
        catalog.GENERATED_DIR = self._old
        catalog.reset_generated()
        self.tmp.cleanup()

    def names(self):
        return {c[0] for c in self.st.calls}

    def test_first_load(self):
        with no_ollama():
            run_app(self.st)
        self.assertIsNone(self.st.session_state.get("state"))
        self.assertTrue(any(c[0] == "warning" and "Ollama is not running" in str(c[1]) for c in self.st.calls))

    def test_default_model_is_qwen3_4b(self):
        with no_ollama():
            run_app(self.st)
        self.assertEqual(self.st.session_state["ollama_model"], "qwen3:4b")
        self.st.clicks = {"Plan my trip"}
        with mock.patch("requests.post", side_effect=tg.fake_post()) as post, \
                mock.patch("requests.get", return_value=tg.resp({"models": [{"name": "qwen3:4b"}]})):
            run_app(self.st)
        models = {c.kwargs["json"]["model"] for c in post.call_args_list}
        self.assertEqual(models, {"qwen3:4b"})
        self.assertTrue(any(c[0] == "success" and "Model ready: qwen3:4b" in str(c[1]) for c in self.st.calls))

    def test_model_missing_message(self):
        with mock.patch("requests.get", return_value=tg.resp({"models": [{"name": "llama3.2:3b"}]})):
            run_app(self.st)
        self.assertTrue(any(c[0] == "warning" and "ollama pull qwen3:4b" in str(c[1]) for c in self.st.calls))

    def test_plan_and_export_without_ollama(self):
        self.st.clicks = {"Plan my trip"}
        with no_ollama():
            run_app(self.st)
        state = self.st.session_state["state"]
        self.assertIn(state["status"], ("ok", "warnings"), state["message"])
        self.assertTrue({"metric", "map"} <= self.names())
        self.st.clicks = set()
        self.st.session_state["approved"] = True
        self.st.calls.clear()
        with no_ollama():
            run_app(self.st)
        self.assertEqual(sum(1 for c in self.st.calls if c[0] == "download_button"), 3)

    def test_no_duplicate_inputs_or_disruption_panel(self):
        self.st.clicks = {"Plan my trip"}
        with no_ollama():
            run_app(self.st)
        text = " ".join(str(c[1]) for c in self.st.calls)
        for gone in ("disruption", "Used when the text", "Budget (₹, total)", "Origin city"):
            self.assertNotIn(gone.lower(), text.lower())
        self.assertFalse(hasattr(self.st, "_asked_budget"))

    def test_sample_buttons_and_tight_budget(self):
        self.st.clicks = {"Manali, tight budget"}
        with no_ollama():
            run_app(self.st)
        self.assertIn("Manali", self.st.session_state["prompt"])
        self.st.clicks = {"Plan my trip"}
        with no_ollama():
            run_app(self.st)
        self.assertEqual(self.st.session_state["state"]["status"], "infeasible")
        self.assertTrue(any(c[0] == "error" for c in self.st.calls))

    def test_new_place_message_when_ollama_down(self):
        self.st.session_state["prompt"] = tg.HAMPI
        self.st.clicks = {"Plan my trip"}
        with no_ollama():
            run_app(self.st)
        self.assertEqual(self.st.session_state["state"]["status"], "needs_input")
        self.assertTrue(any(c[0] == "warning" and "ollama serve" in str(c[1]) for c in self.st.calls))

    def test_any_destination_through_ui_then_delete(self):
        self.st.session_state["prompt"] = tg.HAMPI
        self.st.clicks = {"Plan my trip"}
        with mock.patch("requests.post", side_effect=tg.fake_post()), \
                mock.patch("requests.get", return_value=tg.resp({"models": [{"name": "qwen3:4b"}]})):
            run_app(self.st)
        state = self.st.session_state["state"]
        self.assertIn(state["status"], ("ok", "warnings"), state["message"])
        self.assertEqual(state["request"]["destination"], "Hampi")
        self.assertTrue(any(c[0] == "warning" and "estimates" in str(c[1]) for c in self.st.calls))
        self.st.clicks = {"Delete saved places"}
        with no_ollama():
            run_app(self.st)
        self.assertEqual(catalog.generated_destinations(), [])


if __name__ == "__main__":
    unittest.main()
