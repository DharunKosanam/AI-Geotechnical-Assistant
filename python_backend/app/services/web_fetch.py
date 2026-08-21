"""Safe fetcher for KB web-page ingestion (WEB_INGEST_ENABLED).

A pasted URL makes THIS SERVER issue a request to an address the user chose;
on a campus network that includes internal hosts, so this module treats SSRF
as the primary risk, ahead of content quality:

* Scheme must be http/https and the port 80/443 (or unset) — at EVERY hop.
* The hostname is resolved BEFORE the request and every resolved address must
  be globally routable: private, loopback, link-local, CGN, multicast and
  reserved ranges are rejected (IPv4-mapped IPv6 is unwrapped first). This
  runs at every redirect hop, so a public redirector cannot bounce the fetch
  to 10.x/127.x/169.254.x. Known gap: httpx re-resolves DNS when connecting,
  so a rebinding attacker with sub-second TTLs could pass the check and then
  connect elsewhere — accepted for now (documented in WEB_INGEST_ROLLOUT.md);
  pinning the connection to the checked IP needs a custom transport.
* Redirects are followed manually (bounded by WEB_INGEST_MAX_REDIRECTS) so the
  checks above run per hop. The ALLOWLIST (registrable-domain style suffix
  match, see config.WEB_INGEST_ALLOWED_DOMAINS) is enforced on any host that
  would SERVE content; a non-allowlisted host (a share.google short link) may
  only ever answer with a redirect. The final resolved URL is reported to the
  caller — it becomes ``canonicalUrl``.
* Response size is capped (streamed, WEB_INGEST_MAX_BYTES) and non-HTML
  content types are rejected naming the type.
* NetLink/CAS sign-in pages are detected (auth-host patterns, password form,
  short body full of sign-in words) and returned as a distinct LOGIN_WALL
  failure — a sign-in page must never be ingested as a document.

Sync on purpose: callers in async routes run it via ``asyncio.to_thread``.
No DB, no app state; limits read from config at call time so tests can
monkeypatch them.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlsplit

import httpx

from app.core import config

USER_AGENT = "GeoAI-KB-WebIngest/1.0 (UVic geotechnical assistant; contact: Lin lab)"
ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"
HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# Error codes — the API layer and the UI map these to specific messages, so
# they are part of the contract; never rename casually.
NOT_ALLOWLISTED = "not_allowlisted"
PRIVATE_ADDRESS = "private_address"
BAD_SCHEME = "bad_scheme"
BAD_PORT = "bad_port"
INVALID_URL = "invalid_url"
DNS_FAILURE = "dns_failure"
TOO_MANY_REDIRECTS = "too_many_redirects"
TIMEOUT = "timeout"
TOO_LARGE = "too_large"
WRONG_CONTENT_TYPE = "wrong_content_type"
LOGIN_WALL = "login_wall"
HTTP_ERROR = "http_error"
FETCH_ERROR = "fetch_error"

# Hosts that exist to authenticate, not to inform. Prefix-of-hostname match on
# the first label; "netlink" matches anywhere in the host.
_AUTH_HOST_PREFIXES = ("login.", "logon.", "idp.", "cas.", "sso.", "auth.",
                       "shib.", "shibboleth.", "webauth.", "signin.")
_AUTH_KEYWORDS = ("netlink", "sign in", "log in", "login", "sign-in",
                  "central authentication", "single sign-on", "webauth",
                  "authentication required", "please authenticate")
# Body length (crudely de-tagged) under which sign-in keywords are damning: a
# real content page about logging in would say much more than a login form.
_SHORT_BODY_CHARS = 1200


@dataclass
class FetchResult:
    """Outcome of one guarded fetch. ``ok`` is True only for an allowlisted,
    HTML, non-login-wall page within limits; otherwise ``error`` holds one of
    the module's error codes and ``message`` a user-renderable sentence."""

    requested_url: str
    ok: bool = False
    error: Optional[str] = None
    message: str = ""
    url: str = ""                      # final resolved URL (canonicalUrl)
    redirect_chain: List[str] = field(default_factory=list)
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    html: str = ""
    size_bytes: int = 0


def _fail(res: FetchResult, code: str, message: str) -> FetchResult:
    res.ok = False
    res.error = code
    res.message = message
    return res


def host_allowed(host: str, allowed_domains: Optional[Sequence[str]] = None) -> bool:
    """Suffix match on whole labels: host equals an allowed domain or ends with
    "." + domain. "uvic.ca.evil.com" does not end with ".uvic.ca" and fails."""
    if allowed_domains is None:
        allowed_domains = config.WEB_INGEST_ALLOWED_DOMAINS
    host = (host or "").lower().rstrip(".")
    for d in allowed_domains:
        d = d.lower().rstrip(".")
        if d and (host == d or host.endswith("." + d)):
            return True
    return False


def _blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    # is_global is False for private, loopback, link-local, CGN (100.64/10),
    # reserved, unspecified and multicast — exactly the set that must never be
    # reachable from a pasted URL.
    return not ip.is_global


def resolve_and_check_host(host: str) -> Tuple[Optional[str], str]:
    """Resolve ``host`` and check every address. Returns (error_code, detail);
    (None, "") when all resolved addresses are globally routable."""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _blocked_ip(literal):
            return PRIVATE_ADDRESS, f"{host} is a private, loopback or otherwise non-public address"
        return None, ""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        return DNS_FAILURE, f"could not resolve {host} ({e})"
    if not infos:
        return DNS_FAILURE, f"could not resolve {host} (no addresses)"
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr.split("%", 1)[0])  # strip v6 zone id
        except ValueError:
            return PRIVATE_ADDRESS, f"{host} resolved to an unparseable address ({addr})"
        if _blocked_ip(ip):
            return PRIVATE_ADDRESS, f"{host} resolves to a non-public address ({addr})"
    return None, ""


_TAG_STRIP_RE = re.compile(r"<script\b.*?</script>|<style\b.*?</style>|<[^>]+>",
                           re.IGNORECASE | re.DOTALL)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_PASSWORD_INPUT_RE = re.compile(r"<input\b[^>]*type\s*=\s*[\"']?password", re.IGNORECASE)


def _looks_like_auth_host(host: str) -> bool:
    host = (host or "").lower()
    return host.startswith(_AUTH_HOST_PREFIXES) or "netlink" in host


def detect_login_wall(final_host: str, html: str) -> Tuple[bool, str]:
    """(is_login_wall, evidence). Heuristics ordered by confidence: an auth
    host can only ever be a sign-in page; a password form on the FINAL page
    means the content is behind it; sign-in keywords on an unexpectedly short
    body catch interstitials that render the form via a template."""
    if _looks_like_auth_host(final_host):
        return True, f"the URL resolved to a sign-in host ({final_host})"
    if _PASSWORD_INPUT_RE.search(html):
        return True, "the page contains a password sign-in form"
    title = ""
    m = _TITLE_RE.search(html)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
    body_text = re.sub(r"\s+", " ", _TAG_STRIP_RE.sub(" ", html)).strip()
    if len(body_text) < _SHORT_BODY_CHARS:
        # An unexpectedly short body: scan the RAW html (lowercased), not the
        # de-tagged text — auth interstitials often carry their tell only in a
        # script/meta redirect URL (e.g. Brightspace's
        # window.location.replace('/d2l/login?...') stub, 272 bytes, no form).
        # A false positive here fails SAFE: a near-empty page could not be
        # ingested anyway, and the login-wall message is the clearer one.
        lowered_raw = (title + " " + html[:8000]).lower()
        if any(k in lowered_raw for k in _AUTH_KEYWORDS) or "signin" in lowered_raw:
            return True, (f"the page body is unexpectedly short ({len(body_text)} chars) "
                          f"and points at a sign-in flow"
                          + (f" (title: \"{title}\")" if title else ""))
    return False, ""


def _normalise(url: str) -> str:
    url = (url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    return url.split("#", 1)[0]  # canonicalUrl never carries a fragment


def _decode_body(raw: bytes, content_type_header: str) -> str:
    m = re.search(r"charset=([A-Za-z0-9_.\-]+)", content_type_header or "", re.IGNORECASE)
    encodings = [m.group(1)] if m else []
    encodings += ["utf-8"]
    for enc in encodings:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def fetch_web_page(
    url: str,
    *,
    allowed_domains: Optional[Sequence[str]] = None,
    max_redirects: Optional[int] = None,
    timeout_seconds: Optional[float] = None,
    max_bytes: Optional[int] = None,
) -> FetchResult:
    """Fetch one page under the full guard set. Never raises for a bad URL or
    a misbehaving server — every failure comes back as a coded FetchResult."""
    if max_redirects is None:
        max_redirects = config.WEB_INGEST_MAX_REDIRECTS
    if timeout_seconds is None:
        timeout_seconds = config.WEB_INGEST_TIMEOUT_SECONDS
    if max_bytes is None:
        max_bytes = config.WEB_INGEST_MAX_BYTES

    res = FetchResult(requested_url=url)
    current = _normalise(url)
    if not current:
        return _fail(res, INVALID_URL, "No URL was provided.")

    try:
        with httpx.Client(
            follow_redirects=False,
            timeout=timeout_seconds,
            headers={"User-Agent": USER_AGENT, "Accept": ACCEPT},
        ) as client:
            for _hop in range(max_redirects + 1):
                parts = urlsplit(current)
                host = (parts.hostname or "").lower().rstrip(".")
                res.url = current
                if parts.scheme not in ("http", "https"):
                    return _fail(res, BAD_SCHEME,
                                 f"Only http(s) URLs can be fetched (got '{parts.scheme or 'no scheme'}').")
                if not host:
                    return _fail(res, INVALID_URL, "The URL has no hostname.")
                code, detail = resolve_and_check_host(host)
                if code == PRIVATE_ADDRESS:
                    return _fail(res, PRIVATE_ADDRESS,
                                 f"This address is not reachable from here: {detail}. "
                                 f"Internal or private hosts cannot be ingested.")
                if code == DNS_FAILURE:
                    return _fail(res, DNS_FAILURE, f"Could not look up the host: {detail}.")
                if parts.port not in (None, 80, 443):
                    return _fail(res, BAD_PORT,
                                 f"Port {parts.port} is not allowed (only standard web ports 80/443).")

                with client.stream("GET", current) as resp:
                    res.status_code = resp.status_code
                    location = resp.headers.get("location")
                    if resp.status_code in REDIRECT_STATUSES and location:
                        res.redirect_chain.append(current)
                        current = _normalise(urljoin(current, location))
                        continue

                    # This host is SERVING content — it must pass the allowlist.
                    if not host_allowed(host, allowed_domains):
                        return _fail(res, NOT_ALLOWLISTED,
                                     f"{host} is not on the allowed-domains list "
                                     f"({', '.join(allowed_domains or config.WEB_INGEST_ALLOWED_DOMAINS)}). "
                                     f"Resolved URL: {current}")
                    if resp.status_code >= 400:
                        return _fail(res, HTTP_ERROR,
                                     f"The page returned HTTP {resp.status_code}.")
                    ct_header = resp.headers.get("content-type", "")
                    ctype = ct_header.split(";", 1)[0].strip().lower()
                    res.content_type = ctype
                    if ctype not in HTML_CONTENT_TYPES:
                        return _fail(res, WRONG_CONTENT_TYPE,
                                     f"Only web pages (HTML) can be ingested; this URL serves "
                                     f"'{ctype or 'an unknown content type'}'. For a PDF, download "
                                     f"it and use the file upload instead.")
                    declared = resp.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_bytes:
                        return _fail(res, TOO_LARGE,
                                     f"The page is {int(declared):,} bytes — over the "
                                     f"{max_bytes:,}-byte limit.")
                    raw = bytearray()
                    for chunk in resp.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > max_bytes:
                            return _fail(res, TOO_LARGE,
                                         f"The page exceeded the {max_bytes:,}-byte limit.")
                    res.size_bytes = len(raw)
                    res.html = _decode_body(bytes(raw), ct_header)

                    walled, evidence = detect_login_wall(host, res.html)
                    if walled:
                        return _fail(res, LOGIN_WALL,
                                     f"This page appears to be behind a sign-in (NetLink) wall: "
                                     f"{evidence}. Sign-in pages are never ingested. If the "
                                     f"content is public, paste the public URL instead.")
                    res.ok = True
                    res.message = "fetched"
                    return res

            res.redirect_chain.append(current)
            return _fail(res, TOO_MANY_REDIRECTS,
                         f"Gave up after {max_redirects} redirects "
                         f"({' -> '.join(res.redirect_chain)}).")
    except httpx.TimeoutException:
        return _fail(res, TIMEOUT,
                     f"The page did not respond within {timeout_seconds:.0f} seconds.")
    except httpx.HTTPError as e:
        return _fail(res, FETCH_ERROR, f"Could not fetch the page: {e}.")
