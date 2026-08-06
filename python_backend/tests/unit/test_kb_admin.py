"""Phase 6: admin gating + removal-CLI filter construction (pure, no DB)."""
from types import SimpleNamespace

from app.routers import kb
from app.scripts import kb_remove


def test_is_admin():
    assert kb._is_admin(SimpleNamespace(role="admin")) is True
    assert kb._is_admin(SimpleNamespace(role="professor")) is True
    assert kb._is_admin(SimpleNamespace(role="user")) is False
    assert kb._is_admin(SimpleNamespace()) is False  # missing role -> treated as user


def test_cli_filters_by_batch():
    desc, cf, bf = kb_remove._filters(SimpleNamespace(batch="abc", uploader=None, project=None))
    assert cf == {"category": "knowledge_base", "batchId": "abc"}
    assert bf == {"docType": "kb_batch", "batchId": "abc"}
    assert "batch=abc" in desc


def test_cli_filters_by_uploader_and_project():
    _, cf, bf = kb_remove._filters(SimpleNamespace(batch=None, uploader="U1", project=None))
    assert cf["uploaderId"] == "U1" and cf["category"] == "knowledge_base"
    assert bf == {"docType": "kb_batch", "uploaderId": "U1"}

    _, cf, bf = kb_remove._filters(SimpleNamespace(batch=None, uploader=None, project="Site B"))
    assert cf["projectTag"] == "Site B"
    assert bf == {"docType": "kb_batch", "projectTag": "Site B"}
