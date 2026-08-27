"""Phase 6 — backup and restore (dry-run diff, merge vs replace, gates)."""

from datetime import datetime

import pytest
from fastapi import HTTPException

from app.routers import inventory as inv
from models import User

pytestmark = pytest.mark.unit


def _user(role="user"):
    return User(id="u1", email="x@uvic.ca", hashed_password="x", full_name="X", role=role)


class FakeColl:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.inserted, self.replaced, self.deleted = [], [], []

    def find(self, query=None, *a, **k):
        docs = [dict(d) for d in self.docs]

        async def gen():
            for d in docs:
                yield d
        return gen()

    async def insert_one(self, d):
        self.inserted.append(d)

    async def replace_one(self, flt, doc, upsert=False):
        self.replaced.append((flt, doc, upsert))
        for i, d in enumerate(self.docs):
            if d.get("id") == flt.get("id"):
                self.docs[i] = doc
                return
        if upsert:
            self.docs.append(doc)

    async def delete_many(self, flt):
        ids = set(flt["id"]["$in"])
        self.deleted.append(sorted(ids))
        self.docs = [d for d in self.docs if d.get("id") not in ids]


@pytest.fixture()
def fakes(monkeypatch):
    colls = {
        "inv_items_collection": FakeColl([{"id": "LL-A", "name": "Probe", "qty": 1, "updatedAt": datetime(2026, 8, 1)},
                                          {"id": "LL-B", "name": "Gone soon", "qty": 2}]),
        "inv_tx_collection": FakeColl(), "inv_res_collection": FakeColl(),
        "inv_plaxis_collection": FakeColl(), "inv_users_collection": FakeColl(),
        "inv_audit_collection": FakeColl(),
    }
    for name, c in colls.items():
        monkeypatch.setattr(inv, name, c)
    return colls


BACKUP = {
    "schemaVersion": 1, "exportedAt": "2026-08-21T12:00:00",
    "collections": {
        "items": [
            {"id": "LL-A", "name": "Probe (renamed)", "qty": 1, "updatedAt": "2026-08-01T00:00:00"},  # changed
            {"id": "LL-C", "name": "New one", "qty": 3},                                              # added
        ],
        "users": [{"id": "U-1", "name": "Someone", "email": "s@uvic.ca"}],
    },
}


# --- pure diff -----------------------------------------------------------------
def test_restore_diff_counts_added_changed_removed_per_mode():
    existing = [{"id": "LL-A", "name": "Probe", "qty": 1, "updatedAt": datetime(2026, 8, 1)},
                {"id": "LL-B", "name": "Gone soon", "qty": 2}]
    merge = inv.restore_diff(BACKUP["collections"]["items"], existing, "merge")
    assert (merge["added"], merge["changed"], merge["removed"]) == (1, 1, 0)
    assert merge["addedIds"] == ["LL-C"] and merge["changedIds"] == ["LL-A"]
    replace = inv.restore_diff(BACKUP["collections"]["items"], existing, "replace")
    assert (replace["added"], replace["changed"], replace["removed"]) == (1, 1, 1)
    assert replace["removedIds"] == ["LL-B"]


def test_restore_diff_treats_iso_strings_and_datetimes_as_equal():
    existing = [{"id": "X", "updatedAt": datetime(2026, 8, 1, 9, 30)}]
    same = [{"id": "X", "updatedAt": "2026-08-01T09:30:00"}]
    assert inv.restore_diff(same, existing, "merge")["changed"] == 0


# --- endpoints ------------------------------------------------------------------
async def test_backup_is_manager_only_and_carries_schema_version(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.export_backup(current_user=_user("user"))
    assert ei.value.status_code == 403
    out = await inv.export_backup(current_user=_user("professor"))
    assert out["schemaVersion"] == 1
    assert set(out["collections"]) == {"items", "tx", "res", "plaxis", "users", "audit"}
    assert [d["id"] for d in out["collections"]["items"]] == ["LL-A", "LL-B"]
    assert fakes["inv_audit_collection"].inserted[-1]["action"] == "backup"


async def test_restore_rejects_wrong_schema_version_and_unknown_collections(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.restore_backup({"backup": {"schemaVersion": 2, "collections": {}}}, current_user=_user("admin"))
    assert ei.value.status_code == 400 and "schemaVersion" in ei.value.detail
    with pytest.raises(HTTPException) as ei2:
        await inv.restore_backup({"backup": {"schemaVersion": 1, "collections": {"secrets": []}}},
                                 current_user=_user("admin"))
    assert ei2.value.status_code == 400


async def test_dry_run_reports_the_diff_and_writes_nothing(fakes):
    out = await inv.restore_backup({"backup": BACKUP, "mode": "replace", "dryRun": True},
                                   current_user=_user("professor"))
    assert out["dryRun"] is True
    assert out["diff"]["items"]["removed"] == 1 and out["diff"]["users"]["added"] == 1
    assert fakes["inv_items_collection"].replaced == [] and fakes["inv_items_collection"].deleted == []
    assert fakes["inv_audit_collection"].inserted == []  # nothing happened, nothing to audit


async def test_merge_upserts_by_id_and_never_deletes(fakes):
    out = await inv.restore_backup({"backup": BACKUP, "mode": "merge", "dryRun": False},
                                   current_user=_user("professor"))
    items = fakes["inv_items_collection"]
    assert sorted(d["id"] for d in items.docs) == ["LL-A", "LL-B", "LL-C"]   # LL-B survives
    assert next(d for d in items.docs if d["id"] == "LL-A")["name"] == "Probe (renamed)"
    assert isinstance(next(d for d in items.docs if d["id"] == "LL-A")["updatedAt"], datetime)  # ISO parsed
    assert items.deleted == []
    assert out["diff"]["items"]["removed"] == 0
    assert fakes["inv_audit_collection"].inserted[-1]["action"] == "restore"


async def test_replace_deletes_only_ids_absent_from_the_backup(fakes):
    await inv.restore_backup({"backup": BACKUP, "mode": "replace", "dryRun": False},
                             current_user=_user("professor"))
    items = fakes["inv_items_collection"]
    assert sorted(d["id"] for d in items.docs) == ["LL-A", "LL-C"]
    assert items.deleted == [["LL-B"]]
    # Collections absent from the backup are untouched (no drop).
    assert fakes["inv_tx_collection"].deleted == []


async def test_restore_is_manager_only(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.restore_backup({"backup": BACKUP, "dryRun": True}, current_user=_user("user"))
    assert ei.value.status_code == 403
