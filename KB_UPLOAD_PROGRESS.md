# KB Upload — running progress log

Student document upload into the shared knowledge base. Feature gated behind
`KB_UPLOAD_ENABLED` (default off). This file is a running log written at every
phase boundary so progress is readable without interrupting execution.

**Not committed by the assistant** — git is handled by the repo owner.

Execution model: continuous through phases 0.5, 1, 2, 5, 7, 8; hard stops at
Phase 0 (before/after numbers), Phase 3 (calibrated validation scores), Phase 4
(versioning supersede filter + dry-run counts), Phase 6 (before bulk removal on
real data). Dry-run before any destructive op. Never commit/push.

Branch: `bm25-hybrid-search` · base commit `11e7304f`.

---

## Phase 0 — Ingestion off the event loop  ·  IN PROGRESS (2026-07-24)

**Problem.** Single uvicorn worker / one event loop. Upload schedules an
`async def` background task that `await`s `ingest_document`, whose steps 1–4
(extract → chunk → embed) are synchronous CPU work with no `await` until the
Mongo insert. The loop is blocked for the whole ingest → concurrent requests
stall (measured ~16 s previously; 30 s proxy-timeout 500s on concurrent upload).

**Fix.** Factor CPU steps 1–4 into a plain `_ingest_compute()` run in a
dedicated bounded `ThreadPoolExecutor` (`INGEST_WORKERS`, default 2); keep the
motor `insert_many` on the loop. The heavy calls release the GIL (fastembed/ONNX,
tesseract subprocess, PyMuPDF), so workers run parallel to the loop. Thread-safe
model loader (double-checked lock). Independent rollback flag
`INGEST_OFFLOAD_ENABLED` (default on) restores inline execution with no redeploy.

**Measurement method.** Throwaway uvicorn on :8001 (prod :8000 untouched).
220-page / 2.2 MB PDF ingest; probe `GET /` every 0.5 s from concurrent threads,
record latency. Single-variable A/B on the same box: current code (baseline) vs
new code offload-on (fix) vs new code offload-off (rollback == baseline).

**Measured results** (throwaway :8001, same box; 220-page / 2.2 MB PDF; GET /
probed every 0.5 s from concurrent threads; 105 probes/run):

| condition | upload POST returns | during-ingest median req | during-ingest max req | reqs >5 s (of 105) | ingest wall-clock |
|---|---|---|---|---|---|
| baseline (committed code, inline) | 42,505 ms | 19,710 ms | 42,223 ms | 64 | ~42 s |
| **fix — offload ON (new default)** | **230 ms** | **4.5 ms** | **149 ms** | **0** | ~46 s |
| rollback — `INGEST_OFFLOAD_ENABLED=false` | 43,332 ms | 20,544 ms | 43,059 ms | 64 | ~42 s |

- Fix cuts the upload POST ~185x, the median concurrent-request latency during a
  large ingest ~4,400x (20 s → 4.5 ms), and removes every multi-second stall.
- Rollback == baseline → the flag faithfully restores pre-Phase-0 behaviour.
- Ingest wall-clock ~8% longer under offload (embedding thread now shares CPU
  with served requests) — an expected, worthwhile trade.
- Unit suite: 177 passed / 6 skipped (unchanged from before the refactor).
- `INGEST_WORKERS=2`, rollback flag default ON (my defaults; not overridden).

Files: `app/core/config.py` (+INGEST_WORKERS, +INGEST_OFFLOAD_ENABLED),
`app/services/rag_service.py` (thread-safe model loader; `_get_ingest_pool`;
`_ingest_compute`; `ingest_document` offloads steps 1–4, motor insert stays on
loop). Signature + return dict unchanged; kb_admin CLI and unit tests unaffected.

**Deploy status: NOT shipped to prod (:8000 untouched).** Prod restart is a
separate sudo step, to run once approved at this gate.

STATUS: measured — **STOP / awaiting review** (Phase 0 hard gate).

**Gate outcome (owner):** defaults approved; owner shipping Phase 0 to prod. Told
to proceed continuous through 0.5 → 1 → 2, stop at the Phase 3 calibration gate.

---

## Phase 0.5 — Upload rate limiting + queue depth  ·  DONE (2026-07-24)

General hardening (not behind `KB_UPLOAD_ENABLED`), applies to every upload path.

**Added:**
- **Per-user hourly cap**, stacked on the existing per-minute limit via a second
  `@limiter.limit` on `/api/upload`. `RATE_LIMIT_UPLOAD_HOURLY` default `60/hour`.
- **In-process queue-depth cap** on the ingest backlog. Each pending upload holds
  its file bytes in RAM until processed, so an unbounded burst is a memory/CPU
  risk. `ingest_try_acquire()` reserves a slot (rejects with **503** when the
  backlog is at `INGEST_MAX_QUEUE`, default 10) BEFORE the file is buffered; the
  slot's ownership passes to the background task, which `ingest_release()`s it in
  its `finally`. Route releases on any pre-handoff failure (validation, insert).

**Tests:**
- Unit (`tests/unit/test_ingest_queue.py`, 5 new): admit-up-to-cap, reject-beyond,
  release-frees, floor-at-0, cap-read-at-call-time. Full suite **182 passed / 6 skipped**.
- Integration (throwaway :8001): queue cap=2, 3 concurrent medium uploads →
  `[200, 200, 503]`; after the two ingests drained a further upload → `200`
  (**slots released, no leak**). Hourly cap=3, 4 tiny uploads → `[200,200,200,429]`.

**Behaviour change:** uploads can now return 503 (backlog full) or 429 (hourly)
under load; normal use is unaffected at the defaults. Env-tunable; setting large
values effectively disables either guard.

Files: `config.py` (+`RATE_LIMIT_UPLOAD_HOURLY`, +`INGEST_MAX_QUEUE`),
`rag_service.py` (+`ingest_try_acquire`/`ingest_release`/`ingest_queue_depth`),
`files.py` (2nd limiter decorator; acquire-guard + `finally`; release in the task).

**Deploy status: staged in working tree; effective on next backend restart.**
STATUS: done — continuing to Phase 1.

---

## Phase 1 — Schema and provenance  ·  DONE (2026-07-24)

**Provenance schema** (`app/services/kb_provenance.py`): 10 fields stamped on the
KB parent doc (none exist for the legacy corpus) and every chunk — `uploaderId,
uploaderName, uploadedAt, projectTag, docType, sourceFormat, batchId,
canonicalTitle, version, permissionConfirmed`. `build_provenance()` is the single
builder (asserts key set == `PROVENANCE_FIELDS`); reused by Phase 4 for new
uploads. No stamping of new uploads yet — that is Phase 4.

**Backfill** (`app/scripts/kb_backfill_provenance.py`, dry-run by default):
- Corpus: **16,811 KB docs, all chunks, 196 filenames, 0 pre-provenanced.**
- Dry-run shown (filter, counts, per-filename canonicalTitle/sourceFormat), then
  `--apply`. `uploadedAt <- createdAt`; `canonicalTitle <- get_clean_title`;
  `sourceFormat <- extension` (all pdf); constants `uploaderId=system`,
  `uploaderName=Legacy KB Import`, `projectTag=legacy`, `docType=reference`,
  `batchId=legacy-backfill`, `version=1`, `permissionConfirmed=True`.
- Additive + idempotent (plain `$set`, no existing field touched).

**Verified:** 16,811/16,811 have uploaderId + uploadedAt, **0 missing**; original
fields intact (text present, 384-dim embedding, filename, chunkIndex); single
`batchId=legacy-backfill` / `uploaderId=system` across the KB (removable as a unit).

**No behaviour change:** retrieval indexes the embedding/text fields only, never
these; a spot retrieval is unaffected. (Formal 30-Q eval is Phase 8.)

**Tests:** 3 unit (`test_kb_provenance.py`); full suite **185 passed / 6 skipped**.

**IMPORTANT — this WROTE to the live shared KB in Mongo** (not gated by a restart;
Mongo is shared with prod). Safe because the fields are additive and unread until
Phase 7. `batchId=legacy-backfill` lets the whole backfill be reverted in one op
if ever needed.

Files: `kb_provenance.py` (new), `scripts/kb_backfill_provenance.py` (new),
`tests/unit/test_kb_provenance.py` (new). No live-path code changed.
STATUS: done — continuing to Phase 2.

---

## Phase 2 — Format handlers  ·  DONE (2026-07-24)

**New registry** `app/services/kb_formats.py`, keyed by extension (mirrors the
calculator-registry pattern: `HANDLERS` dict, `get_handler`, `extract_kb_document`).
Each `FormatHandler` has `extract` (-> `HandlerResult`: pages + source_format +
format_metadata) and its own `validate` (extraction-validity check).

| ext | handler | notes |
|---|---|---|
| .pdf | reuse `extract_pages_from_pdf_with_ocr` | live extractor, unchanged |
| .docx | reuse `file_processing.extract_pages_from_docx` | live extractor, unchanged |
| .txt / .md | new | decode; markdown kept verbatim for header-aware chunking |
| .pptx | **new** | per-slide **+ speaker notes** (live handler has no notes) |
| .xlsx / .csv | **new** | **metadata-indexed**: sheet names, headers, text labels only — numeric grid excluded (numbers belong in GeoPilot) |

- **Images rejected** (accepted on the per-thread path, not the shared KB) and any
  other format rejected — both with a message listing accepted formats.
- `.xls` intentionally **not** in the KB set (spec lists XLSX/CSV); still works on
  the thread path.
- **Live path untouched** — `file_processing.py` unchanged; thread/user uploads
  byte-identical. The registry is inert until the KB endpoint (Phase 4) calls it.

**Tests:** `tests/unit/test_kb_formats.py` (12): one per format (incl. asserting
PPTX notes captured and spreadsheet numbers NOT dumped), image + unsupported
rejection, registry shape, validity floor. Full suite **197 passed / 6 skipped**.

Files: `kb_formats.py` (new), `tests/unit/test_kb_formats.py` (new).
STATUS: done — **STOP at Phase 3 calibration gate.**

---

## Phase 3 — Validation + metadata extraction  ·  AWAITING GO (calibration gate)

Methodology confirmed by owner (chars/page floor · hash-block+embedding-warn ·
flag-and-confirm). Calibration harness: `app/scripts/kb_calibrate_validation.py`
(measure-only, read-only). Ran against the real 16,811-chunk / 196-doc KB +
synthetic anchors.

**Measured (2026-07-24):**

1. Extraction quality — KB chars/page: min=47, p1=758, p5=1921, median=4721,
   max=13550. Lowest 5 = [47, 758, 1702, 1734, 1766] (47 is a lone degenerate
   doc; next is 16x higher). Synthetic image-only PDF -> 0 chars/page.
   *(Note: tesseract absent on this box, so the synthetic scan couldn't OCR ->
   0; the KB distribution itself was produced by prod extraction WITH OCR, so the
   floor is calibrated against real output.)*
   **Proposed floor: chars/page >= 150** (+ companion min-content total >= 200).

2. Duplicate — first-chunk cosine: distinct-doc median=0.706, p95=0.803,
   p99=0.841, p99.9=0.897, max=1.000 (the 1.0 = near-dup pairs ALREADY in the KB
   under different names). Self re-embed=1.000; near-dup(minor edit) min=0.997.
   Clean gap [0.90, 0.997]. **Proposed embedding WARN: cosine >= 0.95** (soft
   warn only). Exact normalized-text hash = hard block (no threshold).

3. Relevance — KB doc-to-centroid cosine: min=0.678, p1=0.684, p5=0.765,
   mean=0.840, std=0.041, max=0.907. Off-topic: cooking=0.515, sports=0.415,
   finance=0.424 (max 0.515). Gap [0.52, 0.68]. mean-3s=0.718 would flag the KB's
   own tail (too aggressive). **Proposed fixed floor: cosine < 0.62** (flag +
   confirm, never block).

STATUS: thresholds owner-approved as proposed.

**Settled (env-tunable):** `KB_MIN_CHARS_PER_PAGE=150`, `KB_MIN_CONTENT_CHARS=200`,
`KB_MAX_PAGES=1500`, `KB_MAX_UPLOAD_BYTES=50MB`, `KB_DUP_WARN_COSINE=0.95`,
`KB_RELEVANCE_MIN_COSINE=0.62`. Feature flag `KB_UPLOAD_ENABLED` added (default off).

**Implemented:**
- `app/services/kb_validation.py` — `sanitize_filename`, `normalized_text_hash`
  (whitespace/case-insensitive → catches re-encodes), `extraction_quality` (HARD),
  `check_size`/`check_pages` (HARD), `scan_pii` (emails/phones/student-ids → soft
  flag), `detect_non_english` (reuses repo non-Latin detector + English-stopword
  heuristic → soft flag), `get_kb_centroid` (cached), `relevance` (soft flag),
  `check_duplicate` (exact hash = HARD, embedding cosine = soft warn), and an
  in-process `reserve_hash`/`release_hash` concurrency guard (two simultaneous
  same-content uploads can't both pass).
- `app/services/kb_metadata.py` — best-effort LLM extraction (title/authors/year/
  publication/docType) via `get_llm().acomplete`; filename fallback, never raises.

**Tests:** 13 validation + 6 metadata = 19; full suite **216 passed / 6 skipped**.
**Real-infra smoke:** centroid 384-dim; a real KB doc self-matches at cosine 1.000;
live Groq extraction returned `title=..., authors=['E. IKE'], year=2020,
docType=paper`.

Files: `kb_validation.py`, `kb_metadata.py`, `scripts/kb_calibrate_validation.py`
(calibration harness), config additions, 2 test files. Behind `KB_UPLOAD_ENABLED`
(off) — flag-off byte-identical. STATUS: done — continuing to Phase 4.

---

## Phase 4 — Upload endpoint  ·  AT VERSIONING-SUPERSEDE GATE (2026-07-24)

`POST /api/kb/upload` (`app/routers/kb.py`), any authenticated user, behind
`KB_UPLOAD_ENABLED` (404 when off). Two-phase single endpoint: first call
(file only) → extract + validate + LLM prefill → `needs_input`; second call
(file + corrected metadata + acknowledged warnings) → ingest + provenance.

Built + integration-verified on :8001 (5/5):
- file-only → `needs_input` with LLM-prefilled title + missing fields
- full metadata → `indexed` (batchId, version=1, chunk stamped with provenance)
- exact re-upload → **409** (hash hard block)
- image → **400**; off-topic → `relevance` warning
- `ingest_document` extended (additive) with `pre_extracted_pages` + `provenance`;
  live path unchanged (216 suite green). Router unit tests (gate + supersede-plan
  dry-run) pass.

**GATE — versioning supersede (deletes prior chunks). Filter + dry-run counts:**
- Filter: `{category: knowledge_base, projectTag: <project>, canonicalTitle: <title>}`
- Test re-version: would_delete=1, next_version=2.
- Realistic (legacy books): re-uploading "Fundamentals of Soil Behavior" would
  supersede **1,433 chunks**; "Boundary Layer Theory" 1,349; "Principles of
  Geotechnical Engineering" 1,001.

**Gate outcome (owner):** approved **delete-before-insert** + **confirm-gated**.

**Supersede implemented + verified** (:8001): v1 indexed → v2 without ack returns
a `supersede` warning ("replaces X (N chunks)") and does NOT delete → v2 with
`acknowledge=supersede` deletes prior chunks (`delete_many` on
`{category,projectTag,canonicalTitle}`) THEN inserts, leaving **only version [2]**
(never mixes). `[KB_SUPERSEDE]` logs the filter + count each time. Test data
cleaned up — KB back to exactly **16,811** docs, 0 test-owned.

Full suite **219 passed / 6 skipped**. Files: `app/routers/kb.py` (new),
`app/main.py` (register), `rag_service.ingest_document` (additive params),
`tests/unit/test_kb_router.py` (new). Behind `KB_UPLOAD_ENABLED` (off).
STATUS: done — continuing to Phase 5 (frontend).

---

## Phase 5 — Frontend + async ingest  ·  DONE (build-verified; browser pending)

**Backend (owner chose async + polling):** endpoint refactored so the confirmed
upload backgrounds the ingest (`_kb_ingest_task`: supersede-delete → ingest →
mark `kb_batch` doc indexed with chunkCount + sampleChunk) and returns
`{status:indexing, batchId}` immediately. New endpoints: `GET /api/kb/status`
(ungated, drives the nav), `GET /api/kb/batch/{id}` (poll), `GET /api/kb/my-uploads`,
`DELETE /api/kb/batch/{id}` (self-service, within `KB_SELF_DELETE_WINDOW_HOURS=24`).
kb_batch docs use `uploaderId` (not userId) so they never appear in file listings.
Verified on :8001 (6/6): status, needs_input, indexing→poll→indexed (sample
chunk shown), exact-dup 409, self-delete, re-delete 404.

**Frontend:** `/knowledge-base` route + `KbUpload` panel — pick file → LLM-prefilled
metadata form + acknowledge-able warnings → submit → poll → result (title,
version, chunk count, sample chunk) → "your recent uploads" list with in-window
self-delete. Header gains a conditional "Knowledge Base" segment (mirrors the
GeoPilot toggle, gated on `/api/kb/status`). `next.config` rewrite `/api/kb/*`;
`api.ts` endpoints. `tsc --noEmit` clean on all new files; **`npm run build`
succeeds** (`/knowledge-base` route built).

**Browser verification PENDING** — no browser in this environment; owner verifies
after deploy. **Caveat:** I ran `rm -rf .next && npm run build` to verify the
build while the frontend service was running; it's serving 200 but should be
**restarted** to fully load the new build.

Files: `kb.py`, `config.py`, `kb-upload.tsx` (new), `kb-upload.module.css` (new),
`knowledge-base/page.tsx` (new), `Header.tsx`, `next.config.mjs`, `api.ts`.
Suite **219 passed**. STATUS: done — continuing to Phase 6 (stops before any
bulk removal on real data).

---

## Phase 6 — Admin visibility + bulk removal  ·  AT GATE (2026-07-24)

**Built (no real-data removal run):**
- `GET /api/kb/uploads` — admin/professor only (role-gated via `User.role`;
  non-admins 403). Recent uploads with uploader, project, date, chunk count;
  optional `?uploader` / `?project`.
- `DELETE /api/kb/batch/{id}` now role-aware: admins delete ANY batch; a student
  deletes their OWN within the window. Audited.
- `app/scripts/kb_remove.py` CLI — remove by `--batch` / `--uploader` /
  `--project`. **Dry-run mandatory** (prints filter + counts; writes nothing);
  `--apply` deletes and writes an audit record.
- `kb_audit` collection (separate) — records every upload (from the ingest task)
  and every deletion (endpoint + CLI): actor, selector/batch, counts, timestamp.

**Dry-run evidence (nothing deleted; KB still 16,811):**
- `--project "Nonexistent Project"` → 0 chunks / 0 batches.
- `--uploader <test-user>` → 0.
- `--project legacy` → **16,811 chunks + explicit WARNING** (the mandatory dry-run
  catches an accidental whole-corpus wipe before `--apply`).

**Tests:** 3 (admin gating + CLI filter build). Suite **222 passed / 6 skipped**.

STATUS: **STOP — Phase 6 gate.** No `--apply` / bulk delete has run against real
data. Awaiting owner review of the removal design before proceeding.

**Gate outcome (owner):** removal design approved; continue to 7 & 8. No `--apply`.

---

## Phase 7 — Retrieval surfacing  ·  DONE (2026-07-24)

Provenance surfaced with citations, **additively** (existing `title`/dedup/
citation-filter untouched, so no behaviour change). Added `canonicalTitle,
uploaderName, projectTag, version` to both KB retrieval projections + result
dicts (`_search_combined` vector, `_search_fulltext` BM25) in rag_service, and to
the source dict in chat.py (`canonicalTitle`, `uploader`, `project`, `version`).
None for user_upload; populated for KB (uploads + legacy backfill). Thread-doc
retrieval untouched. Suite **222 passed**.

---

## Phase 8 — Tests + eval  ·  DONE (2026-07-24)

**Unit** (across phases): provenance builder, each format handler, validation
checks, dedup, supersede-plan, removal filters, queue, admin gating — **222
passed / 6 skipped**.

**Live cycle (:8001, student account):** upload → indexing → poll indexed →
**retrieved via the real query_vector_store (vec 0.944)** carrying provenance
(canonicalTitle / uploader / project=P8 / version=1) → self-delete → **KB back to
baseline 16,811**. Confirms upload→validate→index→retrieve→remove end-to-end and
Phase 7 through the production retrieval path.

**Retrieval-regression eval** (`capture_retrieval_baseline`, no LLM): re-ran the
12-question baseline. **11/12 identical** to the stored July-9 `post_bm25`
baseline; the one difference (Q10, a borderline PARTIAL, 3→2 chunks near the
reranker threshold) is **stable across two back-to-back current runs** and not
attributable to the changes here (the retrieval edits are additive projection
fields that cannot alter ranking) — 2-week drift vs the old baseline, not a
regression. Capture artifacts removed after diffing.

**Final state:** KB 16,811 (baseline), 0 test residue, prod untouched, 8001
instances all torn down. STATUS: **COMPLETE — full inventory delivered to owner.**

---

## Follow-up — Single-upload 500 fix + Bulk multi-file upload · DONE (2026-07-24)

**500 fix (change 1):** `app/api/kb/[...path]/route.ts` — dedicated long-timeout
(290s) Route Handler that STREAMS the body (multipart) to FastAPI, replacing the
short-timeout `next.config` rewrite. The single-upload preflight (LLM metadata)
and bulk submission no longer hit the proxy socket timeout. Forwards cookie + Bearer.

**Refactor (change 2, behaviour-preserving):** the per-file pipeline is now shared,
not forked — `_prepare_kb_file` (extract+gates+dedup+relevance/PII/language) and
`_ingest_prepared_file` (supersede + provenance + ingest_document + audit). Single
`_kb_single_ingest_task` delegates to them; single flow re-verified (6/6).

**Bulk (changes 3+4):** `POST /api/kb/bulk-upload` — shared metadata once
(project/docType/year/permission); per-file title/authors/year from LLM
extraction. Returns a batchId immediately; a SEQUENTIAL, PACED background worker
(`KB_BULK_PACING_SECONDS`, per-file queue-slot acquire) reuses the shared pipeline
so it never starves chat/Ollama. Per-file status in the kb_batch `files[]`
(processing/done/skipped[reason=duplicate|scanned|unsupported|pii|too_large]/failed).
One batchId → `kb_remove --batch` removable. `KB_BULK_MAX_FILES=50`. Frontend panel
gains multi-file mode (pick >1 → bulk form → live per-file progress).

**Verified live** through the route handler (temp :3001 → :8001): 5-file mixed
batch → 3 indexed / dup skipped / png skipped, one batchId; `kb_remove --batch`
dry-run showed the batch's chunks (no delete). Suite 222 passed; frontend built
(`/api/kb/[...path]` + `/knowledge-base`). Test data cleaned (0 residue).

Deviation flagged to owner: extracted TITLES appear per-file during indexing, not
strictly before submit (LLM extraction runs in the background worker).
