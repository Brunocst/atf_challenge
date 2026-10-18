"""Rate-limit handling.

The Gemini free tier enforces two limits that look identical in a 429 body but mean
completely different things:

- **per minute** (RPM) - wait about a minute and continue. Roughly 60 seconds.
- **per day** (RPD) - wait until midnight Pacific. Five figures of seconds.

Waiting on the second is how a five-minute job turns into an overnight one, so the
retry hint is used to tell them apart and the caller is told to stop rather than sleep.

Limits are applied per *project* and per *model*, which is the fact that makes model
rotation work as a throughput strategy: two models are two separate daily allowances.
"""

from __future__ import annotations

import random
import re

# "Please retry in 54.084260005s." and "'retryDelay': '54s'" both appear in 429 bodies.
RETRY_HINT = re.compile(r"retry in ([\d.]+)s|'retryDelay':\s*'(\d+(?:\.\d+)?)s'")
LIMIT_RE = re.compile(r"limit:\s*(\d+),\s*model:\s*([\w.\-]+)")
MODEL_RE = re.compile(r"model:\\s*'([\w.\-]+)'|model:\\s*([\w.\-]+)")
QUOTA_MARKERS = ("RESOURCE_EXHAUSTED", "429", "quota", "rate limit")
DAILY_MARKERS = ("perday", "per day", "requests per day")

# Anything longer than this is a daily cap, not a per-minute one. A minute-limit hint
# is ~60s; the daily hint observed in practice is ~20,000s.
DAILY_THRESHOLD_SECONDS = 3600.0


def _hint_seconds(exc: BaseException) -> float | None:
    match = RETRY_HINT.search(str(exc))
    if not match:
        return None
    value = match.group(1) or match.group(2)
    return float(value) if value else None


def is_quota_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}"
    return any(marker.lower() in text.lower() for marker in QUOTA_MARKERS)


def is_daily_exhaustion(exc: BaseException) -> bool:
    """True when the failure is a per-day cap, which cannot be waited out in a run."""
    text = str(exc).lower()
    if any(marker in text for marker in DAILY_MARKERS):
        return True
    hint = _hint_seconds(exc)
    return hint is not None and hint > DAILY_THRESHOLD_SECONDS


def suggested_wait(exc: BaseException, default: float = 70.0) -> float:
    """Seconds to wait for a *per-minute* limit, from the server's own hint.

    Never called for a daily cap - `is_daily_exhaustion` is checked first by callers.
    Jitter keeps parallel callers from retrying in lockstep.
    """
    hint = _hint_seconds(exc)
    if hint is not None and hint <= DAILY_THRESHOLD_SECONDS:
        return hint + random.uniform(2.0, 6.0)
    return default + random.uniform(2.0, 8.0)


def quota_details(exc: BaseException) -> dict:
    """Pull the useful numbers out of a 429 for reporting."""
    text = str(exc)
    limit_match = LIMIT_RE.search(text)
    model_match = MODEL_RE.search(text)
    return {
        "limit": int(limit_match.group(1)) if limit_match else None,
        "model": (limit_match.group(2) if limit_match
                  else next((g for g in (model_match.groups() if model_match else ())
                             if g), None)),
        "retry_after": _hint_seconds(exc),
        "daily": is_daily_exhaustion(exc),
    }
