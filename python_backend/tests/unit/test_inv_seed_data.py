"""Unit tests over the REAL Lin Lab seed data (data/linlab_seed.json):
seeder loading/validation, and the snapshot + feasibility stack run against
the demo-activity variant with a pinned clock. No Mongo, no LLM."""

from datetime import datetime

import pytest

from app.scripts.inv_seed import load_seed, validate_seed
from app.services.inventory_service import (
    ItemRequest,
    compute_feasibility,
    render_snapshot,
)

pytestmark = pytest.mark.unit

# The demo activity was authored around this date (the ODiSI loan is due back
# 2026-08-18, PLAXIS sessions end 08-19/08-20/08-21).
NOW = datetime(2026, 8, 21, 12, 0)


@pytest.fixture(scope="module")
def demo():
    return load_seed(demo=True)


@pytest.fixture(scope="module")
def clean():
    return load_seed(demo=False)


# --- seed file shape ---------------------------------------------------------
def test_both_variants_load_and_validate_clean(demo, clean):
    assert validate_seed(demo) == []
    assert validate_seed(clean) == []
    assert len(demo["users"]) == 20 and len(demo["items"]) == 36
    assert len(clean["users"]) == 20 and len(clean["items"]) == 36
    assert {len(demo[s]) for s in ("tx", "res", "plaxis", "audit")} == {5, 4, 6, 3}
    assert "tx" not in clean  # clean ledger carries no activity


def test_interrogator_model_is_6104(demo, clean):
    for variant in (demo, clean):
        odisi = next(i for i in variant["items"] if i["id"] == "LL-FOS-001")
        assert odisi["model"] == "ODiSI 6104"


def test_dates_are_coerced_to_datetimes(demo):
    odisi = next(i for i in demo["items"] if i["id"] == "LL-FOS-001")
    assert isinstance(odisi["purchaseDate"], datetime)
    assert odisi["expiryDate"] is None            # "" -> None
    assert isinstance(demo["tx"][0]["ts"], datetime)
    # users.since stays a year LABEL, never parsed as a date
    assert next(u for u in demo["users"] if u["id"] == "U-002")["since"] == "2024"


# --- the real lab through the snapshot serializer ----------------------------
def _snapshot_data(variant):
    return {
        "items": variant["items"],
        "open_loans": variant.get("tx", []),
        "reservations": variant.get("res", []),
        "plaxis": variant.get("plaxis", []),
    }


def test_demo_snapshot_fits_cap_and_carries_known_state(demo):
    result = render_snapshot(_snapshot_data(demo), "all", NOW, cap_tokens=4000)
    # The real lab fits the 4000-token cap whole — nothing dropped or trimmed.
    assert result.omitted == [] and result.trimmed == {}
    text = result.text
    # ODiSI loan (due 2026-08-18) shows as 3 days overdue in OPEN LOANS.
    assert "Fiber-optic interrogator (LUNA ODiSI) | Yongxuan Gao | 1" in text
    # Approved ODiSI reservation inside the 30d horizon.
    assert "Saeed Mahjoubi" in text
    # Only the un-logged-out PLAXIS sessions render (Sangam's session is
    # logged out; split on the exact header — an ITEMS row also says PLAXIS).
    plaxis_section = text.split("PLAXIS SEATS (2 concurrent)")[1].split("ALERTS")[0]
    assert "Shane Smith" in plaxis_section
    assert "Sangam Acharya" not in plaxis_section
    # Alerts: overdue loan, ZL6 maintenance overdue, erosion-apparatus
    # calibration overdue, depleted packing tape, pH-solution expiry.
    alerts = text.split("ALERTS")[1]
    assert "overdue" in alerts
    assert "ZL6 datalogger" in alerts
    assert "Rotating erosion apparatus" in alerts
    assert "Clear packing tape" in alerts
    assert "Calibration standard solution pH 7" in alerts


# --- the real lab through the feasibility engine -----------------------------
def test_odisi_blocked_then_free_after_saeeds_reservation(demo):
    # Ask for the interrogator over Aug 23–24: the unit is still out on the
    # overdue loan AND Saeed holds an approved reservation for that window.
    report = compute_feasibility(
        [ItemRequest("LL-FOS-001", 1)],
        datetime(2026, 8, 23, 9), datetime(2026, 8, 24, 9),
        demo["items"], demo["tx"], demo["res"], now=NOW,
    )
    entry = report.items[0]
    assert report.feasible is False
    assert entry.status == "conflicts_with"
    assert any(c["user"] == "Saeed Mahjoubi" for c in entry.conflicts)
    # Projection: the loan frees at its (past-due) expectedReturn and the
    # reservation ends 08-24 09:00 — the earliest same-length window.
    assert report.earliest_available == datetime(2026, 8, 24, 9)


def test_clean_ledger_makes_same_request_available(clean):
    report = compute_feasibility(
        [ItemRequest("LL-FOS-001", 1)],
        datetime(2026, 8, 23, 9), datetime(2026, 8, 24, 9),
        clean["items"], [], [], now=NOW,
    )
    assert report.feasible is True
    assert report.items[0].status == "available"
