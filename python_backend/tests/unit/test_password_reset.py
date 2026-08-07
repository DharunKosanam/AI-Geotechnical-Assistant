"""Password reset (Phases 2+3): flag gate, token_version bump semantics.

The critical case: a user doc with NO token_version field at all. The
effective default everywhere (get_current_user, token minting) is 1, so the
reset bump MUST land the stored value at 2 -- an $inc on the missing field
would create it AT 1, equal to the default, and silently fail to invalidate
outstanding sessions. _apply_password_reset therefore reads
effective_token_version() and writes value + 1 with $set.
"""
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core import config
from app.routers import auth
from app.routers.auth import ResetPasswordRequest
from app.services.auth_service import effective_token_version
from models import UserCreate, validate_password_rules


class _FakeUsers:
    """Captures the update_one call so the test can inspect the operators."""

    def __init__(self):
        self.filter = None
        self.update = None

    async def update_one(self, flt, update):
        self.filter = flt
        self.update = update

        class _Result:
            matched_count = 1

        return _Result()


def test_require_reset_enabled_404_when_off(monkeypatch):
    monkeypatch.setattr(config, "PASSWORD_RESET_ENABLED", False)
    with pytest.raises(HTTPException) as e:
        auth._require_reset_enabled()
    assert e.value.status_code == 404


def test_require_reset_enabled_ok_when_on(monkeypatch):
    monkeypatch.setattr(config, "PASSWORD_RESET_ENABLED", True)
    auth._require_reset_enabled()  # must not raise


def test_effective_token_version_defaults():
    assert effective_token_version(None) == 1
    assert effective_token_version(1) == 1
    assert effective_token_version(7) == 7


@pytest.mark.asyncio
async def test_reset_bumps_missing_token_version_to_2(monkeypatch):
    """User doc with NO token_version field: the bump must write 2, not 1."""
    fake = _FakeUsers()
    monkeypatch.setattr(auth, "users_collection", fake)

    user_doc = {"_id": "abc123", "email": "x@example.com"}  # field absent
    ok = await auth._apply_password_reset(user_doc, "new-password")

    assert ok is True
    assert "$inc" not in fake.update  # $inc would create the field AT 1
    assert fake.update["$set"]["token_version"] == 2
    assert fake.filter == {"_id": "abc123"}
    # The new hash must be a real bcrypt hash of the new password, set in the
    # SAME atomic update as the version bump.
    assert fake.update["$set"]["hashed_password"].startswith("$2b$")


@pytest.mark.asyncio
async def test_reset_bumps_explicit_token_version(monkeypatch):
    """token_version: 5 -> 6 (and None behaves like the missing field)."""
    fake = _FakeUsers()
    monkeypatch.setattr(auth, "users_collection", fake)

    await auth._apply_password_reset({"_id": "u1", "token_version": 5}, "pw")
    assert fake.update["$set"]["token_version"] == 6

    await auth._apply_password_reset({"_id": "u2", "token_version": None}, "pw")
    assert fake.update["$set"]["token_version"] == 2


# ---------------------------------------------------------------------------
# Shared password rules: ONE validator (models.validate_password_rules) gates
# both UserCreate.password (signup) and ResetPasswordRequest.new_password
# (reset). These model-level rejections ARE the endpoint rejections: FastAPI
# validates the body against these models before any handler code runs.
# ---------------------------------------------------------------------------


def test_password_rules_function():
    assert validate_password_rules("longenough") == "longenough"
    assert validate_password_rules("has spaces ok") == "has spaces ok"  # no trim
    with pytest.raises(ValueError):
        validate_password_rules("")
    with pytest.raises(ValueError):
        validate_password_rules("        ")  # 8 chars but whitespace-only
    with pytest.raises(ValueError):
        validate_password_rules("short12")  # 7 chars


def test_signup_model_rejects_short_password():
    with pytest.raises(ValidationError):
        UserCreate(email="a@example.com", password="short12")
    with pytest.raises(ValidationError):
        UserCreate(email="a@example.com", password="")
    with pytest.raises(ValidationError):
        UserCreate(email="a@example.com", password="        ")
    UserCreate(email="a@example.com", password="longenough")  # must not raise


def test_reset_model_rejects_short_password():
    with pytest.raises(ValidationError):
        ResetPasswordRequest(token="t", new_password="short12")
    with pytest.raises(ValidationError):
        ResetPasswordRequest(token="t", new_password="")
    with pytest.raises(ValidationError):
        ResetPasswordRequest(token="t", new_password="        ")
    ResetPasswordRequest(token="t", new_password="longenough")  # must not raise
