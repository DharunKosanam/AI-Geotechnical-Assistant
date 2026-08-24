"""Unit tests for the inventory feasibility engine (the part the model must
not compute). Pure-core only: prefetched data + explicit now, no Mongo, no
LLM. Covers the owner-specified cases: exact-boundary overlap, partial
shortfall, multi-item where one blocks, consumables vs equipment, and the
zero-qty request — plus the defensive extraction parser."""

import json
from datetime import datetime, timedelta

import pytest

from app.services.inventory_service import (
    ItemRequest,
    compute_feasibility,
    parse_feasibility_extraction,
    render_feasibility_report,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 21, 12, 0)
START = datetime(2026, 9, 1, 9, 0)
END = datetime(2026, 9, 5, 17, 0)


def _item(item_id="IT01", qty=3, kind="equipment", name="Total station", **extra):
    return {"id": item_id, "name": name, "qty": qty, "qtyOut": 0, "kind": kind, **extra}


def _loan(item_id="IT01", qty=1, due=None, user="Ana Silva", returned=None):
    return {
        "id": "tx1", "itemId": item_id, "type": "checkout", "qty": qty,
        "user": user, "ts": NOW - timedelta(days=3),
        "expectedReturn": due, "actualReturn": returned,
    }


def _res(item_id="IT01", qty=1, start=None, end=None, status="Approved", user="Bo Chen"):
    return {
        "id": "r1", "itemId": item_id, "qty": qty, "user": user,
        "start": start or START, "end": end or END, "status": status,
    }


def _run(requests, items, loans=(), res=(), start=START, end=END):
    return compute_feasibility(requests, start, end, items, list(loans), list(res), now=NOW)


# --- exact-boundary overlap (half-open: res.start < end and res.end > start) --
def test_reservation_ending_exactly_at_start_does_not_conflict():
    r = _res(start=START - timedelta(days=4), end=START)
    report = _run([ItemRequest("IT01", 1)], [_item(qty=1)], res=[r])
    assert report.items[0].status == "available"
    assert report.feasible is True


def test_reservation_starting_exactly_at_end_does_not_conflict():
    r = _res(start=END, end=END + timedelta(days=2))
    report = _run([ItemRequest("IT01", 1)], [_item(qty=1)], res=[r])
    assert report.items[0].status == "available"


def test_reservation_overlapping_by_a_minute_conflicts():
    r = _res(start=END - timedelta(minutes=1), end=END + timedelta(days=1))
    report = _run([ItemRequest("IT01", 1)], [_item(qty=1)], res=[r])
    entry = report.items[0]
    assert entry.status == "conflicts_with"
    assert entry.conflicts and entry.conflicts[0]["user"] == "Bo Chen"
    assert report.feasible is False


def test_pending_reservations_do_not_block():
    r = _res(status="Pending")
    report = _run([ItemRequest("IT01", 1)], [_item(qty=1)], res=[r])
    assert report.items[0].status == "available"


# --- partial shortfall -------------------------------------------------------
def test_partial_shortfall_reports_short_by_n():
    loans = [_loan(qty=2, due=END + timedelta(days=10))]
    report = _run([ItemRequest("IT01", 4)], [_item(qty=5)], loans=loans)
    entry = report.items[0]
    assert entry.status == "short_by"
    assert entry.short_by == 1  # 5 total - 2 out = 3 available, 4 asked
    assert report.feasible is False


def test_returned_loans_do_not_count():
    loans = [_loan(qty=2, returned=NOW - timedelta(days=1))]
    report = _run([ItemRequest("IT01", 4)], [_item(qty=5)], loans=loans)
    assert report.items[0].status == "available"


# --- multi-item where one blocks ---------------------------------------------
def test_multi_item_one_blocking_reservation_sets_verdict_and_earliest():
    items = [_item("IT01", qty=2), _item("IT02", qty=1, name="Interrogator")]
    blocking = _res(item_id="IT02", start=START - timedelta(days=1), end=START + timedelta(days=2))
    report = _run([ItemRequest("IT01", 1), ItemRequest("IT02", 1)], items, res=[blocking])
    by_id = {e.itemId: e for e in report.items}
    assert by_id["IT01"].status == "available"
    assert by_id["IT02"].status == "conflicts_with"
    assert report.feasible is False
    # The full set becomes available when the blocking reservation ends.
    assert report.earliest_available == START + timedelta(days=2)


def test_earliest_uses_loan_expected_return():
    due = START + timedelta(days=3)
    report = _run([ItemRequest("IT01", 1)], [_item(qty=1)], loans=[_loan(due=due)])
    assert report.feasible is False
    assert report.earliest_available == due


def test_loan_without_expected_return_never_frees():
    report = _run([ItemRequest("IT01", 1)], [_item(qty=1)], loans=[_loan(due=None)])
    assert report.feasible is False
    assert report.earliest_available is None


# --- consumables vs equipment ------------------------------------------------
def test_consumable_availability_is_on_hand_qty_not_loan_math():
    # Consumption model: a consumable checkout already decremented qty, so an
    # open checkout row must NOT be double-counted against availability.
    item = _item(kind="consumable", qty=10, name="Epoxy cartridges")
    report = _run([ItemRequest("IT01", 5)], [item], loans=[_loan(qty=4, due=None)])
    assert report.items[0].status == "available"


def test_equipment_open_loan_blocks_even_outside_window():
    # Equipment that is physically out counts regardless of the window until
    # it is actually returned.
    item = _item(kind="equipment", qty=1)
    past_due = START - timedelta(days=30)
    report = _run([ItemRequest("IT01", 1)], [item], loans=[_loan(due=past_due)])
    assert report.items[0].status == "short_by"


def test_consumable_shortfall():
    item = _item(kind="consumable", qty=2, name="Strain gauges")
    report = _run([ItemRequest("IT01", 5)], [item])
    entry = report.items[0]
    assert entry.status == "short_by" and entry.short_by == 3


# --- zero-qty + unknown item ---------------------------------------------------
def test_zero_qty_request_is_trivially_available():
    report = _run([ItemRequest("IT01", 0)], [_item(qty=0)])
    assert report.items[0].status == "available"
    assert report.feasible is True
    assert report.earliest_available == START


def test_unknown_item_reported_and_never_schedulable():
    report = _run([ItemRequest("NOPE", 1)], [_item()])
    assert report.items[0].status == "unknown_item"
    assert report.feasible is False
    assert report.earliest_available is None


# --- rendered report is deterministic text -----------------------------------
def test_render_feasibility_report_contains_verdict_and_rows():
    report = _run([ItemRequest("IT01", 4)], [_item(qty=5)],
                  loans=[_loan(qty=2, due=END + timedelta(days=10))])
    text = render_feasibility_report(report)
    assert "FEASIBILITY CHECK" in text
    assert "VERDICT: NOT FEASIBLE" in text
    assert "short by 1" in text
    assert "EARLIEST FULL AVAILABILITY" in text


# --- extraction parser (defensive, same posture as _parse_mode) --------------
def test_extraction_parses_valid_payload():
    raw = json.dumps({
        "requests": [{"itemId": "IT01", "qty": 2}],
        "start": "2026-09-01T09:00", "end": "2026-09-05T17:00",
    })
    parsed = parse_feasibility_extraction(raw, ["IT01"])
    assert parsed is not None
    requests, start, end = parsed
    assert requests[0].itemId == "IT01" and requests[0].qty == 2
    assert start == datetime(2026, 9, 1, 9, 0) and end == datetime(2026, 9, 5, 17, 0)


def test_extraction_accepts_fenced_json():
    raw = '```json\n{"requests": [{"itemId": "IT01"}], "start": "2026-09-01", "end": "2026-09-02"}\n```'
    parsed = parse_feasibility_extraction(raw, ["IT01"])
    assert parsed is not None
    assert parsed[0][0].qty == 1  # default


@pytest.mark.parametrize("raw", [
    "not json at all",
    '{"requests": []}',                                             # not a booking question
    '{"requests": [{"itemId": "GHOST", "qty": 1}], "start": "2026-09-01", "end": "2026-09-02"}',  # unknown id
    '{"requests": [{"itemId": "IT01"}], "start": "someday", "end": "2026-09-02"}',                # bad date
    '{"requests": [{"itemId": "IT01"}], "start": "2026-09-02", "end": "2026-09-01"}',             # end <= start
])
def test_extraction_rejects_malformed_payloads(raw):
    assert parse_feasibility_extraction(raw, ["IT01"]) is None
