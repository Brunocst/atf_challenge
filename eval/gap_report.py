"""Doc-gap report: turn escalations into a documentation backlog.

    .venv/bin/python eval/gap_report.py

The second payoff of the escalation feature. The business problem is not "answer
questions", it is reduce load on ops. A question that no policy answers is a policy
that does not exist yet, and every associate will hit it again next week. Grouping
escalations by topic gives ops a ranked list of documents to write, which removes the
cause instead of absorbing the symptom.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "of", "to", "is", "it", "we", "our", "us",
    "what", "how", "do", "does", "can", "could", "should", "customer", "customers",
    "nova", "novamart", "order", "want", "wants", "asking", "about", "with", "on",
    "in", "at", "if", "be", "are", "s", "t",
}


def keywords(text: str) -> list[str]:
    words = re.findall(r"[a-z]+", (text or "").lower())
    return [w for w in words if len(w) > 3 and w not in STOPWORDS]


def main() -> int:
    from app import config

    if not config.OPS_LOG.exists():
        print(f"No escalations logged yet ({config.OPS_LOG} does not exist).")
        print('Escalate something first: python -m app.cli "What is our shipping policy for Canada?"')
        return 0

    packets = [
        json.loads(line)
        for line in config.OPS_LOG.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not packets:
        print("No escalations logged yet.")
        return 0

    counts: Counter[str] = Counter()
    topics: dict[str, list[dict]] = defaultdict(list)
    for packet in packets:
        words = keywords(packet.get("question", ""))
        topic = " ".join(words[:2]) if words else "uncategorised"
        counts[topic] += 1
        topics[topic].append(packet)

    print(f"DOC-GAP REPORT  -  {len(packets)} escalated questions\n")
    print(f"{'count':>5}  topic")
    print(f"{'-' * 5}  {'-' * 40}")
    for topic, count in counts.most_common():
        print(f"{count:>5}  {topic}")

    print("\nOPEN DECISIONS FOR OPS\n")
    for topic, _ in counts.most_common():
        for packet in topics[topic]:
            print(f"  {packet['packet_id']}  {packet.get('decision_needed', '')}")
            print(f"          asked: {packet.get('question', '')}")
        print()

    repeated = [(t, c) for t, c in counts.most_common() if c > 1]
    if repeated:
        print("REPEATED TOPICS -> candidate policy documents to write:")
        for topic, count in repeated:
            print(f"  - {topic} ({count} escalations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
