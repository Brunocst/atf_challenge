"""Escalation to ops - the product's second output, not a failure state.

The packet is the point. A vague ticket ("customer asked about a return, please
advise") costs ops the same thinking the associate could not do. The packet carries
the question, the policies that were considered, the order snapshot, and the single
decision ops still has to make, so ops can decide without re-doing the lookup.

Packets also accumulate into a doc-gap report (see eval/gap_report.py): the questions
that could not be answered are the documentation backlog.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from app import config


def _next_packet_id() -> str:
    if not config.OPS_LOG.exists():
        return "OPS-0001"
    last = 0
    for line in config.OPS_LOG.read_text(encoding="utf-8").splitlines():
        match = re.search(r'"packet_id":\s*"OPS-(\d+)"', line)
        if match:
            last = max(last, int(match.group(1)))
    return f"OPS-{last + 1:04d}"


def draft_ops_escalation(
    question: str,
    reason: str,
    decision_needed: str,
    policies_considered: str = "",
    order_id: str = "",
) -> dict:
    """Hand a question to the operations team when policy does not answer it.

    Call this only when the retrieved policy documents do NOT decide the case: nothing
    relevant was found, the retrieved sections do not contain the rule that decides it,
    the rule is genuinely ambiguous for these facts, the question is outside NovaMart's
    scope, or the request needs something the policy explicitly leaves to a human (an
    exception, a goodwill credit, a price override).

    Do NOT call this because the customer will be unhappy with the answer. If the
    retrieved policy decides the case - including deciding against the customer - that
    is a resolve. A refused request that policy already settles is not a decision ops
    can improve on, and escalating it is what makes this system cost ops time instead
    of saving it.

    Args:
        question: The employee's question, in their own words.
        reason: Why the documents do not decide this, in one sentence.
        decision_needed: The single decision the ops team has to make. Be specific -
            this is the point of the escalation.
        policies_considered: Comma-separated doc_ids that were retrieved and read,
            so ops does not repeat the search. Fill this in whenever anything was
            retrieved, even if it did not settle the case.
        order_id: The order id, if one was looked up. Empty string otherwise.

    Returns:
        A dict with the 'packet_id' to quote back to the employee, and the packet.
    """
    packet = {
        "packet_id": _next_packet_id(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "question": question,
        "reason": reason,
        "decision_needed": decision_needed,
        "policies_considered": [
            p.strip() for p in policies_considered.split(",") if p.strip()
        ],
        "order_id": order_id or None,
        "raised_by": "novamart-copilot",
    }
    with config.OPS_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(packet) + "\n")

    return {
        "status": "escalated",
        "packet_id": packet["packet_id"],
        "decision_needed": packet["decision_needed"],
        "message": (
            f"Escalation raised as {packet['packet_id']}. Tell the employee this "
            f"reference and what happens next. Do not add a policy answer."
        ),
    }
