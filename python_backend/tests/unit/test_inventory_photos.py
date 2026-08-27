"""Phase 8 — damage photos (INVENTORY_PHOTOS_ENABLED): content-sniffed upload
into the existing files collection, flag-gated serving, tx reference."""

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.core import config
from app.routers import inventory as inv
from app.routers.inventory import sniff_image_mime
from models import User

pytestmark = pytest.mark.unit

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 64


def _user(role="user"):
    return User(id="u1", email="x@uvic.ca", hashed_password="x", full_name="X", role=role)


class FakeUpload:
    def __init__(self, data: bytes, filename="photo.jpg"):
        self._data, self.filename = data, filename

    async def read(self, n=-1):
        return self._data if n < 0 else self._data[:n]


class _Inserted:
    def __init__(self):
        self.inserted_id = ObjectId()


class FakeFiles:
    def __init__(self):
        self.docs = {}

    async def insert_one(self, d):
        r = _Inserted()
        self.docs[r.inserted_id] = d
        return r

    async def find_one(self, q, *a, **k):
        d = self.docs.get(q.get("_id"))
        return d if d and d.get("category") == q.get("category") else None


class FakeColl:
    def __init__(self, doc=None):
        self.doc, self.inserted = doc, []

    async def find_one(self, *a, **k):
        return self.doc

    async def insert_one(self, d):
        self.inserted.append(d)

    async def update_one(self, *a, **k):
        pass


@pytest.fixture()
def fakes(monkeypatch):
    files = FakeFiles()
    monkeypatch.setattr(inv, "files_collection", files)
    monkeypatch.setattr(inv, "inv_audit_collection", FakeColl())
    monkeypatch.setattr(inv, "inv_users_collection", FakeColl(doc=None))
    monkeypatch.setattr(inv, "inv_items_collection", FakeColl(doc={"id": "LL-A", "kind": "equipment", "qty": 1}))
    tx = FakeColl()
    monkeypatch.setattr(inv, "inv_tx_collection", tx)
    monkeypatch.setattr(inv, "_RESOURCES", {"tx": (tx, inv._TX_FIELDS), "items": (inv.inv_items_collection, inv._ITEM_FIELDS)})
    monkeypatch.setattr(config, "INVENTORY_PHOTOS_ENABLED", True)
    monkeypatch.setattr(config, "INVENTORY_PHOTO_MAX_BYTES", 1024)
    return files


@pytest.mark.parametrize("head,mime", [(JPEG, "image/jpeg"), (PNG, "image/png"), (WEBP, "image/webp")])
def test_sniff_recognises_jpeg_png_webp_by_content(head, mime):
    assert sniff_image_mime(head[:16]) == mime


def test_sniff_rejects_non_images_regardless_of_name():
    assert sniff_image_mime(b"%PDF-1.7 ....") is None
    assert sniff_image_mime(b"GIF89a......") is None   # not in the accepted set
    assert sniff_image_mime(b"") is None


async def test_upload_404s_when_the_flag_is_off(fakes, monkeypatch):
    monkeypatch.setattr(config, "INVENTORY_PHOTOS_ENABLED", False)
    with pytest.raises(HTTPException) as ei:
        await inv.upload_photo(file=FakeUpload(JPEG), current_user=_user())
    assert ei.value.status_code == 404


async def test_upload_rejects_oversize_and_non_image_by_content(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.upload_photo(file=FakeUpload(JPEG + b"\x00" * 2000), current_user=_user())
    assert ei.value.status_code == 413
    with pytest.raises(HTTPException) as ei2:
        await inv.upload_photo(file=FakeUpload(b"%PDF-1.7" + b"\x00" * 40, filename="totally.jpg"), current_user=_user())
    assert ei2.value.status_code == 415   # extension lies; content decides


async def test_upload_stores_inline_in_files_collection_and_serves_it(fakes):
    out = await inv.upload_photo(file=FakeUpload(PNG, filename="crack<>.png"), current_user=_user())
    assert out["mimetype"] == "image/png" and out["url"].endswith(out["photoId"])
    stored = fakes.docs[ObjectId(out["photoId"])]
    assert stored["category"] == "inventory_photo" and stored["content"] == PNG
    assert stored["filename"] == "crack_.png"  # sanitised (run of unsafe chars -> one _)
    resp = await inv.get_photo(out["photoId"], current_user=_user("user"))  # any member can view
    assert resp.media_type == "image/png" and resp.body == PNG


async def test_get_photo_404s_for_bad_or_unknown_ids(fakes):
    for pid in ("not-an-id", str(ObjectId())):
        with pytest.raises(HTTPException) as ei:
            await inv.get_photo(pid, current_user=_user())
        assert ei.value.status_code == 404


async def test_damage_tx_requires_a_real_photo_reference(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.create_resource("tx", {"type": "damage", "itemId": "LL-A", "photoId": str(ObjectId())},
                                  current_user=_user())
    assert ei.value.status_code == 400
    up = await inv.upload_photo(file=FakeUpload(JPEG), current_user=_user())
    out = await inv.create_resource("tx", {"type": "damage", "itemId": "LL-A", "photoId": up["photoId"]},
                                    current_user=_user())
    assert out["photoId"] == up["photoId"]
    # a photo on a non-damage transaction is dropped, not stored
    co = await inv.create_resource("tx", {"type": "checkout", "itemId": "LL-A", "qty": 1, "photoId": up["photoId"]},
                                   current_user=_user())
    assert "photoId" not in co
