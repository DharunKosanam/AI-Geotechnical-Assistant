# Instrument data feature — rollout log

Flag: `INSTRUMENT_PARSERS_ENABLED` (default `false`). Run mode: unattended,
Phases 1–6, 2026-08-18. Nothing committed; tree left dirty for review.

---

## ⚠ Blockers found before Phase 1 (each stated once)

1. **The fixtures are not on this machine.** `python_backend/tests/fixtures/`
   did not exist and a full-disk search (`find / -xdev`) found no
   `extraction_manifest.json`, no `ODiSI_6000*`, no `2024-05-10.dat`. Per the
   "make the call, log it, keep going" precedent (`design/reference/ROLLOUT-LOG.md`)
   the run proceeds with **SYNTHETIC stand-ins generated from Section 3's written
   description** — `python_backend/scripts/make_synthetic_instrument_fixtures.py`
   (seeded, deterministic) writes them to `python_backend/tests/fixtures/synthetic/`
   with a `README_SYNTHETIC.md`. They reproduce every structural claim in
   Section 3 (header keys, row kinds, 7795 gages, x-axis 0.08→20.4424,
   497/534/606/600 timesteps, contiguous passes 16:23:46→16:28:14, 33 header
   lines, start-inclusive/end-exclusive manifest, 864,000 rows at 10 Hz,
   RECORD from 249671, four `*_kPa` channels with the stated means/maxima)
   but **the signals are synthetic traffic**. Consequently:
   - every "verified against the fixture" claim below is against the synthetic
     files; **the real files must be re-run through `scripts/verify_parsers.py`**
     and any discrepancy treated as a Section-3 contradiction;
   - I could NOT confirm Section 3's claims against reality (quoting, exact
     header key spelling, separator line, label columns). The parsers were
     written tolerant on each of those points (see Phase 1/2 notes) and warn
     rather than adapt silently.
2. **No `systemctl restart` possible.** This account has no sudo
   (`systemctl restart geoai-backend` → "Interactive authentication required").
   Prod (`geoai-backend` :8000, `geoai-frontend` :3000) therefore keeps running
   the PRE-CHANGE code for the whole run; the .env flag is `false` so the first
   restart you do is flag-off. Per-phase "restart + clean startup" checks were
   done on throwaway processes: uvicorn on **:8010** (same venv, same .env, flag
   toggled per run) and a Next build+start on **:3010** from a scratchpad copy of
   the tree (`rsync` + `node_modules` symlink) so the live `.next` is never
   removed under the running `next start`. Both throwaways are killed at the end.
   Exact restart commands are in the final report.
3. **No charting library is installed in the frontend** (`package.json`: no
   recharts/d3/chart.js/plotly; `node_modules` grep empty) and installing is
   forbidden. The Phase-5 strain-envelope chart is an inline SVG component
   (`app/workspace/components/strain-chart.tsx`, no dependency).
4. **Fixture-size vs proxies.** `next.config.mjs` rewrites truncate bodies at
   10 MB and time out at 30 s (documented in the repo), and the GeoPilot document
   endpoint caps uploads at 5 MB. Dataset uploads (22–58 MB) therefore go through
   a dedicated streaming Route Handler (Phase 3/4) and a separate size ceiling
   applies only to sniffed instrument files. nginx `client_max_body_size 55M`
   still blocks the 58 MB `.dat` end-to-end until you apply the change in the
   final report (not applied by me).

Interpretation decisions taken without you (also collected in the final report):

- **"parsers/ sibling of calculators/"** — the calculators live at
  `python_backend/app/workspace/calculators/` and a `python_backend/app/workspace/parsers/`
  package already existed (the CPT text parser). The instrument parsers were added
  INTO that package (`base.py`, `registry.py`, `odisi.py`, `campbell.py`);
  `cpt.py` is untouched and still exported.
- **"the existing upload handler"** = GeoPilot's `POST /api/workspace/documents`
  (the panel, the calculators, the result cards and the trigger phrases all live
  in GeoPilot). Main Chat's `/api/upload` (RAG/embedding path) is NOT touched —
  it is Main Chat, which is on the do-not-touch list, and it already rejects
  `.tsv`/`.dat` by extension so instrument files cannot enter the embedding
  path through it. "The PDF still embeds normally" is therefore true by
  construction (that handler is byte-identical) and was re-checked at Phase 3.
- Flag read uses the project's exact existing pattern
  `os.getenv(...).strip().lower() in ("1","true","yes","on")` — the repo's
  pattern includes `"on"`; the brief's three-value list is a subset.
- `scripts/` = `python_backend/scripts/` (new; the backend had only `app/scripts/`
  which are app-context CLIs). `verify_parsers.py` needs no DB/network/app.

---

## Phase 1 — Parser package and ODiSI reader ✅

**Files created**
- `python_backend/app/workspace/parsers/base.py` — `ParserResult` (`parser_id`,
  `dataset_kind`, `metadata`, `arrays`, `warnings`; plus `shapes()`/`dtypes()`),
  `Parser` (id, dataset_kind, label, advisory extensions, `sniff`, `parse`),
  `ParserError`, `SNIFF_BYTES = 2048`, `ProgressCallback`.
- `python_backend/app/workspace/parsers/registry.py` — `register()`, `get()`,
  `all_parsers()`, `dataset_kinds()`, `sniff(head_bytes)` (pure over the first
  2 KB, first-match, exceptions in a signature never propagate).
- `python_backend/app/workspace/parsers/odisi.py` — `dataset_kind="strain_distributed"`,
  `parser_id="odisi_tsv"`. Streams the file in binary mode line by line (exact
  byte progress). Header → snake_case keys (`Gage Pitch (mm):` → `gage_pitch_mm`),
  raw lines under `metadata["_raw_header"]`, canonical derived keys
  (`gage_pitch_mm`, `sensor_length_m`, `measurement_rate_hz`, `sensor_serial`,
  `tare_name` (kept as text — "0409" is a label), `units`, `n_gages`,
  `n_timesteps`, `x_min_m/x_max_m`, `first/last_timestamp`, `duration_s`,
  `sample_rate_hz` inferred from timestamps). Arrays: `x_axis` f64, `tare` f64,
  `strain` f32 (n_timesteps × n_gages, stored AS RECORDED — tare kept alongside
  so "relative to tare" is an explicit downstream subtraction), `timestamps`
  datetime64[ms], `timestamp_text`. Values from column 3 on every data row per
  Section 3; if the x-axis row's column 3 is not numeric the value column is
  auto-detected AND a warning is appended (never silent). Warnings (not raises)
  for tare/x-axis length mismatch, ragged measurement rows (padded NaN /
  truncated, one aggregated warning), header-declared gage count ≠ parsed,
  unparseable timestamps, non-numeric tokens, duplicate Tare/x-axis rows.
  Raises `ParserError` only when there is neither an x-axis row nor any
  measurement row. Separator line optional; leading BOM tolerated; keys with or
  without trailing ':'.
- `python_backend/app/workspace/parsers/__init__.py` — registers `ODISI_PARSER`
  (registration order = sniff precedence); CPT exports unchanged.
- `python_backend/scripts/verify_parsers.py` — sniff + parse + report (parser id,
  kind, key metadata, shapes/dtypes, warnings, wall time, tracemalloc peak and
  RSS peak); compares with a sibling `extraction_manifest.json` if present and
  reports the boundary convention. Exit 0 = parsed or clean no-match, 1 = error, 2 = usage.
- `python_backend/scripts/make_synthetic_instrument_fixtures.py` — see blocker 1.
- `python_backend/tests/unit/test_instrument_parsers.py` — 11 tests (registry,
  sniff negatives for PDF/CSV/CPT/prose, clean parse, no-separator, each warning
  path, value-column auto-detect, ParserError, monotonic progress).

**Files modified**
- `.gitignore` — added `python_backend/tests/fixtures/` and
  `python_backend/data/instrument_datasets/`.
- `python_backend/.env` — `grep` showed no `INSTRUMENT_PARSERS_ENABLED`; file
  ended with a newline (checked with `xxd`); appended `INSTRUMENT_PARSERS_ENABLED=false`
  (line 47). Nothing reads it yet.

**Verification (synthetic fixtures — see blocker 1). Verbatim:**

```
== tests/fixtures/synthetic/odisi/ODiSI_6000_2026-04-09_16-18-51_ch3_pass_001.tsv
  elapsed_s: 4.526
  peak_tracemalloc_mb: 31.8
  peak_rss_mb: 61.9
  shapes: {"x_axis": [7795], "tare": [7795], "strain": [497, 7795], "timestamps": [497], "timestamp_text": [497]}
  dtypes: {"x_axis": "float64", "tare": "float64", "strain": "float32", "timestamps": "datetime64[ms]", "timestamp_text": "<U23"}
  warnings: []
  x_axis_first: 0.08
  x_axis_last: 20.4424
  manifest_span: 497
  convention: end-exclusive (end - start == parsed timesteps)
  count_matches: True
== tests/fixtures/synthetic/odisi/ODiSI_6000_2026-04-09_16-18-51_ch3_pass_002.tsv
  elapsed_s: 4.864
  peak_tracemalloc_mb: 34.2
  peak_rss_mb: 64.0
  shapes: {"x_axis": [7795], "tare": [7795], "strain": [534, 7795], "timestamps": [534], "timestamp_text": [534]}
  dtypes: {"x_axis": "float64", "tare": "float64", "strain": "float32", "timestamps": "datetime64[ms]", "timestamp_text": "<U23"}
  warnings: []
  x_axis_first: 0.08
  x_axis_last: 20.4424
  manifest_span: 534
  convention: end-exclusive (end - start == parsed timesteps)
  count_matches: True
== tests/fixtures/synthetic/odisi/ODiSI_6000_2026-04-09_16-18-51_ch3_pass_003.tsv
  elapsed_s: 5.605
  peak_tracemalloc_mb: 38.7
  peak_rss_mb: 68.6
  shapes: {"x_axis": [7795], "tare": [7795], "strain": [606, 7795], "timestamps": [606], "timestamp_text": [606]}
  dtypes: {"x_axis": "float64", "tare": "float64", "strain": "float32", "timestamps": "datetime64[ms]", "timestamp_text": "<U23"}
  warnings: []
  x_axis_first: 0.08
  x_axis_last: 20.4424
  manifest_span: 606
  convention: end-exclusive (end - start == parsed timesteps)
  count_matches: True
== tests/fixtures/synthetic/odisi/ODiSI_6000_2026-04-09_16-18-51_ch3_pass_004.tsv
  elapsed_s: 5.611
  peak_tracemalloc_mb: 38.3
  peak_rss_mb: 68.6
  shapes: {"x_axis": [7795], "tare": [7795], "strain": [600, 7795], "timestamps": [600], "timestamp_text": [600]}
  dtypes: {"x_axis": "float64", "tare": "float64", "strain": "float32", "timestamps": "datetime64[ms]", "timestamp_text": "<U23"}
  warnings: []
  x_axis_first: 0.08
  x_axis_last: 20.4424
  manifest_span: 600
  convention: end-exclusive (end - start == parsed timesteps)
  count_matches: True
```

All four passes: `x_axis` 7795 (first 0.08, last 20.4424), `tare` 7795,
`strain` float32 with shapes (497|534|606|600, 7795), zero warnings, timestep
counts equal to the manifest. Manifest convention: **end-exclusive**
(`end - start == parsed timesteps`) — no off-by-one seen; the check code path
that would report end-INCLUSIVE is in `verify_parsers._manifest_check`.
Wall time 4.3–5.5 s per file; peak RSS 62–69 MB (tracemalloc peak 32–39 MB).

Non-instrument inputs (must not crash): PDF (`clean_text.pdf` from an earlier
session's scratchpad — the repo itself contains no PDF), a plain CSV, the CPT
sample, a PNG → all `parser_id: None / no matching parser`, exit 0. Missing path
→ exit 1.

```
== /tmp/claude-1099451091/-home-dharunk-geotech-AI-Geotechnical-Assistant/d19125b4-1d7d-491f-b5bb-df3d7eadb89a/scratchpad/clean_text.pdf
  parser_id: None
  result: no matching parser (falls through to the document path)
exit=$?
== app/workspace/data/sample_sounding.CPT
  parser_id: None
  result: no matching parser (falls through to the document path)
exit=$?
```

Unit tests: `pytest tests/unit/test_instrument_parsers.py` → 11 passed; whole
unit suite (minus the live-Ollama `test_query_rewriter.py` baseline) → 423
passed, 6 skipped.

Flag-off parity: nothing in the app imports the new modules yet
(`app/workspace/parsers/__init__.py` only gained pure-Python exports; the
running app imports `parse_cpt_text` from `parsers.cpt` directly). No route,
no config constant, no frontend change in this phase.

Open (Phase 1): the real ODiSI files may spell header keys differently from the
synthetic ones — the canonical-key derivation is substring-based
(`gage_pitch`, `sensor_length`, `rate`+`hz`, `serial`, `tare_name`, `units`),
and `_raw_header` always carries the truth. If the real x-axis row does not put
values at column 3, the parser warns and auto-detects.

---

## Phase 2 — Campbell `.dat` reader ✅

**Files created**
- `python_backend/app/workspace/parsers/campbell.py` — `dataset_kind="pressure_timeseries"`,
  `parser_id="campbell_dat"`. Signature (`sniff_campbell`): one of the first 8
  lines is a comma-separated header row containing `TIMESTAMP` AND `RECORD` AND
  at least one `*_kPa` field (case-insensitive) — a generic CSV never matches
  (tested: `TIMESTAMP,RECORD,Temp_C` → no; `TIMESTAMP,TP4144_kPa` without RECORD
  → no). Channels are detected dynamically (every `*_kPa` header field, column
  order); other columns are listed under `metadata["other_columns"]` and
  ignored. Tolerates the classic TOA5 dressing (env line + units + processing
  rows → kept in `_raw_header`, skipped) and double-quoted fields. Streams line
  by line, converts in 100k-row chunks. Arrays: `timestamps` datetime64[ms],
  `record` int64, `pressure` float32 (n_samples × n_channels). Metadata:
  `channel_names`, `n_channels`, `n_samples`, inferred `sample_rate_hz` /
  `sample_interval_s` (median timestamp diff), `first/last_timestamp`,
  `duration_s`, `record_first/last`, per-channel `column_min/mean/max`
  (mean in float64), `n_time_gaps`. Warnings (never raises) for wrong-width rows
  (skipped+counted), NAN/non-numeric tokens, unparseable timestamps,
  non-monotonic time, RECORD discontinuities, time gaps > 1.5× median interval.
  `ParserError` only if no such header row exists or there are no data rows.
- 8 more unit tests in `tests/unit/test_instrument_parsers.py` (sniff
  positives/negatives incl. TOA5+quotes, dynamic 2/4/6 channels, extra column,
  each warning path, ParserError, progress) → file total 19 passed.

**Files modified**
- `python_backend/app/workspace/parsers/__init__.py` — registers
  `CAMPBELL_PARSER` after `ODISI_PARSER` (sniff precedence).
- `python_backend/scripts/verify_parsers.py` — reports the pressure per-channel
  max/mean (float64); `--tracemalloc` is now opt-in because tracing slowed the
  864k-row parse from 2.5 s to 20 s and would have misreported the wall time.

**Verification (synthetic `.dat` — see blocker 1). Verbatim:**

```
== tests/fixtures/synthetic/pressure/2024-05-10.dat
  parser_id: campbell_dat
  dataset_kind: pressure_timeseries
  elapsed_s: 2.481
  rss_before_mb: 26.5
  peak_rss_mb: 144.4
  peak_tracemalloc_mb: 111.5
  metadata: {"source_filename": "2024-05-10.dat", "file_size_bytes": 49407868, "sample_rate_hz": 10.0, "units": "kPa", "first_timestamp": "2024-05-10T00:00:00.000", "last_timestamp": "2024-05-10T23:59:59.900", "duration_s": 86399.9, "n_samples": 864000, "n_channels": 4, "channel_names": ["TP4144_kPa", "TP4145_kPa", "TP4148_kPa", "TP4149_kPa"], "record_first": 249671, "record_last": 1113670, "column_min": [11.23900032043457, 9.545999526977539, 9.765000343322754, 7.144999980926514], "column_mean": [11.60999976382653, 9.930000969167109, 10.159999773212053, 7.54000076285391], "column_max": [29.959999084472656, 27.8700008392334, 40.90999984741211, 39.869998931884766], "n_time_gaps": 0, "other_columns": []}
  shapes: {"timestamps": [864000], "record": [864000], "pressure": [864000, 4]}
  dtypes: {"timestamps": "datetime64[ms]", "record": "int64", "pressure": "float32"}
  warnings: []
  pressure_max_per_channel: [29.96, 27.87, 40.91, 39.87]
  pressure_mean_per_channel: [11.61, 9.93, 10.16, 7.54]
exit=0
```

864,000 rows; first `2024-05-10T00:00:00.000`, last `2024-05-10T23:59:59.900`;
four channels detected as `TP4144_kPa, TP4145_kPa, TP4148_kPa, TP4149_kPa`;
per-column max 29.96 / 27.87 / 40.91 / 39.87 and mean 11.61 / 9.93 / 10.16 /
7.54 (within 0.01 of Section 3 — by construction of the synthetic file, so
this proves the parser reads what is written, not that Section 3 is right).
Parse time 2.5 s; peak RSS 144 MB (Python-heap peak 112 MB). ODiSI timings
untraced for reference: `  elapsed_s: 0.52
  peak_rss_mb: 68.3`.

Unit suite (minus live-Ollama baseline): 431 passed, 6 skipped.

**nginx change — generated as text, NOT applied** (I cannot read
`/etc/nginx/sites-enabled` and must not edit/reload nginx). The brief states
`client_max_body_size` is 55M and the `.dat` fixture is 58 MB, so an end-to-end
upload of that file will be rejected by nginx with 413 until this lands:

```nginx
# /etc/nginx/sites-enabled/<chenglin-geoai site>  — inside the server { } block
# (or the location / block that proxies to Next.js :3000)
#   was:  client_max_body_size 55M;
#   now:  instrument datasets are 22-58 MB; give headroom for a 24 h logger day.
client_max_body_size 128M;
```
Then: `sudo nginx -t && sudo systemctl reload nginx`. Related, in the app
(applied): the sniffed-dataset branch uses its own ceiling
`INSTRUMENT_MAX_UPLOAD_MB` (default 200) instead of the 5 MB document cap; the
Next.js proxy for dataset uploads is a streaming Route Handler (no 10 MB
rewrite cap). Both are in Phase 3/4.

Flag-off parity: still no runtime consumer of the new modules (library code
only). Frontend untouched.

---

## Phase 3 — Persistence, jobs, upload wiring ✅

**Files created**
- `python_backend/app/workspace/dataset_files.py` — disk layer:
  `INSTRUMENT_DATA_DIR/raw/<dataset_id><ext>` (retained raw upload, for retry)
  and `INSTRUMENT_DATA_DIR/npz/<dataset_id>.npz` (`np.savez_compressed`, written
  to `.part` then renamed); `load_arrays` (`allow_pickle=False`); `json_safe`
  (NaN/inf → null so metadata is valid JSON+BSON).
- `python_backend/app/workspace/dataset_store.py` — Mongo pointer docs + job
  docs (`workspace_datasets`, `workspace_parse_jobs`; every query `user_id`-scoped;
  malformed/foreign ids → None → 404). Job states `queued → parsing → parsed | failed`
  with `progress` 0..100 and `error`; the dataset doc mirrors status/progress/error
  so the panel needs one query. Read-time staleness rule (`effective_state`):
  queued/parsing with no update for `INSTRUMENT_PARSE_TIMEOUT_SECONDS` (900) →
  reported failed "interrupted … Retry" (nothing mutated on startup — mirrors
  `effective_ingest_status`). `dataset_badge` → `DFOS · 7795 gages` /
  `Pressure · 4 channels`. `set_segments` for Phase 6.
- `python_backend/app/workspace/instrument_ingest.py` — `ingest_upload`
  (streams the rest of the upload to disk in 1 MB chunks after the 2 KB sniff
  head; ceiling `INSTRUMENT_MAX_UPLOAD_MB`=200 → 413 and partial file removed;
  creates dataset+job docs; schedules the job) and `run_parse_job`
  (BackgroundTasks → parse in a dedicated `ThreadPoolExecutor`
  (`INSTRUMENT_PARSE_WORKERS`=1) so the event loop stays free; progress
  callback throttled to ≥1 %/≥0.5 s, fire-and-forget `run_coroutine_threadsafe`;
  npz written in the pool; `mark_parsed`/`mark_failed` with a user-facing
  message; never raises). `retry_dataset` re-queues from the retained raw file
  (409 if it is gone). Same shape as the existing ingest offload
  (`rag_service._get_ingest_pool` + status doc).
- `python_backend/app/workspace/dataset_routes.py` — `GET /api/workspace/datasets`,
  `GET /datasets/{id}` (full metadata incl. `_raw_header`, paths, never arrays),
  `GET /datasets/jobs/{job_id}`, `POST /datasets/{id}/retry`, `DELETE /datasets/{id}`
  (pointer + jobs + npz + raw). Registered like highlights: `register(app)`
  includes the router ONLY when the flag is on (route table identical when
  off) AND every route carries a call-time gate that answers exactly like an
  absent route (`404 {"detail":"Not Found"}`) then the workspace gate.
- `python_backend/tests/integration/test_workspace_datasets.py` — 13 tests:
  flag-off status bytes == `{"enabled":true}`, flag-off dataset routes 404 with
  the absent-route body, flag-off TSV+PDF take the document path with the
  pre-feature response shape and no artifact/job/disk; flag-on ODiSI and
  Campbell uploads parse to artifacts (badge, metadata, shapes, dtypes,
  npz on disk, job 100 %) with NO ndarray in the Mongo doc; PDF still a
  document; a >2 KB non-instrument file is re-joined whole after the sniff;
  failed parse → error → retry creates a new job; delete removes doc+jobs+files;
  user scoping; bad ids; staleness rule; register() adds nothing when off.

**Files modified**
- `python_backend/app/core/config.py` — `INSTRUMENT_PARSERS_ENABLED` (project
  pattern), `INSTRUMENT_DATA_DIR` (default `python_backend/data/instrument_datasets`,
  gitignored), `INSTRUMENT_MAX_UPLOAD_MB`=200, `INSTRUMENT_PARSE_WORKERS`=1,
  `INSTRUMENT_PARSE_TIMEOUT_SECONDS`=900.
- `python_backend/app/core/database.py` — the two collections; indexes created in
  `ensure_indexes` ONLY when the flag is on (`user_id_1_created_at_-1`, `dataset_id_1`).
- `python_backend/app/main.py` — `workspace_dataset_routes.register(app)` after
  `highlights.register(app)`.
- `python_backend/app/workspace/routes.py` — `GET /status` adds
  `instrument_parsers: true` + `instrument_extensions` ONLY when both flags are on;
  `POST /documents` gained the sniff branch:
  ```python
  if config.INSTRUMENT_PARSERS_ENABLED:
      head = await file.read(SNIFF_BYTES)
      parser_id = parser_registry.sniff(head)
      if parser_id is not None:
          return await instrument_ingest.ingest_upload(file, head, parser_id, current_user.id, background_tasks)
      raw = head + await file.read()
  else:
      raw = await file.read()          # exactly the pre-feature line
  ```
  (+ a `BackgroundTasks` parameter, injected by FastAPI, no request/response change).
- `python_backend/app/workspace/instrument_ingest.py` stamps the ORIGINAL upload
  name into `metadata["source_filename"]` (the file on disk is named by id).

**Not touched (proof):** `git diff --stat -- app/routers/files.py app/services/rag_service.py app/routers/chat.py app/dependencies/auth.py app/routers/auth.py` → empty. `/api/upload` (RAG/embedding path) is byte-identical, so "the PDF still embeds normally" holds by construction; the GeoPilot document path never embedded anything.

**Verification — throwaway processes (prod untouched, see blocker 2).**
Throwaway user `instrument.rollout.check@example.com` (id `6a84eb6fbdc1595e65f5cbf5`)
created via `/auth/signup` on the shared Atlas DB — see residue note below.

*Clean startup, both flag states* (uvicorn on :8010, same venv + .env, flag on the command line):
```
INFO:     Started server process [971104]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8010 (Press CTRL+C to quit)
--- (flag on)
INFO:     Started server process [972025]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8010 (Press CTRL+C to quit)
```

*Flag OFF parity vs the git-HEAD oracle* (`git archive HEAD python_backend` run on :8011 with the same .env; uuids normalised). Byte-identical for every probe — status bytes, the five dataset paths (absent-route 404s), small TSV / small .dat / PDF uploads (document path, `status: ready`), the 27 MB TSV (413 on both — the document cap), and the document listing. GeoPilot document uploads never create Mongo documents (in-memory session store), so the "diff the resulting Mongo documents" check is: counts for the throwaway user after the flag-off uploads → `files 0, files chunks 0, workspace_datasets 0, workspace_parse_jobs 0` (identical to the oracle by construction: nothing is written). Verbatim:
```
### :8010
status:      {"enabled":true} [200]
GET datasets: {"detail":"Not Found"} [404]
GET job:      {"detail":"Not Found"} [404]
POST retry:   {"detail":"Not Found"} [404]
POST datasets:{"detail":"Not Found"} [404]
upload small tsv: {"id":"<uuid>","filename":"small_pass.tsv","extension":".tsv","status":"ready"} [201]
upload small dat: {"id":"<uuid>","filename":"small_pressure.dat","extension":".dat","status":"ready"} [201]
upload pdf:       {"id":"<uuid>","filename":"clean_text.pdf","extension":".pdf","status":"ready"} [201]
upload 27MB tsv:  {"detail":"File too large. Max 5 MB."} [413]
list documents:   {"documents":[{"id":"<uuid>","filename":"clean_text.pdf","extension":".pdf","status":"ready"},{"id":"<uuid>","filename":"small_pressure.dat","extension":".dat","status":"ready"},{"id":"<uuid>","filename":"small_pass.tsv","extension":".tsv","status":"ready"}]} [200]
### :8011
status:      {"enabled":true} [200]
GET datasets: {"detail":"Not Found"} [404]
GET job:      {"detail":"Not Found"} [404]
POST retry:   {"detail":"Not Found"} [404]
POST datasets:{"detail":"Not Found"} [404]
upload small tsv: {"id":"<uuid>","filename":"small_pass.tsv","extension":".tsv","status":"ready"} [201]
upload small dat: {"id":"<uuid>","filename":"small_pressure.dat","extension":".dat","status":"ready"} [201]
upload pdf:       {"id":"<uuid>","filename":"clean_text.pdf","extension":".pdf","status":"ready"} [201]
upload 27MB tsv:  {"detail":"File too large. Max 5 MB."} [413]
list documents:   {"documents":[{"id":"<uuid>","filename":"clean_text.pdf","extension":".pdf","status":"ready"},{"id":"<uuid>","filename":"small_pressure.dat","extension":".dat","status":"ready"},{"id":"<uuid>","filename":"small_pass.tsv","extension":".tsv","status":"ready"}]} [200]
```

*Flag ON* (:8010 restarted with `INSTRUMENT_PARSERS_ENABLED=true`):
```
status: {"enabled":true,"instrument_parsers":true,"instrument_extensions":[".tsv",".txt",".dat",".csv"]}
upload 27 MB TSV -> {"id":"6a84ec13a2075a274422664a","kind":"dataset","filename":"ODiSI_6000_2026-04-09_16-18-51_ch3_pass_001.tsv","extension":".tsv","status":"queued","progress":0,"dataset_id":"6a84ec13a2075a274422664a","job_id":"6a84ec13a2075a274422664b","parser_id":"odisi_tsv","dataset_kind":"strain_distributed","label":"DFOS","badge":"DFOS","size_bytes":27271898} [201] 0.485s
poll 1: state=parsing progress=0
poll 2: state=parsing progress=99
poll 3: state=parsed  progress=100 elapsed_s=1.253
GET /datasets/{id}: badge "DFOS · 7795 gages", shapes {x_axis:[7795], tare:[7795], strain:[497,7795], timestamps:[497], timestamp_text:[497]}, dtypes strain float32, warnings [], npz_path .../npz/6a84ec13a2075a274422664a.npz, raw_path .../raw/6a84ec13a2075a274422664a.tsv
PDF upload (flag on): {"id":"<uuid>","filename":"clean_text.pdf","extension":".pdf","status":"ready"} [201]   <- document path, unchanged
upload 49 MB .dat -> {... "kind":"dataset","parser_id":"campbell_dat","dataset_kind":"pressure_timeseries","label":"Pressure","size_bytes":49407868} [201] 0.584s
poll: parsing 0 -> 34 -> 80 -> 99 -> parsed 100
list: 2024-05-10.dat parsed 100 "Pressure · 4 channels" shapes {timestamps:[864000], record:[864000], pressure:[864000,4]} channel_names [TP4144_kPa,TP4145_kPa,TP4148_kPa,TP4149_kPa] n_samples 864000 sample_rate_hz 10.0 first 2024-05-10T00:00:00.000 last 2024-05-10T23:59:59.900 column_max [29.96,27.87,40.91,39.87]
Mongo pointer docs (read-only probe): doc_size_bytes 1587 (.dat) / 3370 (.tsv); top-level types = str/int/dict/list/datetime/float only — NO ndarray, no 'arrays'/'strain'/'pressure' key; metadata keys carry _raw_header, shapes, npz_path.
Mongo counts for the throwaway user after both uploads: files 0, files chunks 0 (=> no embeddings), workspace_datasets 2, workspace_parse_jobs 2.
Disk: npz 11.3 MB (.tsv) + 9.2 MB (.dat); raw 27.3 MB + 49.4 MB.
failure path: header-only .dat -> status failed, error "Could not parse this file: Campbell header found but the file has no data rows."; POST retry -> queued, new job_id -> failed again (same file), honest.
cleanup: DELETE x3 -> {"deleted":...}; list [] ; npz/raw dirs empty; Mongo counts back to 0 datasets / 0 jobs.
```

Tests: `pytest tests/integration/test_workspace_datasets.py tests/integration/test_workspace_routes.py tests/integration/test_workspace_chat_routes.py tests/integration/test_workspace_history.py` → 38 passed. Unit suite unchanged (431 passed).

**Residue (needs your confirmation to delete, per the standing rule):** the
throwaway user doc `users.email = instrument.rollout.check@example.com`
(`_id 6a84eb6fbdc1595e65f5cbf5`). All its datasets/jobs were deleted through the
API; `files` has 0 docs for it. Later phases add `workspace_runs`/`workspace_threads`
for it (calculator runs) — enumerated in the final report with the exact
cleanup command.

---

## Phase 4 — Frontend: dataset panel ✅

Design language: existing dark tokens only (`--s1..--s4`, `--line`, `--t1..--t3`,
`--accent`, `--warn-*`, `--danger-*`, `--r1..--r3`, `--hi`, `--e1`, IBM Plex via
`--font-sans/--font-cond/--font-mono`), lucide icons at the panel's existing
14 px row size / 16 px card size, group labels reuse the History group treatment.
No new tokens.

**Files created**
- `app/api/workspace/upload/route.ts` — streaming Route Handler
  `POST /api/workspace/upload → FastAPI POST /api/workspace/documents` (the SAME
  backend handler; needed because the `/api/workspace/:path*` rewrite truncates
  bodies at 10 MB / 30 s). Used by the page ONLY when the backend advertises the
  capability.
- `app/workspace/instruments.ts` — types (`DatasetRecord`, `ParseJob`, `Segment`),
  `summaryTiles()` (metric tiles per dataset kind, from parser metadata only),
  `statusLine()`, `CALCULATOR_HINT` (trigger phrase per kind), `kindTitle()`.
- `app/workspace/components/dataset-rows.tsx` — `DatasetRow`: kind icon
  (Waves = DFOS, Gauge = pressure), filename, compact badge (`DFOS · 7795 gages` /
  `Pressure · 4 channels`, plus `· N events` when segments exist), **progress bar +
  percentage on the row** (`role=progressbar`, live region) while queued/parsing,
  failed state = error text (2-line clamp, full text in the tooltip) + retry
  control, expander that lists segments as children (Phase 6 events), remove (×).
- `app/workspace/components/dataset-cards.tsx` — `DatasetMessage`: the ONE
  thread message per dataset. Rendered from the live record so it updates in
  place: progress line (queued/parsing %) → parse-summary card (metric tiles,
  parser warnings, and the explicit line "No calculation has run on this
  dataset … To compute, type `run dfos pass strain`") → or failure text.
- `app/workspace/__tests__/instruments.test.ts` (6) and
  `app/workspace/__tests__/dataset-panel.test.tsx` (6, page-level with fetch mocked
  per URL): flag-off page has no Datasets group / `.cpt,.CPT` picker / no dataset
  requests / uploads to `/api/workspace/documents`; flag-on shows both groups,
  widens the picker to `.cpt,.CPT,.tsv,.txt,.dat,.csv`, uploads via
  `/api/workspace/upload`, a sniffed upload becomes a row with the on-row
  progress bar (aria-valuenow follows the job), exactly one `role=status`
  progress message which is REPLACED (not appended) by the summary card when the
  job completes, the card shows the tiles + hint; failed row shows error + retry;
  expander lists segments; warnings render.

**Files modified**
- `app/workspace/page.tsx` — reads `/api/workspace/status` once; `instrument_parsers
  === true` (absent = off) enables: `GET /api/workspace/datasets` on mount, the
  grouped panel (`Documents` / `Datasets` with counts), the streaming upload
  path, the dataset branch on upload (`kind: "dataset"` → row + one thread
  message), 1 s polling of active jobs → dataset refetch on terminal state,
  remove (DELETE) / retry (POST retry), the extra welcome sentence, the widened
  `accept`. Flag off: every one of those is behind `instrumentEnabled` and the
  markup is exactly the previous flat list (see tests). "New session" clears
  documents + thread as before; datasets are durable artifacts (like History)
  and stay listed — decision logged in the final report.
- `app/workspace/workspace.module.css` — dataset/tile/progress classes appended
  before the trailing media query (which must stay last).

**Verification.**
- `npx tsc --noEmit`: no errors in `app/workspace`, `app/api/workspace`.
- `npx vitest run`: 6 files, 61 tests passed (12 new).
- Build — in the scratchpad copy (`rsync` of the tree + hard-linked
  `node_modules`; Turbopack refuses a symlinked `node_modules`), `.env.local`
  repointed to :8010 for the rewrites, `rm -rf .next && npm run build`:
```
> assistants-nextjs@0.1.0 build
> next build
▲ Next.js 16.1.6 (Turbopack)
- Environments: .env.local
  Creating an optimized production build ...
✓ Compiled successfully in 4.9s
  Skipping validation of types
  Collecting page data using 2 workers ...
[OpenAI] No root .env file found (OK if using Python backend)
[OpenAI] No root .env file found (OK if using Python backend)
  Generating static pages using 2 workers (0/14) ...
  Generating static pages using 2 workers (3/14) 
  Generating static pages using 2 workers (6/14) 
  Generating static pages using 2 workers (10/14) 
✓ Generating static pages using 2 workers (14/14) in 465.9ms
  Finalizing page optimization ...
Route (app)
┌ ○ /
├ ○ /_not-found
├ ƒ /api/assistants
├ ƒ /api/assistants/files
├ ƒ /api/assistants/threads
├ ƒ /api/assistants/threads/[threadId]/actions
├ ƒ /api/assistants/threads/[threadId]/history
├ ƒ /api/assistants/threads/[threadId]/messages
├ ƒ /api/assistants/threads/[threadId]/messages-history
├ ƒ /api/assistants/threads/[threadId]/title
├ ƒ /api/assistants/threads/history
├ ƒ /api/assistants/update
├ ƒ /api/chat
├ ƒ /api/chat/stream
├ ƒ /api/files/[fileId]
├ ƒ /api/formats/status
├ ƒ /api/formats/stream
├ ƒ /api/kb/[...path]
├ ƒ /api/upload/[[...path]]
├ ƒ /api/workspace/chat
├ ƒ /api/workspace/cpt/interpret
├ ƒ /api/workspace/upload
├ ○ /forgot-password
├ ○ /knowledge-base
├ ○ /login
├ ○ /reset-password
├ ○ /signup
└ ○ /workspace
○  (Static)   prerendered as static content
ƒ  (Dynamic)  server-rendered on demand
```
- Served from that build on **:3010** (`next start`, `PYTHON_API_URL=http://127.0.0.1:8010`,
  backend flag ON), curl with the throwaway user's cookie:
```
GET /workspace -> 200 (HTML)
status via rewrite: {"enabled":true,"instrument_parsers":true,"instrument_extensions":[".tsv",".txt",".dat",".csv"]}
POST /api/workspace/upload (29 MB TSV via the Next route handler) -> 201 {"kind":"dataset", ... "status":"queued", "size_bytes":29305498} in 0.59 s
poll via rewrite (3 s later): {"state":"parsed","progress":100,"error":null,...}
list via rewrite: badge "DFOS · 7795 gages", status parsed, shapes strain [534,7795], warnings [], segments []
POST /api/workspace/documents via the REWRITE with the same 29 MB file: [500] after 30.03 s   <- the documented rewrite cap; this is why the route handler exists
small .cpt via /api/workspace/documents rewrite (the flag-off frontend path): {"id":"<uuid>","filename":"sample_sounding.CPT","extension":".cpt","status":"ready"}
DELETE via rewrite -> {"deleted": ...}; list after: {"datasets":[]}; npz/ and raw/ empty
```
- Row states / summary card vs parser output: pinned by the page-level vitest
  flow above (upload → queued → parsing 40 % on the row → parsed → card tiles
  from the same metadata the parser returned). No browser was driven (standing
  rule); you will see the live rendering when you test.
- Flag-off parity: the page-level tests assert the pre-feature markup and
  request set; the throwaway :3010 build's rewrite path to `/api/workspace/documents`
  behaves as before.

Note for your restart: the live `.next` under the repo was NOT rebuilt (the
running `geoai-frontend` serves from it and I cannot stop it) — see the final
report for the exact stop → `rm -rf .next` → build → start sequence.

---

## Phase 5 — DFOS strain calculator ✅

**Files created**
- `python_backend/app/workspace/calculators/dfos_pass_strain.py` — `dfos_pass_strain`,
  `required_dataset_kind="strain_distributed"` (bound to the kind; `required_extension=""`).
  Triggers: `run dfos pass strain`, `dfos pass strain`, `analyse/analyze strain pass`,
  `fibre/fiber optic strain`, `run dfos`. `compute(DatasetInput, filename, params)` — pure
  numpy, all relative to tare (`eps_rel = strain − tare`): peak strain + x-position per
  timestep (direct `nanargmax` over gages), max/min envelope along the fibre, load position
  vs time + implied speed (least-squares slope of peak position vs time over timesteps whose
  peak ≥ `peak_fraction` (0.2) × global peak; direction; R²), influence line at a selectable
  gage (`gage_x` param, aliases "influence line at / gage at / at x"; default = global-peak
  gage). `reference` states the ODiSI file format, the tare convention, and that peak
  tracking is a DIRECT maximum, not a fitted model (also a visible `info` notice and a
  Summary row). Returns the standard `ComputeResult`: three full-precision tables
  ("Peak per timestep", "Envelope" (7,795 rows), "Influence line") + `summary` → the
  untouched generic exporter produces the workbook; `charts` (envelope max/min, influence
  line, load position) downsampled server-side to ≤ 2,000 points/series
  (`calculators/dataset_charts.py`: extreme-preserving binning, `n_source` reported).
  `interpret` = `interpretation/dataset_interpret.py` (AI draft from the compact `raw`
  numbers, same raw `ollama.AsyncClient(think=False)` as the CPT hook; `is_ai_draft: true`).
- `python_backend/app/workspace/calculators/dataset_charts.py` — `downsample_xy`, `series`, `chart`.
- `python_backend/app/workspace/interpretation/dataset_interpret.py` — see above.
- `app/workspace/components/strain-chart.tsx` — inline-SVG `LineChart` (no dependency; see
  blocker 3): axes, nice ticks, zero line, one path per series, markers, legend with
  "N of M points shown".
- Tests: `tests/unit/test_dfos_pass_strain.py` (7: kind binding, determinism, tare-relative,
  speed recovery, ≤2000-point charts with peak preserved, full-precision export tables,
  gage/peak-fraction params, all-NaN → ValueError); `tests/integration/test_workspace_datasets.py`
  +3 (need_upload naming the Datasets panel → upload → result with charts/notices/summary,
  determinism across two runs, export workbook by run id with the `DFOS_` filename;
  dataset selection by name else newest; flag-off catalog has no DFOS and the trigger
  phrase runs nothing); `app/workspace/__tests__/result-card-dataset.test.tsx` (2) and a
  page-level card test (deterministic block, visible notice, chart, export button, AI draft
  collapsed until "Show AI draft interpretation — for engineer review" is clicked).

**Files modified**
- `calculators/base.py` — ADDITIVE: `DatasetInput`; `ComputeResult.charts / segments /
  notices` (default empty); `Calculator.required_dataset_kind: Optional[str] = None`
  (frozen dataclass, defaulted last field → the CPT plugin and every existing test are
  untouched). Docstring updated.
- `calculators/registry.py` — installs `DFOS_CALCULATOR`; dataset-bound calculators are
  INSTALLED ONLY while `INSTRUMENT_PARSERS_ENABLED` is on (call-time `_installed()`), so
  flag-off `available_tests_text()`, `match_calculator` and the LLM-router catalog are
  byte-identical (`I can run: CPT interpretation - say 'run cpt'.`).
- `routes.py` — `_handle_calculator` branches to `_handle_dataset_calculator` when
  `required_dataset_kind` is set: dataset chosen by `_select_dataset` (explicit: a run of
  ≥2 filename-stem tokens in the message — "pass 001", "pass_003", the full name — else the
  NEWEST parsed dataset of the kind), arrays loaded and compute run in `asyncio.to_thread`,
  AI draft best-effort, `segments` attached to the dataset row, run persisted with
  `charts/notices/summary/dataset_id/export_prefix`; reply gains `summary`, `charts`,
  `notices`, `dataset_id`, `dataset_kind`, `segments`. Export route passes
  `prefix=payload.get("export_prefix") or "CPT"`.
- `export/xlsx.py` — (a) `export_filename(..., prefix="CPT")` (default unchanged);
  (b) **performance fix in `_write_table_sheet`**: the loop called `ws.max_row` per cell,
  which scans every cell (quadratic) — a 7,795-row table took **31 s** (and would have hit
  the 30 s proxy timeout). Replaced by a local row counter: same cells, same formats,
  **golden-file parity tests still pass** (`test_cpt_export.py`), export now 0.6 s. Logged
  as a decision: this is the only edit to a pre-existing exporter line.
- `app/workspace/page.tsx` — `ResultPayload` gains optional `summary/charts/notices/
  dataset_id/dataset_kind/segments`; `ResultCard` renders, for dataset results: notices
  (visible, inside the deterministic block), the deterministic block (label/value grid from
  the exportable `summary`), charts (`LineChart`), reference, Export to Excel, and the AI
  draft COLLAPSED behind "Show AI draft interpretation — for engineer review". CPT results
  render exactly as before (layers table + expanded AI section). `openRun` carries the
  dataset fields; History rows show the run `headline`; a result's `segments` update the
  dataset row.
- `workspace.module.css` — deterministic-block / notice / chart / aiToggle classes.

**Verification (synthetic passes — blocker 1). Standalone (no server), all four passes,
two computes each:**
```
== ODiSI_6000_2026-04-09_16-18-51_ch3_pass_001.tsv
  compute 0.18s / 0.16s  identical=True  sha=78948e638286abc4
  peak 109.853 µε @ x=13.5948 m, t=2026-04-09 16:24:10.120; env max 109.85 min -8.06; speed 1.5305795078361921 m/s (+x (increasing fibre position)) R2 0.9645305445778448 loaded 120/497; influence gage x=13.5948
  chart points {'envelope': [2000, 2000], 'influence': [497], 'load_position': [497]}
  xlsx 0.42 MB in 0.6s sheets=['Peak per timestep', 'Envelope', 'Influence line', 'Summary'] Envelope rows=7795 first max cell=103.0119978027344 (float64 repr from table: 103.01199780273437)
== ODiSI_6000_2026-04-09_16-18-51_ch3_pass_002.tsv
  compute 0.19s / 0.17s  identical=True  sha=b641e74fd5d1a2b0
  peak 124.587 µε @ x=0.7462 m, t=2026-04-09 16:25:03.520; env max 124.59 min -7.95; speed 1.7095249578438942 m/s (+x (increasing fibre position)) R2 0.9656183966140627 loaded 108/534; influence gage x=0.7462
  chart points {'envelope': [2000, 2000], 'influence': [534], 'load_position': [534]}
  xlsx 0.43 MB in 0.7s sheets=['Peak per timestep', 'Envelope', 'Influence line', 'Summary'] Envelope rows=7795 first max cell=119.6929975585937 (float64 repr from table: 119.69299755859375)
== ODiSI_6000_2026-04-09_16-18-51_ch3_pass_003.tsv
  compute 0.21s / 0.21s  identical=True  sha=7407dc4c07866db2
  peak 139.560 µε @ x=16.3981 m, t=2026-04-09 16:26:16.720; env max 139.56 min -8.07; speed 1.8836547093064029 m/s (+x (increasing fibre position)) R2 0.965691622275008 loaded 98/606; influence gage x=16.3981
  chart points {'envelope': [2000, 2000], 'influence': [606], 'load_position': [606]}
  xlsx 0.43 MB in 0.7s sheets=['Peak per timestep', 'Envelope', 'Influence line', 'Summary'] Envelope rows=7795 first max cell=132.9749952392578 (float64 repr from table: 132.97499523925782)
== ODiSI_6000_2026-04-09_16-18-51_ch3_pass_004.tsv
  compute 0.21s / 0.22s  identical=True  sha=fc216bd20c81c19c
  peak 154.712 µε @ x=13.1585 m, t=2026-04-09 16:27:27.160; env max 154.71 min -7.79; speed 2.0469365216556232 m/s (+x (increasing fibre position)) R2 0.9654147260844966 loaded 90/600; influence gage x=13.1585
  chart points {'envelope': [2000, 2000], 'influence': [600], 'load_position': [600]}
  xlsx 0.43 MB in 0.7s sheets=['Peak per timestep', 'Envelope', 'Influence line', 'Summary'] Envelope rows=7795 first max cell=144.8360029296875 (float64 repr from table: 144.8360029296875)
```
compute 0.16–0.22 s; two runs identical (deep-equal on metadata/summary/tables/charts,
same SHA); envelope chart 2,000 of 7,795 points, influence/position at native
497–606 points; xlsx 0.42–0.43 MB in 0.6–0.7 s with the 7,795-row full-precision
Envelope sheet. (Sanity: the recovered speeds 1.53/1.71/1.88/2.05 m/s are ~15 % under
the synthetic generator's 1.8/2.0/2.2/2.4 m/s because the direct maximum jumps from the
front to the rear axle when the front one leaves the fibre — exactly the "direct
maximum, not a fitted model" caveat the reference states; R² 0.965 shows it.)

**Live (throwaway :3010 → :8010, flag on, gemma4:12b for the draft):**
```
[7.2s] type=result calc=dfos_pass_strain file=ODiSI_6000_2026-04-09_16-18-51_ch3_pass_001.tsv run_id=6a84f27b707703b5fde988a2 exportable=True
  summary_text: Global peak 109.9 µε (relative to tare) at x = 13.59 m, timestep 201 (2026-04-09 16:24:10.120); envelope max 109.9 µε, min -8.1 µε; implied speed 1.53 m/s (5.5 km/h) +x (increasing fibre position), R² 0.965 over 120 loaded timesteps; influence line at gage x = 13.59 m.
  notices: [{'level': 'info', 'text': 'Peak tracking is a direct per-timestep maximum relative to tare, not a fitted model.'}]
  charts: {'envelope': [2000, 2000], 'influence': [497], 'load_position': [497]}  deterministic-sha: 695725183bfd85bc
  segments: 0  params: {}
  interpretation: is_ai_draft=True model=gemma4:12b error=None narrative='The analysis of the fiber optic strain data identifies a global peak microstrain of 109.85 at a position of 13.595 meters in the +x direction. The envelope of r'
  reply bytes: 77142
[5.5s] type=result calc=dfos_pass_strain file=ODiSI_6000_2026-04-09_16-18-51_ch3_pass_002.tsv run_id=6a84f280707703b5fde988a4 exportable=True
  summary_text: Global peak 124.6 µε (relative to tare) at x = 0.75 m, timestep 149 (2026-04-09 16:25:03.520); envelope max 124.6 µε, min -8.0 µε; implied speed 1.71 m/s (6.2 km/h) +x (increasing fibre position), R² 0.966 over 108 loaded timesteps; influence line at gage x = 0.75 m.
  notices: [{'level': 'info', 'text': 'Peak tracking is a direct per-timestep maximum relative to tare, not a fitted model.'}]
  charts: {'envelope': [2000, 2000], 'influence': [534], 'load_position': [534]}  deterministic-sha: 3380490aa55af120
  segments: 0  params: {}
  interpretation: is_ai_draft=True model=gemma4:12b error=None narrative='The analysis of the fiber optic strain data identifies a global peak and envelope maximum of 124.59 microstrain located at 0.746 meters in the +x direction. The'
  reply bytes: 77957
[8.9s] type=result calc=dfos_pass_strain file=ODiSI_6000_2026-04-09_16-18-51_ch3_pass_003.tsv run_id=6a84f289707703b5fde988a6 exportable=True
  summary_text: Global peak 139.6 µε (relative to tare) at x = 16.40 m, timestep 225 (2026-04-09 16:26:16.720); envelope max 139.6 µε, min -8.1 µε; implied speed 1.88 m/s (6.8 km/h) +x (increasing fibre position), R² 0.966 over 98 loaded timesteps; influence line at gage x = 16.40 m.
  notices: [{'level': 'info', 'text': 'Peak tracking is a direct per-timestep maximum relative to tare, not a fitted model.'}]
  charts: {'envelope': [2000, 2000], 'influence': [606], 'load_position': [606]}  deterministic-sha: 8208a0a61c3f0e96
  segments: 0  params: {}
  interpretation: is_ai_draft=True model=gemma4:12b error=None narrative='The analysis of the data from file ODiSI_6000_2026-04-09_16-18-51_ch3_pass_003.tsv processed 7795 gages over 606 timesteps. The global peak strain was recorded '
  reply bytes: 80231
[6.2s] type=result calc=dfos_pass_strain file=ODiSI_6000_2026-04-09_16-18-51_ch3_pass_004.tsv run_id=6a84f28f707703b5fde988a8 exportable=True
  summary_text: Global peak 154.7 µε (relative to tare) at x = 13.16 m, timestep 206 (2026-04-09 16:27:27.160); envelope max 154.7 µε, min -7.8 µε; implied speed 2.05 m/s (7.4 km/h) +x (increasing fibre position), R² 0.965 over 90 loaded timesteps; influence line at gage x = 13.16 m.
  notices: [{'level': 'info', 'text': 'Peak tracking is a direct per-timestep maximum relative to tare, not a fitted model.'}]
  charts: {'envelope': [2000, 2000], 'influence': [600], 'load_position': [600]}  deterministic-sha: a363820a482c5e94
  segments: 0  params: {}
  interpretation: is_ai_draft=True model=gemma4:12b error=None narrative='The analysis of the fiber optic strain data identified a global peak microstrain of 154.71 at position 13.159 meters in the +x direction. The envelope values ra'
  reply bytes: 80144
--- determinism re-run pass 002:
[6.2s] type=result calc=dfos_pass_strain file=ODiSI_6000_2026-04-09_16-18-51_ch3_pass_002.tsv run_id=6a84f296707703b5fde988aa exportable=True
  summary_text: Global peak 124.6 µε (relative to tare) at x = 0.75 m, timestep 149 (2026-04-09 16:25:03.520); envelope max 124.6 µε, min -8.0 µε; implied speed 1.71 m/s (6.2 km/h) +x (increasing fibre position), R² 0.966 over 108 loaded timesteps; influence line at gage x = 0.75 m.
  notices: [{'level': 'info', 'text': 'Peak tracking is a direct per-timestep maximum relative to tare, not a fitted model.'}]
  charts: {'envelope': [2000, 2000], 'influence': [534], 'load_position': [534]}  deterministic-sha: 3380490aa55af120
  segments: 0  params: {}
  interpretation: is_ai_draft=True model=gemma4:12b error=None narrative='The analysis of the fiber optic strain data identified a global peak and envelope maximum of 124.59 microstrain at a position of 0.746 meters in the +x directio'
  reply bytes: 78051
```
Determinism across two live runs of pass 002: identical deterministic SHA
(`3380490aa55af120`) with different AI-draft wording (as expected — the draft is not part of
the deterministic block). Reply ≈ 78–80 KB (downsampled charts), never 7,795 × 600.
Named selection works ("on pass 00N" → that pass).

Excel export through the Next rewrite (`GET /api/workspace/export/{run_id}`):
```
sheets: ['Peak per timestep', 'Envelope', 'Influence line', 'Summary']
  Peak per timestep: rows=535 cols=8 header=['Timestep', 'Timestamp', 'Time (s)', 'Peak strain (microstrain)', 'Peak position x (m)', 'Peak gage index'] row2=[0, '2026-04-09 16:24:45.640', 0, 5.415999958038331, 11.9411, 4540] freeze=A2 bold=True
  Envelope: rows=7796 cols=6 header=['Gage index', 'Position x (m)', 'Tare (microstrain)', 'Max strain rel. tare (microstrain)', 'Min strain rel. tare (microstrain)', 'Timestep of max'] row2=[0, 0.08, 4.492, 119.6929975585937, -3.780999974727631, 146] freeze=A2 bold=True
  Influence line: rows=535 cols=4 header=['Timestep', 'Timestamp', 'Time (s)', 'Strain at x=0.7462 m (microstrain)'] row2=[0, '2026-04-09 16:24:45.640', 0, 0.8909998970031738] freeze=A2 bold=True
  Summary: rows=23 cols=2 header=['Field', 'Value'] row2=['Source file', 'ODiSI_6000_2026-04-09_16-18-51_ch3_pass_002.tsv'] freeze=A2 bold=True
Envelope max-strain column: 7795 rows, max |xlsx - recomputed| = 5.684341886080802e-14 (float64 round-trip)
Summary rows: 22 | Peak tracking = direct maximum per timestep (not a fitted model) | Implied speed (m/s) = 1.709524957843894
export via Next rewrite: [200] 428116 bytes in 1.37 s; new runs download as DFOS_<stem>_<date>.xlsx (old runs keep CPT_)
```

Chart "renders without jank": the payload is ≤ 2,000 points per series and the SVG is a
single path per series (pinned by `test_charts_are_downsampled_to_at_most_2000_points`
and the component test); no browser was driven.

Build (scratchpad copy, `rm -rf .next && npm run build`): `✓ Compiled successfully`, all
routes present (log `phase5_build.log`); :3010 restarted, `GET /workspace` 200.
Tests: backend unit 438 passed, 6 skipped (live-Ollama rewriter file deselected) / workspace integration files 41 passed; vitest 63 passed.

Flag-off parity: registry hides dataset calculators (test), status/routes as Phase 3,
ResultCard CPT branch unchanged (test), page flag-off tests unchanged.

---

## Phase 6 — Pressure cell calculator ✅

**Files created**
- `python_backend/app/workspace/calculators/event_detection.py` — NAMED, swappable
  strategies: `STRATEGIES = {"percentile_mad": detect_percentile_mad}`, `get_strategy(name)`
  (ValueError for an unknown name), `DetectionParams` (strategy, baseline_percentile,
  baseline_window_s, mad_multiplier, min_channels, merge_gap_s, min_duration_s;
  `describe()` names the exact method + parameters), `block_percentile_baseline` (per-channel
  percentile over time blocks, interpolated), `detect_percentile_mad`: baseline → residual →
  noise = 1.4826 × MAD → threshold = k × noise → per-sample active (≥ min_channels over
  threshold) → merge runs closer than merge_gap_s → drop runs shorter than min_duration_s.
  Adding a method = adding a function + a name.
- `python_backend/app/workspace/calculators/traffic_load_monitoring.py` —
  `traffic_load_monitoring`, `required_dataset_kind="pressure_timeseries"` (bound to the kind).
  Triggers: `run traffic load monitoring`, `traffic load monitoring`, `run traffic load`,
  `detect load events`, `pressure cell events`, `count vehicle passes`. Parameters come from
  config (`INSTRUMENT_EVENT_*`) with inline overrides (`mad multiplier 8`, `percentile 30`,
  `merge gap 2`, `min duration 0.5`, `min channels 2`). Per event: start, end, duration, peak
  kPa per channel (raw AND above baseline), peak sum (Σ per-channel peaks above baseline),
  instantaneous peak sum, channel peak ORDER → direction of travel (`TP4144 → TP4149`), hour.
  Day summary: event count, 24-bin hourly histogram, 10-bin peak-sum distribution, five
  largest events, per-channel noise/threshold. Events are returned as `segments` (children
  of the dataset row) — the route attaches them to the dataset doc. Charts (bar: hourly,
  peak distribution; line: day trace of total residual downsampled to 2,000 points with the
  five largest events marked). Tables: "Events (provisional)" (per-event row incl. a
  `Validation status = PROVISIONAL - not validated` column), "Hourly histogram", "Peak
  distribution", "Channels"; Summary carries `Validation status`, the strategy and every
  parameter. **Provisional status is rendered in FOUR places**: `reference` (states it and
  names the exact method + parameters), a first-class `notices[0] = {level: "provisional"}`
  that the result card renders INSIDE the deterministic block (amber banner with a
  "Provisional" tag — not a tooltip/footnote), the Excel Summary sheet + per-row column,
  and every count is worded "(provisional)" / "not validated" (`summary_text`, tiles, AI
  prompt notes). Plausibility: outside `INSTRUMENT_EVENT_PLAUSIBLE_MIN..MAX` (1..5000) a
  `warning` notice says the method is wrong for this data and the run is a method
  failure, not a result. Export prefix `TRAFFIC`.
- Tests: `tests/unit/test_traffic_load_monitoring.py` (8: kind binding + reference wording,
  determinism + direction both ways + largest event, provisional in notices/summary/tables/
  reference and no "validated counts" wording, config vs inline parameters, implausible-count
  warning, strategy registry swap + unknown strategy, baseline drift tracking, ≤2000-point
  charts + export sheets); `tests/integration/test_workspace_datasets.py` +1 (chat run →
  provisional reply, 6 events with direction, segments on the dataset doc + list, determinism,
  inline `mad multiplier 200` echoed, export `TRAFFIC_…xlsx` with the provisional Summary
  row); `result-card-dataset.test.tsx` +1 (bar series → one rect per bin); the page-level
  card test already pins the amber notice.

**Files modified**
- `app/core/config.py` — `INSTRUMENT_EVENT_STRATEGY` (percentile_mad),
  `_BASELINE_PERCENTILE` (20), `_BASELINE_WINDOW_S` (300), `_MAD_MULTIPLIER` (6),
  `_MIN_CHANNELS` (1), `_MERGE_GAP_S` (1.0), `_MIN_DURATION_S` (0.3),
  `_PLAUSIBLE_MIN/MAX` (1/5000), each commented as NOT yet engineer-approved.
- `calculators/registry.py` — installs `TRAFFIC_CALCULATOR` (flag-gated like DFOS).
- `app/workspace/components/strain-chart.tsx` — `kind: "bar"` series → rects on a zero
  baseline (histograms).

**Verification.** Standalone against the synthetic day log (blocker 1), two computes:
```
compute 1.50s / 1.48s identical=True
params: strategy=percentile_mad; baseline = p20 per channel over 300 s blocks (interpolated); noise = 1.4826 x MAD of residual; threshold = 6 x noise; active if >= 1 channel(s) over threshold; merge gaps <= 1 s; drop events < 0.3 s
events: 334 plausible: True hourly: [5, 1, 1, 1, 2, 8, 18, 18, 24, 31, 12, 9, 18, 18, 18, 27, 30, 26, 27, 14, 11, 3, 8, 4]
noise kPa: [0.068, 0.07, 0.074, 0.076] threshold kPa: [0.41, 0.422, 0.447, 0.455]
five largest:
   {"index": 23, "start": "2024-05-10 06:23:11.400", "duration_s": 4.999999999996362, "peak_sum_kpa": 99.73792268147469, "peak_kpa": [29.959999084472656, 27.8700008392334, 40.90999984741211, 39.869998931884766], "channel_order": ["TP4149_kPa", "TP4148_kPa", "TP4145_kPa", "TP4144_kPa"], "direction": "TP4149_kPa \u2192 TP4144_kPa"}
   {"index": 201, "start": "2024-05-10 15:38:54.200", "duration_s": 4.900000000001455, "peak_sum_kpa": 95.82304066999754, "peak_kpa": [29.19700050354004, 27.165000915527344, 39.696998596191406, 38.6349983215332], "channel_order": ["TP4144_kPa", "TP4145_kPa", "TP4148_kPa", "TP4149_kPa"], "direction": "TP4144_kPa \u2192 TP4149_kPa"}
   {"index": 110, "start": "2024-05-10 10:00:44.500", "duration_s": 3.7999999999956344, "peak_sum_kpa": 95.28603274464608, "peak_kpa": [29.115999221801758, 27.08300018310547, 39.48699951171875, 38.46900177001953], "channel_order": ["TP4144_kPa", "TP4145_kPa", "TP4148_kPa", "TP4149_kPa"], "direction": "TP4144_kPa \u2192 TP4149_kPa"}
   {"index": 304, "start": "2024-05-10 19:40:03.000", "duration_s": 3.2000000000043656, "peak_sum_kpa": 73.11014388885498, "peak_kpa": [25.02899932861328, 22.98900032043457, 32.76100158691406, 31.202999114990234], "channel_order": ["TP4149_kPa", "TP4148_kPa", "TP4145_kPa", "TP4144_kPa"], "direction": "TP4149_kPa \u2192 TP4144_kPa"}
   {"index": 227, "start": "2024-05-10 16:32:22.200", "duration_s": 4.30000000000291, "peak_sum_kpa": 71.00493879342079, "peak_kpa": [24.780000686645508, 22.56599998474121, 32.047000885009766, 30.483999252319336], "channel_order": ["TP4149_kPa", "TP4148_kPa", "TP4145_kPa", "TP4144_kPa"], "direction": "TP4149_kPa \u2192 TP4144_kPa"}
notices: ['provisional'] | Threshold method PROVISIONAL - pending validation by the supervising engineer. Event counts and event statistics from th
charts: {'hourly': [24], 'peak_distribution': [10], 'day_trace': [2000]}
summary_text: PROVISIONAL detection (percentile_mad, k=6): 334 event(s) over 864,000 samples on 4 channel(s); busiest hour 09:00; largest: #23 2024-05-10 06:23:11.400 peak-sum 99.7 kPa (TP4149_kPa → TP4144_kPa); #201 2024-05-10 15:38:54.200 peak-sum 95.8 kPa (TP4144_kPa → TP4149_kPa); #110 2024-05-10 10:00:44.500 peak-sum 95.3 kPa (TP4144_kPa → TP4149_kPa). Counts are not validated.
xlsx 63 KB in 0.1s sheets=['Events (provisional)', 'Hourly histogram', 'Peak distribution', 'Channels', 'Summary'] events rows=334
Summary 'Validation status' = PROVISIONAL - threshold method pending engineering validation; counts NOT validated
Events sheet last column header/first value: Validation status / PROVISIONAL - not validated
```
→ **334 events** (the synthetic generator planted 340 seeded passes, a few overlapping →
merged); parameters used exactly as logged above; five largest listed; two runs identical;
compute 1.5 s; charts 24/10/2000 points; xlsx 63 KB in 0.1 s. Plausibility: 334 events in a
24 h road log is inside the 1–5000 band — neither zero nor thousands — so it is reported;
BUT this is a synthetic file whose events were planted, so it says nothing about the real
site's traffic; the real `.dat` must be run and eyeballed against expectations, and the
band itself is a config knob for you.

Live (throwaway :3010 → :8010, flag on):
```
[8.7s] type=result calc=traffic_load_monitoring file=2024-05-10.dat run_id=6a84f4a45d0ea8e4be100a39 exportable=True
  summary_text: PROVISIONAL detection (percentile_mad, k=6): 334 event(s) over 864,000 samples on 4 channel(s); busiest hour 09:00; largest: #23 2024-05-10 06:23:11.400 peak-sum 99.7 kPa (TP4149_kPa → TP4144_kPa); #201 2024-05-10 15:38:54.200 peak-sum 95.8 kPa (TP4144_kPa → TP4149_kPa); #110 2024-05-10 10:00:44.500 peak-sum 95.3 kPa (TP4144_kPa → TP4149_kPa). Counts are not validated.
  notices: [{'level': 'provisional', 'text': 'Threshold method PROVISIONAL - pending validation by the supervising engineer. Event counts and event statistics from this run are NOT validated. Method: strategy=percentile_mad; baseline = p20 per channel over 300 s blocks (interpolated); noise = 1.4826 x MAD of residual; threshold = 6 x noise; active if >= 1 channel(s) over threshold; merge gaps <= 1 s; drop events < 0.3 s.'}]
  charts: {'hourly': [24], 'peak_distribution': [10], 'day_trace': [2000]}  deterministic-sha: ceb8233f4b11294f
  segments: 334  params: {}
  interpretation: is_ai_draft=True model=gemma4:12b error=None narrative='The traffic load monitoring analysis for 2024-05-10 identified a total of 334 events. The largest recorded event reached a peak sum of 99.74 kPa, while the seco'
  reply bytes: 153580
--- re-run (determinism):
[6.5s] type=result calc=traffic_load_monitoring file=2024-05-10.dat run_id=6a84f4ab5d0ea8e4be100a3b exportable=True
  summary_text: PROVISIONAL detection (percentile_mad, k=6): 334 event(s) over 864,000 samples on 4 channel(s); busiest hour 09:00; largest: #23 2024-05-10 06:23:11.400 peak-sum 99.7 kPa (TP4149_kPa → TP4144_kPa); #201 2024-05-10 15:38:54.200 peak-sum 95.8 kPa (TP4144_kPa → TP4149_kPa); #110 2024-05-10 10:00:44.500 peak-sum 95.3 kPa (TP4144_kPa → TP4149_kPa). Counts are not validated.
  notices: [{'level': 'provisional', 'text': 'Threshold method PROVISIONAL - pending validation by the supervising engineer. Event counts and event statistics from this run are NOT validated. Method: strategy=percentile_mad; baseline = p20 per channel over 300 s blocks (interpolated); noise = 1.4826 x MAD of residual; threshold = 6 x noise; active if >= 1 channel(s) over threshold; merge gaps <= 1 s; drop events < 0.3 s.'}]
  charts: {'hourly': [24], 'peak_distribution': [10], 'day_trace': [2000]}  deterministic-sha: ceb8233f4b11294f
  segments: 334  params: {}
  interpretation: is_ai_draft=True model=gemma4:12b error=None narrative='The traffic load monitoring analysis for 2024-05-10 identified a total of 334 events. The largest recorded events reached peak sums of 99.74 kPa, 95.82 kPa, and'
  reply bytes: 153510
--- segments on the dataset row:
   2024-05-10.dat Pressure · 4 channels segments= 334
    Event 1 · 00:07:37 40.65 kPa 2.7 s TP4149_kPa → TP4144_kPa
    Event 2 · 00:08:33 51.039 kPa 4.0 s TP4149_kPa → TP4144_kPa
    Event 3 · 00:15:40 26.192 kPa 2.7 s TP4144_kPa → TP4149_kPa
    Event 4 · 00:31:19 18.048 kPa 3.2 s TP4144_kPa → TP4149_kPa
    Event 5 · 00:59:21 22.835 kPa 3.3 s TP4149_kPa → TP4144_kPa
```
Determinism across two live runs: identical deterministic SHA `ceb8233f4b11294f`. Reply
≈ 154 KB (334 segments + downsampled charts). Segments appear as children of the dataset
row (list + detail endpoints), so the panel's expander shows "Pressure · 4 channels · 334
events" with the event rows.

Excel export through Next (provisional status present):
```
export via Next rewrite: [200] 62607 bytes in 0.426468s
content-disposition: attachment; filename="TRAFFIC_2024-05-10_20260818.xlsx"
sheets: ['Events (provisional)', 'Hourly histogram', 'Peak distribution', 'Channels', 'Summary']
Events rows: 334 | columns: ['Event #', 'Start', 'End', 'Duration (s)', 'Peak TP4144_kPa (kPa)', 'Peak TP4145_kPa (kPa)', 'Peak TP4148_kPa (kPa)', 'Peak TP4149_kPa (kPa)', 'Peak above baseline TP4144_kPa (kPa)', 'Peak above baseline TP4145_kPa (kPa)', 'Peak above baseline TP4148_kPa (kPa)', 'Peak above baseline TP4149_kPa (kPa)', 'Peak sum above baseline (kPa)', 'Channel peak order', 'Direction of travel', 'Hour', 'Validation status']
Event #23 row: [23, '2024-05-10 06:23:11.400', '2024-05-10 06:23:16.300', 4.999999999996362, 29.95999908447266, 27.8700008392334, 40.90999984741211, 39.86999893188477, 18.42844489336014, 18.02229557847977, 30.85243663549423, 32.43474557414055, 99.73792268147469, 'TP4149_kPa > TP4148_kPa > TP4145_kPa > TP4144_kPa', 'TP4149_kPa → TP4144_kPa', 6, 'PROVISIONAL - not validated']
  Summary['Validation status'] = 'PROVISIONAL - threshold method pending engineering validation; counts NOT validated'
  Summary['Detection strategy'] = 'percentile_mad'
  Summary['MAD multiplier'] = 6
  Summary['Baseline percentile'] = 20
  Summary['Merge gap (s)'] = 1
  Summary['Min event duration (s)'] = 0.3
  Summary['Events detected (provisional)'] = 334
  Summary['Largest event #'] = 23
  Summary['Method / Standard reference'][:140] = Campbell pressure-cell log (kPa, per-channel). Event detection: strategy=percentile_mad; baseline = p20 per channel over 300 s blocks (inter
Hourly histogram sheet: [[0, 5], [1, 1], [2, 1]] ...
```

Result-card rendering of the provisional notice: pinned by the page-level vitest
("renders notice + deterministic block + chart + export, AI draft collapsed until asked":
the amber `Provisional` banner text is asserted inside the card body); no browser driven.

Build: `✓ Compiled successfully in 6.3s` (`phase6_build.log`); :3010 restarted, `GET
/workspace` 200. Tests: unit `========== 446 passed, 6 skipped, 15 deselected, 2 warnings in 9.07s ===========`; workspace integration
`======================== 42 passed, 5 warnings in 0.75s ========================`; vitest 7 files, 65 tests passed.

Flag-off parity: unchanged from Phases 3–5 (registry hides both dataset calculators; no
routes; page identical).

---

# Final report

## Phase-by-phase status

| Phase | Status | Notes |
|---|---|---|
| 1 Parser package + ODiSI reader | ✅ done | verified on SYNTHETIC passes (blocker 1); real files still to be run |
| 2 Campbell `.dat` reader | ✅ done | same caveat; nginx change generated, not applied |
| 3 Persistence, jobs, upload wiring | ✅ done | flag-off byte-identical vs git-HEAD oracle; live parse on throwaway :8010 |
| 4 Frontend dataset panel | ✅ done | build green in a scratchpad copy; served on throwaway :3010; no browser driven |
| 5 DFOS strain calculator | ✅ done | 4 passes, deterministic, ≤2000-pt charts, full-precision Excel |
| 6 Pressure-cell calculator | ✅ done | 334 events on the synthetic day, PROVISIONAL status in card + Excel + reference |
| Prod restart / rebuild | ⛔ not done | no sudo — see "Exact commands" below; prod still runs the pre-change code with the flag `false` |

## Every file created or modified

**Created (backend)**
- `python_backend/app/workspace/parsers/base.py`, `registry.py`, `odisi.py`, `campbell.py`
- `python_backend/app/workspace/dataset_files.py`, `dataset_store.py`, `instrument_ingest.py`, `dataset_routes.py`
- `python_backend/app/workspace/calculators/dataset_charts.py`, `dfos_pass_strain.py`, `event_detection.py`, `traffic_load_monitoring.py`
- `python_backend/app/workspace/interpretation/dataset_interpret.py`
- `python_backend/scripts/verify_parsers.py`, `python_backend/scripts/make_synthetic_instrument_fixtures.py`
- `python_backend/tests/unit/test_instrument_parsers.py` (19), `test_dfos_pass_strain.py` (7), `test_traffic_load_monitoring.py` (8)
- `python_backend/tests/integration/test_workspace_datasets.py` (17)
- (generated, gitignored) `python_backend/tests/fixtures/synthetic/{README_SYNTHETIC.md, odisi/*.tsv, odisi/extraction_manifest.json, pressure/2024-05-10.dat}`

**Created (frontend)**
- `app/api/workspace/upload/route.ts`
- `app/workspace/instruments.ts`
- `app/workspace/components/dataset-rows.tsx`, `dataset-cards.tsx`, `strain-chart.tsx`
- `app/workspace/__tests__/instruments.test.ts`, `dataset-panel.test.tsx`, `result-card-dataset.test.tsx`

**Modified**
- `.gitignore` (+ `python_backend/tests/fixtures/`, `python_backend/data/instrument_datasets/`)
- `python_backend/.env` (+ `INSTRUMENT_PARSERS_ENABLED=false`, line 47; nothing else touched)
- `python_backend/app/core/config.py` (flag + `INSTRUMENT_*` settings)
- `python_backend/app/core/database.py` (2 collections; flag-gated indexes)
- `python_backend/app/main.py` (`workspace_dataset_routes.register(app)`)
- `python_backend/app/workspace/parsers/__init__.py` (registers the two parsers; CPT exports unchanged)
- `python_backend/app/workspace/calculators/__init__.py` (exports `DatasetInput`)
- `python_backend/app/workspace/calculators/base.py` (additive: `DatasetInput`; `ComputeResult.charts/segments/notices`; `Calculator.required_dataset_kind`)
- `python_backend/app/workspace/calculators/registry.py` (installs DFOS + TRAFFIC, flag-gated at call time)
- `python_backend/app/workspace/export/xlsx.py` (`prefix` kwarg; quadratic `ws.max_row` loop fixed — golden parity tests pass)
- `python_backend/app/workspace/routes.py` (status capability fields; sniff branch in `POST /documents`; `_select_dataset` + `_handle_dataset_calculator`; export prefix)
- `app/workspace/page.tsx`, `app/workspace/workspace.module.css`
- `INSTRUMENT_PARSERS_ROLLOUT.md` (this file)

Untouched, by `git diff --stat`: `app/routers/*` (incl. `files.py` = `/api/upload`), `app/services/*` (RAG, retrieval, reranking), `app/dependencies/auth.py`, `app/core/rate_limit.py`, Main Chat components (`chat.tsx`, `composer.tsx`, `thread-documents.tsx`), the CPT calculator/parser math.

## The nginx change (NOT applied)

```nginx
# /etc/nginx/sites-enabled/<chenglin-geoai site>, in the server { } (or the location / that proxies to :3000)
#   was:  client_max_body_size 55M;
client_max_body_size 128M;   # instrument datasets are 22-58 MB; headroom for a 24 h logger day
```
`sudo nginx -t && sudo systemctl reload nginx`. Until then the 58 MB `.dat` will be rejected
by nginx (413) on the public URL; everything ≤ 55 MB works. The app-side ceiling for a
sniffed instrument upload is `INSTRUMENT_MAX_UPLOAD_MB` (200).

## Flag-off parity evidence per phase (one line each)

- **P1/P2**: library code only; no runtime consumer; unit suite 431/438 passed unchanged.
- **P3**: throwaway :8010 (new code, flag off) vs :8011 (`git archive HEAD`, same .env), same
  cookie: `GET /api/workspace/status` → `{"enabled":true}` byte-identical; five dataset paths →
  `404 {"detail":"Not Found"}` on both (router not registered + absent-route body); small
  TSV / .dat / PDF uploads → identical `{id,filename,extension,status:"ready"}` (uuid
  normalised); 27 MB TSV → identical `413 File too large. Max 5 MB.`; document listing identical;
  Mongo counts for the throwaway user after flag-off uploads: files 0, chunks 0, datasets 0, jobs 0.
  Route-table parity is structural: `dataset_routes.register(app)` adds nothing when off (test).
- **P4**: page-level tests with `status = {enabled:true}`: no "Datasets" group, `accept=".cpt,.CPT"`,
  no `/api/workspace/datasets*` requests, uploads to `/api/workspace/documents` via the rewrite;
  live: the rewrite path still serves a `.CPT` upload as before.
- **P5/P6**: registry with the flag off = `(CPT_CALCULATOR,)` → `I can run: CPT interpretation - say 'run cpt'.`
  byte-identical, LLM-router catalog identical, `run dfos pass strain` runs nothing (test);
  `ResultCard` CPT branch unchanged (test); `export_filename` default prefix `CPT` (golden tests).

## Open concerns (each once)

1. **Real fixtures never seen.** All numeric verification is against synthetic files built
   from Section 3's text. First thing to do when the real files land:
   `python scripts/verify_parsers.py tests/fixtures/odisi/*.tsv tests/fixtures/pressure/2024-05-10.dat`
   and compare with Section 3; a warning or a count mismatch there is a Section-3
   contradiction, not something to paper over.
2. **Prod not restarted / not rebuilt** (no sudo). Prod serves the old code with the flag
   `false`; the new code has never run under systemd. Commands below.
3. **Throwaway residue in prod Mongo** (needs your confirmation to delete, per the standing
   rule): user `instrument.rollout.check@example.com` (`_id 6a84eb6fbdc1595e65f5cbf5`),
   its **11 `workspace_runs`** and **11 `workspace_threads`** (from the live calculator runs;
   all datasets/jobs were deleted through the API and the disk is clean); the now-existing
   empty collections `workspace_datasets` / `workspace_parse_jobs` with their indexes
   (`user_id_1_created_at_-1`, `dataset_id_1`), created by the flag-on throwaway's
   `ensure_indexes`. Negative-scope check: `workspace_datasets`/`workspace_parse_jobs` totals
   are 0 across ALL users; the runs/threads query is `user_id`-scoped. Cleanup (dry-run first):
   ```
   # in mongosh against ai-geotech-db
   db.workspace_runs.countDocuments({user_id:"6a84eb6fbdc1595e65f5cbf5"})    // expect 11
   db.workspace_threads.countDocuments({user_id:"6a84eb6fbdc1595e65f5cbf5"}) // expect 11
   db.workspace_runs.deleteMany({user_id:"6a84eb6fbdc1595e65f5cbf5"})
   db.workspace_threads.deleteMany({user_id:"6a84eb6fbdc1595e65f5cbf5"})
   db.users.deleteOne({_id: ObjectId("6a84eb6fbdc1595e65f5cbf5")})
   ```
   Also on this box: the earlier session's throwaways on **:8001 / :3001** are still running
   (not mine, left alone); mine (:8010, :8011, :3010) are stopped.
4. **Provisional detection method** (Phase 6) — needs the supervising engineer's sign-off on
   `percentile_mad` and its parameters; until then every result says so. Also decide the
   plausibility band (`INSTRUMENT_EVENT_PLAUSIBLE_MIN/MAX`, now 1..5000).
5. **Excel export size/time**: the DFOS Envelope sheet is 7,795 rows (0.43 MB, ~0.6 s server
   time; the exporter's quadratic loop is fixed). Exports go through the Next REWRITE
   (`/api/workspace/export/*`, 30 s / 10 MB response cap) — fine at this size; a much larger
   dataset export would need its own Route Handler like the upload.
6. **Datasets are durable, not session-scoped**: "New session" clears documents + thread but
   keeps datasets listed (they are artifacts on disk + Mongo, like History runs); remove with
   the row's ×. Retained raw uploads (22–58 MB each) live under
   `python_backend/data/instrument_datasets/raw/` until the dataset is deleted — no sweeper.
7. **Instrument-shaped CSVs uploaded to Main Chat** still take the RAG path (extension
   `.csv` is supported there; `.tsv`/`.dat` are rejected as before). Hooking `/api/upload` was
   deliberately not done (Main Chat is on the do-not-touch list).
8. **`.txt`/`.csv` in the GeoPilot picker** (flag on): they are the parsers' advisory
   extensions; a non-instrument `.csv` simply falls to the document path.

## Exact commands to test each feature yourself

Backend restart + frontend rebuild (the part I could not do):
```bash
# turn the feature on
cd /home/dharunk/geotech/AI-Geotechnical-Assistant/python_backend
grep -n INSTRUMENT_PARSERS_ENABLED .env                       # line 47, currently false
sed -i 's/^INSTRUMENT_PARSERS_ENABLED=.*/INSTRUMENT_PARSERS_ENABLED=true/' .env
sudo systemctl restart geoai-backend && sleep 5 && systemctl status geoai-backend --no-pager | head -5
curl -s http://127.0.0.1:8000/                                 # {"status":"ok",...}

# frontend: stop -> rm .next -> build -> start (never build under a running next start)
cd /home/dharunk/geotech/AI-Geotechnical-Assistant
sudo systemctl stop geoai-frontend && rm -rf .next && npm run build && sudo systemctl start geoai-frontend
```

Parsers only (no DB, no server):
```bash
cd python_backend
venv/bin/python scripts/verify_parsers.py tests/fixtures/synthetic/odisi/*.tsv          # or your real files
venv/bin/python scripts/verify_parsers.py tests/fixtures/synthetic/pressure/2024-05-10.dat --tracemalloc
venv/bin/python scripts/verify_parsers.py some.pdf                                       # -> no matching parser, exit 0
venv/bin/python scripts/make_synthetic_instrument_fixtures.py                            # regenerate the stand-ins
```

Tests:
```bash
cd python_backend
venv/bin/python -m pytest tests/unit/test_instrument_parsers.py tests/unit/test_dfos_pass_strain.py tests/unit/test_traffic_load_monitoring.py -q
venv/bin/python -m pytest tests/integration/test_workspace_datasets.py -q
venv/bin/python -m pytest tests/unit -q --deselect tests/unit/test_query_rewriter.py       # 446 passed, 6 skipped
cd .. && npx vitest run                                                                    # 65 passed
```

UI (after the restart with the flag on): open GeoPilot → the left panel shows **Documents** and
**Datasets** groups → click **+** → pick an ODiSI `.tsv` or the Campbell `.dat` → row appears
under Datasets with a progress bar; the thread shows one progress message that becomes the
summary card ("No calculation has run…") → type `run dfos pass strain` (or `… on pass 002`,
`… influence line at 8.4 m`) → result card: deterministic block, reference, three SVG charts,
Export to Excel, "Show AI draft interpretation — for engineer review" → for the `.dat`:
`run traffic load monitoring` (or `… mad multiplier 8`) → amber PROVISIONAL banner in the
block, hourly/peak charts, export `TRAFFIC_….xlsx`; the dataset row now expands to its
events. Flag off: none of that appears (`.cpt` picker, flat document list, `run dfos…` →
"I can run: CPT interpretation").

curl equivalents (cookie from a login):
```bash
curl -s -c c.txt -X POST http://127.0.0.1:8000/auth/login -H 'Content-Type: application/json' -d '{"email":"...","password":"..."}' >/dev/null
curl -s -b c.txt http://127.0.0.1:8000/api/workspace/status
curl -s -b c.txt -F "file=@python_backend/tests/fixtures/synthetic/odisi/ODiSI_6000_2026-04-09_16-18-51_ch3_pass_001.tsv" http://127.0.0.1:8000/api/workspace/documents
curl -s -b c.txt http://127.0.0.1:8000/api/workspace/datasets
curl -s -b c.txt http://127.0.0.1:8000/api/workspace/datasets/jobs/<job_id>
curl -s -b c.txt -X POST http://127.0.0.1:8000/api/workspace/chat -H 'Content-Type: application/json' -d '{"message":"run dfos pass strain"}'
curl -s -b c.txt -o out.xlsx http://127.0.0.1:8000/api/workspace/export/<run_id>
```
(Through the public URL use `curl --resolve chenglin-geoai.cive.uvic.ca:443:127.0.0.1 https://chenglin-geoai.cive.uvic.ca/api/workspace/upload ...`;
the 58 MB `.dat` needs the nginx change first.)

## Decisions taken without you (and what I decided)

1. Real fixtures absent → built and used SYNTHETIC stand-ins from the written spec (generator + README); every verification is labelled as such.
2. No sudo → no `systemctl restart`, no rebuild of the live `.next`; verification on throwaway :8010/:8011/:3010 (all stopped), prod untouched and left on the old code with the flag `false`.
3. `parsers/` = the existing `app/workspace/parsers/` package (sibling of `calculators/`); `scripts/` = new `python_backend/scripts/`.
4. "Existing upload handler" = GeoPilot's `POST /api/workspace/documents`; Main Chat's `/api/upload` untouched.
5. Flag read with the repo's exact pattern (includes `"on"`).
6. Dataset routes registered only when the flag is on (highlights pattern) AND call-time gated with the absent-route body; dataset-bound calculators installed only when the flag is on.
7. Additive calculator-contract fields (`DatasetInput`, `charts`, `segments`, `notices`, `required_dataset_kind`) instead of a second registry.
8. Dataset selection for a run: explicit filename-token match else newest parsed dataset of the kind.
9. Datasets are durable (Mongo pointer + `.npz` + retained raw upload); "New session" keeps them; row × deletes doc + jobs + files. Retry re-parses from the retained raw file.
10. Frontend charts = inline SVG (no chart library installed, none installable); dataset uploads use a streaming Route Handler (`/api/workspace/upload`) because of the 10 MB rewrite cap.
11. Fixed the exporter's quadratic `ws.max_row` loop (31 s → 0.6 s) — the only pre-existing exporter line touched; golden parity tests pass. Export filename prefix per calculator (`CPT` default, `DFOS`, `TRAFFIC`).
12. Phase-6 defaults: `percentile_mad`, p20 baseline over 300 s blocks, 6 × MAD, merge 1.0 s, min 0.3 s, ≥1 channel — all in config, all marked provisional; plausibility band 1..5000 with a warning notice outside it.
13. AI draft for dataset results reuses the CPT hook's Ollama client (`think=False`), collapsed in the card behind an explicit review affordance; the deterministic block never depends on it.
14. Progress writes throttled (≥1 % and ≥0.5 s); parse runs in a 1-thread pool; a job with no progress for 900 s reads as failed/interrupted (retryable) rather than being mutated on startup.

Full backend integration suite (`pytest tests/integration --ignore=tests/integration/test_api.py`):
122 passed, 2 failed — the two failures are `test_thread_delete_cascade.py` ("Event loop is
closed" against live Mongo) and **fail identically on the untouched `git archive HEAD` copy**,
i.e. pre-existing and unrelated (as is the stale `test_api.py`).

State at hand-off: tree dirty (nothing committed), `python_backend/.env` line 47
`INSTRUMENT_PARSERS_ENABLED=false`, prod `geoai-backend`/`geoai-frontend` active on the
pre-change build, my throwaway processes stopped, `python_backend/data/` removed (recreated
lazily on first parse), scratchpad copies/logs under this session's scratchpad.

---

# Fix — DFOS end-of-fibre artifact and gage quality reporting (2026-08-18, after the rollout)

Scope: `dfos_pass_strain` + the ODiSI parser only. Nothing else touched (git diff: the
Phase-6 calculator, upload path, routes and RAG files unchanged).

**Where the real file came from.** The real fixtures are still not under `tests/fixtures/`,
but you had restarted prod with the flag ON (17:31) and uploaded pass 001 through GeoPilot,
so its retained raw upload `python_backend/data/instrument_datasets/raw/6a84f9c13da46b0bb67756b7.tsv`
(22.6 MB, CRLF, `Tare<TAB><TAB>strain<TAB>values…`, 30 header lines) is the real pass 001 and
was used for every check below. Passes 002–004 were NOT available (only synthetic stand-ins,
regenerated to the corrected convention — see Fix 0); their figures below are synthetic.

## Fix 0 (prerequisite, beyond the four listed) — the tare was being subtracted twice

Your expected values (peak ≈ 3,563 µε, envelope min ≈ −2,134 µε) are the file's RAW
measurement values. Verified on the real file: the per-gage median of the recorded strain over
the pass is ≈ 0 (median 6, p5 −387, p95 +575 µε) while the `Tare` row spans −15,792…+5,964 µε,
correlation(median strain, tare) = 0.006. If the rows were absolute they would track the tare
(corr ≈ 1). So the ODiSI 6xxx writes measurement rows ALREADY relative to the named tare and the
`Tare` row is the reference baseline. Phase 5's `eps_rel = strain − tare` double-tared: on the
real file it produced a peak of 5,011.9 / min −6,306.8 (with the 40-gage trim) instead of your
3,563.2 / −2,133.7. Fixed: the recorded strain IS the tare-relative strain; the Tare row is
kept in the export and not subtracted again. New config `DFOS_SUBTRACT_TARE` (default `false`;
set `true` only for a file whose rows are absolute). The reference states this. The synthetic
generator now writes tared rows (plus the termination artifact and 14 dead gages) — the
previous synthetic files were absolute, which is why the double subtraction went unnoticed.

## Fix 1 — Parser reports gage quality ✅
`parsers/odisi.py`: `metadata.nan_count`, `nan_fraction`, `dead_gage_count` (NaN across all
timesteps), `dead_gage_indices` (capped at 200, count always stored, `dead_gage_indices_truncated`),
`tare_nan_count`; a warning (never a raise) when `dead_gage_count > 0` or `nan_fraction > 0.1 %`.
Also while there: `measurement_rate_hz` now parses the real header's `"8.33333 Hz"` value and
`sensor_length_m` the real `Length (m):` key; the declared-gage-count check now looks at header
keys only (my own `dead_gage_count` had matched it once — caught in testing).

## Fix 2 — Fibre ends excluded before peak tracking ✅
`DFOS_EDGE_EXCLUDE_GAGES` (config, default 40; inline override `edge exclude N`) trimmed from
the head AND the tail for peak tracking, envelope, speed fit, global peak and the influence-gage
lookup. The `.npz` is untouched and the export keeps the full fibre (Envelope sheet: all 7,795
gages, new "In analysed span" and "Dead gage" columns; full-fibre max/min also in metadata).
Trim length is PROVISIONAL: stated in `reference` (by how much, both ends), rendered as the FIRST
notice in the card (amber, "Provisional" tag) alongside the peak-tracking notice, and in the
Summary sheet (`Trim validation status`). Trim leaving < 3 gages → clean ValueError → chat error.

## Fix 3 — Quality in the deterministic block ✅
Summary/deterministic block rows: "Gages excluded at each end (provisional trim)", "Analysed
gages", "Analysed span (m)" (a to b, span of total), "Dead gages (all-NaN), whole fibre", "Dead
gages inside analysed span", "NaN fraction (%)", "Tare handling", "Trim validation status"; a
"Gage quality" info notice; a WARNING notice when the speed-fit R² < 0.5 ("do not read the
implied speed as a vehicle speed").

## Fix 4 — Chart ✅
Envelope chart carries only the analysed span; `x_range` = full fibre and `bands` = the two
trimmed regions (`strain-chart.tsx` shades them with a "trimmed 40 gages" label); the peak-position
chart shades the same regions on its y axis. Downsampling unchanged (2,000 points).

**Files modified:** `python_backend/app/workspace/parsers/odisi.py`,
`python_backend/app/workspace/calculators/dfos_pass_strain.py` (rewritten),
`python_backend/app/core/config.py` (+`DFOS_EDGE_EXCLUDE_GAGES`, `DFOS_SUBTRACT_TARE`),
`python_backend/scripts/verify_parsers.py` (prints the quality fields),
`python_backend/scripts/make_synthetic_instrument_fixtures.py` (tared rows + artifact + dead gages; regenerated),
`python_backend/tests/unit/test_dfos_pass_strain.py` (12 tests: tare convention, subtract flag,
trim excludes the artifact but export keeps the full fibre + span flag + dead flag, config vs
inline trim, low-R² warning, …), `python_backend/tests/integration/test_workspace_datasets.py`
(graceful error when the trim exceeds the fibre; trim notice/summary/bands in the chat reply),
`app/workspace/components/strain-chart.tsx` (+`x_range/y_range/bands/bands_y`),
`app/workspace/workspace.module.css` (band classes), `app/workspace/__tests__/result-card-dataset.test.tsx` (+1).
No change to `.env`.

## Verification (verbatim)

```
config: DFOS_EDGE_EXCLUDE_GAGES = 40 | DFOS_SUBTRACT_TARE = False

== REAL pass_001 (retained raw upload)
  parser: gages 7795 timesteps 497 | NaN 30,807 (0.80%) | dead gages 14 [151, 3640, 5740, 5741, 5742, 5743, 5744, 5745, 5746, 5747, 5748, 5749, 5750, 5751] | warnings: ['14 dead gage(s) (NaN across all 497 timesteps): indices 151, 3640, 5740, 5741, 5742, 5743, 5744, 5745, 5746, 5747, 5748, 5749, ....', '30,807 NaN strain values (0.80% of 3,874,115); reductions ignore them (nan-aware).']
  calc (0.22s, identical across 2 runs=True, sha=e4a14a12031af1cf):
    trim 40/end -> analysed gages 40..7754 (7715), x 0.185-20.338 m (span 20.153 of 20.362 m); dead in span 14; tare subtracted=False
    global peak 3563.2 µε @ x=20.338 m (gage 7754, t=197); envelope max 3563.2 / min -2133.7 (full-fibre max 11267.6 / min -21400.2)
    speed fit: loaded 497/497, speed 0.05680361610994449, R2 0.10704033763763499, +x (increasing fibre position)
    notices: ['provisional', 'info', 'info', 'warning']
    export: 399 KB, Envelope rows 7795 (full fibre), last-3-gage max in xlsx [11267.599609375, 7772.39990234375, -15405.7998046875] vs raw [11267.6, 7772.4, -15405.8]; spot-check untrimmed precision ok=True; span flag row2='no (trimmed)' row42='yes'
    Summary: trim status='PROVISIONAL - trim length pending engineering validation'; excluded/end=40; dead=14; NaN%=0.795

== SYNTHETIC pass_001
  parser: gages 7795 timesteps 497 | NaN 6,958 (0.18%) | dead gages 14 [151, 3640, 5740, 5741, 5742, 5743, 5744, 5745, 5746, 5747, 5748, 5749, 5750, 5751] | warnings: ['14 dead gage(s) (NaN across all 497 timesteps): indices 151, 3640, 5740, 5741, 5742, 5743, 5744, 5745, 5746, 5747, 5748, 5749, ....', '6,958 NaN strain values (0.18% of 3,874,115); reductions ignore them (nan-aware).']
  calc (0.24s, identical across 2 runs=True, sha=59802ef5ffd351c9):
    trim 40/end -> analysed gages 40..7754 (7715), x 0.184-20.338 m (span 20.153 of 20.362 m); dead in span 14; tare subtracted=False
    global peak 109.9 µε @ x=13.595 m (gage 5173, t=201); envelope max 109.9 / min -8.1 (full-fibre max 10140.8 / min -13865.2)
    speed fit: loaded 119/497, speed 1.5358031916156296, R2 0.964818976732493, +x (increasing fibre position)
    notices: ['provisional', 'info', 'info']
    export: 476 KB, Envelope rows 7795 (full fibre), last-3-gage max in xlsx [10140.83984375, 6995.16015625, -13865.2197265625] vs raw [10140.8, 6995.2, -13865.2]; spot-check untrimmed precision ok=True; span flag row2='no (trimmed)' row42='yes'
    Summary: trim status='PROVISIONAL - trim length pending engineering validation'; excluded/end=40; dead=14; NaN%=0.18

== SYNTHETIC pass_002
  parser: gages 7795 timesteps 534 | NaN 7,476 (0.18%) | dead gages 14 [151, 3640, 5740, 5741, 5742, 5743, 5744, 5745, 5746, 5747, 5748, 5749, 5750, 5751] | warnings: ['14 dead gage(s) (NaN across all 534 timesteps): indices 151, 3640, 5740, 5741, 5742, 5743, 5744, 5745, 5746, 5747, 5748, 5749, ....', '7,476 NaN strain values (0.18% of 4,162,530); reductions ignore them (nan-aware).']
  calc (0.22s, identical across 2 runs=True, sha=871c8198d0bd733a):
    trim 40/end -> analysed gages 40..7754 (7715), x 0.184-20.338 m (span 20.153 of 20.362 m); dead in span 14; tare subtracted=False
    global peak 124.6 µε @ x=0.746 m (gage 255, t=149); envelope max 124.6 / min -8.0 (full-fibre max 11267.6 / min -15405.8)
    speed fit: loaded 107/534, speed 1.7049862605066712, R2 0.9649852315573989, +x (increasing fibre position)
    notices: ['provisional', 'info', 'info']
    export: 479 KB, Envelope rows 7795 (full fibre), last-3-gage max in xlsx [11267.599609375, 7772.39990234375, -15405.7998046875] vs raw [11267.6, 7772.4, -15405.8]; spot-check untrimmed precision ok=True; span flag row2='no (trimmed)' row42='yes'
    Summary: trim status='PROVISIONAL - trim length pending engineering validation'; excluded/end=40; dead=14; NaN%=0.18

== SYNTHETIC pass_003
  parser: gages 7795 timesteps 606 | NaN 8,484 (0.18%) | dead gages 14 [151, 3640, 5740, 5741, 5742, 5743, 5744, 5745, 5746, 5747, 5748, 5749, 5750, 5751] | warnings: ['14 dead gage(s) (NaN across all 606 timesteps): indices 151, 3640, 5740, 5741, 5742, 5743, 5744, 5745, 5746, 5747, 5748, 5749, ....', '8,484 NaN strain values (0.18% of 4,723,770); reductions ignore them (nan-aware).']
  calc (0.26s, identical across 2 runs=True, sha=8ffd365fc8d0fa6e):
    trim 40/end -> analysed gages 40..7754 (7715), x 0.184-20.338 m (span 20.153 of 20.362 m); dead in span 14; tare subtracted=False
    global peak 139.6 µε @ x=16.398 m (gage 6246, t=225); envelope max 139.6 / min -8.1 (full-fibre max 12394.4 / min -16946.4)
    speed fit: loaded 98/606, speed 1.867471453436108, R2 0.964657084891467, +x (increasing fibre position)
    notices: ['provisional', 'info', 'info']
    export: 486 KB, Envelope rows 7795 (full fibre), last-3-gage max in xlsx [12394.3603515625, 8549.6396484375, -16946.380859375] vs raw [12394.4, 8549.6, -16946.4]; spot-check untrimmed precision ok=True; span flag row2='no (trimmed)' row42='yes'
    Summary: trim status='PROVISIONAL - trim length pending engineering validation'; excluded/end=40; dead=14; NaN%=0.18

== SYNTHETIC pass_004
  parser: gages 7795 timesteps 600 | NaN 8,400 (0.18%) | dead gages 14 [151, 3640, 5740, 5741, 5742, 5743, 5744, 5745, 5746, 5747, 5748, 5749, 5750, 5751] | warnings: ['14 dead gage(s) (NaN across all 600 timesteps): indices 151, 3640, 5740, 5741, 5742, 5743, 5744, 5745, 5746, 5747, 5748, 5749, ....', '8,400 NaN strain values (0.18% of 4,677,000); reductions ignore them (nan-aware).']
  calc (0.24s, identical across 2 runs=True, sha=ef4b405e9aea678b):
    trim 40/end -> analysed gages 40..7754 (7715), x 0.184-20.338 m (span 20.153 of 20.362 m); dead in span 14; tare subtracted=False
    global peak 154.7 µε @ x=13.159 m (gage 5006, t=206); envelope max 154.7 / min -7.8 (full-fibre max 13521.1 / min -18487.0)
    speed fit: loaded 89/600, speed 2.041488792418568, R2 0.9647629675092365, +x (increasing fibre position)
    notices: ['provisional', 'info', 'info']
    export: 483 KB, Envelope rows 7795 (full fibre), last-3-gage max in xlsx [13521.1201171875, 9326.8798828125, -18486.9609375] vs raw [13521.1, 9326.9, -18487.0]; spot-check untrimmed precision ok=True; span flag row2='no (trimmed)' row42='yes'
    Summary: trim status='PROVISIONAL - trim length pending engineering validation'; excluded/end=40; dead=14; NaN%=0.18
```

| Field | Pass 001 expectation | Real pass 001 result |
|---|---|---|
| Global peak | ≈ 3,563 µε, not at x > 20.35 m | **3,563.2 µε at x = 20.338 m** (gage 7754 — the LAST analysed gage, i.e. on the trim boundary) |
| Envelope min | ≈ −2,134 µε | **−2,133.7 µε** |
| Dead gages | 14 | **14** (151, 3640, 5740–5751) |
| NaN fraction | 0.80 % | **0.80 %** (30,807 of 3,874,115) |
| Speed fit | loaded ≫ 8, R² ≫ 0.010 | **loaded 497/497, R² 0.107, 0.06 m/s** — ⛔ criterion NOT met |

Determinism: identical across two runs (standalone and live, SHA `8382de8c46471478` live twice).
Excel: Envelope sheet 7,795 rows, untrimmed — the last three gages export as 11267.6 /
7772.4 / −15405.8 (float64 round-trip spot-checked ok), span flag column present; Summary
carries the provisional trim status, excluded/end, dead gages, NaN %.
Live (throwaway :8010, isolated `INSTRUMENT_DATA_DIR`, real pass 001 uploaded → parsed with the
two quality warnings on the row → `run dfos pass strain` twice): ` row: real_pass_001.tsv parsed DFOS · 7795 gages | warnings: ['14 dead gage(s) (NaN across all 497 timesteps): indices 151, 3640, 5740, 5741, 5742, 5743, 5744, 5745, 5746, 5747, 5748, 5749, ....', '3`
Flag-off parity (throwaway restarted with the flag off): status `{"enabled":true}`, datasets
`{"detail":"Not Found"}`, `run dfos pass strain` → `I can run: CPT interpretation - say 'run cpt'.`;
the 5 flag-off integration tests pass. Suites: unit 451 passed / 6 skipped; dataset integration
17 passed; vitest 66 passed; `tsc` clean; scratchpad build `✓ Compiled successfully in 5.2s`.

## ⛔ Stop-and-log: the tracker still does not follow a truck on the real pass 001

Per your instruction the trim was NOT tuned upward. Diagnostics (recorded, not acted on):

```
REAL pass 001 — why the speed fit fails after a 40-gage trim (diagnostics, no tuning applied)
1) Peak position over time is dominated by stationary features:
   x =  11.94 m holds the per-timestep maximum in 327 of 497 timesteps
   x =   7.89 m holds the per-timestep maximum in  79 of 497 timesteps
   x =  20.34 m holds the per-timestep maximum in  10 of 497 timesteps
   x =  19.33 m holds the per-timestep maximum in   7 of 497 timesteps
   x =   2.42 m holds the per-timestep maximum in   7 of 497 timesteps
   x =  11.58 m holds the per-timestep maximum in   5 of 497 timesteps
   per-timestep peak value: min 896, median 1175, max 3563 µε -> never below 20% of the global peak (713), so all 497 timesteps count as 'loaded'
2) Envelope near the analysed tail (gage index : max µε), last 80 analysed gages in steps of 8:
   7675:1955, 7683:2216, 7691:2167, 7699:2083, 7707:2110, 7715:2082, 7723:2172, 7731:2470, 7739:1656, 7747:2004
3) Gages with envelope max > 2000 µε: 1230 gages; ranges: 818-924, 997-1001, 1667-1750, 2338-2423, 2472-2485, 3135-3262, 3615-3615, 3617-3617, 3628-3628, 3632-3632, 3649-3687, 3691-3693, 3697-3699, 3702-3729, 3734-3736, 3742-3742, 3744-3745, 3815-3839, 4362-4374, 4376-4436, 4442-4480, 4482-4590, 4592-4592, 4594-4601, 5115-5254, 5304-5351, 6001-6051, 6505-6507, 6509-6509, 6511-6538, 7313-7430, 7656-7657, 7659-7664, 7666-7671, 7674-7674, 7676-7683, 7687-7691, 7698-7701, 7703-7725, 7727-7736, 7741-7745, 7747-7754
4) A moving front IS visible if one looks at where strain > 1000 µε is centred (not the maximum):
   centroid of (strain > 1000 µε) every 10 timesteps: ['t80:3.0', 't90:8.5', 't100:8.9', 't110:12.9', 't120:13.8', 't130:13.4', 't140:11.9', 't150:11.9', 't160:11.9', 't170:5.8', 't180:12.2', 't190:17.2', 't200:14.5', 't210:12.6', 't220:12.6', 't230:12.5', 't240:12.5', 't250:12.5', 't260:12.5', 't270:12.5']
   least-squares slope of that centroid over timesteps 80-140 (the crossing): 1.47 m/s, R2 0.669 — for information only; NOT used by the calculator
5) Before the crossing (t<80) the maximum sits at ~7.89 m (~900 µε); after it (t>140) at ~11.94 m (~1170 µε): residual/locked-in strain that does not return to zero within the pass.
```

Reading: (a) two stationary strain plateaus dominate the per-timestep maximum — ~900 µε at
7.89 m before the truck arrives and ~1,170 µε at 11.94 m after it — so the peak never drops
below 20 % of the global peak and every timestep counts as "loaded"; (b) the global peak itself
sits on the last analysed gage (7754), and 1,230 analysed gages have envelope maxima > 2,000 µε
in ~40 separate ranges across the whole fibre (818–924, 1667–1750, 2338–2423, 3135–3262,
3649–3745, 4362–4601, 5115–5351, 6001–6051, 6505–6538, 7313–7430, 7656–7754), so "more gages
are bad than the edge trim accounts for" is a live possibility — or the pavement really is that
strained; (c) a moving front is visible in the CENTROID of strain > 1,000 µε (3.0 → 8.9 → 13.8 m
over timesteps 80–140, ≈ 1.5 m/s at R² 0.67 — information only, not implemented), which is
what a fitted-load tracker would follow. Whether to (i) treat the plateaus as locked-in strain
and track change relative to the pre-arrival state, (ii) exclude the > 2,000 µε ranges as bad
gages, or (iii) accept that this pass has no clean moving peak, is Dr. Lin's call — the
calculator now says plainly (WARNING notice) that the implied speed is not a vehicle speed.

## Notes for Dr. Lin (recorded, not acted on)
- Even trimmed, 3,563 µε (and −2,134 µε) is high for pavement under a slow truck; the extent of
  the > 2,000 µε regions inboard suggests either real local behaviour (debonding, cracking, poor
  coupling) or further bad gages. Not compensated numerically.
- The Tare row's magnitude (−15,792…+5,964 µε) means large locked-in strain relative to the
  tare state; not interpreted here.

## Residue update / what you need to do
- Prod is currently running the PRE-FIX code with the flag ON (your 17:31 restart): restart the
  backend to pick up the fix; the frontend copy builds clean — rebuild prod's `.next` for the
  shaded trim bands (`sudo systemctl stop geoai-frontend && rm -rf .next && npm run build && sudo systemctl start geoai-frontend`).
  Then re-run `run dfos pass strain` on your uploaded pass 001 (the stored dataset needs no
  re-upload — but re-uploading gives the row its new quality warnings; the parser fields are only
  written at parse time).
- Throwaway user residue is now 13 `workspace_runs` + 14 `workspace_threads` (2 runs / 3 threads
  added by this fix's live checks); its dataset/job docs were deleted through the API and its files
  lived in the session scratchpad, not the prod data dir. Same cleanup commands as before.
- Real passes 002–004 have not been through the parser or the calculator; when uploaded, run
  `venv/bin/python scripts/verify_parsers.py <path>` and `run dfos pass strain on pass 00N`.

---

# Fix 2 — Trim sizing and speed suppression (2026-08-18)

Scope: `dfos_pass_strain` + its config only. Parser, upload path, Phase 6 untouched
(`git diff` on those files empty). Tare handling not revisited. Real data available: still only
pass 001 (the retained raw upload); passes 002–004 below are the synthetic stand-ins.

## Fix 1 — Two separate exclusions ✅
`DFOS_EDGE_EXCLUDE_GAGES` removed. New: `DFOS_LEADIN_EXCLUDE_M` = 1.10 (head; fibre position
x < 1.10 m; reason string "unbonded lead-in") and `DFOS_TAIL_EXCLUDE_M` = 0.50 (far end;
x > x_last − 0.50; reason "fibre termination artifact"); inline overrides `lead-in exclude 1.2`,
`tail exclude 0.6`. Both stated separately with their reasons in the deterministic block
("Lead-in excluded at head (m) - unbonded lead-in (provisional)" + gages, "Tail excluded at far end
(m) - fibre termination artifact (provisional)" + gages, "Exclusion validation status"), in the
`reference`, in the first (provisional) notice, and as the two shaded chart bands. `.npz` and
Excel keep the full fibre (Envelope sheet: 7,795 rows with per-gage region flag). Metadata records
`global_peak_on_trim_boundary`; a warning notice is raised if it is ever true (it is not on pass 001).

## Fix 2 — Speed suppressed when the fit is not credible ✅
`DFOS_SPEED_MIN_R2` = 0.70. Below it: `implied_speed_m_s`, `implied_speed_kmh`, `direction` are
`None` in metadata; the deterministic block / Excel Summary carry "Implied speed (m/s) = not
determinable (R² 0.157)", "Implied speed (km/h) = not determinable", "Direction of travel = not
determinable", "Load tracking = peak tracking did not resolve a moving load on this dataset (R² …
< 0.7)"; `summary_text`, the warning notice and the AI-draft input all say the same; nothing is
softened into a caveated number. Above the threshold speed/direction are reported as before (the
four synthetic passes: R² 0.96, 1.51–2.01 m/s).

## Fix 3 — Band profile ✅
`band_profile()`: max |strain| per `DFOS_BAND_WIDTH_M` (0.5 m) band across the FULL fibre incl.
excluded regions — x range, gage range, gage count, dead gages, max |strain|, median of the per-gage
max |strain|, fraction of gages above `DFOS_BAND_HIGH_STRAIN_MICROSTRAIN` (2000), region label.
Exported as its own "Band profile" sheet, exposed as `metadata.band_profile` (the values behind
the new `band_profile` bar chart, excluded ends shaded). No commentary attached.

**Files modified:** `python_backend/app/core/config.py` (DFOS_* block), 
`python_backend/app/workspace/calculators/dfos_pass_strain.py` (rewritten),
`python_backend/tests/unit/test_dfos_pass_strain.py` (13 tests), 
`python_backend/tests/integration/test_workspace_datasets.py` (exclusion/band assertions). No `.env` change.

## Verification (verbatim; real pass 001 + four synthetic passes)
```
config: DFOS_LEADIN_EXCLUDE_M=1.1 DFOS_TAIL_EXCLUDE_M=0.5 DFOS_SPEED_MIN_R2=0.7 DFOS_BAND_WIDTH_M=0.5 DFOS_SUBTRACT_TARE=False

== REAL pass_001 (retained raw upload)
  parser: gages 7795 timesteps 497 | NaN 30,807 (0.80%) | dead 14
  calc (0.27s, identical across 2 runs=True, sha=f3fba1e0705ddd46)
    exclusions: lead-in 1.1 m (unbonded lead-in, 391 gages) | tail 0.5 m (fibre termination artifact, 192 gages) -> analysed x 1.102-19.941 m (7212 gages), dead in span 13
    global peak 3497.3 µε @ x=11.857 m (gage 4508, t=178); on exclusion boundary: False; envelope max 3497.3 / min -2133.7
    speed: R2 0.15698755186173274 (threshold 0.7), credible=False, implied_speed_m_s=None, direction=None | block: 'not determinable (R² 0.157)' / 'not determinable' / 'peak tracking did not resolve a moving load on this dataset (R² 0.157 < 0.7)'
    notices: ['provisional', 'info', 'info', 'warning']
    export 404 KB sheets=['Peak per timestep', 'Envelope', 'Influence line', 'Band profile', 'Summary']; Summary speed rows: Peak fraction for speed fit=0.2; Speed fit R2=0.1569875518617327; Speed credibility threshold R2=0.7; Load tracking='peak tracking did not resolve a moving load on this dataset (R² 0.157 < 0.7)'; Implied speed (m/s)='not determinable (R² 0.157)'; Implied speed (km/h)='not determinable'; Direction of travel='not determinable'; numeric speed leaks: []
    Envelope sheet rows 7795 (full fibre); Band profile sheet rows 41
    Band profile (band: x_from-x_to | max|ε| | median gage max|ε| | frac>2000 | dead | region):
       1:   0.08-  0.58 |     33.2 |     3.7 | 0.00 | 1 | lead-in (excluded: unbonded lead-in)
       2:   0.58-  1.08 |     21.8 |     8.6 | 0.00 | 0 | lead-in (excluded: unbonded lead-in)
       3:   1.08-  1.58 |    473.1 |   138.0 | 0.00 | 0 | partly excluded
       4:   1.58-  2.08 |    883.0 |   523.4 | 0.00 | 0 | analysed
       5:   2.08-  2.58 |   2686.1 |  2109.3 | 0.56 | 0 | analysed
       6:   2.58-  3.08 |   2048.2 |  1079.5 | 0.03 | 0 | analysed
       7:   3.08-  3.58 |   1831.6 |   944.9 | 0.00 | 0 | analysed
       8:   3.58-  4.08 |   1012.1 |   827.6 | 0.00 | 0 | analysed
       9:   4.08-  4.58 |   2378.5 |  1793.9 | 0.29 | 0 | analysed
      10:   4.58-  5.08 |   2259.7 |   818.2 | 0.15 | 0 | analysed
      11:   5.08-  5.58 |    880.2 |   489.4 | 0.00 | 0 | analysed
      12:   5.58-  6.08 |   1738.7 |   871.5 | 0.00 | 0 | analysed
      13:   6.08-  6.58 |   2754.7 |  2039.7 | 0.52 | 0 | analysed
      14:   6.58-  7.08 |   1931.3 |  1072.6 | 0.00 | 0 | analysed
      15:   7.08-  7.58 |   1388.5 |  1161.0 | 0.00 | 0 | analysed
      16:   7.58-  8.08 |   2114.7 |  1456.0 | 0.06 | 0 | analysed
      17:   8.08-  8.58 |   2786.3 |  2333.5 | 0.62 | 0 | analysed
      18:   8.58-  9.08 |   2271.9 |   804.6 | 0.05 | 0 | analysed
      19:   9.08-  9.58 |   2461.1 |   960.5 | 0.02 | 0 | analysed
      20:   9.58- 10.08 |   2725.9 |  1997.6 | 0.48 | 1 | analysed
      21:  10.08- 10.58 |   2192.0 |  1618.9 | 0.21 | 0 | analysed
      22:  10.58- 11.08 |   1382.5 |  1146.3 | 0.00 | 0 | analysed
      23:  11.08- 11.58 |   2563.6 |  1080.6 | 0.20 | 0 | analysed
      24:  11.58- 12.08 |   3497.3 |  2618.2 | 0.96 | 0 | analysed
      25:  12.08- 12.58 |   2254.4 |   913.6 | 0.04 | 0 | analysed
      26:  12.58- 13.08 |   1167.9 |   420.7 | 0.00 | 0 | analysed
      27:  13.08- 13.58 |   2956.5 |  1118.2 | 0.28 | 0 | analysed
      28:  13.58- 14.08 |   2961.1 |  2340.4 | 0.71 | 0 | analysed
      29:  14.08- 14.58 |   1843.0 |   754.7 | 0.00 | 0 | analysed
      30:  14.58- 15.08 |    621.3 |   400.1 | 0.00 | 2 | analysed
      31:  15.08- 15.58 |    901.2 |   115.9 | 0.00 | 10 | analysed
      32:  15.58- 16.08 |   2524.8 |  1589.7 | 0.27 | 0 | analysed
      33:  16.08- 16.58 |   1119.9 |   800.9 | 0.00 | 0 | analysed
      34:  16.58- 17.08 |   2093.0 |  1245.7 | 0.01 | 0 | analysed
      35:  17.08- 17.58 |   2228.3 |  1298.6 | 0.16 | 0 | analysed
      36:  17.58- 18.08 |   1327.3 |   752.8 | 0.00 | 0 | analysed
      37:  18.08- 18.58 |    655.6 |   499.6 | 0.00 | 0 | analysed
      38:  18.58- 19.08 |   1627.6 |  1215.8 | 0.00 | 0 | analysed
      39:  19.08- 19.58 |   2662.7 |  2237.8 | 0.62 | 0 | analysed
      40:  19.58- 20.08 |   1980.9 |  1044.6 | 0.00 | 0 | partly excluded
      41:  20.08- 20.44 |  21400.2 |  2135.5 | 0.72 | 0 | tail (excluded: fibre termination artifact)
    Gages with max|strain| > 2000 µε in the analysed span: 1193 gages in 33 contiguous runs; longest run 0.36 m (gages 5115-5254); runs >= 0.10 m: 2.22-2.49 m, 4.44-4.65 m, 6.19-6.41 m, 8.27-8.60 m, 11.51-11.67 m, 11.79-12.07 m, 13.44-13.81 m, 13.94-14.06 m, 15.76-15.89 m, 19.19-19.49 m

== SYNTHETIC pass_001
  parser: gages 7795 timesteps 497 | NaN 6,958 (0.18%) | dead 14
  calc (0.22s, identical across 2 runs=True, sha=5fda4e5504f6c464)
    exclusions: lead-in 1.1 m (unbonded lead-in, 391 gages) | tail 0.5 m (fibre termination artifact, 192 gages) -> analysed x 1.101-19.941 m (7212 gages), dead in span 13
    global peak 109.9 µε @ x=13.595 m (gage 5173, t=201); on exclusion boundary: False; envelope max 109.9 / min -8.1
    speed: R2 0.9596841679860674 (threshold 0.7), credible=True, implied_speed_m_s=1.5103094849956755, direction='+x (increasing fibre position)' | block: 1.5103094849956755 / '+x (increasing fibre position)' / 'peak tracking resolved a moving load (fit credible)'
    notices: ['provisional', 'info', 'info']
    export 481 KB sheets=['Peak per timestep', 'Envelope', 'Influence line', 'Band profile', 'Summary']; Summary speed rows: Peak fraction for speed fit=0.2; Speed fit R2=0.9596841679860674; Speed credibility threshold R2=0.7; Load tracking='peak tracking resolved a moving load (fit credible)'; Implied speed (m/s)=1.510309484995676; Implied speed (km/h)=5.437114145984432; Direction of travel='+x (increasing fibre position)'; numeric speed leaks: []
    Envelope sheet rows 7795 (full fibre); Band profile sheet rows 41

== SYNTHETIC pass_002
  parser: gages 7795 timesteps 534 | NaN 7,476 (0.18%) | dead 14
  calc (0.22s, identical across 2 runs=True, sha=568e65f09c9268ef)
    exclusions: lead-in 1.1 m (unbonded lead-in, 391 gages) | tail 0.5 m (fibre termination artifact, 192 gages) -> analysed x 1.101-19.941 m (7212 gages), dead in span 13
    global peak 124.4 µε @ x=19.481 m (gage 7426, t=227); on exclusion boundary: False; envelope max 124.4 / min -7.5
    speed: R2 0.9600178648270664 (threshold 0.7), credible=True, implied_speed_m_s=1.6769858572093652, direction='+x (increasing fibre position)' | block: 1.6769858572093652 / '+x (increasing fibre position)' / 'peak tracking resolved a moving load (fit credible)'
    notices: ['provisional', 'info', 'info']
    export 484 KB sheets=['Peak per timestep', 'Envelope', 'Influence line', 'Band profile', 'Summary']; Summary speed rows: Peak fraction for speed fit=0.2; Speed fit R2=0.9600178648270664; Speed credibility threshold R2=0.7; Load tracking='peak tracking resolved a moving load (fit credible)'; Implied speed (m/s)=1.676985857209365; Implied speed (km/h)=6.037149085953715; Direction of travel='+x (increasing fibre position)'; numeric speed leaks: []
    Envelope sheet rows 7795 (full fibre); Band profile sheet rows 41

== SYNTHETIC pass_003
  parser: gages 7795 timesteps 606 | NaN 8,484 (0.18%) | dead 14
  calc (0.40s, identical across 2 runs=True, sha=810ed5fe9ed00524)
    exclusions: lead-in 1.1 m (unbonded lead-in, 391 gages) | tail 0.5 m (fibre termination artifact, 192 gages) -> analysed x 1.101-19.941 m (7212 gages), dead in span 13
    global peak 139.6 µε @ x=16.398 m (gage 6246, t=225); on exclusion boundary: False; envelope max 139.6 / min -8.1
    speed: R2 0.9595447749430533 (threshold 0.7), credible=True, implied_speed_m_s=1.8471668105153385, direction='+x (increasing fibre position)' | block: 1.8471668105153385 / '+x (increasing fibre position)' / 'peak tracking resolved a moving load (fit credible)'
    notices: ['provisional', 'info', 'info']
    export 491 KB sheets=['Peak per timestep', 'Envelope', 'Influence line', 'Band profile', 'Summary']; Summary speed rows: Peak fraction for speed fit=0.2; Speed fit R2=0.9595447749430533; Speed credibility threshold R2=0.7; Load tracking='peak tracking resolved a moving load (fit credible)'; Implied speed (m/s)=1.847166810515338; Implied speed (km/h)=6.649800517855219; Direction of travel='+x (increasing fibre position)'; numeric speed leaks: []
    Envelope sheet rows 7795 (full fibre); Band profile sheet rows 41

== SYNTHETIC pass_004
  parser: gages 7795 timesteps 600 | NaN 8,400 (0.18%) | dead 14
  calc (0.29s, identical across 2 runs=True, sha=9ae1d3a67c751ea4)
    exclusions: lead-in 1.1 m (unbonded lead-in, 391 gages) | tail 0.5 m (fibre termination artifact, 192 gages) -> analysed x 1.101-19.941 m (7212 gages), dead in span 13
    global peak 154.7 µε @ x=13.159 m (gage 5006, t=206); on exclusion boundary: False; envelope max 154.7 / min -7.8
    speed: R2 0.9600267423616812 (threshold 0.7), credible=True, implied_speed_m_s=2.00822391049443, direction='+x (increasing fibre position)' | block: 2.00822391049443 / '+x (increasing fibre position)' / 'peak tracking resolved a moving load (fit credible)'
    notices: ['provisional', 'info', 'info']
    export 489 KB sheets=['Peak per timestep', 'Envelope', 'Influence line', 'Band profile', 'Summary']; Summary speed rows: Peak fraction for speed fit=0.2; Speed fit R2=0.9600267423616812; Speed credibility threshold R2=0.7; Load tracking='peak tracking resolved a moving load (fit credible)'; Implied speed (m/s)=2.00822391049443; Implied speed (km/h)=7.229606077779949; Direction of travel='+x (increasing fibre position)'; numeric speed leaks: []
    Envelope sheet rows 7795 (full fibre); Band profile sheet rows 41
```
- Analysed span **1.102–19.941 m** (7,212 gages; 391 lead-in + 192 tail gages excluded).
- Global peak **3,497.3 µε at x = 11.857 m** (gage 4508, timestep 178) — matches your middle-region
  max; **not on either exclusion boundary**. Envelope min −2,133.7 µε.
- Speed: R² 0.157 → **not determinable** everywhere. A regex scan of every cell of every sheet of
  the live export for a number followed by m/s or km/h found nothing; the Summary rows read
  `'not determinable (R² 0.157)'` / `'not determinable'`.
- Determinism: identical across two runs (standalone SHA `f3fba1e0705ddd46`; live twice
  `882b83bd73ca300b`). Live (throwaway :8010, isolated data dir):
```
[8.1s] type=result calc=dfos_pass_strain file=real_pass_001.tsv run_id=6a8503814c9e6ec71963638f exportable=True
  summary_text: Analysed x = 1.10-19.94 m (7212 gages; lead-in 1.1 m and tail 0.5 m excluded, provisional; 14 dead gages, 0.80% NaN). Global peak 3497.3 µε at x = 11.86 m, timestep 178 (2026-04-09 16:24:07.666345); envelope max 3497.3 µε, min -2133.7 µε; implied speed and direction not determinable - peak tracking did not resolve a moving load on this dataset (speed-fit R² 0.157 < 0.7, 497 timesteps above the peak fraction); influence line at gage x = 11.86 m.
  notices: [{'level': 'provisional', 'text': 'Fibre-end exclusions: lead-in 1.1 m at the head (unbonded lead-in; 391 gages, x < 1.102 m) and tail 0.5 m at the far end (fibre termination artifact; 192 gages, x > 19.941 m) are not used for peak tracking, envelope, speed fit or the global peak; analysed span 18.84 m of 20.36 m. Both exclusion lengths are PROVISIONAL - not validated by the supervising engineer. Full untrimmed data remains in the export.'}, {'level': 'info', 'text': 'Peak tracking is a direct per-timestep maximum over the analysed gages, not a fitted model.'}, {'level': 'info', 'text': 'Gage quality: 14 dead gage(s) (NaN across all timesteps, 13 inside the analysed span); 30,807 NaN values (0.80%). Reductions ignore NaN.'}, {'level': 'warning', 'text': 'Peak tracking did not resolve a moving load on this dataset: speed-fit R² 0.157 is below the 0.7 credibility threshold. Implied speed and direction of travel are not determinable and are not reported.'}]
  charts: {'envelope': [2000, 2000], 'band_profile': [41], 'influence': [497], 'load_position': [497]}  deterministic-sha: 882b83bd73ca300b
  segments: 0  params: {}
  interpretation: is_ai_draft=True model=gemma4:12b error=None narrative='The analysis of real_pass_001.tsv involved 7,795 gages across 497 timesteps over an analyzed span of 1.102 to 19.941 meters. The global peak strain was recorded'
  reply bytes: 94119
  charts: {'envelope': [2000, 2000], 'band_profile': [41], 'influence': [497], 'load_position': [497]}  deterministic-sha: 882b83bd73ca300b
content-disposition: attachment; filename="DFOS_real_pass_001_20260818.xlsx"
export sheets: ['Peak per timestep', 'Envelope', 'Influence line', 'Band profile', 'Summary']
cells containing a numeric speed with units: []
Summary: Implied speed (m/s) = 'not determinable (R² 0.157)' | Direction = 'not determinable'
Band profile rows: 41 | header: ['Band', 'x from (m)', 'x to (m)', 'Gage from', 'Gage to', 'Gages']
```
- Suites: unit 452 passed / 6 skipped; dataset integration 17 passed; vitest 66 passed; `tsc` clean.

## For Dr. Lin (recorded, not acted on)
Band profile above: head bands 0.08–1.08 m read 33 / 22 µε max (median 4 / 9), tail band
20.08–20.44 m reads 21,400 (median 2,136, 72 % of gages > 2,000), middle max 3,497 at 11.6–12.1 m.
Gages with max |strain| > 2,000 µε inside the analysed span: 1,193 gages in 33 contiguous runs,
longest 0.36 m (gages 5115–5254, 13.44–13.81 m); runs ≥ 0.10 m at 2.22–2.49, 4.44–4.65, 6.19–6.41,
8.27–8.60, 11.51–11.67, 11.79–12.07, 13.44–13.81, 13.94–14.06, 15.76–15.89, 19.19–19.49 m; stable
plateaus near 7.89 m and 11.94 m. (Your count was 45 runs on your span; mine is 33 on
1.10–19.94 m with this exact threshold — the difference is span/threshold bookkeeping, not a
different picture.) Isolated bad gages do not cluster in runs of that length: patch debonding or
real pavement features. Not filtered, smoothed or compensated. No windowed / segmented / adaptive
tracking was attempted; the earlier R² 0.67 "moving front" note is withdrawn as evidence.

## Residue / what you need to do
- Prod (flag ON) still runs the code from before this fix; restart `geoai-backend` to pick it up
  (frontend needs no rebuild for this fix — the card renders the new rows/chart generically;
  the previous fix's shaded-band chart change still needs the frontend rebuild noted earlier).
- Throwaway user residue: now 15 `workspace_runs` + 16 `workspace_threads` (2 runs / 2 threads
  from this fix's live checks); datasets/jobs deleted via the API; files in the session scratchpad.
- Real passes 002–004 remain unverified against real data.
