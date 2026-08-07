"""Email service (Phase 5): factory selection, no-raise contract, key hygiene.

No network: httpx.post is monkeypatched. The two invariants that matter:
send_email NEVER raises (a mail failure must degrade to False so
forgot-password still returns its generic 200), and the API key can never
reach a log line, even via a provider error body or exception message.
"""
import pytest

from app.core import config
from app.services import email_service
from app.services.email_service import (
    BrevoEmailSender,
    ConsoleEmailSender,
    ResendEmailSender,
    get_email_sender,
)


def test_factory_console_default(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "console")
    assert isinstance(get_email_sender(), ConsoleEmailSender)


def test_factory_real_providers(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_API_KEY", "k")
    monkeypatch.setattr(config, "EMAIL_FROM_ADDRESS", "noreply@example.com")
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "resend")
    assert isinstance(get_email_sender(), ResendEmailSender)
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "brevo")
    assert isinstance(get_email_sender(), BrevoEmailSender)


def test_factory_unknown_provider_raises(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "sendgrid")
    with pytest.raises(ValueError):
        get_email_sender()


def test_factory_missing_credentials_raise(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "resend")
    monkeypatch.setattr(config, "EMAIL_API_KEY", "")
    with pytest.raises(ValueError) as e:
        get_email_sender()
    assert "EMAIL_API_KEY" in str(e.value)

    monkeypatch.setattr(config, "EMAIL_API_KEY", "k")
    monkeypatch.setattr(config, "EMAIL_FROM_ADDRESS", "")
    with pytest.raises(ValueError) as e:
        get_email_sender()
    assert "EMAIL_FROM_ADDRESS" in str(e.value)


SECRET = "sk-test-secret-key-000"


def _configured(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_API_KEY", SECRET)
    monkeypatch.setattr(config, "EMAIL_FROM_ADDRESS", "noreply@example.com")
    monkeypatch.setattr(config, "EMAIL_FROM_NAME", "Test App")


def test_send_never_raises_and_scrubs_key(monkeypatch, capsys):
    """httpx raising an exception that QUOTES the key: send_email must return
    False and the logged line must not contain the key."""
    _configured(monkeypatch)

    def boom(**kwargs):
        raise RuntimeError("connect failed; header Bearer " + SECRET)

    monkeypatch.setattr(email_service.httpx, "post", boom)
    ok = ResendEmailSender().send_email("a@b.com", "s", "<p>h</p>", "t")
    out = capsys.readouterr().out
    assert ok is False
    assert SECRET not in out
    assert "[redacted]" in out


def test_send_non_2xx_returns_false_and_scrubs_body(monkeypatch, capsys):
    _configured(monkeypatch)

    class _Resp:
        is_success = False
        status_code = 401
        text = "unauthorized; presented key " + SECRET

    monkeypatch.setattr(email_service.httpx, "post", lambda **k: _Resp())
    ok = BrevoEmailSender().send_email("a@b.com", "s", "<p>h</p>", "t")
    out = capsys.readouterr().out
    assert ok is False
    assert SECRET not in out


def test_send_success_and_timeout_present(monkeypatch):
    _configured(monkeypatch)
    seen = {}

    class _Resp:
        is_success = True
        status_code = 200
        text = "ok"

    def fake_post(**kwargs):
        seen.update(kwargs)
        return _Resp()

    monkeypatch.setattr(email_service.httpx, "post", fake_post)
    assert ResendEmailSender().send_email("a@b.com", "s", "<p>h</p>", "t") is True
    assert seen["timeout"] == 10.0
    assert seen["json"]["to"] == ["a@b.com"]
    assert seen["headers"]["Authorization"] == "Bearer " + SECRET
