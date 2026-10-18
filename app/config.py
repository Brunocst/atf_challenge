"""Shared configuration. No secrets here - everything comes from the environment."""

import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
ORDERS_PATH = ROOT / "data" / "orders.json"
_ops_env = os.getenv("NOVAMART_OPS_LOG")
OPS_LOG = Path(_ops_env) if _ops_env else ROOT / "ops_log.jsonl"
EMBEDDINGS_CACHE = ROOT / ".embeddings_cache.json"

# Model names are resolved at build time against the live API rather than trusted
# from memory: `gemini-2.5-flash`, the obvious choice, returns 404 "no longer
# available to new users" as of Oct 2026, and `text-embedding-004` is gone in favour
# of `gemini-embedding-001`. Keep this list verified - `python -m app.cli --list-models`
# prints what the configured key can actually reach.
MODEL_PRESETS: dict[str, str] = {
    "flash": "gemini-flash-latest",           # default: best quality/latency balance
    "flash-lite": "gemini-flash-lite-latest",  # cheapest; separate quota pool
    "flash-3.7": "gemini-3.7-flash",
    "flash-3.6": "gemini-3.6-flash",
    "flash-3.5": "gemini-3.5-flash",
    "lite-3.1": "gemini-3.1-flash-lite",
    "pro": "gemini-pro-latest",
}


def resolve_model(name: str | None = None) -> str:
    """Map a preset alias to a concrete model id. Unknown names pass through, so an
    explicit model string always works without editing this file."""
    raw = name or os.getenv("NOVAMART_MODEL") or "flash"
    return MODEL_PRESETS.get(raw, raw)


MODEL = resolve_model()
EMBED_MODEL = os.getenv("NOVAMART_EMBED_MODEL", "gemini-embedding-001")

# Models worth rotating across for throughput when the results do not need to be
# comparable. Never rotate inside a single evaluation run - see README.
ROTATION_POOL = [
    MODEL_PRESETS["flash-lite"],
    MODEL_PRESETS["lite-3.1"],
    MODEL_PRESETS["flash-3.5"],
    MODEL_PRESETS["flash"],
]

# Retrieval knobs. MIN_SIMILARITY is the abstention threshold: below it we skip
# generation entirely and escalate, so it is the first line of defence against a
# confident answer to a question the corpus does not cover.
#
# Calibrated with `python eval/calibrate_threshold.py`, which scores every golden case
# with embeddings only. Measured on this corpus: 12 answerable cases land at 0.726-0.805
# and 4 out-of-corpus cases at 0.644-0.721. The two bands nearly touch, so:
#
#   0.55 (the original guess) rejected *nothing* - every out-of-corpus case passed.
#   0.72 separated them best (3 of 4 rejected) but sits 0.006 under the weakest real
#        question, i.e. it is fitted to this golden set and would start escalating real
#        questions the moment an employee phrases one differently.
#   0.70 keeps a 0.026 margin under the weakest real question and still rejects half.
#
# The threshold is deliberately biased towards recall for that reason. The remaining
# out-of-corpus cases are topically adjacent rather than distant, and no threshold
# separates those - the model has to judge whether a retrieved section actually
# contains the rule. Those cases stay in the golden set because they test exactly that.
TOP_K = int(os.getenv("NOVAMART_TOP_K", "4"))
MIN_SIMILARITY = float(os.getenv("NOVAMART_MIN_SIMILARITY", "0.70"))


def as_of() -> date:
    """The clock the agent reasons about.

    Frozen via NOVAMART_AS_OF so evaluation results are reproducible: the golden
    set asserts 'delivered 45 days ago vs a 30 day window', which would silently
    drift if it read the wall clock. The demo leaves it unset and gets today.
    """
    override = os.getenv("NOVAMART_AS_OF")
    if override:
        return date.fromisoformat(override)
    return date.today()
