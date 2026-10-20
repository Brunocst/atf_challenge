"""Preset aliases must be resolved before they reach ADK.

`--model flash-lite` used to be handed to the agent unresolved, which fails with:

    ValueError: Model flash-lite not found.

ADK's registry only knows provider model names (`gemini-3.5-flash-lite`), so our
presets have to be mapped first. The README documents `--model flash-lite` and
`--model flash-3.7`, and the demo uses them, so this is worth a test rather than a
comment.
"""

from google.adk.models.registry import LLMRegistry

from app import config
from app.agent import build_agent

ALIASES = ("flash", "flash-lite", "flash-3.7", "flash-3.5", "pro")


def test_every_preset_maps_to_a_concrete_model_id():
    for alias, model_id in config.MODEL_PRESETS.items():
        assert config.resolve_model(alias) == model_id
        # An alias that equals its own target is not an alias.
        assert alias != model_id or alias in ("flash", "pro")


def test_build_agent_hands_adk_a_name_it_can_actually_resolve():
    for alias in ALIASES:
        assert alias in config.MODEL_PRESETS, f"{alias} is not a known preset"
        agent = build_agent(alias)
        # The exact call that blew up before the fix.
        LLMRegistry.resolve(agent.model)
        assert agent.model == config.MODEL_PRESETS[alias]


def test_unknown_names_pass_through_and_none_uses_the_configured_default():
    # An explicit model id must always work without editing config.py.
    raw = "gemini-9.9-does-not-exist"
    assert config.resolve_model(raw) == raw
    assert build_agent().model == config.MODEL
