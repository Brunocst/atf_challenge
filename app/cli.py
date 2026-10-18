"""One-command demo.

    python -m app.cli "Can I return order NM-1042?"
    python -m app.cli --json "What is the return window for a standard item?"

Prints the resolve/escalate outcome, the citations, and the tool trace, so the demo
shows the control loop rather than just the final text.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load before importing config, so environment overrides (model, threshold, frozen
# clock) take effect. Explicit path because find_dotenv() cannot walk the stack when
# a caller is a heredoc or an embedded interpreter.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from app import config  # noqa: E402

APP_NAME = "novamart_copilot"
USER_ID = "associate"
JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json(text: str) -> dict | None:
    """Pull the contract object out of the model's final turn.

    Used when structured output comes back wrapped in prose or a fenced block, so a
    formatting wobble degrades to "we could not parse" instead of a crash.
    """
    if not text:
        return None
    for candidate in (text, *(m.group(1) for m in JSON_BLOCK.finditer(text))):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict) and "state" in parsed:
                return parsed
        except json.JSONDecodeError:
            start, end = candidate.find("{"), candidate.rfind("}")
            if start != -1 and end > start:
                try:
                    parsed = json.loads(candidate[start : end + 1])
                    if isinstance(parsed, dict) and "state" in parsed:
                        return parsed
                except json.JSONDecodeError:
                    pass
    return None


async def ask(question: str, model: str | None = None) -> tuple[dict | None, str, list[str]]:
    """Run one turn. Returns (contract, raw_text, tool_trace)."""
    from app.agent import build_agent

    runner = InMemoryRunner(agent=build_agent(model), app_name=APP_NAME)
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID
    )
    message = types.Content(role="user", parts=[types.Part(text=question)])

    trace: list[str] = []
    final_text = ""
    async for event in runner.run_async(
        user_id=USER_ID, session_id=session.id, new_message=message
    ):
        for call in event.get_function_calls() or []:
            trace.append(f"{call.name}({json.dumps(call.args or {}, default=str)})")
        if event.is_final_response() and event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    final_text += part.text
    return extract_json(final_text), final_text, trace


def render(contract: dict | None, raw: str, trace: list[str], as_json: bool) -> str:
    if as_json:
        return json.dumps(
            {"contract": contract, "tool_trace": trace, "raw": raw},
            indent=2,
            default=str,
        )

    if not trace:
        trace_line = "  (no tools called)"
    else:
        trace_line = "\n".join(f"  {i + 1}. {t}" for i, t in enumerate(trace))

    if contract is None:
        return (
            f"TOOL TRACE\n{trace_line}\n\n"
            f"OUTCOME  could not parse the contract object from the model output\n"
            f"RAW      {raw.strip()[:600]}"
        )

    state = contract.get("state", "?")
    header = "RESOLVED" if state == "resolve" else "ESCALATED  (needs human)"
    lines = [f"TOOL TRACE\n{trace_line}", "", f"OUTCOME  {header}", ""]

    if state == "escalate":
        lines.append(f"PACKET   {contract.get('packet_id') or '(none quoted)'}")
        lines.append("")
        lines.append(contract.get("answer", "").strip())
    else:
        lines.append(contract.get("answer", "").strip())
        script = (contract.get("customer_script") or "").strip()
        if script:
            lines += ["", f'SAY TO CUSTOMER  "{script}"']
        sources = contract.get("sources") or []
        if sources:
            rendered = ", ".join(
                f"{s.get('doc_id')} ({s.get('effective_date')})" for s in sources
            )
            lines += ["", f"CITATIONS  {rendered}"]

    lines += ["", f"CONFIDENCE  {contract.get('confidence')}",
              f"NEEDS HUMAN {contract.get('needs_human')}"]
    return "\n".join(lines)


def list_models() -> int:
    """Print the configured presets, then what this key can actually reach."""
    print("Presets (use with --model or NOVAMART_MODEL):\n")
    for alias, model_id in config.MODEL_PRESETS.items():
        marker = " (default)" if model_id == config.MODEL else ""
        print(f"  {alias:<12} -> {model_id}{marker}")

    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        print("\n(no API key set, skipping the live model list)")
        return 0
    from google import genai

    try:
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
        reachable = [
            m.name.removeprefix("models/")
            for m in client.models.list()
            if not getattr(m, "supported_actions", None)
            or "generateContent" in (m.supported_actions or [])
        ]
    except Exception as exc:  # noqa: BLE001
        print(f"\nCould not list models: {type(exc).__name__}: {str(exc)[:160]}")
        return 1
    print(f"\nReachable with this key ({len(reachable)}):\n")
    for name in reachable:
        print("  ", name)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NovaMart store-floor copilot")
    parser.add_argument("question", nargs="*", help="the employee's question")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--as-of",
        metavar="YYYY-MM-DD",
        help="freeze the clock (reproducible demos and evaluation runs)",
    )
    parser.add_argument(
        "--model",
        metavar="ALIAS|MODEL_ID",
        help="model preset or explicit id, e.g. flash-lite or gemini-3.5-flash "
        "(default: $NOVAMART_MODEL or 'flash')",
    )
    parser.add_argument(
        "--list-models", action="store_true", help="show presets and reachable models"
    )
    args = parser.parse_args(argv)

    if args.list_models:
        return list_models()

    question = " ".join(args.question).strip()
    if not question:
        parser.error(
            'no question given, e.g. python -m app.cli "Can I return NM-1042?" '
            "(or use --list-models)"
        )
    if args.as_of:
        os.environ["NOVAMART_AS_OF"] = args.as_of

    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        print(
            "GEMINI_API_KEY is not set.\n"
            "  cp .env.example .env   then add a key from "
            "https://aistudio.google.com/apikey",
            file=sys.stderr,
        )
        return 2

    try:
        contract, raw, trace = asyncio.run(ask(question, args.model))
    except Exception as exc:  # noqa: BLE001
        from app.quota import is_quota_error, suggested_wait

        if is_quota_error(exc):
            print(
                f"Hit the Gemini API rate limit; retry in about "
                f"{suggested_wait(exc):.0f}s.\n"
                "The free tier allows only a few model calls per minute per model, and "
                "a single turn makes one call per tool step.",
                file=sys.stderr,
            )
            return 3
        raise

    print(f"Q  {question}  [as of {config.as_of().isoformat()}]\n")
    print(render(contract, raw, trace, args.json))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
