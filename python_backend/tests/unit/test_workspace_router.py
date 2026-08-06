"""Unit tests for the LLM intent router (fast-path + defensive JSON parsing)."""

import json

import pytest

from app.core import config
from app.workspace import router
from app.workspace.calculators.cpt import CPT_CALCULATOR

pytestmark = pytest.mark.unit


class _FakeClient:
    """Records whether it was called and returns a scripted content string."""

    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    async def chat(self, **kwargs):
        self.calls += 1
        return {"message": {"content": self.content}}


# --- Defensive parsing -----------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"calculator_id": "cpt_interpretation"}', "cpt_interpretation"),
        ('```json\n{"calculator_id": "cpt_interpretation"}\n```', "cpt_interpretation"),
        ('Sure! {"calculator_id": "cpt_interpretation"} done', "cpt_interpretation"),
        ('{"calculator_id": null}', None),
        ('{"calculator_id": "terzaghi"}', None),  # unknown id -> None
        ("not json at all", None),
        ('{"foo": "bar"}', None),
        ("", None),
    ],
)
def test_parse_calculator_id(raw, expected):
    assert router._parse_calculator_id(raw) == expected


def test_catalog_lists_registered_ids():
    cat = router._catalog()
    assert "id: cpt_interpretation" in cat
    assert CPT_CALCULATOR.description[:20] in cat


# --- route_message ---------------------------------------------------------
@pytest.mark.asyncio
async def test_fast_path_skips_llm(monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_ENABLED", True)
    client = _FakeClient('{"calculator_id": null}')  # would say null if called
    calc = await router.route_message("run cpt", client=client)
    assert calc is CPT_CALCULATOR
    assert client.calls == 0  # exact phrase -> no LLM call


@pytest.mark.asyncio
async def test_llm_routes_natural_phrasing(monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_ENABLED", True)
    client = _FakeClient(json.dumps({"calculator_id": "cpt_interpretation"}))
    calc = await router.route_message("run the cpt test please", client=client)
    assert calc is CPT_CALCULATOR
    assert client.calls == 1  # fast-path missed -> LLM consulted


@pytest.mark.asyncio
async def test_llm_null_returns_none(monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_ENABLED", True)
    client = _FakeClient(json.dumps({"calculator_id": None}))
    assert await router.route_message("is layer 3 a concern?", client=client) is None


@pytest.mark.asyncio
async def test_unreachable_model_treated_as_null(monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_ENABLED", True)

    class _BoomClient:
        async def chat(self, **kwargs):
            raise ConnectionError("ollama down")

    assert await router.route_message("run the cpt test", client=_BoomClient()) is None
