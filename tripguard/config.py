"""Central configuration for TripGuard."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = ROOT / "cache"
GENERATED_DIR = CACHE_DIR / "generated"      # destination profiles written by the Ollama generator
EXPORT_DIR = ROOT / "exports"

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
if not OLLAMA_HOST.startswith("http"):          # Ollama itself accepts "host:port" without a scheme
    OLLAMA_HOST = "http://" + OLLAMA_HOST.replace("0.0.0.0", "localhost")
DEFAULT_OLLAMA_MODEL = os.getenv("TRIPGUARD_MODEL", "qwen3:4b")

HTTP_TIMEOUT_S = 6          # live adapters (weather / routing)
GEN_TIMEOUT_S = 600                           # profile generation can be slow on CPU-only laptops
LLM_TIMEOUT_S = 180         # local model calls can be slow on CPU

RESERVE_PCT = 0.05          # emergency reserve added on top of the subtotal
RAIN_BLOCK_PROB = 60        # >= this rain probability blocks outdoor activities
RAIN_WARN_PROB = 40
MAX_ACTIVITIES_PER_DAY = 4
MAX_ACTIVE_HOURS_PER_DAY = 10
MIN_USABLE_HOURS = 6        # a transport pair must leave at least this much time at the destination
LOCAL_TRANSFER_MIN = 30     # station <-> stay buffer
ARRIVAL_SETTLE_MIN = 60     # time to reach the stay and freshen up after arriving

LIVE_MAX_AGE_MIN = 30       # live data older than this is flagged by the Critic

INTERESTS = ["beach", "food", "nightlife", "heritage", "adventure",
             "nature", "spiritual", "shopping", "culture", "relax"]

DEFAULT_SETTINGS = {
    "llm": "mock",                    # "mock" | "ollama"
    "ollama_model": DEFAULT_OLLAMA_MODEL,
    "ollama_host": OLLAMA_HOST,
    "live": False,                    # False = cached/bundled data only (offline-safe)
    "simulate_down": False,           # force live adapters to fail to show the fallback path
    "max_retries": 4,
    "engine": "auto",                 # "auto" | "langgraph" | "builtin"
    "today": None,                    # ISO date override for deterministic tests
}
