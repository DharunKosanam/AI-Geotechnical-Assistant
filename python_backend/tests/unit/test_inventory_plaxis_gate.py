"""PLAXIS seat-window gate (INVENTORY_PERSONAL_VIEW): the pure rule
(seat_conflicts) and the server-side gate on create and on updates that move
seat/start/end. Same conventions as the reservation gate: half-open windows,
never owner-aware, 409 names the holder. Route functions called directly
with async fakes; no Mongo."""

from datetime import datetime

import pytest
from fastapi import HTTPException

from app.core import config
from app.routers import inventory as inv
from app.services.inventory_service import PLAXIS_SEATS, seat_conflicts
from models import User

pytestmark = pytest.mark.unit


def T(day, hour):
    return datetime(2026, 9, day, hour)


HELD = {"id": "PX-HELD", "seat": 0, "user": "Jiming Liu", "loggedOut": False,
        "email": "jiming@uvic.ca", "start": T(1, 9), "end": T(1, 12), "updatedAt": None}

CALLER = User(id="u2", email="subash@uvic.ca", hashed_password="x",
              full_name="Subash Koirala", role="user")
OWNER = User(id="u1", email="jiming@uvic.ca", hashed_password="x",
             full_name="Jiming Liu", role="user")


# --- pure rule ----------------------------------------------------------------
def test_seat_conflicts_half_open_and_seat_scoped():
    sessions = [HELD]
    # True overlap on the same seat.
    assert seat_conflicts(sessions, 0, T(1, 10), T(1, 11)) != []
    # Back-to-back never conflicts (half-open, both edges).
    assert seat_conflicts(sessions, 0, T(1, 12), T(1, 14)) == []
    assert seat_conflicts(sessions, 0, T(1, 7), T(1, 9)) == []
    # The other seat is free.
    assert seat_conflicts(sessions, 1, T(1, 10), T(1, 11)) == []


def test_seat_conflicts_ignores_logged_out_and_self():
    ended = {**HELD, "loggedOut": True}
    assert seat_conflicts([ended], 0, T(1, 10), T(1, 11)) == []
    # exclude_id: a row never conflicts with itself when edited.
    assert seat_conflicts([HELD], 0, T(1, 10), T(1, 11), exclude_id="PX-HELD") == []


def test_seat_conflicts_holder_shape_feeds_conflict_message():
    holders = seat_conflicts([HELD], 0, T(1, 10), T(1, 11))
    from app.services.inventory_service import conflict_message
    msg = conflict_message({"name": "PLAXIS seat 1"}, holders)
    assert "PLAXIS seat 1" in msg and "Jiming Liu" in msg


# --- router wiring ------------------------------------------------------------
class _Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    def __aiter__(self):
        async def gen():
            for d in self.docs:
                yield dict(d)
        return gen()


class FakeColl:
    def __init__(self, doc=None, docs=None):
        self.doc = doc
        self.docs = docs or []
        self.inserted = []
        self.updated = []

    async def find_one(self, query=None, *a, **k):
        return dict(self.doc) if isinstance(self.doc, dict) else self.doc

    def find(self, query=None, *a, **k):
        return _Cursor(self.docs)

    async def insert_one(self, d):
        self.inserted.append(d)

    async def update_one(self, *a, **k):
        self.updated.append((a, k))


@pytest.fixture()
def fakes(monkeypatch):
    monkeypatch.setattr(config, "INVENTORY_PERSONAL_VIEW", True)
    plaxis = FakeColl(doc=dict(HELD), docs=[dict(HELD)])
    users = FakeColl(doc=None)
    audit = FakeColl()
    monkeypatch.setattr(inv, "inv_plaxis_collection", plaxis)
    monkeypatch.setattr(inv, "inv_users_collection", users)
    monkeypatch.setattr(inv, "inv_audit_collection", audit)
    monkeypatch.setattr(inv, "_RESOURCES", {
        "plaxis": (plaxis, inv._PLAXIS_FIELDS),
        "users": (users, inv._USER_FIELDS),
    })
    return {"plaxis": plaxis, "users": users}


def _booking(seat=0, start="2026-09-01T10:00:00", end="2026-09-01T11:00:00", user="Subash Koirala"):
    return {"seat": seat, "user": user, "start": start, "end": end, "loggedOut": False}


async def test_overlapping_booking_is_409_and_not_inserted(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.create_resource("plaxis", _booking(), current_user=CALLER)
    assert ei.value.status_code == 409
    assert "PLAXIS seat 1" in ei.value.detail and "Jiming Liu" in ei.value.detail
    assert fakes["plaxis"].inserted == []


async def test_two_users_same_seat_same_window_is_409(fakes):
    # The same person double-booking is refused identically — the gate is
    # not owner-aware.
    with pytest.raises(HTTPException) as ei:
        await inv.create_resource("plaxis", _booking(user="Jiming Liu"),
                                  current_user=OWNER)
    assert ei.value.status_code == 409
    assert fakes["plaxis"].inserted == []


async def test_back_to_back_and_other_seat_are_written(fakes):
    out = await inv.create_resource(
        "plaxis", _booking(start="2026-09-01T12:00:00", end="2026-09-01T14:00:00"),
        current_user=CALLER)
    assert out["id"]
    out2 = await inv.create_resource("plaxis", _booking(seat=1), current_user=CALLER)
    assert out2["seat"] == 1
    assert len(fakes["plaxis"].inserted) == 2


async def test_overlap_with_logged_out_session_is_written(fakes):
    fakes["plaxis"].docs = [{**HELD, "loggedOut": True}]
    out = await inv.create_resource("plaxis", _booking(), current_user=CALLER)
    assert out["id"]
    assert fakes["plaxis"].inserted


async def test_editing_a_rows_own_window_never_conflicts_with_itself(fakes):
    out = await inv.update_resource("plaxis", "PX-HELD", {"end": "2026-09-01T13:00:00"},
                                    current_user=OWNER)
    assert out["id"] == "PX-HELD"
    assert fakes["plaxis"].updated


async def test_moving_a_row_into_a_conflict_is_409(fakes):
    other = {"id": "PX-2", "seat": 0, "user": "Subash Koirala", "loggedOut": False,
             "email": "subash@uvic.ca", "start": T(1, 13), "end": T(1, 15), "updatedAt": None}
    fakes["plaxis"].doc = dict(other)          # the row being edited
    fakes["plaxis"].docs = [dict(HELD), dict(other)]
    with pytest.raises(HTTPException) as ei:
        await inv.update_resource("plaxis", "PX-2", {"start": "2026-09-01T10:00:00",
                                                     "end": "2026-09-01T11:00:00"},
                                  current_user=CALLER)
    assert ei.value.status_code == 409
    assert fakes["plaxis"].updated == []


@pytest.mark.parametrize("payload, fragment", [
    (_booking(seat=7), "Choose Seat 1 or Seat 2."),
    (_booking(start="2026-09-01T11:00:00", end="2026-09-01T10:00:00"), "after the start"),
    ({"seat": 0, "user": "Subash Koirala", "loggedOut": False}, "needs a start and an end"),
])
async def test_invalid_bookings_are_400(fakes, payload, fragment):
    with pytest.raises(HTTPException) as ei:
        await inv.create_resource("plaxis", payload, current_user=CALLER)
    assert ei.value.status_code == 400
    assert fragment in ei.value.detail
    assert fakes["plaxis"].inserted == []


async def test_race_lost_insert_surfaces_as_the_same_409(fakes):
    # The partial unique index on held sessions is the TOCTOU backstop: when
    # two concurrent identical bookings both pass the read-then-check gate,
    # the second INSERT raises DuplicateKeyError — which must surface as the
    # same 409 the gate produces, never a 500.
    from pymongo.errors import DuplicateKeyError

    async def racing_insert(d):
        raise DuplicateKeyError("E11000 duplicate key error: uniq_held_seat_window")

    fakes["plaxis"].docs = [dict(HELD)]
    fakes["plaxis"].insert_one = racing_insert
    with pytest.raises(HTTPException) as ei:
        # Back-to-back window passes the gate; the "index" then refuses it.
        await inv.create_resource(
            "plaxis", _booking(start="2026-09-01T12:00:00", end="2026-09-01T14:00:00"),
            current_user=CALLER)
    assert ei.value.status_code == 409
    assert "PLAXIS seat 1" in ei.value.detail


async def test_flag_off_create_stays_unconditional(fakes, monkeypatch):
    # Parity: the gate (and its validations) exist only flag-on.
    monkeypatch.setattr(config, "INVENTORY_PERSONAL_VIEW", False)
    out = await inv.create_resource("plaxis", _booking(seat=7), current_user=CALLER)
    assert out["seat"] == 7
    assert fakes["plaxis"].inserted


def test_known_seat_set_matches_the_two_seat_license():
    assert sorted(PLAXIS_SEATS) == [0, 1]
