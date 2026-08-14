"""DIAGRAM_EDITOR_ENABLED capability handshake (Phase 1).

The flag surfaces to the frontend ONLY through /api/upload/config:
  - flag ON  -> the payload gains "diagramEditor": True (additive);
  - flag OFF -> the field is OMITTED entirely (not false), so the response is
    byte-identical to before the feature existed and an old or flag-off
    frontend renders today's plain "+" button unchanged.

Deterministic: both flags monkeypatched, no network, no DB.
"""

import pytest

from app.core import config
from app.routers import files as files_router

pytestmark = pytest.mark.unit


async def test_upload_config_flag_off_omits_diagram_field(monkeypatch):
    monkeypatch.setattr(config, "VISION_EXTRACTION_ENABLED", False)
    monkeypatch.setattr(config, "DIAGRAM_EDITOR_ENABLED", False)
    out = await files_router.upload_config(current_user=object())
    # Omitted, not false: exact-equality proves no new key leaks out flag-off.
    assert out == {
        "extensions": [".pdf", ".docx", ".xlsx", ".xls", ".csv", ".pptx"],
        "label": "PDF, DOCX, XLSX, XLS, CSV, PPTX",
    }


async def test_upload_config_flag_on_adds_diagram_field(monkeypatch):
    monkeypatch.setattr(config, "VISION_EXTRACTION_ENABLED", False)
    monkeypatch.setattr(config, "DIAGRAM_EDITOR_ENABLED", True)
    out = await files_router.upload_config(current_user=object())
    assert out["diagramEditor"] is True
    # Additive only: the existing upload-type contract is untouched.
    assert out["extensions"] == [".pdf", ".docx", ".xlsx", ".xls", ".csv", ".pptx"]
    assert out["label"] == "PDF, DOCX, XLSX, XLS, CSV, PPTX"
