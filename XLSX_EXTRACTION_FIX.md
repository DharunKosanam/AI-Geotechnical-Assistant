# XLSX extraction fix — row structure preserved (v3-xlsx)

Date: 2026-08-19. Status: implemented + verified locally, **uncommitted**, prod
backend **not restarted** (no sudo). No data deleted, no data re-ingested, no
database writes of any kind.

## Diagnosis (Step 1)

**The bug is NOT in `file_processing.extract_pages_from_xlsx`.** That function
(live thread/user upload path) already emits one line per row. The flattened
`Headers:` / `Labels:` output quoted from production comes from
`app/services/kb_formats.py::_extract_xlsx` — the KB upload registry, which is
the path the three production files went through (confirmed: their chunks in
Mongo carry `category=knowledge_base`, batch `abfe8126ebcd4c2cbecb934d6153b84e`,
and text in exactly that shape). The old KB handler was *deliberately*
"metadata-indexed": docstring said spreadsheets index "sheet names, header rows
and text labels only, never the full numeric grid".

How the old KB handler lost the data:

1. **Row grouping lost:** it iterated `ws.iter_rows()` and appended qualifying
   cells to a single flat `labels` list — rows and columns were never
   reconstructed. (`kb_formats.py`, old lines 258–277.)
2. **Values deduplicated:** a `seen` set dropped any repeated cell value
   sheet-wide, so the 2nd and 3rd "Interrogator" bookings vanished.
3. **Non-string cells dropped entirely:** `_is_label()` returned False for
   anything that wasn't a str — every real date (datetime), every quantity
   (int/float) was discarded. This, not formula caching, is why the Booking
   dates never appeared.
4. **`Headers:`** = the first non-empty row's cells (Nones squeezed out, so
   positions shifted); **`Labels:`** = every later string cell that didn't
   parse as a number, capped at 60 per sheet, first-seen order.
5. **Formula cells / `data_only=True`:** production evidence says cached values
   WERE present — the PLAXIS `Log` sheet's formula-driven "Current Status
   (Auto)" strings (`🔴 ACTIVE`, `✅ DONE`, `⚠️ OVERDUE`) and the Dashboard's
   `Week of 2026-08-17` reached the old output, which is only possible if
   `data_only=True` returned cached values. So formula-None was **not** the
   dominant loss for these files; the label filter + dedup + flattening was.
   (Fallback still implemented — see Step 3 — because workbooks saved by tools
   that skip recalculation carry no cache.)
6. **Cell-count accounting:** the three original files are NOT retained
   anywhere on this host — neither upload path persists original bytes (KB
   ingests pre-extracted pages; bulk temp files are deleted) and there is no
   GridFS. Exact non-empty-cell vs reached-output counts for the real files
   are therefore not computable locally. From the production chunks: Lab
   Inventory 3 chunks / 3,111 chars, PLAXIS 1 chunk / 569 chars,
   Lab Lessons.docx 6 chunks / 7,875 chars — matching the reported symptoms.

The live path (`file_processing.extract_pages_from_xlsx`) kept rows but had
its own defects: `str(cell)` datetime reprs ("2026-01-15 00:00:00"), no row
cap, no per-chunk header repetition, phantom trailing columns not trimmed.

## Fix (Steps 2–3)

One shared row-structured renderer now serves BOTH paths:

- `file_processing.py`: new `_format_cell`, `_render_sheet_rows`,
  `_merge_formula_row`, `extract_xlsx_sheets`, `extract_csv_rows`; rewritten
  `extract_pages_from_xlsx` / `extract_pages_from_csv` (stdlib csv, handles
  quoting/ragged rows) / `extract_pages_from_xls` (pandas → same renderer).
- `kb_formats.py`: `_extract_xlsx` / `_extract_csv` now delegate to the shared
  renderer. `_is_label`, dedup, and the 60-label cap are gone.
- Contract: one `| a | b | c |` line per non-empty row, columns in order,
  blank cells kept positionally, trailing empty columns trimmed, **no
  deduplication**. Sheet name + header row restated at the top of every
  ≤1200-char block (`## Sheet: X (continued)`), sized so the v2 chunker never
  splits inside a block — verified end-to-end: every chunk of a 299-row sheet
  contains the column header.
- Dates → `2026-01-15` (time appended only when non-midnight), int-valued
  floats → `5`, booleans → `TRUE`/`FALSE`; newlines/pipes inside a cell are
  replaced so a row stays one line.
- Cap: `XLSX_MAX_ROWS_PER_SHEET` (config, default 5000, env-overridable);
  when hit the sheet text ends with
  `[Truncated: N additional non-empty row(s) … are not included]`.
- Formula fallback: workbook read twice (`data_only=True` + `False`); a
  formula cell with no cached value falls back to its formula text and the
  count is logged (`[XLSX] file: N formula cell(s) had no cached value…`).
  Verified both ways: fires on an openpyxl-saved workbook (no cache), zero
  fallbacks on a cached workbook.

## chunkingVersion (Step 4)

`config.XLSX_CHUNKING_VERSION = "v3-xlsx"`; `rag_service._chunking_version_for`
tags chunks from `.xlsx`/`.xls`/`.csv` ingests as `v3-xlsx` (everything else
stays `CHUNKING_VERSION` = v2). Retrieval never filters on this field
(verified) — it is provenance only. Existing chunks untouched. Note:
`kb_admin` stats bucket versions as v2-vs-other, so `v3-xlsx` chunks will show
in the "v1_or_none" bucket of that report until it learns the new tag.

## Verification

The three production files could not be re-extracted locally (originals not
retained — see Diagnosis #6). Instead: production chunk text pulled read-only
from Mongo as the definitive BEFORE, and replica workbooks reconstructing the
production sheet structure (headers verbatim from prod chunks) extracted
through the OLD code (pre-change snapshot) vs the NEW code.

- docx: `extract_pages_from_docx` and `_extract_docx` untouched; old-vs-new
  extraction of a docx fixture is **identical** on both the KB and live paths.
- PDF/pptx/txt/md: untouched; full unit suite 475 passed / 6 skipped.
  Integration: 128 passed; 3 failures reproduce identically with the
  pre-change code swapped in (2× "Event loop is closed" in
  test_thread_delete_cascade, 1× stale workspace-status assertion from the
  instrument-parsers feature) — pre-existing, unrelated.
- New tests: `tests/unit/test_xlsx_row_extraction.py` (8) pin the contract;
  `test_kb_formats.py` updated — spreadsheets are row-indexed now (the two
  tests asserting numbers-not-dumped inverted to assert full rows).

Char counts:

| File | Before (prod chunks) | After (replica, new extractor) |
|---|---|---|
| Lab Inventory and Bookings.xlsx | 3,111 | 2,339 (replica has fewer rows than the real file; old code on the same replica: 1,760) |
| PLAXIS booking sheet.xlsx | 569 | 738 cached-values variant / 822 no-cache variant (old: 560) |
| Lab Lessons.docx | 7,875 | unchanged — path untouched, byte-identical on fixture |

Booking sheet, BEFORE (verbatim production chunk, `pageStart=3`):

```
## Sheet: Booking
Headers: S. No. | Instrument Name | Booked By | Start Date | End Date | Purpose | Waiting | Remarks
Labels: Interrogator | Saeed | 2025/MM/DD | TIP | Jiming | MTS tests with Geogrid | 1-2 hours from 10 am on | Yongxuan | TBD (1/24/2026) | Big box F-T cycle | 2 bare DFOS & 2 5-m rugged DFOS | Keep adding… | Note: Please give details in the remarks section...
```

Booking sheet, AFTER (replica through the new extractor):

```
## Sheet: Booking
| S. No. | Instrument Name | Booked By | Start Date | End Date | Purpose | Waiting | Remarks |
| 1 | Interrogator | Saeed | 2026-01-15 | 2026-01-20 | TIP |  |  |
| 2 | Interrogator | Jiming | 2026-02-03 | 2026-02-10 | MTS tests with Geogrid | 1-2 hours from 10 am on |  |
| 3 | Interrogator | Yongxuan | TBD (1/24/2026) |  | Big box F-T cycle |  | 2 bare DFOS & 2 5-m rugged DFOS |
|  | Note: Please give details in the remarks section if you want to convey anything about your intended use... |  |  |  |  |  |  |
```

Row-association questions, answered from the new text alone:
who booked the interrogator + purpose ✔ (each booking row);
what is ACTIVE in the PLAXIS Log ✔ (`| Jiming Liu | … | 🔴 ACTIVE |`);
quantity remaining + storage location of an inventory item ✔
(`| 1 | Ziplock bags | … | 120 | Nos | Storage room |`).

## Not done (awaiting approval / user action)

- **Prod restart:** geoai-backend must be restarted to load the new extractor
  (no sudo here). Until then, uploads still use the old code.
- **Re-ingest:** nothing deleted or re-ingested. Dry-run plan:
  - Affected KB spreadsheet docs: exactly **2** (no KB CSVs), both in batch
    `abfe8126ebcd4c2cbecb934d6153b84e`, project "Lab inventory and Plaxis
    booking": Lab Inventory and Bookings.xlsx (title "Inventory", 3 chunks) and
    PLAXIS booking sheet.xlsx (title "PLAXIS booking sheet", 1 chunk).
  - Originals are not stored server-side → re-ingest requires re-uploading the
    two files. Sequence: restart backend → re-upload each via KB upload with
    the SAME project + canonical title and acknowledge the supersede warning
    (delete-before-insert removes the old flattened chunks in the same flow) —
    or admin `kb_remove --batch abfe8126…` then fresh uploads.
  - Thread-path spreadsheet docs (CPT_040C11_20260714.xlsx 153 chunks,
    A1_Internal_energy.csv 1 chunk) went through the row-preserving live
    extractor — NOT affected by the flattening bug; no re-ingest needed.
