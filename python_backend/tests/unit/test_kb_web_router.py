"""Web-ingest router (Phase 3): registration gate, structured fetch errors,
URL dedup, refresh supersede keyed on canonicalUrl, provenance stamping.
Everything network/DB is monkeypatched — no Mongo, no HTTP."""
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core import config
from app.core.rate_limit import limiter, rate_limit_identify
from app.routers import kb_web
from app.services.web_fetch import FetchResult

PAGE_HTML = ("<html><head><title>Travel funding - UVic</title></head><body><main>"
             "<h1>Travel funding</h1>" + "<p>Amounts, eligibility and deadlines.</p>" * 30 +
             "</main></body></html>")
URL = "https://www.uvic.ca/graduatestudies/finances/travel-and-conference-funding/index.php"


def _ok_fetch(url, **kw):
    return FetchResult(requested_url=url, ok=True, url=URL, html=PAGE_HTML,
                       status_code=200, content_type="text/html",
                       size_bytes=len(PAGE_HTML))


class FakeCollection:
    def __init__(self):
        self.docs = []          # existing chunk docs find_one returns from
        self.inserted = []
        self.deleted_filters = []
        self.count = 0
        self.versions = []

    async def find_one(self, flt, proj=None):
        for d in self.docs:
            if all(self._match(d, k, v) for k, v in flt.items()):
                return d
        return None

    @staticmethod
    def _match(doc, key, val):
        if isinstance(val, dict) and "$ne" in val:
            return doc.get(key) != val["$ne"]
        return doc.get(key) == val

    async def count_documents(self, flt):
        return self.count

    async def distinct(self, field, flt):
        return self.versions

    async def delete_many(self, flt):
        self.deleted_filters.append(flt)
        return SimpleNamespace(deleted_count=self.count)

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return SimpleNamespace(inserted_id="x")


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setattr(config, "WEB_INGEST_ENABLED", True)
    monkeypatch.setattr(limiter, "enabled", False)

    fake = FakeCollection()
    monkeypatch.setattr(kb_web, "files_collection", fake)
    monkeypatch.setattr(kb_web, "fetch_web_page", _ok_fetch)

    ingest_calls = []

    async def fake_ingest(filename, content, category=None, user_id=None,
                          pre_extracted_pages=None, provenance=None, **kw):
        ingest_calls.append({"filename": filename, "category": category,
                             "user_id": user_id, "pages": pre_extracted_pages,
                             "provenance": provenance})
        return {"chunks_created": 3}

    monkeypatch.setattr(kb_web, "ingest_document", fake_ingest)
    monkeypatch.setattr(kb_web, "ingest_try_acquire", lambda: True)
    monkeypatch.setattr(kb_web, "ingest_release", lambda: None)
    monkeypatch.setattr(kb_web.val, "reserve_hash", lambda h: True)
    monkeypatch.setattr(kb_web.val, "release_hash", lambda h: None)

    async def fake_centroid(force=False):
        return None

    monkeypatch.setattr(kb_web.val, "get_kb_centroid", fake_centroid)

    async def fake_audit(*a, **k):
        return None

    monkeypatch.setattr(kb_web, "_audit", fake_audit)

    app = FastAPI()
    app.state.limiter = limiter
    kb_web.register(app)
    app.dependency_overrides[rate_limit_identify] = lambda: SimpleNamespace(
        id="u1", email="student@uvic.ca", full_name="Test Student")
    client = TestClient(app)
    return client, fake, ingest_calls


# --- registration gate --------------------------------------------------------
def test_register_absent_when_flag_off(monkeypatch):
    monkeypatch.setattr(config, "WEB_INGEST_ENABLED", False)
    app = FastAPI()
    kb_web.register(app)
    # The route is ABSENT (404 from the default handler), not present-and-404ing
    # from our gate — this FastAPI wraps included routers, so probe by request.
    r = TestClient(app).get("/api/kb/web/status")
    assert r.status_code == 404


def test_register_present_when_flag_on(monkeypatch):
    monkeypatch.setattr(config, "WEB_INGEST_ENABLED", True)
    app = FastAPI()
    kb_web.register(app)
    r = TestClient(app).get("/api/kb/web/status")
    assert r.status_code == 200
    assert r.json()["enabled"] is True


def test_call_time_gate(monkeypatch):
    monkeypatch.setattr(config, "WEB_INGEST_ENABLED", False)
    with pytest.raises(HTTPException) as e:
        kb_web._require_enabled()
    assert e.value.status_code == 404


# --- supersede plan (dry run) -------------------------------------------------
@pytest.mark.asyncio
async def test_web_supersede_plan_keyed_on_url(monkeypatch):
    fake = FakeCollection()
    fake.count, fake.versions = 5, [1, 2]
    monkeypatch.setattr(kb_web, "files_collection", fake)
    plan = await kb_web._web_supersede_plan(URL)
    assert plan["filter"] == {"category": "knowledge_base", "canonicalUrl": URL}
    assert plan["would_delete_chunks"] == 5 and plan["next_version"] == 3
    assert fake.deleted_filters == []          # dry run never deletes


# --- preview ------------------------------------------------------------------
def test_preview_returns_title_and_text(app_client):
    client, fake, _ = app_client
    r = client.post("/api/kb/web/preview", json={"url": URL})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resolvedUrl"] == URL
    assert body["title"] == "Travel funding - UVic"
    assert "Amounts, eligibility and deadlines." in body["preview"]
    assert body["ingestable"] is True
    assert body["alreadyIngested"] is None


def test_preview_structured_fetch_error(app_client, monkeypatch):
    client, _, _ = app_client

    def walled(url, **kw):
        return FetchResult(requested_url=url, ok=False, error="login_wall",
                           message="behind NetLink", url=url)

    monkeypatch.setattr(kb_web, "fetch_web_page", walled)
    r = client.post("/api/kb/web/preview", json={"url": URL})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "login_wall"


def test_preview_reports_already_ingested(app_client):
    client, fake, _ = app_client
    fake.docs.append({"category": "knowledge_base", "canonicalUrl": URL,
                      "chunkIndex": 0, "canonicalTitle": "Old title",
                      "fetchedAt": datetime(2026, 8, 1, 12, 0), "version": 1,
                      "contentHash": "aaaa"})
    body = client.post("/api/kb/web/preview", json={"url": URL}).json()
    assert body["alreadyIngested"]["canonicalTitle"] == "Old title"
    assert body["alreadyIngested"]["fetchedAt"].startswith("2026-08-01")


# --- ingest -------------------------------------------------------------------
def test_ingest_requires_project_and_permission(app_client):
    client, _, _ = app_client
    r = client.post("/api/kb/web/ingest", json={"url": URL})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "missing_project"
    r = client.post("/api/kb/web/ingest", json={"url": URL, "project": "funding"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "permission_not_confirmed"


def test_ingest_happy_path_stamps_web_provenance(app_client):
    client, fake, ingest_calls = app_client
    r = client.post("/api/kb/web/ingest", json={
        "url": URL, "project": "funding", "permissionConfirmed": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "indexed" and body["chunkCount"] == 3
    assert body["canonicalUrl"] == URL and body["version"] == 1
    assert body["previousFetchedAt"] is None and body["contentChanged"] is True

    call = ingest_calls[0]
    assert call["filename"] == URL and call["category"] == "knowledge_base"
    prov = call["provenance"]
    assert prov["sourceFormat"] == "web"
    assert prov["canonicalUrl"] == URL
    assert isinstance(prov["fetchedAt"], datetime)
    assert prov["canonicalTitle"] == "Travel funding - UVic"
    assert prov["permissionConfirmed"] is True and prov["docType"] == "web_page"

    batch = fake.inserted[0]
    assert batch["sourceFormat"] == "web" and batch["canonicalUrl"] == URL
    assert batch["status"] == "indexed"


def test_ingest_rejects_already_ingested_without_refresh(app_client):
    client, fake, ingest_calls = app_client
    fake.docs.append({"category": "knowledge_base", "canonicalUrl": URL,
                      "chunkIndex": 0, "canonicalTitle": "Old title",
                      "fetchedAt": datetime(2026, 8, 1), "version": 1,
                      "contentHash": "aaaa"})
    r = client.post("/api/kb/web/ingest", json={
        "url": URL, "project": "funding", "permissionConfirmed": True})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "already_ingested"
    assert ingest_calls == [] and fake.deleted_filters == []


def test_ingest_refresh_supersedes_by_url(app_client):
    client, fake, ingest_calls = app_client
    fake.docs.append({"category": "knowledge_base", "canonicalUrl": URL,
                      "chunkIndex": 0, "canonicalTitle": "Old title",
                      "fetchedAt": datetime(2026, 8, 1, 9, 30), "version": 1,
                      "contentHash": "aaaa"})
    fake.count, fake.versions = 4, [1]
    r = client.post("/api/kb/web/ingest", json={
        "url": URL, "project": "funding", "permissionConfirmed": True,
        "refresh": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 2 and body["superseded"] == 4
    assert body["previousFetchedAt"].startswith("2026-08-01")
    assert body["contentChanged"] is True
    assert fake.deleted_filters == [{"category": "knowledge_base", "canonicalUrl": URL}]
    assert len(ingest_calls) == 1


def test_ingest_rejects_duplicate_content_other_url(app_client):
    client, fake, ingest_calls = app_client

    async def find_one(flt, proj=None):
        # No doc for this URL; a doc with the same contentHash elsewhere.
        if "contentHash" in flt:
            return {"canonicalTitle": "Mirror copy", "canonicalUrl": "https://uvic.ca/other"}
        return None

    fake.find_one = find_one
    r = client.post("/api/kb/web/ingest", json={
        "url": URL, "project": "funding", "permissionConfirmed": True})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "duplicate_content"
    assert ingest_calls == []
