"""Model layer: a deterministic mock (rule-based) and a local Ollama client.

The language model is deliberately used for only two things:
  1. filling gaps when parsing the free-text request (validated afterwards), and
  2. writing the friendly trip summary (validated by the Critic's grounding check).
All planning, costing and validation logic is plain Python, so a small model is enough.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional

import requests

from .config import DEFAULT_OLLAMA_MODEL, INTERESTS, LLM_TIMEOUT_S, OLLAMA_HOST
from .tools.catalog import destinations, origins


class LLMError(RuntimeError):
    pass


class BaseLLM:
    name = "base"
    is_real = False

    def extract_request(self, text: str) -> Optional[dict]:
        return None

    def write_narrative(self, facts: dict) -> Optional[str]:
        return None

    def chat_json(self, system: str, user: str, timeout: Optional[int] = None) -> dict:
        raise LLMError("no language model selected")


class MockLLM(BaseLLM):
    """No model at all: the rule-based parser and the template summary do the work."""
    name = "mock (rule-based)"


def _strip_thinking(raw: str) -> str:
    """Reasoning models (deepseek-r1, qwen3...) may prefix their answer with <think>...</think>."""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    return re.sub(r"^.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE).strip()   # unbalanced closing tag


def _extract_json(raw: str) -> dict:
    raw = _strip_thinking(raw)
    try:
        return json.loads(raw)
    except ValueError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise LLMError("model did not return JSON")
        return json.loads(m.group(0))


class OllamaLLM(BaseLLM):
    is_real = True

    def __init__(self, model: str, host: str = OLLAMA_HOST):
        self.model = model
        self.host = host.rstrip("/")
        self.name = f"ollama:{model}"

    def _chat(self, system: str, user: str, json_mode: bool = False, timeout: Optional[int] = None) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": 0.1},
            "think": False,          # ask reasoning models to skip their chain of thought (ignored by other models)
        }
        if json_mode:
            payload["format"] = "json"
        try:
            r = requests.post(f"{self.host}/api/chat", json=payload, timeout=timeout or LLM_TIMEOUT_S)
            r.raise_for_status()
            return _strip_thinking(r.json()["message"]["content"])
        except Exception as e:
            raise LLMError(f"{type(e).__name__}: {e}") from e

    def chat_json(self, system: str, user: str, timeout: Optional[int] = None) -> dict:
        """One JSON-mode call; raises LLMError on transport problems or non-JSON replies."""
        try:
            return _extract_json(self._chat(system, user, json_mode=True, timeout=timeout))
        except ValueError as e:
            raise LLMError(f"invalid JSON from model: {e}") from e

    def extract_request(self, text: str) -> Optional[dict]:
        system = (
            "You extract structured trip requests. Reply with ONE JSON object and nothing else. "
            "Keys: origin (string or null), destination (string or null), days (integer or null), "
            "travelers (integer or null), budget_inr (integer total budget in rupees or null), "
            f"interests (list chosen only from {INTERESTS}), transport_pref (cheapest|balanced|fastest|null). "
            f"destination and origin are place names in India exactly as the user wrote them (known ones: {destinations() + origins()}); "
            "Use null when not stated."
        )
        raw = self._chat(system, text, json_mode=True)
        data = _extract_json(raw)
        out: dict = {}
        for key, known, free in (("destination", destinations(), "destination_free"), ("origin", origins(), "origin_free")):
            v = data.get(key)
            if isinstance(v, str) and 2 <= len(v.strip()) <= 40 and re.fullmatch(r"[A-Za-z .'-]+", v.strip()):
                name = v.strip().title()
                if name in known:
                    out[key] = name
                else:
                    out[free] = name          # a place outside the catalog: the generator may resolve it
        for key, field, lo, hi in (("days", "days", 2, 7), ("travelers", "travelers", 1, 8),
                                   ("budget_inr", "budget", 500, 1_000_000)):
            v = data.get(key)
            if isinstance(v, (int, float)) and lo <= int(v) <= hi:
                out[field] = int(v)
        ints = data.get("interests")
        if isinstance(ints, list):
            out["interests"] = [i for i in ints if i in INTERESTS]
        tp = data.get("transport_pref")
        if tp in ("cheapest", "balanced", "fastest"):
            out["transport_pref"] = tp
        return out

    def write_narrative(self, facts: dict) -> Optional[str]:
        system = (
            "You are a friendly travel assistant. Write a summary of at most 110 words for the trip "
            "described in FACTS. Use ONLY information from FACTS. Never invent places, times or prices. "
            "Every rupee amount you write must appear in FACTS exactly. Plain text, no markdown."
        )
        return self._chat(system, "FACTS:\n" + json.dumps(facts, ensure_ascii=False)).strip()


def list_ollama_models(host: str = OLLAMA_HOST) -> List[str]:
    """Names of locally installed Ollama models, or [] if the server is not reachable."""
    try:
        r = requests.get(host.rstrip("/") + "/api/tags", timeout=2.5)
        r.raise_for_status()
        return sorted(m["name"] for m in r.json().get("models", []))
    except Exception:
        return []


def get_llm(settings: dict) -> BaseLLM:
    if settings.get("llm") == "ollama":
        return OllamaLLM(settings.get("ollama_model") or DEFAULT_OLLAMA_MODEL,
                         settings.get("ollama_host") or OLLAMA_HOST)
    return MockLLM()
