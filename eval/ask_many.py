"""Ask many questions fast by rotating across models.

    .venv/bin/python eval/ask_many.py "question one" "question two" ...
    .venv/bin/python eval/ask_many.py --file questions.txt
    .venv/bin/python eval/ask_many.py --models flash-lite,lite-3.1 "q one" "q two"

**Why rotation works:** the free tier's allowance is per model. Four models give
roughly four times the throughput of one, and because the pool spans separate model
versions they are separate quotas.

**When not to use it:** results from different models are not comparable, so this is
for demos, exploration, and load-testing the agent - never for scoring a single
evaluation run. `eval/run_eval.py` deliberately pins one model for that reason.

Concurrency is capped per model at roughly the observed per-minute allowance; anything
beyond it just produces 429s and waits, which is slower than pacing. When a 429 does
arrive, the wait length comes from the server's own retry hint rather than a guess.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PER_MODEL_CONCURRENCY = 4  # roughly the free-tier allowance per minute


async def run_one(question: str, model: str, sem: asyncio.Semaphore) -> dict:
    from app.cli import ask
    from app.quota import is_quota_error, suggested_wait

    async with sem:
        started = time.monotonic()
        for attempt in range(4):
            try:
                contract, raw, trace = await ask(question, model)
                return {
                    "question": question,
                    "model": model,
                    "state": (contract or {}).get("state", "unparsed"),
                    "contract": contract,
                    "trace": trace,
                    "seconds": round(time.monotonic() - started, 2),
                    "error": None,
                }
            except Exception as exc:  # noqa: BLE001
                if is_quota_error(exc) and attempt < 3:
                    await asyncio.sleep(suggested_wait(exc))
                    continue
                return {
                    "question": question,
                    "model": model,
                    "state": "error",
                    "contract": None,
                    "trace": [],
                    "seconds": round(time.monotonic() - started, 2),
                    "error": f"{type(exc).__name__}: {str(exc)[:160]}",
                }
    return {"question": question, "model": model, "state": "error", "contract": None,
            "trace": [], "seconds": None, "error": "exhausted retries"}


async def run_all(questions: list[str], models: list[str], as_of: str | None) -> list[dict]:
    if as_of:
        os.environ["NOVAMART_AS_OF"] = as_of
    sem = asyncio.Semaphore(PER_MODEL_CONCURRENCY)
    tasks = [
        run_one(question, models[index % len(models)], sem)
        for index, question in enumerate(questions)
    ]
    return list(await asyncio.gather(*tasks))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("questions", nargs="*", help="questions to ask")
    parser.add_argument("--file", help="file with one question per line")
    parser.add_argument("--models", help="comma-separated presets or ids "
                                         "(default: config.ROTATION_POOL)")
    parser.add_argument("--as-of", metavar="YYYY-MM-DD", help="freeze the clock")
    parser.add_argument("--json", action="store_true", help="full JSON output")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from app import config

    questions = list(args.questions)
    if args.file:
        questions += [
            line.strip()
            for line in Path(args.file).read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
    if not questions:
        parser.error("no questions given (pass them as arguments or use --file)")

    models = (
        [config.resolve_model(m.strip()) for m in args.models.split(",")]
        if args.models
        else list(config.ROTATION_POOL)
    )

    print(f"{len(questions)} questions across {len(models)} models "
          f"-> round-robin, ~{len(models) * 5}/min theoretical ceiling\n")
    started = time.monotonic()
    rows = asyncio.run(run_all(questions, models, args.as_of))
    wall = round(time.monotonic() - started, 1)

    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0

    print(f"{'model':<28} {'state':<10} {'s':>5}  question")
    print(f"{'-' * 28} {'-' * 10} {'-' * 5}  {'-' * 40}")
    for row in rows:
        print(f"{row['model']:<28} {row['state']:<10} "
              f"{str(row['seconds']):>5}  {row['question'][:58]}")
        if row["error"]:
            print(f"{'':<28} {'':<10} {'':>5}  !! {row['error']}")

    ok = sum(1 for r in rows if not r["error"])
    print(f"\n{ok}/{len(rows)} answered in {wall}s wall clock "
          f"({round(len(rows) / max(wall, 0.1), 2)} answers/s)")

    failures = [r for r in rows if r["error"]]
    if failures:
        print("\nRetry the failed ones as a smaller batch; their model's minute budget\n"
              "was shared with the rest of the burst.")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
