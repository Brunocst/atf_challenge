"""Deterministic evaluation runner.

    .venv/bin/python eval/run_eval.py

No LLM judge. Every check is a string, count, or field assertion, so the result is
reproducible and a failure tells you exactly which expectation broke. An LLM-judged
groundedness pass is a *secondary* signal (see README) precisely because it is not
reproducible - the primary gate has to be deterministic to be usable in CI.

The clock is frozen so the golden set never drifts: case `order-01` asserts that an
order delivered 45 days ago falls outside a 30 day window, which would silently stop
being true if we read the wall clock.

## The free tier, and why this file knows about it

Each case costs a handful of model calls - one per tool step - and the free tier allows
only ~20 requests per day **per model**. A single 18-case pass therefore needs more
than one model's daily allowance. Two honest options:

1. Enable billing (Tier 1) and pin one model. Cheapest, and the results are a clean
   single-model measurement.
2. Pass `--models a,b,c` to spread cases across models. This is the only way to finish
   on the free tier. It is a **weaker measurement** and the report says so: each case
   still runs on exactly one model, but not every case on the same one.

Either way the runner caches per (case, model, clock), so a partial pass resumes and a
re-run costs nothing. It never sleeps through a daily cap - it marks that model spent
and moves the case to the next one.

Exits non-zero if anything fails or the run was incomplete, so it works as a gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_AS_OF = "2026-10-18"

# Words that mean "I am quoting this figure in order to correct it".
#
# Needed because a naive `must_not_contain: ["60-day"]` fails the *correct* answer:
# "the previous 60-day window was superseded" is exactly what the case wants, and the
# substring ban marks it wrong. The check below works at sentence level instead, so a
# figure is only a failure when the sentence states it without correcting it.
#
# This is a heuristic and it is labelled as one. It is still deterministic, and it
# encodes the intent the assertion was always trying to express.
DEFAULT_CORRECTION_CUES = (
    "supersed", "no longer", "no ", "not ", "n't", "never", "without", "previous",
    "previously", "formerly", "void", "outdated", "replac", "updated", "changed",
    "old ", "instead", "rather than", "used to", "incorrect", "wrong", "expired",
)

# An escalation is also a refusal, and the refusal has to reach the employee. These
# cues are the plain-language ways of saying "the documents do not answer this", so a
# bare packet receipt ("Escalation packet OPS-0001 created") fails the case instead of
# being scored as a safe hand-off.
REFUSAL_CUES = (
    "i don't know", "i do not know", "don't have", "do not have", "not cover",
    "not answer", "no answer", "unable to answer", "no policy", "not in the policy",
    "outside what the", "not something the", "outside novamart",
)

# The hand-off is asynchronous: the employee must be told the answer is not coming in
# this conversation. Scored separately from the refusal because "I don't know" plus no
# next step still leaves the associate standing at the counter waiting.
ASYNC_CUES = (
    "follow up", "follow-up", "follows up", "get back to", "be in touch", "notified",
    "notification", "asynchronous", "asynchronously", "contact you", "don't need to wait",
    "do not need to wait", "instead of waiting",
)


def load_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def cited_doc_ids(contract: dict | None) -> set[str]:
    if not contract:
        return set()
    return {str(s.get("doc_id")) for s in (contract.get("sources") or []) if s}


def check(case: dict, contract: dict | None, raw: str, trace: list[str],
          packets_before: int, packets_after: int) -> list[str]:
    """Return a list of failed expectations. Empty means pass."""
    expect = case.get("expect", {})
    failures: list[str] = []

    if contract is None:
        return ["no contract object in output"]

    answer = str(contract.get("answer") or "")
    script = str(contract.get("customer_script") or "")
    haystack = f"{answer}\n{script}".lower()
    cited = cited_doc_ids(contract)
    trace_blob = " ".join(trace)

    if "state" in expect and contract.get("state") != expect["state"]:
        failures.append(f"state={contract.get('state')!r} expected {expect['state']!r}")

    for doc_id in expect.get("must_cite", []):
        if doc_id not in cited:
            failures.append(f"missing citation {doc_id}")
    for doc_id in expect.get("must_not_cite", []):
        if doc_id in cited:
            failures.append(f"cited superseded/forbidden doc {doc_id}")
    if "must_cite_min" in expect and len(cited) < expect["must_cite_min"]:
        failures.append(f"cited {len(cited)} docs, expected >={expect['must_cite_min']}")

    for tool in expect.get("must_call", []):
        if tool not in trace_blob:
            failures.append(f"never called {tool}")
    for tool in expect.get("must_not_call", []):
        if tool in trace_blob:
            failures.append(f"should not have called {tool}")

    for needle in expect.get("must_contain", []):
        if needle.lower() not in haystack:
            failures.append(f"answer lacks {needle!r}")
    for needle in expect.get("must_not_contain", []):
        if needle.lower() in haystack:
            failures.append(f"answer contains forbidden {needle!r}")

    # Sentence-level assertion check: a superseded or false figure is only a failure
    # when stated as fact. See DEFAULT_CORRECTION_CUES.
    spec = expect.get("must_not_assert")
    if spec:
        specs = [spec] if isinstance(spec, dict) else list(spec)
        sentences = re.split(r"(?<=[.!?;])\s+", haystack)
        for entry in specs:
            patterns = [p.lower() for p in entry["patterns"]]
            cues = [c.lower() for c in
                    entry.get("correction_cues", DEFAULT_CORRECTION_CUES)]
            for sentence in sentences:
                if any(p in sentence for p in patterns) and not any(c in sentence
                                                                   for c in cues):
                    failures.append(
                        f"asserts {entry['patterns']} as fact without correcting it: "
                        f"{sentence.strip()[:80]!r}"
                    )
                    break
    for group in expect.get("must_contain_any", []):
        if not any(alt.lower() in haystack for alt in group):
            failures.append(f"answer lacks any of {group}")

    if expect.get("require_packet"):
        has_id = bool(contract.get("packet_id"))
        if not has_id and packets_after <= packets_before:
            failures.append("no escalation packet created")

    if expect.get("require_refusal"):
        if not any(cue in haystack for cue in REFUSAL_CUES):
            failures.append(
                "answer is not an explicit refusal - the employee is not told the "
                "documents do not answer it"
            )

    if expect.get("require_async"):
        if not any(cue in haystack for cue in ASYNC_CUES):
            failures.append(
                "answer does not say the follow-up is asynchronous and needs no wait"
            )

    return failures


def count_packets() -> int:
    from app import config

    if not config.OPS_LOG.exists():
        return 0
    return sum(1 for line in config.OPS_LOG.read_text().splitlines() if line.strip())


def prompt_fingerprint() -> str:
    """Short hash of everything that steers the model but is not a filename.

    The cache is keyed on model, clock, threshold *and prompt*, because editing any of
    these changes every answer without changing a single filename. It covers:

    - the instruction text
    - every tool docstring, since ADK passes those to the model as the function
      descriptions - a tool's prose is prompt, and it competes with the instruction
    - the output schema descriptions

    Miss any of them and a prompt fix keeps reporting the previous prompt's results as
    a pass. That is the same stale-cache trap as the threshold, one level higher up.
    """
    import hashlib

    from app.agent import root_agent
    from app.contract import Answer

    parts = [getattr(root_agent, "instruction", "") or ""]
    for tool in getattr(root_agent, "tools", []) or []:
        parts.append(getattr(tool, "__doc__", "") or "")
    parts.append(json.dumps(Answer.model_json_schema(), sort_keys=True))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:8]


def cache_key(case: dict, model: str, as_of: str, threshold: float,
              prompt: str) -> str:
    """Reuse a finished case across runs, but only under identical configuration."""
    return f"{case['id']}|{model}|{as_of}|{threshold}|{prompt}"


def load_cache(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_cache(path: Path, cache: dict) -> None:
    path.write_text(json.dumps(cache, indent=1))


def run_case(case: dict, sleep: float, model: str, attempts: int = 4) -> dict:
    """Run one case, waiting out a per-minute limit but never a per-day one."""
    from app.cli import ask
    from app.quota import is_daily_exhaustion, is_quota_error, suggested_wait

    last_error = None
    for attempt in range(attempts):
        started = time.monotonic()
        try:
            contract, raw, trace = asyncio.run(ask(case["question"], model))
            return {
                "contract": contract,
                "raw": raw,
                "trace": trace,
                "model": model,
                "seconds": round(time.monotonic() - started, 2),
                "error": None,
                "daily_exhausted": False,
            }
        except Exception as exc:  # noqa: BLE001 - surface the reason, keep going
            last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            if is_daily_exhaustion(exc):
                # Retrying is pointless and sleeping is worse.
                return {"contract": None, "raw": "", "trace": [], "model": model,
                        "seconds": None, "error": last_error,
                        "daily_exhausted": True}
            if attempt < attempts - 1:
                if is_quota_error(exc):
                    wait = suggested_wait(exc)
                    print(f"      per-minute limit, waiting {wait:.0f}s ...", flush=True)
                    time.sleep(wait)
                else:
                    time.sleep(sleep + 2)
    return {"contract": None, "raw": "", "trace": [], "model": model,
            "seconds": None, "error": last_error, "daily_exhausted": False}


def render_report(rows: list[dict], meta: dict) -> str:
    from collections import defaultdict

    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        buckets[row["bucket"]].append(row)

    steps = [len(r.get("trace") or []) for r in rows]
    avg_steps = round(sum(steps) / len(steps), 1) if steps else 0

    lines = [
        "# Evaluation results",
        "",
        f"- model(s): `{'`, `'.join(meta['models'])}`",
        f"- clock frozen at: `{meta['as_of']}`",
        f"- cases: {len(rows)} of {meta['total_cases']} across {len(buckets)} buckets",
        f"- mean tool steps per case (each step is one model call): {avg_steps}",
        f"- generated: {meta['generated']}",
    ]
    if meta.get("degraded"):
        lines += [
            "",
            "> **Cross-model run.** The free tier allows roughly 20 requests per day *per "
            "model*, so cases were spread across several models. Each case ran on exactly "
            "one model (recorded below), but this is not a single-model measurement. A "
            "paid-tier run pins one model and removes this caveat.",
        ]

    lines += ["", "## Summary by bucket", "", "| Bucket | Passed | Total |", "|---|---|---|"]
    for bucket in sorted(buckets):
        passed = sum(1 for r in buckets[bucket] if not r["failures"])
        lines.append(f"| {bucket} | {passed} | {len(buckets[bucket])} |")
    total_passed = sum(1 for r in rows if not r["failures"])
    lines += [
        f"| **total** | **{total_passed}** | **{len(rows)}** |",
        "",
        "## Cases",
        "",
        "| Case | Bucket | Model | State | Steps | Seconds | Result |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        state = (row["contract"] or {}).get("state", "-")
        result = "pass" if not row["failures"] else "FAIL"
        lines.append(
            f"| {row['id']} | {row['bucket']} | `{row.get('model', '-')}` | {state} | "
            f"{len(row.get('trace') or [])} | "
            f"{row['seconds'] if row['seconds'] is not None else '-'} | {result} |"
        )

    failures = [r for r in rows if r["failures"]]
    if failures:
        lines += ["", "## Failure detail", ""]
        for row in failures:
            lines.append(f"**{row['id']}** - {row['question']}")
            for failure in row["failures"]:
                lines.append(f"- {failure}")
            lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic golden set")
    parser.add_argument("--cases", default=str(ROOT / "eval" / "cases.jsonl"))
    parser.add_argument("--as-of", default=DEFAULT_AS_OF)
    parser.add_argument("--bucket", help="only run one bucket")
    parser.add_argument("--only", metavar="IDS",
                        help="comma-separated case ids to run, for iterating on "
                             "specific failures")
    parser.add_argument("--model", metavar="ALIAS|MODEL_ID",
                        help="single model preset or id (default: $NOVAMART_MODEL)")
    parser.add_argument("--models", metavar="A,B,C",
                        help="rotate cases across several models to stay inside the "
                             "free tier's per-model daily allowance (weaker "
                             "measurement; see module docstring)")
    parser.add_argument("--fallbacks", metavar="A,B,C",
                        help="models to fall back to, in order, and only once the "
                             "primary model has exhausted its daily allowance. Prefer "
                             "this to --models: it keeps every case on one model "
                             "whenever that is possible, so a run only becomes "
                             "cross-model when it has to.")
    parser.add_argument("--sleep", type=float, default=12.0,
                        help="pause between cases. Pace *requests*, not cases: a case "
                             "costs ~2.9 model calls, so 12s keeps a run under the "
                             "15 requests/minute Flash-Lite allows (full Flash allows "
                             "5). Lower values just buy 429 waits instead.")
    parser.add_argument("--out", default=None,
                        help="where to write the report. Defaults to eval/results.md, "
                             "or eval/results-<filter>.md for --only/--bucket runs, so "
                             "a targeted re-run cannot overwrite the scored report")
    parser.add_argument("--no-cache", action="store_true",
                        help="ignore cached results and re-run every case")
    args = parser.parse_args(argv)

    # Must be set before app.config is first imported: the model name is resolved
    # at import time.
    if args.model:
        os.environ["NOVAMART_MODEL"] = args.model

    # Freeze the clock and isolate escalation packets so a run never pollutes the
    # demo's ops_log.jsonl.
    os.environ["NOVAMART_AS_OF"] = args.as_of
    run_log = ROOT / "eval" / ".ops_log.run.jsonl"
    if run_log.exists():
        run_log.unlink()
    os.environ["NOVAMART_OPS_LOG"] = str(run_log)

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    from app import config

    cases = load_cases(Path(args.cases))
    if args.bucket:
        cases = [c for c in cases if c["bucket"] == args.bucket]
    if args.only:
        wanted = {i.strip() for i in args.only.split(",") if i.strip()}
        cases = [c for c in cases if c["id"] in wanted]
        if not cases:
            print(f"No cases matched --only {args.only}", file=sys.stderr)
            return 2

    if args.models:
        # Explicit rotation: throughput at the cost of comparability.
        pool = [config.resolve_model(m.strip()) for m in args.models.split(",")]
        rotate = True
    else:
        # Primary first, then fallbacks in order, with no rotation - so every case
        # lands on the primary for as long as it still has daily quota.
        primary = config.resolve_model(args.model) if args.model else config.MODEL
        pool = [primary] + [config.resolve_model(m.strip())
                            for m in (args.fallbacks or "").split(",") if m.strip()]
        rotate = False

    as_of = config.as_of().isoformat()
    threshold = config.MIN_SIMILARITY
    prompt = prompt_fingerprint()

    cache_path = ROOT / "eval" / ".run_cache.json"
    cache = {} if args.no_cache else load_cache(cache_path)

    print(f"models={pool} ({'rotate' if rotate else 'primary+fallbacks'})  "
          f"as_of={as_of}  threshold={threshold}  prompt={prompt}  "
          f"cases={len(cases)}\n")

    rows: list[dict] = []
    exhausted: set[str] = set()
    cursor = 0
    incomplete = False

    for index, case in enumerate(cases, start=1):
        result = None
        label = ""
        used_index = None

        # Pass 1: reuse a finished case from any model in the pool before spending a
        # fresh call. A cached case costs no quota, so it is always the better choice.
        for index_in_pool, model in enumerate(pool):
            cached = cache.get(cache_key(case, model, as_of, threshold, prompt))
            if cached and not cached.get("error"):
                result, label, used_index = cached, "cache", index_in_pool
                break

        # Pass 2: run it on the next model in rotation that still has daily quota.
        for offset in range(len(pool)):
            if result is not None:
                break
            index_in_pool = (cursor + offset) % len(pool)
            model = pool[index_in_pool]
            if model in exhausted:
                continue

            before = count_packets()
            attempt = run_case(case, args.sleep, model)
            if attempt.get("daily_exhausted"):
                exhausted.add(model)
                print(f"      `{model}` daily quota exhausted; trying the next model",
                      flush=True)
                continue

            attempt["packets_before"] = before
            attempt["packets_after"] = count_packets()
            cache[cache_key(case, model, as_of, threshold, prompt)] = attempt
            save_cache(cache_path, cache)
            result, label, used_index = attempt, "fresh", index_in_pool
            break

        if result is None:
            incomplete = True
            print(f"\nAll {len(pool)} models are out of daily quota. "
                  f"{len(rows)} of {len(cases)} cases completed and cached.")
            print("Re-run after midnight Pacific to continue (cached cases are free), "
                  "or enable billing for a single-model run.")
            break

        cursor = (used_index + 1) % len(pool) if rotate else 0
        failures = (
            [f"run error: {result['error']}"]
            if result["error"]
            else check(case, result["contract"], result["raw"], result["trace"],
                       result.get("packets_before", 0), result.get("packets_after", 0))
        )
        rows.append({**case, "failures": failures, **result})
        state = (result["contract"] or {}).get("state", "-") if result["contract"] else "ERR"
        mark = "pass" if not failures else "FAIL"
        print(f"[{index:>2}/{len(cases)}] {mark:4}  {case['id']:<16} {state:<9} "
              f"{result['seconds']}s {label} [{result.get('model', '-')}]"
              + (f"  {failures}" if failures else ""), flush=True)
        if label == "fresh" and index < len(cases):
            time.sleep(args.sleep)

    # Only warn about a cross-model run if one actually happened: with fallbacks in
    # place, a run that never needed them is still a clean single-model measurement.
    models_used = sorted({r.get("model", "?") for r in rows})
    meta = {
        "models": models_used or pool,
        "degraded": len(models_used) > 1,
        "as_of": as_of,
        "total_cases": len(cases),
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    # A targeted re-run is a diagnostic, not the measurement. Keep it away from the
    # scored report file unless the caller asked for that path explicitly.
    if args.out:
        out_path = Path(args.out)
    elif args.only or args.bucket:
        out_path = ROOT / "eval" / f"results-{args.bucket or 'targeted'}.md"
    else:
        out_path = ROOT / "eval" / "results.md"
    out_path.write_text(render_report(rows, meta), encoding="utf-8")

    passed = sum(1 for r in rows if not r["failures"])
    print(f"\n{passed}/{len(rows)} passed  ->  {out_path}")
    if incomplete:
        print("Run incomplete - not a valid result yet.")
        return 2
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
