"""Phase 4: KB upload router — feature-flag gate + supersede-plan shape."""
import pytest
from fastapi import HTTPException

from app.core import config
from app.routers import kb


def test_require_enabled_404_when_off(monkeypatch):
    monkeypatch.setattr(config, "KB_UPLOAD_ENABLED", False)
    with pytest.raises(HTTPException) as e:
        kb._require_enabled()
    assert e.value.status_code == 404


def test_require_enabled_ok_when_on(monkeypatch):
    monkeypatch.setattr(config, "KB_UPLOAD_ENABLED", True)
    kb._require_enabled()  # must not raise


@pytest.mark.asyncio
async def test_supersede_plan_dry_run(monkeypatch):
    """_supersede_plan reports counts/next-version and NEVER deletes."""
    seen = {}

    class _Fake:
        async def count_documents(self, flt):
            seen["filter"] = flt
            return 7

        async def distinct(self, field, flt):
            return [1, 2]

        async def delete_many(self, *a, **k):  # must never be called at the gate
            raise AssertionError("supersede must not delete before the gate")

    monkeypatch.setattr(kb, "files_collection", _Fake())
    plan = await kb._supersede_plan("proj-x", "A Title")
    assert plan["would_delete_chunks"] == 7
    assert plan["next_version"] == 3  # max(1,2)+1
    assert seen["filter"] == {"category": "knowledge_base", "projectTag": "proj-x", "canonicalTitle": "A Title"}
