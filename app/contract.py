"""The resolve/escalate contract.

Enforced as a schema rather than as prompt prose, so "it refused" is an assertable
fact about a run instead of a sentence we hoped the model would produce.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Source(BaseModel):
    """A policy document the answer was grounded in."""

    doc_id: str
    effective_date: Optional[str] = None


class Answer(BaseModel):
    state: Literal["resolve", "escalate"] = Field(
        description="resolve = answered from policy; escalate = handed to ops."
    )
    answer: str = Field(
        description="The response to the employee. Policy claims must be in the "
        "retrieved documents. Never invent a rule. Never compute a date yourself."
    )
    customer_script: Optional[str] = Field(
        default=None,
        description="For a resolve only: one or two sentences the associate can say "
        "to the customer standing in front of them.",
    )
    sources: list[Source] = Field(
        default_factory=list,
        description="Every doc_id quoted. Empty when escalating.",
    )
    confidence: float = Field(
        description="0.0-1.0. Below 0.5 means state must be escalate."
    )
    needs_human: bool = Field(description="True whenever state is escalate.")
    packet_id: Optional[str] = Field(
        default=None,
        description="The OPS-#### id returned by draft_ops_escalation. Required "
        "when state is escalate.",
    )
