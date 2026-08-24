"""Web-ingest fetcher (Phase 1): allowlist matching, address blocking,
login-wall detection, and the redirect/allowlist/limit semantics via a mocked
transport (no network). The live-network checks are in
scripts/verify_web_fetch.py; nothing here goes online."""
import httpx
import ipaddress

import pytest

from app.services import web_fetch as wf


# --- allowlist: registrable-domain style, never substring ---------------------
@pytest.mark.parametrize("host,ok", [
    ("uvic.ca", True),
    ("www.uvic.ca", True),
    ("gss.uvic.ca", True),
    ("WWW.UVIC.CA", True),
    ("uvic.ca.", True),                 # trailing dot normalised
    ("4163.cupe.ca", True),
    ("uvic.ca.evil.com", False),        # the substring trap
    ("evil-uvic.ca.com", False),
    ("notuvic.ca", False),              # suffix without a label boundary
    ("cupe.ca", False),                 # parent of an allowed subdomain
    ("cupe4163.ca", False),             # the e-mail domain, NOT the website
    ("", False),
])
def test_host_allowed(host, ok):
    assert wf.host_allowed(host, ["uvic.ca", "4163.cupe.ca"]) is ok


# --- address blocking ---------------------------------------------------------
@pytest.mark.parametrize("addr,blocked", [
    ("127.0.0.1", True), ("10.0.0.1", True), ("172.16.5.5", True),
    ("192.168.1.1", True), ("169.254.169.254", True), ("100.64.0.1", True),
    ("0.0.0.0", True), ("::1", True), ("fe80::1", True), ("fc00::1", True),
    ("::ffff:10.0.0.1", True),          # v4-mapped v6 unwrapped
    ("142.104.197.10", False),          # uvic.ca (public)
    ("8.8.8.8", False),
    ("2607:f8b0::1", False),
])
def test_blocked_ip(addr, blocked):
    assert wf._blocked_ip(ipaddress.ip_address(addr)) is blocked


def test_resolve_ip_literal_private():
    code, _ = wf.resolve_and_check_host("192.168.0.10")
    assert code == wf.PRIVATE_ADDRESS


def test_resolve_unknown_host_is_dns_failure():
    code, _ = wf.resolve_and_check_host("definitely-not-a-real-host.invalid")
    assert code == wf.DNS_FAILURE


# --- login-wall detection -----------------------------------------------------
def test_login_wall_auth_host():
    walled, why = wf.detect_login_wall("login.uvic.ca", "<html><body>x</body></html>")
    assert walled and "login.uvic.ca" in why


def test_login_wall_password_form():
    html = "<html><body><form><input type='password' name='p'></form></body></html>"
    walled, why = wf.detect_login_wall("www.uvic.ca", html)
    assert walled and "password" in why


def test_login_wall_short_signin_body():
    html = "<html><head><title>Sign in with your NetLink ID</title></head><body>Please sign in.</body></html>"
    walled, _ = wf.detect_login_wall("www.uvic.ca", html)
    assert walled


def test_login_wall_js_redirect_stub():
    """Brightspace-style interstitial: 272 bytes, no form, no keywords in the
    de-tagged body — the tell is the login URL inside the script."""
    html = ("<!DOCTYPE html><html><head><meta charset='utf-8' /><script>"
            "window.location.replace('/d2l/login?sessionExpired=0&target=%2fd2l%2fhome');"
            "</script><title></title></head><body></body></html>")
    walled, why = wf.detect_login_wall("bright.uvic.ca", html)
    assert walled and "short" in why


def test_login_wall_not_triggered_by_content_page():
    body = "<p>Travel funding for graduate students. " + "Eligibility details. " * 200 + "</p>"
    html = f"<html><head><title>Travel funding</title></head><body>{body}</body></html>"
    walled, _ = wf.detect_login_wall("www.uvic.ca", html)
    assert not walled


# --- fetch loop against a mocked transport (no DNS, no network) ---------------
PAGE = "<html><head><title>Funding</title></head><body><main>" + \
       "<h1>Travel funding</h1><p>Amounts and deadlines.</p>" * 40 + \
       "</main></body></html>"


_RealClient = httpx.Client  # captured before any monkeypatching of httpx.Client


def _client_factory(handler):
    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return _RealClient(transport=transport, **{k: v for k, v in kwargs.items()
                                                   if k in ("follow_redirects", "timeout", "headers")})
    return factory


@pytest.fixture
def no_dns(monkeypatch):
    """Skip real DNS in mocked-transport tests; per-host overrides re-patch."""
    monkeypatch.setattr(wf, "resolve_and_check_host", lambda host: (None, ""))


def _fetch(monkeypatch, handler, url, **kw):
    monkeypatch.setattr(wf.httpx, "Client", _client_factory(handler))
    return wf.fetch_web_page(url, **kw)


def test_shortlink_redirector_to_allowlisted_final_ok(monkeypatch, no_dns):
    """share.google-style: the pasted host is NOT allowlisted but only
    redirects; the final allowlisted host serves the page. Must succeed and
    report the FINAL url."""
    def handler(request):
        if request.url.host == "share.google":
            return httpx.Response(302, headers={"location": "https://www.uvic.ca/funding"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text=PAGE)
    res = _fetch(monkeypatch, handler, "https://share.google/abc123",
                 allowed_domains=["uvic.ca"])
    assert res.ok, res.message
    assert res.url == "https://www.uvic.ca/funding"
    assert res.redirect_chain == ["https://share.google/abc123"]


def test_non_allowlisted_host_serving_content_rejected(monkeypatch, no_dns):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, text=PAGE)
    res = _fetch(monkeypatch, handler, "https://share.google/abc123",
                 allowed_domains=["uvic.ca"])
    assert not res.ok and res.error == wf.NOT_ALLOWLISTED


def test_redirect_to_private_address_rejected(monkeypatch):
    """A public redirector must not be able to bounce the fetch inside."""
    def resolve(host):
        if host == "internal.corp":
            return wf.PRIVATE_ADDRESS, "internal.corp resolves to 10.1.2.3"
        return None, ""
    monkeypatch.setattr(wf, "resolve_and_check_host", resolve)

    def handler(request):
        if request.url.host == "share.google":
            return httpx.Response(302, headers={"location": "http://internal.corp/secret"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text=PAGE)
    res = _fetch(monkeypatch, handler, "https://share.google/abc123",
                 allowed_domains=["uvic.ca"])
    assert not res.ok and res.error == wf.PRIVATE_ADDRESS


def test_redirect_loop_bounded(monkeypatch, no_dns):
    def handler(request):
        return httpx.Response(302, headers={"location": str(request.url) + "x"})
    res = _fetch(monkeypatch, handler, "https://www.uvic.ca/a",
                 allowed_domains=["uvic.ca"], max_redirects=3)
    assert not res.ok and res.error == wf.TOO_MANY_REDIRECTS


def test_wrong_content_type_reported(monkeypatch, no_dns):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")
    res = _fetch(monkeypatch, handler, "https://www.uvic.ca/doc.pdf",
                 allowed_domains=["uvic.ca"])
    assert not res.ok and res.error == wf.WRONG_CONTENT_TYPE
    assert "application/pdf" in res.message


def test_size_cap_streaming(monkeypatch, no_dns):
    big = "<html>" + "x" * 50_000 + "</html>"
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, text=big)
    res = _fetch(monkeypatch, handler, "https://www.uvic.ca/big",
                 allowed_domains=["uvic.ca"], max_bytes=10_000)
    assert not res.ok and res.error == wf.TOO_LARGE


def test_login_wall_final_hop_rejected(monkeypatch, no_dns):
    login = "<html><body><form><input type=\"password\"></form></body></html>"
    def handler(request):
        if request.url.host == "www.uvic.ca":
            return httpx.Response(302, headers={"location": "https://login.uvic.ca/cas"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text=login)
    res = _fetch(monkeypatch, handler, "https://www.uvic.ca/internal-page",
                 allowed_domains=["uvic.ca"])
    assert not res.ok and res.error == wf.LOGIN_WALL


def test_bad_scheme_and_port(monkeypatch, no_dns):
    def handler(request):  # pragma: no cover - never reached
        return httpx.Response(200)
    res = _fetch(monkeypatch, handler, "ftp://www.uvic.ca/x", allowed_domains=["uvic.ca"])
    assert res.error == wf.BAD_SCHEME
    res = _fetch(monkeypatch, handler, "https://www.uvic.ca:8443/x", allowed_domains=["uvic.ca"])
    assert res.error == wf.BAD_PORT


def test_http_error_status(monkeypatch, no_dns):
    def handler(request):
        return httpx.Response(404, headers={"content-type": "text/html"}, text="<html>gone</html>")
    res = _fetch(monkeypatch, handler, "https://www.uvic.ca/missing",
                 allowed_domains=["uvic.ca"])
    assert not res.ok and res.error == wf.HTTP_ERROR


def test_fragment_stripped_and_scheme_added(monkeypatch, no_dns):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, text=PAGE)
    res = _fetch(monkeypatch, handler, "www.uvic.ca/funding#section-3",
                 allowed_domains=["uvic.ca"])
    assert res.ok and res.url == "https://www.uvic.ca/funding"
