"""Shared password validator, exercised AT the endpoints.

A too-short password must be rejected with 422 by BOTH /auth/signup and
/auth/reset-password. The 422 comes from the pydantic field validator during
body validation, BEFORE any handler code runs -- so these tests touch no
database, mint no tokens, and consume no rate-limit budget.
"""
import pytest

from app.core import config


@pytest.mark.integration
async def test_signup_endpoint_rejects_short_password(async_client):
    res = await async_client.post(
        "/auth/signup",
        json={"email": "vp@example.com", "password": "short12"},
    )
    assert res.status_code == 422
    assert "at least 8 characters" in res.text


@pytest.mark.integration
async def test_reset_endpoint_rejects_short_password(async_client, monkeypatch):
    monkeypatch.setattr(config, "PASSWORD_RESET_ENABLED", True)
    res = await async_client.post(
        "/auth/reset-password",
        json={"token": "whatever", "new_password": "short12"},
    )
    assert res.status_code == 422
    assert "at least 8 characters" in res.text


@pytest.mark.integration
async def test_endpoints_reject_whitespace_only_password(async_client, monkeypatch):
    monkeypatch.setattr(config, "PASSWORD_RESET_ENABLED", True)
    res = await async_client.post(
        "/auth/signup",
        json={"email": "vp@example.com", "password": "        "},
    )
    assert res.status_code == 422
    res = await async_client.post(
        "/auth/reset-password",
        json={"token": "whatever", "new_password": "        "},
    )
    assert res.status_code == 422
