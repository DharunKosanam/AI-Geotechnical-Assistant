"""Phase 10 — every alert type the spec names fires from the server."""

from datetime import datetime, timedelta

import pytest

from app.services.inventory_service import alerts_for

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 21, 12, 0)
SPEC_KINDS = {
    "overdue",              # overdue loans
    "upcoming_reservation", # upcoming reservations
    "damaged",              # damaged equipment
    "low_stock",            # low-stock consumables
    "conflict",             # conflicting bookings
    "expiry",               # expiry
    "maintenance",          # maintenance due
}


def test_every_spec_alert_type_fires():
    items = [
        {"id": "A", "name": "Interrogator", "kind": "equipment", "qty": 1, "condition": "Damaged",
         "maintDays": 90, "lastMaint": NOW - timedelta(days=80)},           # damaged + maintenance due (10 d)
        {"id": "B", "name": "Epoxy", "kind": "consumable", "qty": 1, "minStock": 2,
         "expiryDate": NOW + timedelta(days=10)},                          # low stock + expiry
        {"id": "C", "name": "Sensor", "kind": "equipment", "qty": 1},
    ]
    loans = [{"id": "t1", "itemId": "C", "type": "checkout", "qty": 1, "user": "Bo",
              "ts": NOW - timedelta(days=10), "expectedReturn": NOW - timedelta(days=2), "actualReturn": None}]
    res = [
        {"id": "r1", "itemId": "A", "user": "Saeed", "status": "Approved", "qty": 1,
         "start": NOW + timedelta(hours=20), "end": NOW + timedelta(hours=28)},   # upcoming (+ conflicts with r2)
        {"id": "r2", "itemId": "A", "user": "Jiming", "status": "Approved", "qty": 1,
         "start": NOW + timedelta(hours=24), "end": NOW + timedelta(hours=30)},   # overlaps r1 on a qty-1 item
    ]
    alerts = alerts_for(items, loans, res, [], NOW)
    kinds = {a["kind"] for a in alerts}
    missing = SPEC_KINDS - kinds
    assert not missing, f"spec alert types not firing: {sorted(missing)}"
    # Each carries the deep-link target the Dashboard needs.
    assert all(a["itemId"] for a in alerts if a["kind"] in SPEC_KINDS)


def test_upcoming_reservation_window_is_48h_and_approved_only():
    items = [{"id": "A", "name": "X", "kind": "equipment", "qty": 5}]
    res = [
        {"id": "r1", "itemId": "A", "user": "u", "status": "Approved", "start": NOW + timedelta(hours=47), "end": NOW + timedelta(hours=50)},
        {"id": "r2", "itemId": "A", "user": "u", "status": "Approved", "start": NOW + timedelta(hours=49), "end": NOW + timedelta(hours=52)},
        {"id": "r3", "itemId": "A", "user": "u", "status": "Pending", "start": NOW + timedelta(hours=1), "end": NOW + timedelta(hours=2)},
        {"id": "r4", "itemId": "A", "user": "u", "status": "Approved", "start": NOW - timedelta(hours=1), "end": NOW + timedelta(hours=2)},
    ]
    ups = [a for a in alerts_for(items, [], res, [], NOW) if a["kind"] == "upcoming_reservation"]
    assert [a["refId"] for a in ups] == ["r1"]
    assert ups[0]["severity"] == "low" and ups[0]["days"] == 1
