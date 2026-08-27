"""Unit tests for scoped CPT Q&A (context building + answer, no RAG)."""

import pytest

from app.core import config
from app.workspace.interpretation import qa

pytestmark = pytest.mark.unit


def _payload():
    return {
        "calculator_id": "cpt_interpretation",
        "source_file": "sample_sounding.CPT",
        "reference": "Robertson SBTn reference",
        "metadata": {
            "groundwater_level": 1.5,
            "area_ratio": 0.80,
            "area_ratio_source": "MA",
            "max_depth": 12.0,
        },
        "layers": [
            {
                "layer": 1,
                "depth_from": 0.0,
                "depth_to": 2.9,
                "thickness": 2.9,
                "sbt_zone": 4,
                "soil_type": "Silt mixtures",
                "qc_mean": 0.45,
                "ic_mean": 2.85,
                "notes": [],
            },
            {
                "layer": 3,
                "depth_from": 7.9,
                "depth_to": 12.0,
                "thickness": 4.1,
                "sbt_zone": 3,
                "soil_type": "Clays: clay to silty clay",
                "qc_mean": 0.9,
                "ic_mean": 3.1,
                "notes": ["very low cone resistance (mean qc~0.90 MPa) - soft/weak soil"],
            },
        ],
        "flagged_concerns": ["7.90-12.00 m: very low cone resistance - soft/weak soil"],
    }


class _FakeClient:
    """Stand-in Ollama client that records the prompt and returns a canned answer."""

    def __init__(self):
        self.captured = None

    async def chat(self, **kwargs):
        self.captured = kwargs
        return {"message": {"content": "Layer 3 (clay) has low qc - a concern. AI draft."}}


def test_context_includes_layers_flags_and_metadata():
    ctx = qa.build_qa_context(_payload())
    assert "Clays: clay to silty clay" in ctx
    assert "mean qc 0.90 MPa" in ctx
    assert "FLAGGED FOR REVIEW:" in ctx
    assert "soft/weak soil" in ctx
    assert "Groundwater level: 1.50 m" in ctx
    assert "cone area ratio a = 0.80" in ctx
    assert "maximum depth: 12.00 m" in ctx
    assert "Robertson SBTn reference" in ctx


def test_context_no_flags_says_none():
    payload = _payload()
    payload["flagged_concerns"] = []
    assert "FLAGGED FOR REVIEW: none." in qa.build_qa_context(payload)


@pytest.mark.asyncio
async def test_answer_question_uses_scoped_system_prompt(monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_ENABLED", True)
    client = _FakeClient()
    out = await qa.answer_question("is layer 3 a concern?", _payload(), client=client)

    assert out["is_ai_draft"] is True
    assert out["answer"].startswith("Layer 3 (clay)")
    # The exact scoping instruction is sent, and the question + context ride along.
    msgs = client.captured["messages"]
    assert msgs[0]["role"] == "system"
    assert "strictly about THIS CPT sounding result" in msgs[0]["content"]
    assert "is layer 3 a concern?" in msgs[1]["content"]
    assert "Clays: clay to silty clay" in msgs[1]["content"]
    assert client.captured["think"] is True


@pytest.mark.asyncio
async def test_answer_question_gated_by_flag(monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_ENABLED", False)
    with pytest.raises(qa.WorkspaceDisabledError):
        await qa.answer_question("q", _payload(), client=_FakeClient())
