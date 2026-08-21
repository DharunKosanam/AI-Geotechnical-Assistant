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
    """_supersede_plan reports counts/next-version and NEVER deletes. The match
    keys on canonicalTitle ALONE (web docs excluded) — projectTag is reported,
    not filtered on, so a re-upload under a different tag replaces instead of
    silently duplicating."""
    seen = {}

    class _Fake:
        async def count_documents(self, flt):
            seen["filter"] = flt
            return 7

        async def distinct(self, field, flt):
            if field == "version":
                return [1, 2]
            if field == "projectTag":
                return ["proj-x"]
            raise AssertionError(f"unexpected distinct field {field}")

        async def delete_many(self, *a, **k):  # must never be called at the gate
            raise AssertionError("supersede must not delete before the gate")

    monkeypatch.setattr(kb, "files_collection", _Fake())
    plan = await kb._supersede_plan("proj-x", "A Title")
    assert plan["would_delete_chunks"] == 7
    assert plan["next_version"] == 3  # max(1,2)+1
    assert seen["filter"] == {"category": "knowledge_base", "canonicalTitle": "A Title",
                              "canonicalUrl": {"$exists": False}}
    assert plan["prior_project_tags"] == ["proj-x"]
    assert plan["differing_project_tags"] == []  # same tag -> nothing to warn about


@pytest.mark.asyncio
async def test_supersede_plan_reports_differing_project_tags(monkeypatch):
    """The lab-inventory duplicate shape: same title, existing copy under a
    different tag. The plan must MATCH it (no silent duplicate) and name the
    differing tag so the confirm warning can say where it lives."""

    class _Fake:
        async def count_documents(self, flt):
            return 3

        async def distinct(self, field, flt):
            if field == "version":
                return [1]
            if field == "projectTag":
                return ["Lab inventory and Plaxis booking"]
            raise AssertionError(f"unexpected distinct field {field}")

        async def delete_many(self, *a, **k):
            raise AssertionError("supersede must not delete before the gate")

    monkeypatch.setattr(kb, "files_collection", _Fake())
    plan = await kb._supersede_plan("lab inventory", "Inventory")
    assert plan["would_delete_chunks"] == 3
    assert plan["differing_project_tags"] == ["Lab inventory and Plaxis booking"]
    assert plan["next_version"] == 2
