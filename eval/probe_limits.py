"""Find out which models this key can actually use, and how much.

    .venv/bin/python eval/probe_limits.py                    # cheap: 1 call per model
    .venv/bin/python eval/probe_limits.py --budget 25        # burst until the cap shows
    .venv/bin/python eval/probe_limits.py --models flash-lite,lite-3.1

Google no longer publishes the free-tier numbers in the docs - they live behind the
authenticated AI Studio rate-limit page - and the limits enforced for a given key do not
always match what the docs imply. So this asks the API instead.

The free tier enforces a **per-model** daily cap of roughly 20 requests, which is the
problem: one 18-case evaluation needs ~60 model calls, i.e. more than one model's whole
day. Probing tells you how many models you can spread a run across.

`--budget 1` is the useful default: it reveals which models have quota left today
without burning anything. A larger `--budget` sends requests until the server refuses,
which reveals the actual cap at the cost of that model's remaining allowance - only do
that on a model you are not about to run an evaluation on.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_CANDIDATES = ("flash-lite", "lite-3.1", "flash-3.5", "flash-3.7", "flash")


def probe(client, model: str, budget: int) -> dict:
    ok = 0
    details = None
    error = None
    started = time.monotonic()
    for _ in range(budget):
        try:
            client.models.generate_content(model=model, contents="Reply with: ok")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            from app.quota import is_quota_error, quota_details

            details = quota_details(exc) if is_quota_error(exc) else {}
            # A 402 "your prepayment credits are depleted" is not a rate limit at all,
            # and calling it an unexplained refusal is how a quota diagnosis goes wrong
            # for an hour. If no limit or retry hint could be extracted, surface the
            # message itself instead of inventing a verdict.
            if not (details.get("limit") or details.get("retry_after")):
                error = f"{type(exc).__name__}: {str(exc)[:200]}"
            break
    return {
        "model": model,
        "ok": ok,
        "limit": (details or {}).get("limit"),
        "daily": (details or {}).get("daily", False),
        "retry_after": (details or {}).get("retry_after"),
        "error": error,
        "seconds": round(time.monotonic() - started, 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", help="comma-separated presets or model ids")
    parser.add_argument("--budget", type=int, default=1,
                        help="generate calls to attempt per model (default 1)")
    parser.add_argument("--list-reachable", action="store_true",
                        help="also list every model the key can reach (no quota used)")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from google import genai

    from app import config

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("No GEMINI_API_KEY set.", file=sys.stderr)
        return 2
    client = genai.Client(api_key=api_key)

    if args.list_reachable:
        reachable = [
            m.name.removeprefix("models/")
            for m in client.models.list()
            if not getattr(m, "supported_actions", None)
            or "generateContent" in (m.supported_actions or [])
        ]
        print(f"Reachable with this key ({len(reachable)}), no quota used:\n")
        for name in reachable:
            print("  ", name)
        print()

    names = ([m.strip() for m in args.models.split(",") if m.strip()]
             if args.models else list(DEFAULT_CANDIDATES))
    models = [config.resolve_model(n) for n in names]

    print(f"probing {len(models)} models with a budget of {args.budget} request(s) each\n")
    rows = []
    for model in models:
        row = probe(client, model, args.budget)
        rows.append(row)
        if row["error"]:
            verdict = row["error"]
        elif row["limit"] and row["daily"]:
            verdict = (f"daily cap hit at {row['limit']} "
                       f"(retry in {row['retry_after'] / 3600:.1f}h)")
        elif row["limit"]:
            verdict = f"per-minute cap hit at {row['limit']} (retry {row['retry_after']:.0f}s)"
        elif row["ok"] >= args.budget:
            verdict = f"available ({row['ok']}/{args.budget} sent)"
        else:
            verdict = "unclear"
        print(f"  {row['model']:<28} {verdict}", flush=True)
        time.sleep(0.5)

    available = [r for r in rows if not r["error"] and not r["daily"] and r["ok"]]
    print("\n| model | sent | cap seen | scope | retry after |")
    print("|---|---|---|---|---|")
    for r in rows:
        scope = "daily" if r["daily"] else ("minute" if r["limit"] else "-")
        cap = r["limit"] or "-"
        retry = f"{r['retry_after']:.0f}s" if r["retry_after"] else "-"
        print(f"| `{r['model']}` | {r['ok']} | {cap} | {scope} | {retry} |")

    print(f"\n{len(available)} model(s) look usable right now: "
          f"{', '.join(r['model'] for r in available) or 'none'}")
    if len(available) < 3:
        print("A full 18-case evaluation needs ~60 model calls. With a per-model daily "
              "cap of ~20,\nthat means at least 3 models - so either wait for the "
              "midnight-Pacific reset, spread\nacross more models, or enable billing "
              "(Tier 1) and pin one model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
