"""Unit tests for the Chat-tab LLM intent router.

Deterministic, no live Ollama: a scripted fake client stands in for the model,
so these tests exercise the router's PLUMBING and its hard guarantees
(defensive parsing, the no-attachments invariant, the safe fallback, and that
the attachment flag + history actually reach the prompt) -- not the model's
classification quality, which is covered by the opt-in live golden set at the
bottom (skipped unless RUN_LIVE_ROUTER=1).
"""

import json
import os

import pytest

from app.services import intent_router as ir
from app.services.intent_router import (
    DEFAULT_MODE,
    GENERAL,
    KB_QUERY,
    MIXED,
    THREAD_DOC,
    VALID_MODES,
)

pytestmark = pytest.mark.unit


class _FakeClient:
    """Records call kwargs and returns a scripted content string."""

    def __init__(self, content: str):
        self.content = content
        self.calls = 0
        self.last_kwargs = None

    async def chat(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return {"message": {"content": self.content}}


def _mode_json(mode) -> str:
    return json.dumps({"mode": mode})


# --- Defensive parsing ------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"mode": "KB_QUERY"}', KB_QUERY),
        ('{"mode": "GENERAL"}', GENERAL),
        ('{"mode": "MIXED"}', MIXED),
        ('{"mode": "THREAD_DOC"}', THREAD_DOC),
        # code-fenced
        ('```json\n{"mode": "GENERAL"}\n```', GENERAL),
        # trailing/leading prose around the object
        ('Sure! {"mode": "KB_QUERY"} done', KB_QUERY),
        # <think> block stripped
        ('<think>hmm this is general</think>{"mode": "GENERAL"}', GENERAL),
        # case-insensitive value
        ('{"mode": "general"}', GENERAL),
        ('{"mode": "Kb_Query"}', KB_QUERY),
        # unknown / malformed -> None
        ('{"mode": "SMALLTALK"}', None),
        ('{"mode": null}', None),
        ('{"mode": 3}', None),
        ('{"foo": "bar"}', None),
        ("not json at all", None),
        ("", None),
        ("[1, 2, 3]", None),  # valid json, not a dict
    ],
)
def test_parse_mode(raw, expected):
    assert ir._parse_mode(raw) == expected


def test_default_mode_is_kb_query():
    # The safe fallback must reproduce today's always-retrieve behavior.
    assert DEFAULT_MODE == KB_QUERY
    assert DEFAULT_MODE in VALID_MODES


# --- classify() plumbing: each mode round-trips -----------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrasing,scripted_mode",
    [
        # KB_QUERY phrasings
        ("What does the Bolton 1986 paper say about dilatancy?", KB_QUERY),
        ("Summarize the findings in our scour effects report.", KB_QUERY),
        # GENERAL phrasings
        ("Explain what critical state soil mechanics is.", GENERAL),
        ("Can you help me draft an email to a client about a delay?", GENERAL),
        ("What's the difference between drained and undrained shear strength?", GENERAL),
        # MIXED phrasings
        ("Compare what our report says about EICP with standard practice.", MIXED),
    ],
)
async def test_classify_round_trips_each_mode(phrasing, scripted_mode):
    client = _FakeClient(_mode_json(scripted_mode))
    mode = await ir.classify(phrasing, client=client)
    assert mode == scripted_mode
    assert client.calls == 1


# --- No-attachments invariant ----------------------------------------------
@pytest.mark.asyncio
async def test_thread_doc_downgraded_when_no_attachments():
    # Model insists on THREAD_DOC, but the thread has no uploads -> KB_QUERY.
    client = _FakeClient(_mode_json(THREAD_DOC))
    mode = await ir.classify(
        "What does the document I uploaded say?",
        thread_has_attachments=False,
        client=client,
    )
    assert mode == KB_QUERY


@pytest.mark.asyncio
async def test_thread_doc_preserved_when_attachments_present():
    client = _FakeClient(_mode_json(THREAD_DOC))
    mode = await ir.classify(
        "What does the document I uploaded say?",
        thread_has_attachments=True,
        client=client,
    )
    assert mode == THREAD_DOC


@pytest.mark.asyncio
async def test_classify_never_returns_thread_doc_without_attachments():
    # Regardless of scripted output, THREAD_DOC must never survive when the
    # thread has no attachments.
    for scripted in (THREAD_DOC, _mode_json(THREAD_DOC)):
        client = _FakeClient(
            scripted if scripted != THREAD_DOC else _mode_json(THREAD_DOC)
        )
        mode = await ir.classify("about my file", thread_has_attachments=False, client=client)
        assert mode != THREAD_DOC


# --- Safe fallback ----------------------------------------------------------
@pytest.mark.asyncio
async def test_unreachable_model_falls_back_to_default():
    class _BoomClient:
        async def chat(self, **kwargs):
            raise ConnectionError("ollama down")

    mode = await ir.classify("anything at all", client=_BoomClient())
    assert mode == DEFAULT_MODE


@pytest.mark.asyncio
@pytest.mark.parametrize("garbage", ["", "not json", '{"mode": "NONSENSE"}', "{}"])
async def test_unparseable_output_falls_back_to_default(garbage):
    client = _FakeClient(garbage)
    mode = await ir.classify("anything", client=client)
    assert mode == DEFAULT_MODE


@pytest.mark.asyncio
async def test_none_response_falls_back_to_default():
    class _NoneClient:
        async def chat(self, **kwargs):
            return None

    mode = await ir.classify("anything", client=_NoneClient())
    assert mode == DEFAULT_MODE


# --- Prompt construction ----------------------------------------------------
@pytest.mark.asyncio
async def test_prompt_states_attachment_flag_yes():
    client = _FakeClient(_mode_json(GENERAL))
    await ir.classify("hello", thread_has_attachments=True, client=client)
    user_msg = client.last_kwargs["messages"][1]["content"]
    assert "THIS THREAD HAS UPLOADED DOCUMENTS: yes" in user_msg


@pytest.mark.asyncio
async def test_prompt_states_attachment_flag_no():
    client = _FakeClient(_mode_json(GENERAL))
    await ir.classify("hello", thread_has_attachments=False, client=client)
    user_msg = client.last_kwargs["messages"][1]["content"]
    assert "THIS THREAD HAS UPLOADED DOCUMENTS: no" in user_msg


@pytest.mark.asyncio
async def test_prompt_includes_recent_history():
    history = [
        {"role": "user", "content": "Tell me about Terzaghi bearing capacity."},
        {"role": "assistant", "content": "Terzaghi's theory assumes..."},
    ]
    client = _FakeClient(_mode_json(GENERAL))
    await ir.classify("go on", history=history, client=client)
    user_msg = client.last_kwargs["messages"][1]["content"]
    assert "Terzaghi bearing capacity" in user_msg
    assert "LATEST MESSAGE: go on" in user_msg


@pytest.mark.asyncio
async def test_prompt_history_placeholder_when_empty():
    client = _FakeClient(_mode_json(GENERAL))
    await ir.classify("hello", history=None, client=client)
    user_msg = client.last_kwargs["messages"][1]["content"]
    assert "(no prior conversation)" in user_msg


@pytest.mark.asyncio
async def test_classify_uses_think_false_and_json_format():
    client = _FakeClient(_mode_json(GENERAL))
    await ir.classify("hello", client=client)
    kwargs = client.last_kwargs
    assert kwargs["think"] is False
    assert kwargs["format"] == "json"
    # model-agnostic: model comes from config, not a hardcoded name
    from app.core import config

    assert kwargs["model"] == config.OLLAMA_MODEL


def test_history_block_caps_to_last_four_turns():
    history = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
    block = ir._history_block(history)
    assert "msg9" in block and "msg6" in block
    assert "msg5" not in block  # only the last 4 kept


# --- Opt-in live golden set (skipped unless RUN_LIVE_ROUTER=1) --------------
# Real classification quality against a live Ollama. Kept OUT of the default run
# so it never joins the flaky-baseline live tests; run deliberately with:
#   RUN_LIVE_ROUTER=1 pytest tests/unit/test_intent_router.py -k live
_LIVE = os.getenv("RUN_LIVE_ROUTER") == "1"


@pytest.mark.skipif(not _LIVE, reason="live router golden set; set RUN_LIVE_ROUTER=1")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message,attachments,expected",
    [
        ("Explain what liquefaction is.", False, GENERAL),
        ("Help me write a paragraph summarizing my thesis intro.", False, GENERAL),
        ("What does the Bolton 1986 paper conclude about peak friction angle?", False, KB_QUERY),
        ("Summarize the scour effects report in the knowledge base.", False, KB_QUERY),
        ("Compare our EICP report's findings with typical field practice.", False, MIXED),
        ("What are the main findings of the document I just uploaded?", True, THREAD_DOC),
    ],
)
async def test_live_classification_golden(message, attachments, expected):
    mode = await ir.classify(message, thread_has_attachments=attachments)
    assert mode == expected
