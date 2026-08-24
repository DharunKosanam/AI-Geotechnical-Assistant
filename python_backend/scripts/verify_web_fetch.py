#!/usr/bin/env python
"""Fetch URL(s) through the guarded KB web fetcher and print what happened.

Standalone check for the web-ingest fetcher: no DB, no app context (importing
app.core.config does need the usual .env, e.g. MONGODB_URI, but nothing is
connected to). Prints, per URL: the resolved URL, redirect chain, HTTP status,
content type, size, and whether the allowlist and login-wall checks passed.

Exit codes: 0 = every URL fetched clean, 1 = any URL failed, 2 = usage.

Usage:
    python scripts/verify_web_fetch.py <url> [<url> ...] [--allow d1,d2] [--json]

--allow overrides the configured allowlist for this run only (testing
rejections without touching config); --json emits machine-readable lines.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # python_backend/ on sys.path

from app.services import web_fetch  # noqa: E402


def main(argv) -> int:
    urls = []
    allow = None
    as_json = False
    it = iter(argv)
    for a in it:
        if a == "--allow":
            allow = [d.strip() for d in next(it, "").split(",") if d.strip()]
        elif a == "--json":
            as_json = True
        elif a.startswith("--"):
            print(f"unknown flag {a}", file=sys.stderr)
            return 2
        else:
            urls.append(a)
    if not urls:
        print(__doc__, file=sys.stderr)
        return 2

    failures = 0
    for url in urls:
        res = web_fetch.fetch_web_page(url, allowed_domains=allow)
        row = {
            "requested": res.requested_url,
            "resolved": res.url,
            "redirects": res.redirect_chain,
            "status": res.status_code,
            "content_type": res.content_type,
            "size_bytes": res.size_bytes,
            "ok": res.ok,
            "error": res.error,
            "message": res.message,
        }
        if as_json:
            print(json.dumps(row))
        else:
            print(f"URL:          {res.requested_url}")
            print(f"  resolved:   {res.url}")
            if res.redirect_chain:
                print(f"  redirects:  {' -> '.join(res.redirect_chain)} -> {res.url}")
            print(f"  status:     {res.status_code}   type: {res.content_type}   "
                  f"size: {res.size_bytes:,} bytes")
            if res.ok:
                print("  result:     OK (allowlisted, HTML, no login wall)")
            else:
                print(f"  result:     REJECTED [{res.error}] {res.message}")
            print()
        if not res.ok:
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
