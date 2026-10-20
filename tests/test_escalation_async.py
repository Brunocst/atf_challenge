"""Escalation is a refusal, and it is asynchronous.

Both properties are asserted here because both are easy to regress with a prompt edit:
the employee must be told that the documents do not answer the question, and must be
told the answer is queued rather than arriving in this conversation.
"""

import json

from app import config
from app.tools.escalate import draft_ops_escalation
from eval.run_eval import check


def test_packet_is_queued_with_an_sla(tmp_path, monkeypatch):
    log = tmp_path / "ops.jsonl"
    monkeypatch.setattr(config, "OPS_LOG", log)

    result = draft_ops_escalation(
        question="Can we ship to Canada?",
        reason="No international shipping policy exists.",
        decision_needed="Whether to offer international shipping at all.",
    )

    assert result["status"] == "queued"
    assert result["packet_id"].startswith("OPS-")
    assert result["sla"]

    packet = json.loads(log.read_text().splitlines()[0])
    assert packet["status"] == "queued"
    assert packet["sla"] == result["sla"]
    assert packet["decision_needed"] == result["decision_needed"]


def _case(**extra):
    expect = {"state": "escalate", "require_packet": True}
    expect.update(extra)
    return {"id": "probe", "bucket": "abstain", "question": "q", "expect": expect}


def _contract(answer, packet_id="OPS-0001"):
    return {
        "state": "escalate",
        "answer": answer,
        "customer_script": None,
        "sources": [],
        "confidence": 0.2,
        "needs_human": True,
        "packet_id": packet_id,
    }


def test_bare_packet_receipt_is_not_a_refusal():
    failures = check(
        _case(require_refusal=True, require_async=True),
        _contract("Escalation packet OPS-0001 created."),
        "",
        [],
        1,
        2,
    )
    assert any("not an explicit refusal" in f for f in failures)
    assert any("asynchronous" in f for f in failures)


def test_refusal_with_async_follow_up_passes():
    failures = check(
        _case(require_refusal=True, require_async=True),
        _contract(
            "I don't know: our policy documents do not cover international shipping. "
            "I have queued OPS-0001 and operations will follow up asynchronously; you "
            "do not need to wait."
        ),
        "",
        [],
        1,
        2,
    )
    assert failures == []
