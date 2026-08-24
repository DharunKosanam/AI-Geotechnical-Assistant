"""Unit tests for the deterministic inventory snapshot serializer, the
server-side alerts port, the scope inference, and the INVENTORY router-mode
gating. Pure-core only: prefetched data + explicit now, no Mongo, no LLM."""

import json
from datetime import datetime, timedelta

import pytest

from app.core import config
from app.services import intent_router as ir
from app.services import prompt_config
from app.services.inventory_service import (
    ALERTS,
    ITEMS,
    OPEN_LOANS,
    PLAXIS,
    RESERVATIONS,
    alerts_for,
    infer_inventory_scope,
    query_mentions_inventory,
    render_snapshot,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 21, 12, 0)


def _data():
    return {
        "items": [
            {"id": "IT01", "name": "Total station", "kind": "equipment", "qty": 3,
             "qtyOut": 1, "unit": "unit", "status": "Active", "condition": "Good",
             "location": "Cabinet A", "custodian": "Ana Silva"},
            {"id": "IT02", "name": "Epoxy cartridges", "kind": "consumable", "qty": 2,
             "qtyOut": 0, "unit": "pcs", "minStock": 5, "location": "Shelf 2"},
        ],
        "open_loans": [
            {"id": "tx1", "itemId": "IT01", "type": "checkout", "qty": 1,
             "user": "Bo Chen", "ts": NOW - timedelta(days=10),
             "expectedReturn": NOW - timedelta(days=3), "actualReturn": None,
             "purpose": "Site survey"},
        ],
        "reservations": [
            {"id": "r1", "itemId": "IT01", "user": "Dana Wolf", "qty": 1,
             "start": NOW + timedelta(days=2), "end": NOW + timedelta(days=4),
             "status": "Pending", "purpose": "Flume test"},
            {"id": "r2", "itemId": "IT01", "user": "Eli Kim", "qty": 1,
             "start": NOW + timedelta(days=60), "end": NOW + timedelta(days=61),
             "status": "Approved", "purpose": "beyond 30d — hidden"},
        ],
        "plaxis": [
            {"id": "p1", "seat": 1, "user": "Fay Ortiz",
             "start": NOW - timedelta(hours=30), "end": NOW - timedelta(hours=2),
             "loggedOut": False},
        ],
    }


# --- rendering ---------------------------------------------------------------
def test_snapshot_renders_all_sections_with_pipe_rows():
    result = render_snapshot(_data(), "all", NOW, cap_tokens=4000)
    text = result.text
    assert result.included == [ITEMS, OPEN_LOANS, RESERVATIONS, PLAXIS, ALERTS]
    # ITEMS row: avail = qty - out.
    assert "IT01 | Total station | 3 | 1 | 2 | unit" in text
    # OPEN LOANS row carries computed overdue days.
    assert "Total station | Bo Chen | 1" in text and "| 3 |" in text
    # 30d reservation horizon: near one shown, day-60 one hidden.
    assert "Dana Wolf" in text and "Eli Kim" not in text
    # PLAXIS holds the un-logged-out session.
    assert "Fay Ortiz" in text
    assert not result.omitted and not result.trimmed


def test_snapshot_scope_selects_sections():
    result = render_snapshot(_data(), "PLAXIS SEATS", NOW, cap_tokens=4000)
    assert result.included == [PLAXIS]
    assert "Total station" not in result.text
    assert "Fay Ortiz" in result.text


def test_scope_note_names_timestamp_and_sections():
    result = render_snapshot(_data(), "ITEMS,ALERTS", NOW, cap_tokens=4000)
    note = result.scope_note()
    assert "2026-08-21 12:00" in note
    assert "ITEMS" in note and "ALERTS" in note


# --- token cap ---------------------------------------------------------------
def test_cap_drops_sections_by_priority_alerts_and_loans_last():
    result = render_snapshot(_data(), "all", NOW, cap_tokens=60)
    # PLAXIS, RESERVATIONS, ITEMS go first; ALERTS survives longest.
    assert ALERTS in result.included
    assert PLAXIS in result.omitted and RESERVATIONS in result.omitted
    assert result.omitted and result.scope_note().count("Omitted for length") == 1


def test_cap_never_truncates_mid_row():
    data = _data()
    # Many items so the row-trim path (not just section drops) engages.
    data["items"] = [
        {"id": f"IT{i:03d}", "name": f"Item {i}", "kind": "equipment",
         "qty": 5, "qtyOut": 1, "unit": "unit"}
        for i in range(60)
    ]
    result = render_snapshot(data, "ITEMS", NOW, cap_tokens=200)
    lines = result.text.splitlines()
    rows = [l for l in lines if l.startswith("IT0")]
    # Every surviving row is complete (all 10 ITEMS columns).
    assert rows and all(l.count("|") == 9 for l in rows)
    assert result.trimmed.get(ITEMS, 0) > 0
    assert any(l.startswith("(+") and l.endswith("rows omitted)") for l in lines)


# --- alerts (server-side port of alertsFor) ----------------------------------
def test_alerts_cover_the_specified_kinds():
    data = _data()
    data["items"].append({"id": "IT03", "name": "pH probe", "kind": "consumable",
                          "qty": 4, "expiryDate": NOW + timedelta(days=10)})
    data["items"].append({"id": "IT04", "name": "Shear box", "kind": "equipment",
                          "qty": 1, "condition": "Damaged",
                          "maintDays": 90, "lastMaint": NOW - timedelta(days=100)})
    alerts = alerts_for(data["items"], data["open_loans"], data["reservations"],
                        data["plaxis"], NOW)
    kinds = {a["kind"] for a in alerts}
    assert {"overdue", "low_stock", "expiry", "maintenance", "damaged",
            "plaxis_overrun", "pending_approval"} <= kinds
    # high alerts sort first
    assert alerts[0]["severity"] == "high"
    # additive metadata: the overdue alert points at its item + tx and carries
    # the server-clock day count the Reports page reads
    overdue = next(a for a in alerts if a["kind"] == "overdue")
    assert overdue["itemId"] == "IT01" and overdue["refId"] == "tx1" and overdue["days"] == 3


def test_alerts_quiet_on_healthy_data():
    items = [{"id": "A", "name": "Ok item", "kind": "equipment", "qty": 1}]
    assert alerts_for(items, [], [], [], NOW) == []


# --- scope inference ---------------------------------------------------------
@pytest.mark.parametrize("query,expected_section", [
    ("who has the total station checked out?", OPEN_LOANS),
    ("can I book the interrogator next week?", RESERVATIONS),
    ("are any plaxis seats free?", PLAXIS),
    ("anything overdue or needing maintenance?", ALERTS),
    ("how many strain gauges are in stock?", ITEMS),
])
def test_infer_scope_matches_keywords(query, expected_section):
    assert expected_section in infer_inventory_scope(query).split(",")
    assert query_mentions_inventory(query) is True


def test_infer_scope_defaults_to_all_and_mentions_is_false():
    assert infer_inventory_scope("what is the friction angle of dense sand?") == "all"
    assert query_mentions_inventory("what is the friction angle of dense sand?") is False


# --- INVENTORY router-mode gating -------------------------------------------
def test_inventory_mode_is_valid_and_has_a_prompt():
    assert ir.INVENTORY in ir.VALID_MODES
    assert prompt_config.SYSTEM_PROMPTS[ir.INVENTORY] == prompt_config.INVENTORY_PROMPT


def test_parse_mode_rejects_inventory_when_flag_off(monkeypatch):
    monkeypatch.setattr(config, "INVENTORY_ENABLED", False)
    assert ir._parse_mode(json.dumps({"mode": "INVENTORY"})) is None


def test_parse_mode_accepts_inventory_when_flag_on(monkeypatch):
    monkeypatch.setattr(config, "INVENTORY_ENABLED", True)
    assert ir._parse_mode(json.dumps({"mode": "INVENTORY"})) == ir.INVENTORY


def test_router_prompt_byte_identical_with_inventory_off(monkeypatch):
    monkeypatch.setattr(config, "WEB_INGEST_ENABLED", False)
    monkeypatch.setattr(config, "ROUTER_UNCERTAIN_RETRIEVES", False)
    monkeypatch.setattr(config, "INVENTORY_ENABLED", False)
    assert ir._system_prompt() == ir.ROUTER_SYSTEM_PROMPT


def test_router_prompt_offers_inventory_when_flag_on(monkeypatch):
    monkeypatch.setattr(config, "WEB_INGEST_ENABLED", False)
    monkeypatch.setattr(config, "ROUTER_UNCERTAIN_RETRIEVES", False)
    monkeypatch.setattr(config, "INVENTORY_ENABLED", True)
    prompt = ir._system_prompt()
    assert prompt.startswith(ir.ROUTER_SYSTEM_PROMPT)  # base prompt untouched
    assert '"mode": "INVENTORY"' in prompt
    assert "PLAXIS" in prompt


@pytest.mark.asyncio
async def test_classify_returns_inventory_only_when_enabled(monkeypatch):
    class _FakeClient:
        def __init__(self, content):
            self.content = content

        async def chat(self, **kwargs):
            return {"message": {"content": self.content}}

    fake = _FakeClient(json.dumps({"mode": "INVENTORY"}))
    monkeypatch.setattr(config, "INVENTORY_ENABLED", True)
    assert await ir.classify("who has the drill?", client=fake) == ir.INVENTORY
    monkeypatch.setattr(config, "INVENTORY_ENABLED", False)
    # Flag off: the label is unknown -> safe default (KB_QUERY), same as today.
    assert await ir.classify("who has the drill?", client=fake) == ir.DEFAULT_MODE
