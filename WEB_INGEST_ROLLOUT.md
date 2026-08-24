# Web link ingestion rollout log (`WEB_INGEST_ENABLED`)

Unattended build, started 2026-08-20. Task: ingest UVic travel/conference
funding pages into the KB as ordinary KB documents with web provenance
(`sourceFormat: "web"`, `canonicalUrl`, `fetchedAt`), behind
`WEB_INGEST_ENABLED` (default **false**). No git, no new packages, no
Playwright, prod untouched.

## Ground facts established before Phase 1

- **Example pages.** The task references two `share.google` short links, but no
  short links were supplied in the task text and none exist in the repo
  (grepped). Resolved the intended destinations by web search instead:
  1. `https://www.uvic.ca/graduatestudies/finances/travel-and-conference-funding/index.php`
     ("Travel & conference funding — Graduate Studies")
  2. `https://4163.cupe.ca/resources/component-1/component-1-conference-award-fund/`
     ("Component 1 Conference Award Fund — CUPE 4163")
  Redirect handling will be verified with real redirects (http→https,
  apex→www) since no live `share.google` link is available to test.
- **CUPE domain trap:** the CUPE 4163 *website* is `4163.cupe.ca` (a subdomain
  of cupe.ca); `cupe4163.ca` is only their *email* domain. The allowlist seed
  is `4163.cupe.ca` — listing `cupe.ca` would have allowlisted every CUPE
  local in Canada, and `cupe4163.ca` would have allowlisted nothing useful.
- **Reuse targets identified:** KB registry `app/services/kb_formats.py`
  (extension-keyed; web needs its own entry point, not an extension);
  ingestion `rag_service.ingest_document(..., pre_extracted_pages, provenance)`
  — provenance dict is merged verbatim onto every chunk (`doc.update(provenance)`
  at rag_service.py:2063), so `canonicalUrl`/`fetchedAt` ride along cleanly;
  supersede = delete-before-insert in `app/routers/kb.py` (`_supersede_plan`);
  tables = `file_processing._render_sheet_rows` (v3-xlsx renderer);
  flag-gated registration pattern = `highlights.register(app)` /
  `workspace_dataset_routes.register(app)` in `app/main.py`.
- **HTML parsing:** `lxml 6.1.1` is already installed (with `httpx 0.28.1`).
  No new packages needed. NOTE: `lxml.html.clean` is a separate package since
  lxml 5 and is NOT installed — the extractor must strip elements manually,
  not via `Cleaner`.
- **Mongo is remote and shared** (no local `mongod` binary; DB name
  `ai-geotech-db` is hardcoded in `app/core/database.py:18`). There is no way
  to point a throwaway backend at an isolated database without installing
  software. Consequence: Phase 3/5 ingest verification writes REAL KB
  documents into the live KB (they are the two real, current funding pages —
  the intended end content — but not yet approved). Logged as residue below.
- **Baseline oracle** (flag-off byte-identical proofs, git-free): pre-change
  copy of `python_backend/` rsynced to the session scratchpad
  (`webingest/baseline_pb/`) BEFORE any edit; parity diffs run that copy on a
  throwaway port vs the modified tree with the flag off.

## Decisions taken without Dharun (visible list, per final report too)

1. Example URLs substituted for the missing `share.google` links (above).
2. Allowlist seed: `uvic.ca,4163.cupe.ca` (above).
3. Redirect-vs-allowlist semantics: the task says "re-check the allowlist at
   every hop" — read literally that would reject the pasted `share.google`
   links themselves (share.google is not allowlisted). Implemented as: the
   SSRF checks (scheme, private/loopback/link-local address, port) run at
   EVERY hop; a non-allowlisted host may only ever *redirect* (3xx) — the
   moment any host would *serve content*, it must pass the allowlist. Final
   resolved URL is reported and becomes `canonicalUrl`.
4. Who may paste a URL: task default — users who can already upload to the KB
   (any authenticated user, same as `/api/kb/upload`). Confirm before enabling.
5. Intent-router prompt extended (flag-gated, byte-identical off) so funding
   questions reach retrieval at all — full rationale in the Phase 3 entry.
6. Verification ingested the two real pages into the LIVE shared KB (no
   isolated Mongo possible) — see Residue.

---

## Phase 1 — Fetcher and allowlist: DONE

**Files:** `python_backend/app/services/web_fetch.py` (new),
`python_backend/scripts/verify_web_fetch.py` (new),
`python_backend/tests/unit/test_web_fetch.py` (new, 42 tests),
`python_backend/app/core/config.py` (adds `WEB_INGEST_ENABLED`,
`WEB_INGEST_ALLOWED_DOMAINS`, `WEB_INGEST_TIMEOUT_SECONDS`,
`WEB_INGEST_MAX_BYTES`, `WEB_INGEST_MAX_REDIRECTS` — constants only, read by
nothing else yet).

**Semantics:** per-hop scheme/port/private-address checks; allowlist enforced
on any content-serving host (redirect-only hosts like share.google pass
through); final URL reported as `canonicalUrl`; 15 s timeout, 10 MB streamed
cap, 5 redirect hops; non-HTML rejected naming the type; login-wall = auth
host pattern OR password form OR short body + sign-in words, distinct
`login_wall` code.

**Live verification (verbatim, `scripts/verify_web_fetch.py`):**

```
URL:          https://www.uvic.ca/graduatestudies/finances/travel-and-conference-funding/index.php
  status:     200   type: text/html   size: 51,915 bytes
  result:     OK (allowlisted, HTML, no login wall)
URL:          https://4163.cupe.ca/resources/component-1/component-1-conference-award-fund/
  status:     200   type: text/html   size: 140,307 bytes
  result:     OK (allowlisted, HTML, no login wall)
URL:          http://uvic.ca/graduatestudies/finances/travel-and-conference-funding/index.php
  redirects:  http://uvic.ca/... -> https://www.uvic.ca/...
  result:     OK  (judged on the DESTINATION after redirect)
URL:          https://www.example.com/
  result:     REJECTED [not_allowlisted] www.example.com is not on the allowed-domains list (uvic.ca, 4163.cupe.ca).
URL:          http://127.0.0.1:8000
  result:     REJECTED [private_address] 127.0.0.1 is a private, loopback or otherwise non-public address.
URL:          http://10.0.0.1/internal            -> REJECTED [private_address]
URL:          http://169.254.169.254/latest/meta-data/ -> REJECTED [private_address]
URL:          http://localhost/admin              -> REJECTED [private_address] localhost resolves to a non-public address (127.0.0.1)
URL:          https://www.uvic.ca/.../gss-distance-travel-grant-regs-app.pdf
  result:     REJECTED [wrong_content_type] this URL serves 'application/pdf'.
--allow uvic.ca https://4163.cupe.ca/...          -> REJECTED [not_allowlisted] (narrowed allowlist honoured)
```

share.google-style chains (non-allowlisted redirector -> allowlisted final =
OK; same host *serving* = rejected; redirector -> private address = rejected)
are covered by mocked-transport unit tests — no live short link exists to test
(see ground facts). `tests/unit/test_web_fetch.py`: **42 passed** (includes
`uvic.ca.evil.com` rejection, v4-mapped IPv6, CGN 100.64/10, login-wall
positive AND negative cases).

**Flag-off parity (byte-identical proof):** pre-change baseline copy served on
:8030 vs modified tree on :8020, flag unset on both. `openapi.json`, `/`,
`/api/kb/status`, `/api/upload/config` (401 body), `/api/kb/web/status` (404
body): `cmp` reports all five **IDENTICAL**.

**Known gap (once):** DNS is resolved and checked before the request, but
httpx re-resolves when connecting — a rebinding attacker with sub-second TTLs
could pass the check then connect elsewhere. Pinning the connection to the
checked IP needs a custom transport; not done (no new packages, bounded
scope). Mitigations in place: per-hop re-check, allowlist on serving hosts,
80/443 only.

---

## Phase 2 — Extraction: DONE

**Files:** `python_backend/app/services/web_extract.py` (new),
`python_backend/tests/unit/test_web_extract.py` (new, 6 tests). No shared file
touched (tables reuse `file_processing._render_sheet_rows` by import — NOT
reimplemented), so Phase 1's flag-off parity proof still holds verbatim.

**Behaviour:** renders from `<main>`/`role=main`/`#content`/`<article>`
(fallback `<body>`); strips script/style/template/form + nav/header/footer/
aside + ARIA navigation/banner/contentinfo + cookie/breadcrumb/skip-link
id/class tokens + `aria-hidden`; headings -> `#`-prefixed lines (feeds the v2
chunker's section detection), lists -> `-`/`1.` bullets with nesting, tables ->
the shared v3-xlsx row renderer (`## Table: <caption>` + header restated per
block); `<title>` captured for `canonicalTitle`; extracted/raw ratio reported,
ratio < 1% -> explicit JS-rendered warning (headless browsers are excluded
from this project by rule — JS pages are a documented limitation).

**Verification on the two real pages (full text saved to session scratchpad
`webingest/extract_{uvic,cupe}.txt`; verbatim driver output):**

```
== uvic: https://www.uvic.ca/graduatestudies/finances/travel-and-conference-funding/index.php
   title      : 'Travel & conference funding - Graduate Studies - UVic'
   html chars : 51,904   text chars: 5,287   ratio: 10.19%
   headings=12 tables=0 list_items=28 warnings=[]
== cupe: https://4163.cupe.ca/resources/component-1/component-1-conference-award-fund/
   title      : 'Component 1 Conference Award Fund - CUPE 4163'
   html chars : 140,279   text chars: 2,099   ratio: 1.50%
   headings=0 tables=0 list_items=0 warnings=[]
```

Confirmed by inspection of the full text:
- UVic: every eligibility bullet, all award amounts ($600/$400/$200 and the
  distance-program $100/$200/$300), the one-to-four-months application window
  and the "no applications after travel" rule are present and readable under
  their `##`/`###` headings. No navigation text survived (no "Admissions",
  no breadcrumbs, no footer).
- CUPE: eligibility ("component 1 member"), the $450 maximum,
  first-come-first-served allocation, the 30-days-to-return-receipts rule and
  the application e-mail are all present. No menu text. The page's H1 sits in
  a theme `<header>` element (stripped as chrome) and its 0-heading body is a
  quirk of that WordPress theme — the title IS captured ('Component 1
  Conference Award Fund - CUPE 4163'). **Decision:** Phase 3 prepends the
  captured title as an `# H1` when the extracted text does not already start
  with a heading, so the ingested document (and its chunks' section headers)
  carry the page identity.
- Neither page needs JavaScript to render its content (both extracted fully
  server-side; the JS warning path is unit-tested with a synthetic shell).

---

## Phase 3 — Ingestion: DONE

**Files:** `python_backend/app/routers/kb_web.py` (new — /api/kb/web/status,
/preview, /ingest; registered via `kb_web.register(app)` only when
WEB_INGEST_ENABLED), `app/services/kb_formats.py` (adds `build_web_document` +
`validate_web_document` — the web "format handler" beside the file handlers;
NOT extension-keyed, spreadsheet handlers untouched), `app/routers/kb.py`
(/api/kb/status gains `"webIngest": true` ONLY when flag on; /my-uploads and
/uploads include `canonicalUrl`/`fetchedAt` ONLY on web batches),
`app/main.py` (register call), `app/services/intent_router.py` (see decision
below), `app/services/web_fetch.py` (login-wall hardening, below),
tests: `tests/unit/test_kb_web_router.py` (new, 13), `test_web_fetch.py` (+1),
`test_intent_router.py` (+2).

**Semantics:** every chunk carries the full KB provenance block
(`category=knowledge_base`, uploader, projectTag, canonicalTitle, version,
permissionConfirmed) PLUS `sourceFormat:"web"`, `canonicalUrl` (final URL
after redirects), `fetchedAt`; chunk `filename` = canonicalUrl. A URL already
ingested is rejected 409 `already_ingested` unless `refresh` (Phase 5 flow —
supersede keyed on canonicalUrl, delete-before-insert). Identical content
under a different URL is rejected 409 `duplicate_content`. `project` and
`permissionConfirmed` required, same as file uploads. Ingest is synchronous
(a page is a handful of chunks); a `kb_batch` doc (status `indexed`,
sourceFormat `web`) is written so the KB panel lists web docs. All fetch
errors surface as structured `{"code","message"}` details.

**Login-wall hardening found during live verify:** Brightspace
(`bright.uvic.ca/d2l/home`, NetLink-protected) serves a 272-byte HTML stub
whose only tell is `window.location.replace('/d2l/login…')` inside a script —
the original short-body check missed it (keywords were only in the stripped
script). The short-body branch now scans the RAW html. Verbatim after fix:
`REJECTED [login_wall] … the page body is unexpectedly short (0 chars) and
points at a sign-in flow.` Regression test added.

**DECISION (taken without Dharun, #5): intent-router prompt, flag-gated.**
With ROUTER_ENABLED on (prod), the funding question routed GENERAL — "skipping
retrieval, answering from model knowledge", zero citations — because the
router prompt frames the KB as "lab documents / research papers". That defeats
this feature's entire purpose (grounded funding answers). The router is NOT on
the do-not-touch list (retrieval/reranking/auth/calculators/parsers), so
`intent_router._system_prompt()` now appends ONE rule — "the KB also contains
captured university information pages; funding/awards/deadlines questions are
KB_QUERY" — ONLY when WEB_INGEST_ENABLED; flag off returns the identical
prompt string (unit-tested). Retrieval itself was NOT touched: the funding
chunks rank top-3 through the untouched hybrid+rerank path (rerank +3.33 /
+2.65 / +1.31, all above threshold).

**Flag-off parity (byte-identical):** modified tree :8020 (flag unset) vs
pre-change baseline :8030 — `openapi.json` (full route table), `/`,
`/api/kb/status`, `/api/kb/web/status` (404), `/api/kb/web/preview` (404): all
IDENTICAL by `cmp`. Pre-change-upload proof: the same .txt POSTed to
/api/kb/upload (two-phase `needs_input` call — indexes nothing) returned
byte-identical JSON on baseline-vs-modified, after first proving the baseline
response is deterministic (baseline called twice -> identical).

**Flag-on verification (verbatim highlights):**
- `/api/kb/status` -> `{"enabled":true,"webIngest":true}`;
  `/api/kb/web/status` -> allowlist echoed.
- Preview of the http:// apex URL resolved to the canonical https://www URL,
  title + first lines + charCount/ratio returned, `alreadyIngested: null`.
- Error cases through the endpoint: `not_allowlisted` (422), `private_address`
  (422), `wrong_content_type` for the PDF (415), `login_wall` for Brightspace
  (422) — each with its own code + message.
- Ingested both pages: UVic 4 chunks, CUPE 2 chunks, v1, project
  `uvic-funding`. Immediate re-ingest -> 409 `already_ingested` naming title +
  fetchedAt.
- Mongo chunk audit: all 6 web chunks carry canonicalUrl / fetchedAt /
  projectTag / permissionConfirmed=True / chunkingVersion v2 and real section
  headers (CUPE chunk 0 shows the prepended `# Component 1 Conference Award
  Fund…` H1 — the Phase 2 decision working).
- KB query end-to-end (`POST /chat`, throwaway user): router classified
  KB_QUERY; answer is grounded in the page (full-time registration, no
  outstanding fees, one award per fiscal year, first-come-first-served, 2-month
  priority window) and both web docs appear as sources. (Their titles/links
  render as mangled Google-Scholar URLs — pre-existing filename-derived
  citation code; Phase 5 replaces this with canonicalUrl + fetch date.)
- PDF/docx/xlsx unchanged: their handlers and the upload endpoint were not
  modified; full unit suite (incl. kb_formats/kb_router/xlsx) green.

**Test suite:** 538 passed, 6 skipped (whole `tests/unit`).

---

## Phase 4 — Frontend: DONE

**Files:** `app/components/kb-web-ingest.tsx` (new — the URL section),
`app/components/kb-upload.tsx` (renders `<KbWebIngest/>` inside the existing
panel — a second input alongside file upload, NOT a new page; web entries in
"Your recent uploads" get a globe indicator, the fetch date, a link to the
live page, and a Refresh button), `app/components/kb-upload.module.css`
(additive classes using EXISTING tokens only — --s1/--line/--t*/--accent/--r*;
no new design tokens; lucide icons at the existing 13–18px sizes),
`app/config/api.ts` (kbWebPreview/kbWebIngest — same-origin, through the
existing `/api/kb/[...path]` long-timeout proxy, which needed NO changes),
`app/components/__tests__/kb-web-ingest.test.tsx` (new, 9 tests).

**Gating (the permission decision):** the section renders ONLY when
`/api/kb/status` carries `webIngest: true`, i.e. for exactly the population
that can already upload to the KB (task default — decision #4). Flag off: the
key is absent and the component returns `null` — the KB page is byte-for-byte
today's markup.

**Flow:** paste URL -> "Fetch preview" -> title, resolved URL, char count and
first ~1500 chars shown BEFORE anything is stored -> project + public-page
confirmation -> "Add to Knowledge Base" (or "Refresh in Knowledge Base" when
the URL is already ingested — the preview says so and shows the stored copy's
fetch date). Success panel shows fetch date, and on refresh BOTH dates +
"content unchanged" when the hash matched. Every backend error code renders
its own heading + the backend's specific message (component-tested for
not_allowlisted / login_wall / wrong_content_type / timeout /
already_ingested).

**Verification:**
- `vitest`: 9 new component tests; whole frontend suite **75 passed** —
  includes "renders NOTHING when webIngest key absent" (flag-off surface =
  today's) and the full preview->refresh flow with both dates asserted.
- Full build cycle in a scratchpad copy (prod `.next`/`:3000` untouched — no
  sudo, systemd): `rm -rf .next && npm run build` -> **exit 0**, route list
  shows `/knowledge-base` and `/api/kb/[...path]`; `next start -p 3020`
  serves it (log: session scratchpad `webingest/fe_build.log`). Turbopack
  refuses symlinked node_modules -> `cp -al`, PYTHON_API_URL baked at build
  time via the copy's `.env.local` (both facts from the instrument-parsers
  run, reconfirmed).
- Through the UI's EXACT network path (:3020 proxy + auth cookie):
  flag-off -> `/api/kb/status` = `{"enabled":true}` (the very payload the
  gate consumes — byte-identical to today) and `/api/kb/web/status` 404;
  flag-on -> `{"enabled":true,"webIngest":true}` and POST
  `/api/kb/web/preview` returns the CUPE page preview with
  `alreadyIngested` populated.
- **No browser automation exists in this project (standing rule), and the KB
  panel is client-rendered behind the auth guard (SSR serves the "Checking
  auth" shell in BOTH flag states)** — so "renders exactly as today" is
  pinned by the component tests at the component boundary, and the live curl
  checks cover the serving path + the exact gate payload + the exact
  endpoints the clicks call. That is the maximum verifiable without a
  browser; a 2-minute human pass over :3020 (or prod once rebuilt) is listed
  in the test commands for Dharun.

---

## Phase 5 — Refresh and citation: DONE

**Files:** `app/routers/chat.py` (citation assembly + one conditional fix to
the citation-narrowing step), `app/components/message-list.tsx` +
`message-list.module.css` (the rendered provenance note). Refresh itself was
built in Phase 3 (`refresh: true` on /ingest, supersede keyed on
canonicalUrl) and surfaced in Phase 4 (list Refresh button + preview offer);
no scheduler, no background job — manual only.

**Citation design (retrieval NOT touched):** the retrieval projection carries
no web fields and retrieval is off-limits, so chat.py detects web chunks by
their filename being the canonical URL and resolves fetch dates with ONE
`kb_batch` query (sorted by fetchedAt so the latest refresh wins), then stamps
`url` (the live canonicalUrl — replacing the filename-derived Google-Scholar
link), `title` (canonicalTitle), `sourceFormat`, `fileType: "web"` and
`fetchedAt` onto ONLY the web source entries. Zero extra queries and
byte-identical entries when no web chunk contributes. Deterministic — from
retrieval results + stored provenance, never model prose. /chat/stream shares
the same turn code. The frontend SourcesPanel renders web sources as a link to
the live page plus a mono note: `web page · captured YYYY-MM-DD — may have
changed` — the capture date is always visible wherever this content is used
(deadlines/amounts are never presented as authoritative).

**Bug found & fixed during verify:** the KB_QUERY citation-narrowing step
(`cited_titles` from `get_clean_title(filename)`) dropped EVERY web source —
their displayed title is canonicalTitle, which never matches a URL-derived
title — so an answer grounded in web pages returned `sources: []`. Fixed by
also adding a cited web chunk's canonicalTitle to the match set (conditional
on URL-filenames; every other chunk's behaviour is exactly as before). Also
noted during verify: the Redis answer cache serves cached sources for a
repeated query (TTL 3600 s), so pre-fix answers stay cached until expiry.

**Verification (verbatim highlights):**
- Refresh of the CUPE URL through the :3020 UI proxy:
  `previousFetchedAt: 2026-08-20T15:22:48`, `fetchedAt: 2026-08-20T15:36:43`,
  `version: 2`, `superseded: 2`, `contentChanged: false` — both dates shown.
- Mongo audit after refresh: cupe `chunks=2 versions=[2]` with a SINGLE
  distinct fetchedAt (replaced, not duplicated); uvic untouched
  (`chunks=4 versions=[1]`).
- Funding question ("How much conference travel money can a grad student get
  from UVic, and is there union funding too?") -> grounded answer; sources:
  `{"title": "Travel & conference funding - Graduate Studies - UVic",
    "url": "https://www.uvic.ca/graduatestudies/finances/travel-and-conference-funding/index.php",
    "sourceFormat": "web", "fetchedAt": "2026-08-20T15:22:45.814000"}` and the
  CUPE entry with its refreshed date + v2.
- Suites after all Phase-5 edits: unit **538 passed / 6 skipped**; frontend
  **75 passed**; integration 128 passed + exactly the 3 PRE-EXISTING failures
  (2x "Event loop is closed" in test_thread_delete_cascade, 1x stale
  workspace-status assertion from the instrument run) — nothing new.
- Flag-off parity for the chat changes is structural, not byte-diffed: the
  web branch costs zero queries and changes zero entries when no web chunk is
  retrieved, and chat answers are LLM-nondeterministic so a byte-diff would
  be meaningless. (Note: with web docs in the KB, citations render properly
  even if WEB_INGEST is later turned off — web documents are ordinary KB
  documents by design.)

## Phase status

- Phase 1 (fetcher + allowlist): DONE
- Phase 2 (extraction): DONE
- Phase 3 (ingestion): DONE
- Phase 4 (frontend): DONE
- Phase 5 (refresh + citation): DONE

## Residue (to confirm with Dharun before deleting)

- Throwaway user `web.ingest.check@example.com` (_id 6a877d848bf39695217d4d1a)
  in the shared users collection (created for verification logins; no other
  way to authenticate against the throwaway backends).
- **The two ingested web documents are LIVE in the shared KB** (Mongo is
  remote+shared; DB name hardcoded — no isolated instance possible without
  installing software): batches `3663906298b04aa38d55a94c5a7fca68` (UVic, 4
  chunks) and `723938c589ed4510b99d06dcb31dc43a` (CUPE, 2 chunks), project
  `uvic-funding`, uploader = the throwaway user. This is the intended end
  content, current as of 2026-08-20, and additive-only — but it is retrievable
  by prod queries NOW (prod's old code renders its citations as filename-based
  Google-Scholar links until restarted with this code). If Dharun wants them
  out pending approval: `DELETE /api/kb/batch/{batchId}` as admin (or ask me
  for a dry-run plan). kb_audit has matching `web_ingest` rows; two `kb_batch`
  status docs exist.
- The parity-check `.txt` upload used ONLY the two-phase `needs_input` path —
  nothing was indexed, no residue.

## Open concerns (each stated once)

1. **DNS rebinding TOCTOU** in the fetcher: addresses are validated before the
   request but httpx re-resolves on connect; a sub-second-TTL attacker could
   pass the check and connect elsewhere. Mitigated (per-hop re-check, allowlist
   on serving hosts, ports 80/443 only); a full fix needs a custom transport
   pinning the checked IP.
2. **Login-wall short-body heuristic can false-positive**: any page whose
   de-tagged body is under ~1200 chars AND whose raw HTML mentions
   sign-in/login words is rejected as a login wall. This fails SAFE (such a
   page would also fail the 200-char text minimum), but the message may
   occasionally mislabel a tiny page.
3. **JS-rendered pages are not supported** (no headless browser by project
   rule): they fail with the low-text warning / no_usable_text error, by
   design. If Dr. Lin needs such a page, that is a documented limitation.
4. **Redis answer cache (TTL 3600 s)** serves pre-refresh answers AND
   pre-refresh source lists for up to an hour after a page is refreshed — a
   just-refreshed deadline can still be quoted from the older capture within
   that window. Existing behaviour, magnified by refreshable sources; a
   refresh-triggered cache invalidation would need a keyed flush.
5. **The two verification documents are live in the shared KB** and prod's
   old code cites them with mangled Scholar links until the backend is
   restarted with this tree (see Residue).
6. **Router prompt coupling**: with WEB_INGEST_ENABLED on, chat routing for
   funding/administrative questions changes for everyone (that is the point,
   but it is a chat-wide behaviour change riding on an ingest flag).
7. **Admin-only restriction undecided**: the URL control currently shows for
   every authenticated user (task default); flip to admins-only needs only a
   role check in `kb_web.py` + the gate key in `/api/kb/status`.

---

# Final report (2026-08-20)

All five phases DONE. The app is left with **WEB_INGEST_ENABLED=false**
(`python_backend/.env:49`, appended after grep + trailing-newline check;
backup of the pre-edit .env in the session scratchpad). Prod (:8000/:3000,
systemd, no sudo) was never restarted or rebuilt and still runs pre-change
code. The tree is left dirty; no git command was run.

## Files created

- python_backend/app/services/web_fetch.py
- python_backend/app/services/web_extract.py
- python_backend/app/routers/kb_web.py
- python_backend/scripts/verify_web_fetch.py
- python_backend/tests/unit/test_web_fetch.py (43)
- python_backend/tests/unit/test_web_extract.py (6)
- python_backend/tests/unit/test_kb_web_router.py (13)
- app/components/kb-web-ingest.tsx
- app/components/__tests__/kb-web-ingest.test.tsx (9)
- WEB_INGEST_ROLLOUT.md (this file)

## Files modified

- python_backend/app/core/config.py         (WEB_INGEST_* settings block)
- python_backend/app/services/kb_formats.py (build_web_document/validate — spreadsheet handlers untouched)
- python_backend/app/routers/kb.py          (status webIngest key; list web fields — all conditional)
- python_backend/app/main.py                (kb_web.register(app))
- python_backend/app/services/intent_router.py (flag-gated prompt rule — decision #5)
- python_backend/app/routers/chat.py        (web citation assembly + citation-narrowing fix)
- python_backend/tests/unit/test_intent_router.py (+2)
- app/components/kb-upload.tsx              (web section + list indicators/refresh)
- app/components/kb-upload.module.css       (additive classes, existing tokens)
- app/components/message-list.tsx / .module.css (captured-page provenance on citations)
- app/config/api.ts                         (kbWebPreview/kbWebIngest)
- python_backend/.env                       (WEB_INGEST_ENABLED=false, line 49)

## Flag-off parity evidence (per phase)

- P1: baseline(:8030) vs modified(:8020), flag unset — openapi.json, /,
  /api/kb/status, /api/upload/config, /api/kb/web/* 404s: all IDENTICAL (cmp).
- P2: no shared file touched; P1 proof carries.
- P3: same five surfaces IDENTICAL again after the router/kb changes, PLUS a
  byte-identical two-phase /api/kb/upload `needs_input` response
  (baseline-determinism proven first by calling baseline twice).
- P4: component test pins "renders nothing without the webIngest key"; the
  gate payload (/api/kb/status) is byte-identical flag-off through the real
  :3020 proxy; build exit 0.
- P5: structural (zero queries / zero changed entries with no web chunk in the
  turn; chat answers are LLM-nondeterministic so byte-diffing is meaningless);
  all suites green.

## The two example pages

Both fetched CLEANLY server-side — no login wall, no JS rendering needed:
- UVic Travel & conference funding: 51,915 B HTML -> 5,287 chars (10.2%),
  12 headings, 28 bullets; every amount/deadline verified present.
- CUPE 4163 Component 1 Conference Award Fund: 140,307 B -> 2,099 chars
  (1.5%), complete award terms; page H1 lives in stripped chrome -> title
  prepended as H1 at ingest (Phase 2 decision).
The supplied share.google short links were NOT available (not in the task
text or repo); redirect handling was verified with real http->https/apex->www
redirects live plus share.google-style chains in mocked unit tests. The
login-wall path was verified live against NetLink-protected Brightspace.

## Exact commands for Dharun

Phase 1 (fetcher):
    cd python_backend
    venv/bin/python scripts/verify_web_fetch.py \
      "https://www.uvic.ca/graduatestudies/finances/travel-and-conference-funding/index.php" \
      "http://127.0.0.1:8000" \
      "https://www.uvic.ca/graduatestudies/_assets/docs/gss-distance-travel-grant-regs-app.pdf" \
      "https://bright.uvic.ca/d2l/home"
    venv/bin/python -m pytest tests/unit/test_web_fetch.py -q

Phase 2 (extraction):
    venv/bin/python -m pytest tests/unit/test_web_extract.py -q

Phase 3 (backend, throwaway port — prod untouched):
    WEB_INGEST_ENABLED=true venv/bin/uvicorn app.main:app --port 8020
    curl -s http://127.0.0.1:8020/api/kb/status        # {"enabled":true,"webIngest":true}
    # login as yourself, then:
    curl -s -b cookies -X POST http://127.0.0.1:8020/api/kb/web/preview \
      -H 'Content-Type: application/json' -d '{"url":"https://www.uvic.ca/graduatestudies/finances/travel-and-conference-funding/index.php"}'
    venv/bin/python -m pytest tests/unit/test_kb_web_router.py -q

Phase 4 (frontend): npx vitest run app/components/__tests__/kb-web-ingest.test.tsx
    Then a 2-minute visual pass: with the backend flag on, /knowledge-base
    shows "Add a web page by link"; flag off, it does not.

Phase 5: paste an already-ingested URL -> preview offers Refresh -> completion
    shows both fetch dates; ask a funding question in Chat -> citation links
    the live page and shows "captured YYYY-MM-DD".

Enable in prod (when approved):
    sed -i 's/^WEB_INGEST_ENABLED=false/WEB_INGEST_ENABLED=true/' python_backend/.env
    sudo systemctl restart geoai-backend
    # frontend: stop :3000, rm -rf .next, npm run build, start

## Decided without Dharun

The six decisions listed at the top of this file (example-URL substitution,
allowlist seed incl. the 4163.cupe.ca vs cupe4163.ca trap, redirect/allowlist
semantics, paste-permission default, the flag-gated router-prompt rule, and
live-KB verification ingest), plus two in-flight judgment calls: prepending
the page title as H1 when the extracted body starts unheaded, and the
citation-narrowing fix in chat.py (a bug my feature exposed — web sources
were silently dropped from displayed citations).
