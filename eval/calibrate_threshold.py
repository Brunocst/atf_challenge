"""Calibrate the abstention threshold against the golden set.

    .venv/bin/python eval/calibrate_threshold.py

`MIN_SIMILARITY` is the retrieval gate: below it, `search_policies` returns `no_match`
and the agent is instructed to escalate instead of answering. Setting it by feel is the
easy mistake. Two failure modes pull in opposite directions:

- too low  -> an unrelated policy section passes, so the agent answers a question the
              corpus does not cover. A confident wrong answer, which is the worst
              outcome the design admits.
- too high -> real questions fail to retrieve and escalate needlessly, which wastes the
              operations team's time and is the metric the whole system is judged on.

The interesting result is usually that no threshold separates the two groups perfectly.
That is worth knowing rather than hiding: it means the threshold is a first filter and
the model still has to judge whether a retrieved section actually answers the question.

Uses embeddings only - no generate calls, so it costs none of the scarce quota.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", default=str(ROOT / "eval" / "cases.jsonl"))
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    from app import config
    from app.retrieval import load_chunks, retrieve

    cases = [json.loads(line) for line in Path(args.cases).read_text().splitlines()
             if line.strip()]
    chunks = load_chunks()
    current = tuple(c for c in chunks if c.status != "superseded")

    print(f"embed model: {config.EMBED_MODEL}")
    print(f"corpus: {len(chunks)} chunks, {len(current)} current "
          f"({len(chunks) - len(current)} superseded, filtered at retrieval)")
    print(f"threshold now: {config.MIN_SIMILARITY}\n")

    rows = []
    for case in cases:
        hits = retrieve(case["question"], k=config.TOP_K)
        top_score, top_doc = (hits[0][1], hits[0][0].doc_id) if hits else (0.0, "-")
        rows.append({
            "id": case["id"],
            "bucket": case["bucket"],
            "expects": case.get("expect", {}).get("state", "?"),
            "top1": top_score,
            "top1_doc": top_doc,
            "hits": [(c.doc_id, s) for c, s in hits],
            "question": case["question"],
        })

    print("| case | bucket | expects | top-1 sim | top-1 doc | above threshold |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        above = ", ".join(f"{d}({s:.2f})" for d, s in r["hits"]
                          if s >= config.MIN_SIMILARITY) or "none"
        flag = ""
        if r["expects"] == "escalate" and r["top1"] >= config.MIN_SIMILARITY:
            flag = " <= would pass the gate"
        print(f"| {r['id']} | {r['bucket']} | {r['expects']} | {r['top1']:.3f} | "
              f"`{r['top1_doc']}` | {above}{flag} |")

    answerable = [r for r in rows if r["expects"] == "resolve"]
    abstain = [r for r in rows if r["expects"] == "escalate"]

    print(f"\nanswerable cases: {len(answerable)}   abstain cases: {len(abstain)}")
    if answerable:
        print(f"  answerable top-1 similarity: min {min(r['top1'] for r in answerable):.3f} "
              f"max {max(r['top1'] for r in answerable):.3f}")
    if abstain:
        print(f"  abstain    top-1 similarity: min {min(r['top1'] for r in abstain):.3f} "
              f"max {max(r['top1'] for r in abstain):.3f}")

    # Sweep candidate thresholds: maximise correct gate decisions.
    print("\n| threshold | answerable admitted | abstain rejected | total errors |")
    print("|---|---|---|---|")
    best = None
    for step in range(30, 81):
        threshold = step / 100
        admitted = sum(1 for r in answerable if r["top1"] >= threshold)
        rejected = sum(1 for r in abstain if r["top1"] < threshold)
        errors = (len(answerable) - admitted) + (len(abstain) - rejected)
        if best is None or errors < best[1]:
            best = (threshold, errors, admitted, rejected)
        if step % 5 == 0:
            print(f"| {threshold:.2f} | {admitted}/{len(answerable)} | "
                  f"{rejected}/{len(abstain)} | {errors} |")

    if best:
        threshold, errors, admitted, rejected = best
        print(f"\nlowest error at threshold {threshold:.2f}: "
              f"{admitted}/{len(answerable)} answerable admitted, "
              f"{rejected}/{len(abstain)} abstain rejected, {errors} errors")

    # The cases a threshold alone cannot fix are the interesting ones.
    leaks = [r for r in abstain if r["top1"] >= config.MIN_SIMILARITY]
    if leaks:
        print(f"\nAt {config.MIN_SIMILARITY}, {len(leaks)} abstain case(s) pass the "
              f"retrieval gate anyway:")
        for r in leaks:
            print(f"  {r['id']:<16} top-1 {r['top1']:.3f} on `{r['top1_doc']}` - "
                  f"{r['question'][:64]}")
        print("\n  These are not fixable by raising the threshold without also rejecting\n"
              "  real questions. They are the cases where the retrieved section is\n"
              "  topically close but does not contain the rule, so the model has to judge\n"
              "  coverage. The golden set keeps them for exactly that reason.")
    else:
        print(f"\nAt {config.MIN_SIMILARITY}, no abstain case passes the retrieval gate.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
