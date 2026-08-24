"""Tests for the AI Interpretation module.

No live model is used: the Ollama client is replaced with a fake that records
the messages it receives and returns a canned response. Covers prompt grounding
content, response parsing, the WORKSPACE_ENABLED gate, and the grounding
guardrail.
"""

import pytest

from app.core import config
from app.workspace.calculators.cpt_interpretation import interpret_cpt
from app.workspace.data import SAMPLE_CPT_PATH
from app.workspace.interpretation import ai_interpret
from app.workspace.interpretation.ai_interpret import (
    InterpretationResult,
    WorkspaceDisabledError,
    build_interpretation_prompt,
    interpret_sounding,
)
from app.workspace.parsers.cpt import parse_cpt


@pytest.fixture(scope="module")
def results():
    return interpret_cpt(parse_cpt(SAMPLE_CPT_PATH))


class FakeOllamaClient:
    """Stand-in for ollama.AsyncClient that records the call and replies."""

    def __init__(self, reply: str):
        self._reply = reply
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"message": {"content": self._reply}}


@pytest.fixture(autouse=True)
def _enable_workspace(monkeypatch):
    # Default the feature ON for these tests; individual tests can flip it off.
    monkeypatch.setattr(config, "WORKSPACE_ENABLED", True)


# --- Feature flag -----------------------------------------------------------

async def test_disabled_flag_blocks_interpretation(results, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_ENABLED", False)
    with pytest.raises(WorkspaceDisabledError):
        await interpret_sounding(results, client=FakeOllamaClient("x"))


# --- Prompt grounding content ----------------------------------------------

def test_prompt_contains_layer_summary(results):
    system_prompt, user_prompt = build_interpretation_prompt(results)
    # The compact layer summary — and only it — is in the user prompt.
    assert "LAYERS (top to bottom):" in user_prompt
    assert "Sand: clean sand to silty sand" in user_prompt
    assert "Clays: clay to silty clay" in user_prompt
    assert "Groundwater level (GWL): 1.50 m" in user_prompt


def test_prompt_contains_grounding_instructions(results):
    system_prompt, _ = build_interpretation_prompt(results)
    lowered = system_prompt.lower()
    assert "use only the layer data" in lowered
    assert "do not perform any arithmetic" in lowered
    assert "top-to-bottom" in lowered
    assert "ai draft" in lowered


async def test_prompt_sent_to_model_contains_summary_and_grounding(results):
    fake = FakeOllamaClient(
        "The profile comprises silt over sand over clay, an AI draft for review."
    )
    await interpret_sounding(results, client=fake)
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["think"] is True  # prose path: think=True keeps qwen3 reasoning out of content
    sent = "\n".join(m["content"] for m in call["messages"])
    # Layer summary present in what the model actually received...
    assert "LAYERS (top to bottom):" in sent
    assert "Sand: clean sand to silty sand" in sent
    # ...alongside the grounding instructions.
    assert "Use ONLY the layer data" in sent
    assert "Do NOT perform any arithmetic" in sent


# --- Response parsing -------------------------------------------------------

async def test_response_parsed_into_interpretation_result(results):
    reply = (
        "The sounding shows a soft near-surface silt, over a competent sand, "
        "over a firm clay. This is an AI draft for engineering review."
    )
    result = await interpret_sounding(results, client=FakeOllamaClient(reply))

    assert isinstance(result, InterpretationResult)
    assert result.is_ai_draft is True
    assert result.model == config.OLLAMA_MODEL
    assert result.narrative == reply
    # per_layer_notes is deterministic scaffolding, one entry per computed layer.
    assert len(result.per_layer_notes) == 3
    assert result.per_layer_notes[0]["depth_from"] == pytest.approx(0.0)
    assert "dominant_sbt" in result.per_layer_notes[0]


async def test_think_tags_are_stripped(results):
    reply = "<think>scratch reasoning</think>Silt over sand over clay."
    result = await interpret_sounding(results, client=FakeOllamaClient(reply))
    assert "<think>" not in result.narrative
    assert result.narrative == "Silt over sand over clay."


# --- Grounding guardrail ----------------------------------------------------

async def test_guardrail_flags_absent_soil_type(results):
    # 'gravel' is NOT among the computed layers (silt/sand/clay) -> must flag.
    reply = "A dense gravel layer overlies the sand. AI draft for review."
    result = await interpret_sounding(results, client=FakeOllamaClient(reply))
    assert any("gravel" in c.lower() for c in result.flagged_concerns)


async def test_guardrail_passes_consistent_narrative(results):
    reply = (
        "Silt near surface passes into sand and then into clay at depth. "
        "AI draft for review."
    )
    result = await interpret_sounding(results, client=FakeOllamaClient(reply))
    # No grounding-violation concern for clay/sand/silt (only present types).
    assert not any("not among the computed soil types" in c
                   for c in result.flagged_concerns)


def test_ground_check_directly(results):
    from app.workspace.interpretation.layer_summary import summarize

    strat = summarize(results)
    assert ground_check_has_flag("There is peat here.", strat, "peat")
    assert not ground_check_has_flag("Clay and sand and silt.", strat, "clay")


def ground_check_has_flag(narrative, strat, kw):
    flags = ai_interpret.ground_check(narrative, strat)
    return any(kw in f.lower() for f in flags)
