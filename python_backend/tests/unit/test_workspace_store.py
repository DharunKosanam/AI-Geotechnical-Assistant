"""Unit tests for the in-memory per-user GeoPilot session store."""

import pytest

from app.workspace import store

pytestmark = pytest.mark.unit


@pytest.fixture
def user():
    uid = "unit-test-user"
    store.clear_user(uid)
    yield uid
    store.clear_user(uid)


def test_add_and_list_documents_most_recent_first(user):
    a = store.add_document(user, "first.cpt", "D=0.2,QC=1")
    b = store.add_document(user, "second.cpt", "D=0.4,QC=2")
    docs = store.list_documents(user)
    assert [d.id for d in docs] == [b.id, a.id]  # most recent first


def test_latest_document_with_extension(user):
    store.add_document(user, "notes.txt", "hello")
    old = store.add_document(user, "old.cpt", "x")
    new = store.add_document(user, "new.CPT", "y")  # extension is case-insensitive
    latest = store.latest_document_with_extension(user, ".cpt")
    assert latest.id == new.id
    assert old.extension == ".cpt"


def test_latest_document_none_when_absent(user):
    store.add_document(user, "notes.txt", "hello")
    assert store.latest_document_with_extension(user, ".cpt") is None


def test_remove_document(user):
    doc = store.add_document(user, "x.cpt", "x")
    assert store.remove_document(user, doc.id) is True
    assert store.remove_document(user, doc.id) is False
    assert store.list_documents(user) == []


def test_documents_are_user_scoped(user):
    other = "another-user"
    store.clear_user(other)
    store.add_document(user, "mine.cpt", "x")
    assert store.list_documents(other) == []
    store.clear_user(other)


def test_store_and_get_result(user):
    rid = store.store_result(user, {"foo": "bar"})
    assert store.get_result(user, rid) == {"foo": "bar"}
    assert store.get_result(user, "missing") is None


def test_public_record_shape(user):
    doc = store.add_document(user, "sounding.CPT", "x")
    pub = doc.public()
    assert pub == {
        "id": doc.id,
        "filename": "sounding.CPT",
        "extension": ".cpt",
        "status": "ready",
    }
