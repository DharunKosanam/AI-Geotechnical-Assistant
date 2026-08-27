"""backfill_inventory_owner_key: exact-match-only classification, dry-run
writes nothing, --apply writes resolved rows only (audited) and is
idempotent. Fake collections, no Mongo."""

from types import SimpleNamespace

import pytest

from app.scripts.backfill_inventory_owner_key import (
    ACTOR,
    classify_rows,
    run_backfill,
)

pytestmark = pytest.mark.unit

USERS = [
    {"id": "U-1", "name": "Jiming Liu", "email": "jiming@uvic.ca"},
    {"id": "U-2", "name": "Subash Koirala", "email": "subash@uvic.ca"},
    {"id": "U-3", "name": "Alex Chen", "email": "achen@uvic.ca"},
    {"id": "U-4", "name": "Alex Chen", "email": "alex.chen@uvic.ca"},  # duplicate name
    {"id": "U-5", "name": "Shane Smith", "email": ""},                 # blank email
]

RES = [
    {"id": "RS-1", "itemId": "LL-A", "user": "Jiming Liu",
     "start": "2026-09-01", "end": "2026-09-02"},                       # resolved
    {"id": "RS-2", "itemId": "LL-A", "user": "Alex Chen",
     "start": "2026-09-03", "end": "2026-09-04"},                       # ambiguous
    {"id": "RS-3", "itemId": "LL-B", "user": "A Visitor",
     "start": "2026-09-05", "end": "2026-09-06"},                       # unresolved
    {"id": "RS-4", "itemId": "LL-B", "user": "Shane Smith",
     "start": "2026-09-07", "end": "2026-09-08"},                       # blank roster email
    {"id": "RS-5", "itemId": "LL-B", "user": "",
     "start": "2026-09-09", "end": "2026-09-10"},                       # blank stored name
    {"id": "RS-6", "itemId": "LL-C", "user": "Subash Koirala",
     "email": "subash@uvic.ca", "start": "2026-09-11", "end": "2026-09-12"},  # already keyed
]

PLX = [
    {"id": "PX-1", "seat": 0, "user": "Subash Koirala",
     "start": "2026-09-01", "end": "2026-09-02"},                       # resolved
]


class _Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def __aiter__(self):
        async def gen():
            for d in self.docs:
                yield dict(d)
        return gen()


class Coll:
    """Stateful fake: update_one really mutates, so a second backfill pass
    sees the keys the first one wrote."""

    def __init__(self, docs=()):
        self.docs = [dict(d) for d in docs]
        self.updated = []
        self.inserted = []

    def find(self, query=None, *a, **k):
        return _Cursor(self.docs)

    async def update_one(self, filt, update, *a, **k):
        self.updated.append((filt, update))
        for d in self.docs:
            if d.get("id") == filt.get("id"):
                d.update(update.get("$set", {}))

    async def insert_one(self, doc):
        self.inserted.append(doc)


TX = [
    # Already keyed (the normal tx shape) — never classified.
    {"id": "TX-1", "itemId": "LL-A", "type": "checkout", "user": "Jiming Liu",
     "email": "jiming@uvic.ca", "ts": "2026-08-10", "expectedReturn": "2026-08-20"},
]


@pytest.fixture()
def db():
    return SimpleNamespace(
        inv_users_collection=Coll(USERS),
        inv_tx_collection=Coll(TX),
        inv_res_collection=Coll(RES),
        inv_plaxis_collection=Coll(PLX),
        inv_audit_collection=Coll(),
    )


# --- pure classification ------------------------------------------------------
def test_classify_exact_match_only():
    c = classify_rows(RES, USERS)
    assert [(r["id"], e) for r, e in c.resolved] == [("RS-1", "jiming@uvic.ca")]
    assert [r["id"] for r, _ in c.ambiguous] == ["RS-2"]
    reasons = {r["id"]: reason for r, reason in c.unresolved}
    assert reasons == {"RS-3": "no roster match",
                       "RS-4": "roster row has no email",
                       "RS-5": "blank stored name"}
    # RS-6 already carries the key: not classified at all.
    all_ids = {r["id"] for r, _ in c.resolved} \
        | {r["id"] for r, _ in c.ambiguous} \
        | {r["id"] for r, _ in c.unresolved}
    assert "RS-6" not in all_ids


def test_classify_never_fuzzy_matches():
    # Case-insensitive EXACT equality only — substrings, initials, extra
    # whitespace inside the name never resolve.
    c = classify_rows([{"id": "R", "user": "Jiming"}], USERS)
    assert [r["id"] for r, _ in c.unresolved] == ["R"]
    c2 = classify_rows([{"id": "R", "user": "JIMING LIU"}], USERS)
    assert [(r["id"], e) for r, e in c2.resolved] == [("R", "jiming@uvic.ca")]


# --- dry run ------------------------------------------------------------------
async def test_dry_run_writes_nothing(db, capsys):
    summary = await run_backfill(apply=False, db=db)
    assert db.inv_res_collection.updated == []
    assert db.inv_plaxis_collection.updated == []
    assert db.inv_audit_collection.inserted == []
    assert summary["collections"]["res"] == {
        "total": 6, "lacking": 5, "resolved": 1, "ambiguous": 1,
        "unresolved": 3, "written": 0,
    }
    out = capsys.readouterr().out
    # Every non-resolving row is reported individually, with enough to fix it.
    assert "AMBIGUOUS" in out and "RS-2" in out and "U-3, U-4" in out
    assert "UNRESOLVED" in out and "RS-4" in out and "roster row has no email" in out
    assert "nothing written" in out


# --- apply --------------------------------------------------------------------
async def test_apply_writes_resolved_only_and_audits(db):
    summary = await run_backfill(apply=True, db=db)
    assert summary["collections"]["res"]["written"] == 1
    assert summary["collections"]["plaxis"]["written"] == 1
    # Only the resolved rows changed; ambiguous/unresolved untouched.
    updated_ids = [f["id"] for f, _ in db.inv_res_collection.updated]
    assert updated_ids == ["RS-1"]
    res_by_id = {d["id"]: d for d in db.inv_res_collection.docs}
    assert res_by_id["RS-1"]["email"] == "jiming@uvic.ca"
    assert "email" not in res_by_id["RS-2"]
    assert "email" not in res_by_id["RS-4"]
    # Each write audited with the script as the actor.
    audits = db.inv_audit_collection.inserted
    assert len(audits) == 2
    assert all(a["actor"] == ACTOR for a in audits)
    assert {a["entity"] for a in audits} == {"inv_res:RS-1", "inv_plaxis:PX-1"}
    assert audits[0]["owner"] in ("Jiming Liu", "Subash Koirala")


async def test_tx_covered_for_symmetry(db):
    # A legacy tx row lacking the key resolves through the same never-guess
    # rules and is audited under its own action name.
    db.inv_tx_collection = Coll([
        {"id": "TX-OLD", "itemId": "LL-A", "type": "checkout", "user": "Jiming Liu",
         "ts": "2026-07-01", "expectedReturn": "2026-07-10"},
        {"id": "TX-VIS", "itemId": "LL-A", "type": "checkout", "user": "A Visitor",
         "ts": "2026-07-02", "expectedReturn": "2026-07-12"},
    ])
    summary = await run_backfill(apply=True, db=db)
    assert summary["collections"]["tx"] == {
        "total": 2, "lacking": 2, "resolved": 1, "ambiguous": 0,
        "unresolved": 1, "written": 1,
    }
    tx_by_id = {d["id"]: d for d in db.inv_tx_collection.docs}
    assert tx_by_id["TX-OLD"]["email"] == "jiming@uvic.ca"
    assert "email" not in tx_by_id["TX-VIS"]
    assert any(a["action"] == "backfill_owner_key_tx" for a in db.inv_audit_collection.inserted)


async def test_second_apply_pass_writes_nothing(db):
    await run_backfill(apply=True, db=db)
    db.inv_res_collection.updated.clear()
    db.inv_plaxis_collection.updated.clear()
    db.inv_audit_collection.inserted.clear()
    second = await run_backfill(apply=True, db=db)
    assert db.inv_res_collection.updated == []
    assert db.inv_plaxis_collection.updated == []
    assert db.inv_audit_collection.inserted == []
    assert second["collections"]["res"]["resolved"] == 0
    assert second["collections"]["res"]["written"] == 0
    assert second["collections"]["plaxis"]["written"] == 0
