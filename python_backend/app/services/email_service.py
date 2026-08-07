"""
Email sending interface (password reset).

Defines the EmailSender interface and three implementations selected by
EMAIL_PROVIDER:
  * console (default) -- prints the full message to stdout instead of sending;
    enough to develop and test the reset flow end-to-end locally (the reset
    URL appears in the server log).
  * resend -- Resend HTTP API (https://api.resend.com/emails).
  * brevo  -- Brevo HTTP API (https://api.brevo.com/v3/smtp/email).

get_email_sender() rejects any EMAIL_PROVIDER value it does not recognize so
a misconfigured deployment fails loudly rather than silently dropping mail.

HTTP senders never raise out of send_email (they log and return False -- the
forgot-password endpoint must return its generic 200 regardless) and use an
explicit 10s timeout so a hung provider cannot hold a request worker. The
API key is read from config at call time and must NEVER appear in logs or
error messages; everything logged goes through _scrub() as a backstop.
"""
from abc import ABC, abstractmethod

import httpx

from app.core import config

# One ceiling for every provider HTTP call (connect + read combined).
EMAIL_HTTP_TIMEOUT_SECONDS = 10.0


def _scrub(text: str) -> str:
    """Remove the configured API key from a string before it is logged.

    Defense in depth: no current log line includes request headers, but a
    provider error body or an exception message must never be able to leak
    the key even if a future provider echoes it back.
    """
    key = config.EMAIL_API_KEY
    if key:
        text = text.replace(key, "[redacted]")
    return text


class EmailSender(ABC):
    """Interface every email provider implements."""

    @abstractmethod
    def send_email(self, to: str, subject: str, html: str, text: str) -> bool:
        """Send one message. Returns True on success, False on failure.

        Both an HTML body and a plain-text body are required so real
        providers can send a proper multipart message; implementations must
        not raise on delivery failure -- they return False so the caller
        decides how to respond.
        """
        raise NotImplementedError


class ConsoleEmailSender(EmailSender):
    """Prints the full message (headers + both bodies) to stdout.

    The whole point is that any URL in the body -- the reset link -- lands
    in the server log where a developer can copy it, so the entire message
    is printed verbatim, never truncated.
    """

    def send_email(self, to: str, subject: str, html: str, text: str) -> bool:
        sep = "=" * 60
        print(sep)
        print("[email:console] To: " + to)
        print("[email:console] Subject: " + subject)
        print("[email:console] Text body:")
        print(text)
        print("[email:console] HTML body:")
        print(html)
        print(sep)
        return True


class _HttpEmailSender(EmailSender):
    """Shared plumbing for the HTTP API providers.

    Subclasses supply the endpoint, headers, and payload shape; this base
    owns the contract: explicit timeout, treat any 2xx as success, and NEVER
    let an exception escape send_email -- a mail failure must degrade to
    "return False", not break the calling endpoint.
    """

    provider_name = "http"

    def _request_args(self, to: str, subject: str, html: str, text: str) -> dict:
        """Return kwargs for httpx.post: {url, headers, json}."""
        raise NotImplementedError

    def send_email(self, to: str, subject: str, html: str, text: str) -> bool:
        try:
            args = self._request_args(to, subject, html, text)
            response = httpx.post(timeout=EMAIL_HTTP_TIMEOUT_SECONDS, **args)
            if response.is_success:
                return True
            # Status + a short scrubbed body snippet; the key never appears
            # in a body, but _scrub is the backstop either way.
            print(
                "[email:%s] send failed: HTTP %d %s"
                % (self.provider_name, response.status_code,
                   _scrub(response.text[:200]))
            )
            return False
        except Exception as exc:
            # Exception text can quote the request; scrub before logging.
            print(
                "[email:%s] send raised %s: %s"
                % (self.provider_name, type(exc).__name__,
                   _scrub(str(exc)[:200]))
            )
            return False


class ResendEmailSender(_HttpEmailSender):
    """Resend (https://resend.com) -- POST /emails, Bearer auth."""

    provider_name = "resend"

    def _request_args(self, to: str, subject: str, html: str, text: str) -> dict:
        return {
            "url": "https://api.resend.com/emails",
            "headers": {
                "Authorization": "Bearer " + config.EMAIL_API_KEY,
                "Content-Type": "application/json",
            },
            "json": {
                "from": "%s <%s>"
                % (config.EMAIL_FROM_NAME, config.EMAIL_FROM_ADDRESS),
                "to": [to],
                "subject": subject,
                "html": html,
                "text": text,
            },
        }


class BrevoEmailSender(_HttpEmailSender):
    """Brevo (https://brevo.com) -- POST /v3/smtp/email, api-key header."""

    provider_name = "brevo"

    def _request_args(self, to: str, subject: str, html: str, text: str) -> dict:
        return {
            "url": "https://api.brevo.com/v3/smtp/email",
            "headers": {
                "api-key": config.EMAIL_API_KEY,
                "Content-Type": "application/json",
            },
            "json": {
                "sender": {
                    "name": config.EMAIL_FROM_NAME,
                    "email": config.EMAIL_FROM_ADDRESS,
                },
                "to": [{"email": to}],
                "subject": subject,
                "htmlContent": html,
                "textContent": text,
            },
        }


def get_email_sender() -> EmailSender:
    """Construct the EmailSender selected by config.EMAIL_PROVIDER.

    Read at call time (not import time) so tests can toggle the provider on
    the config module without re-import, matching the feature-flag pattern
    used elsewhere in config.py.

    For the real providers, missing credentials fail HERE with a clear
    message (naming the missing VARIABLE, never any value) instead of as an
    opaque 401 from the provider on every send.
    """
    provider = config.EMAIL_PROVIDER
    if provider == "console":
        return ConsoleEmailSender()
    if provider in ("resend", "brevo"):
        if not config.EMAIL_API_KEY:
            raise ValueError(
                "EMAIL_PROVIDER '" + provider + "' requires EMAIL_API_KEY to be set."
            )
        if not config.EMAIL_FROM_ADDRESS:
            raise ValueError(
                "EMAIL_PROVIDER '" + provider + "' requires EMAIL_FROM_ADDRESS "
                "to be set."
            )
        return ResendEmailSender() if provider == "resend" else BrevoEmailSender()
    raise ValueError(
        "Unsupported EMAIL_PROVIDER '" + str(provider) + "': expected 'console', "
        "'resend', or 'brevo'."
    )
