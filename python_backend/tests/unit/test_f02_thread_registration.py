"""Audit F-02 (2026-08-26): POST /api/assistants/threads/history must refuse
a second conversations row for an existing threadId (409), whoever asks --
a duplicate row lets a stranger take the id over once the owner deletes.
Also covers the read-only duplicate-check script's aggregation over a fake.
"""
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import threads as threads_mod
from app.scripts import check_duplicate_threadids as dup_script
from models import CreateThreadHistoryRequest, User
from tests.unit.test_chat_sharing import FakeColl

pytestmark = pytest.mark.unit

OWNER = User(id="uA", email="a@uvic.ca", hashed_password="x")
STRANGER = User(id="uC", email="c@uvic.ca", hashed_password="x")
T0 = datetime(2026, 8, 26, 10, 0, 0)


@pytest.fixture()
def convs(monkeypatch):
    coll = FakeColl([{"threadId": "th-1", "userId": "uA", "name": "one", "isGroup": False,
                      "members": ["uA"], "createdAt": T0, "updatedAt": T0}])
    monkeypatch.setattr(threads_mod, "conversations_collection", coll)
    return coll


def _req(thread_id, name="x"):
    return CreateThreadHistoryRequest(threadId=thread_id, name=name, isGroup=False)


async def test_first_registration_unaffected(convs):
    out = await threads_mod.create_thread_history(_req("th-new"), current_user=OWNER)
    assert out == {"success": True, "message": "Thread created in history"}
    rows = [d for d in convs.docs if d["threadId"] == "th-new"]
    assert len(rows) == 1 and rows[0]["userId"] == "uA" and rows[0]["members"] == ["uA"]


async def test_duplicate_by_another_user_is_409_and_writes_nothing(convs):
    with pytest.raises(HTTPException) as exc:
        await threads_mod.create_thread_history(_req("th-1", "dup"), current_user=STRANGER)
    assert exc.value.status_code == 409
    assert [d["userId"] for d in convs.docs if d["threadId"] == "th-1"] == ["uA"]


async def test_duplicate_by_the_owner_is_409_too(convs):
    with pytest.raises(HTTPException) as exc:
        await threads_mod.create_thread_history(_req("th-1"), current_user=OWNER)
    assert exc.value.status_code == 409
    assert len([d for d in convs.docs if d["threadId"] == "th-1"]) == 1


async def test_409_is_not_flattened_to_500(convs):
    # The blanket handler used to turn every HTTPException into a 500.
    with pytest.raises(HTTPException) as exc:
        await threads_mod.create_thread_history(_req("th-1"), current_user=STRANGER)
    assert exc.value.status_code != 500


# --- the read-only pre-check script -------------------------------------------
class _AggColl(FakeColl):
    """FakeColl + the $group/$match/$sort aggregation the script uses."""

    def aggregate(self, pipeline):
        counts = {}
        for d in self.docs:
            counts[d.get("threadId")] = counts.get(d.get("threadId"), 0) + 1
        out = [{"_id": k, "n": n} for k, n in counts.items() if n > 1]
        out.sort(key=lambda r: (-r["n"], str(r["_id"])))

        async def gen():
            for r in out:
                yield r
        return gen()

    async def count_documents(self, flt):
        return len(self.docs)


async def test_script_reports_duplicates_and_writes_nothing(capsys):
    coll = _AggColl([
        {"threadId": "th-1", "userId": "uA", "createdAt": T0},
        {"threadId": "th-1", "userId": "uC", "createdAt": T0},
        {"threadId": "th-2", "userId": "uB", "createdAt": T0},
    ])
    before = [dict(d) for d in coll.docs]
    n = await dup_script.run_check(db=SimpleNamespace(conversations_collection=coll))
    assert n == 1
    assert coll.docs == before and coll.updates == []
    out = capsys.readouterr().out
    assert "threadId='th-1' x2" in out and "duplicates exist" in out


async def test_script_clean_collection_returns_zero(capsys):
    coll = _AggColl([{"threadId": "th-1", "userId": "uA"}, {"threadId": "th-2", "userId": "uB"}])
    n = await dup_script.run_check(db=SimpleNamespace(conversations_collection=coll))
    assert n == 0
    assert "no duplicate threadIds" in capsys.readouterr().out
