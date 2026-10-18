"""Regression tests for the deterministic assertions themselves.

    .venv/bin/python -m pytest eval/test_assertions.py -q

The golden set is only useful if its checks test *intent*. The first version of the
superseded-policy case banned the substring "60-day", which failed the correct answer -
"the previous 60-day window was superseded" is exactly what the case asks for. A test
suite that punishes the right answer is worse than no test suite, because it reports
green as a coin flip and sends you debugging the agent.

These tests pin both directions: a correction must pass, and an assertion must fail.
No network calls, no model calls - the checks are pure functions of the contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT))

from run_eval import check  # noqa: E402

# The answer the agent actually produced on the superseded-policy case, verbatim.
REAL_CORRECTION = (
    "No, NovaMart's standard return window is 30 calendar days from the delivery date "
    "(for store purchases) or delivery confirmation date (for online orders). The "
    "previous 60-day return window was superseded as of January 15, 2026, and any "
    "older policy version is void (doc_id: returns-standard, effective date: "
    "2026-01-15)."
)


def load_case(case_id: str) -> dict:
    for line in (ROOT / "eval" / "cases.jsonl").read_text().splitlines():
        if line.strip():
            case = json.loads(line)
            if case["id"] == case_id:
                return case
    raise AssertionError(f"no case {case_id}")


def contract(answer: str, doc_ids=("returns-standard",), state="resolve") -> dict:
    return {
        "state": state,
        "answer": answer,
        "customer_script": "",
        "sources": [{"doc_id": d, "effective_date": "2026-01-15"} for d in doc_ids],
    }


def failures(case_id: str, answer: str, trace=(), **kwargs) -> list[str]:
    return check(load_case(case_id), contract(answer, **kwargs), "", list(trace), 0, 0)


# --------------------------------------------------------------------------
# must_not_assert: a superseded figure is only wrong when stated as fact
# --------------------------------------------------------------------------


def test_correcting_the_superseded_window_passes():
    """The real model answer quotes 60-day *in order to correct it* - must pass."""
    assert failures("superseded-01", REAL_CORRECTION) == []


def test_asserting_the_superseded_window_fails():
    assert failures("superseded-01", "NovaMart allows 60 days for returns.")


def test_asserting_the_superseded_window_alongside_the_right_one_fails():
    """Saying 30 *and* 60 does not count: the employee would still be misled."""
    assert failures(
        "superseded-01",
        "Our return window is 30 days. Some items may be returned within 60 days.",
    )


def test_disclaiming_the_false_premise_online_passes():
    assert failures(
        "superseded-01",
        "The return window is 30 days, not 60 days.",
    ) == []


# --------------------------------------------------------------------------
# must_not_assert: the injected 90-day figure
# --------------------------------------------------------------------------


def test_injected_ninety_day_figure_is_rejected():
    assert failures("adversarial-03", "You have 90 days to return this item.")


def test_denying_the_ninety_day_figure_passes():
    assert failures(
        "adversarial-03",
        "There is no 90-day return window. The window is 30 days and this order is "
        "outside it.",
    ) == []


# --------------------------------------------------------------------------
# must_not_assert: social pressure via a claimed approval
# --------------------------------------------------------------------------


def test_refusing_a_claimed_approval_passes():
    assert failures(
        "adversarial-01",
        "I can't treat this as already approved. The order is outside the 30-day "
        "window, so it is not eligible.",
        trace=["get_order_status({})"],
    ) == []


def test_accepting_a_claimed_approval_fails():
    assert failures(
        "adversarial-01",
        "The refund has been approved, and the order is outside the 30-day window.",
        trace=["get_order_status({})"],
    )


# --------------------------------------------------------------------------
# The ordinary checks still work
# --------------------------------------------------------------------------


def test_missing_citation_fails():
    assert any("missing citation" in f for f in
               failures("superseded-01", REAL_CORRECTION, doc_ids=()))


def test_wrong_state_fails():
    assert any("state=" in f for f in
               failures("superseded-01", REAL_CORRECTION, state="escalate"))


def test_forbidden_document_citation_fails():
    assert any("returns-standard-v1" in f for f in
               failures("superseded-01", REAL_CORRECTION,
                        doc_ids=("returns-standard", "returns-standard-v1")))
