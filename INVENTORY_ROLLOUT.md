# Inventory rollout — redaction removal + live lab inventory (INVENTORY_ENABLED)

Built 2026-08-21 on branch `feature/inventory` (created off `feature/web-ingest`
@ e90b584b). All changes are STAGED, NOT COMMITTED, per instructions. Flag
default **OFF**; flag-off behavior proven byte-identical to the pre-change tree
(see Parity proof below).

---

## Phase 0 — findings (what "redaction" actually was)

**There was no name/email redaction anywhere in the pipeline.** Nothing in
ingestion, chunking, extraction, `kb_formats.py`, or any prompt template ever
stripped, masked, or rewrote person names or emails out of document text.
Related code that touches names but is NOT redaction (left intact):

- `citation_filter.py` — *uses* author surnames to verify citations.
- `kb_metadata.py` — *extracts and keeps* author names (LLM metadata prefill).
- `vision_extraction.py:61` — anti-hallucination rule ("never *guess* an
  illegible name"), an accuracy instruction, not privacy stripping.

What existed instead was a **PII detect-and-confirm upload gate** (the
"skips" category from the brief): regex detection of emails / NA-style phone
numbers / UVic V-numbers + context-gated 8-9-digit IDs, where student numbers
and phones soft-gated an upload behind an "acknowledge" confirmation, and the
bulk uploader **skipped** flagged files until batch-acknowledged. Emails were
detected but never gated. Nothing was ever removed from indexed text.

## Phase 1 — redaction call sites removed (not flag-gated, per instructions)

| File | What was removed |
|---|---|
| `python_backend/app/services/kb_validation.py` (was lines 97-137) | `_EMAIL`, `_PHONE`, `_VNUMBER`, `_CONTEXT_ID` regexes; `SENSITIVE_PII_KINDS`; `scan_pii()`; `sensitive_pii()`; docstring updated |
| `python_backend/app/routers/kb.py:169-179` | PII scan in `_prepare_kb_file` + `pii`/`pii_sensitive` prep keys |
| `python_backend/app/routers/kb.py:306-309` | single-upload "Possible sensitive personal data" warning gate |
| `python_backend/app/routers/kb.py:483-486` | bulk-upload skip of sensitive-PII files |
| `python_backend/app/routers/kb.py` bulk endpoint | now-dead `acknowledge` Form field + `acknowledged` param of `_kb_bulk_task` (its only consumer was the PII branch) |
| `app/components/kb-upload.tsx` | `pii` skip label, `resubmitPii()`, "Add N flagged file(s) anyway" button |
| `python_backend/tests/unit/test_kb_validation.py` | the 5 PII-scan tests |

Each former call site carries `# name redaction removed`. Kept untouched:
`sanitize_filename` (path traversal), HTML/injection defenses, non-English
detection, dedup, relevance gates. **Note:** this is the one deliberate
flag-off behavior change of the branch — KB uploads no longer warn/skip on
student numbers or phone numbers, and the bulk endpoint no longer takes an
`acknowledge` field. Recoverable from git history if ever needed again.

## Phase 2 — inventory collections + CRUD

- `python_backend/app/core/database.py` — `inv_items`, `inv_tx`, `inv_res`,
  `inv_plaxis`, `inv_users`, `inv_audit` (flat snake_case, matching the
  existing convention). Indexes in `ensure_indexes()`, created ONLY when
  `INVENTORY_ENABLED`: `inv_items.id` unique; `inv_tx.itemId`;
  `inv_tx.actualReturn` sparse; `inv_res.itemId` + compound `(start, end)`;
  `inv_plaxis.loggedOut`.
- `python_backend/app/core/config.py` — `INVENTORY_ENABLED` (default false,
  standard `os.getenv(...).lower() in ("1","true","yes","on")` pattern) +
  `INVENTORY_SNAPSHOT_TOKEN_CAP` (default 4000).
- `python_backend/app/routers/inventory.py` (new) — `/api/inventory/*` CRUD
  (`items|tx|res|plaxis|users`: list/create/update/delete), JWT auth via the
  existing `get_current_user` (httpOnly cookie or Bearer), registered via the
  `register(app)` flag-gate pattern (off = routes absent, route table
  unchanged). Every mutation writes `inv_audit` (id, ts, actor, action,
  entity, detail; best-effort, never blocking). Names and emails stored
  **as-is**, no transformation. Transactions carry stock side effects
  server-side (checkout/return move `qtyOut` for equipment and `qty` for
  consumables; adjust = qty delta; damage marks condition), so the snapshot
  and feasibility always see consistent numbers. Updates accept an
  `expectedUpdatedAt` precondition → 409 on stale write (Phase 6's conflict
  toast hook). Extra read-only endpoints for verification/frontend:
  `GET /api/inventory/snapshot/build`, `POST /api/inventory/feasibility/check`.
- `python_backend/app/scripts/inv_seed.py` (new) — one-shot idempotent seeder
  (no-ops when `inv_items` is non-empty). Initially blocked on the missing
  `linlab_bench.jsx`; **UNBLOCKED 2026-08-21** when the owner supplied the
  seed data directly. It is stored verbatim in
  `python_backend/data/linlab_seed.json` (already carrying the ODiSI **6104**
  correction) with the file's own two variants: the default seeds
  `reference_only_clean_ledger` (20 real users + 36 real items, no activity —
  the honest starting state for the live system), `--demo` seeds
  `reference_with_demo_activity` (same reference data + the bench's demo
  tx/res/plaxis/audit rows, inserted verbatim with no side effects since the
  demo item quantities already reflect them). `--dry-run` parses, coerces
  ISO dates to datetimes ("" → None; `users.since` stays a year label), and
  runs integrity checks (itemId references, duplicate ids, qtyOut ≤ qty, no
  "6100") without touching the database — both variants validate clean.
  **Not executed against the live Atlas DB this session** (standing no-live-
  writes practice); run from `python_backend/`:
  `python -m app.scripts.inv_seed` (clean) or `--demo`.

## Phase 3 — INVENTORY router mode

- `intent_router.py` — `INVENTORY` added to `VALID_MODES` (it is a real
  dispatchable mode, unlike UNCERTAIN); `_parse_mode` accepts the label ONLY
  while `INVENTORY_ENABLED` is on (flag off → unknown label → `DEFAULT_MODE`,
  exactly as today); `_system_prompt()` gains a flag-gated rule (appended
  after the existing WEB_INGEST / UNCERTAIN extras, numbered sequentially) —
  flag off, the prompt is byte-identical.
- `prompt_config.py` — `INVENTORY_PROMPT` added to `SYSTEM_PROMPTS` (the
  keys == VALID_MODES import-time guard holds). The prompt forbids the model
  from computing availability itself and makes the FEASIBILITY CHECK block
  authoritative.
- `chat.py` — INVENTORY branch beside GENERAL: **skips retrieval and
  reranking entirely — no FastEmbed call, no cross-encoder** (instrument-
  parsers routing-away principle). Context = deterministic snapshot (+
  feasibility report when a booking request parses); deterministic scope note
  appended from serializer output. Live-inventory turns (INVENTORY, and MIXED
  turns whose query matches inventory keywords) are **exempt from the Redis
  answer cache both read and write** — inventory state changes between turns.
  MIXED + inventory: snapshot **appended** to retrieved context (never
  replacing it), and such turns don't take the low-confidence fallback (which
  would discard the snapshot and falsify the note).
- `llm_service.py` — `_build_answer_prompt` gives INVENTORY its own "LIVE
  INVENTORY SNAPSHOT:" context header (mode unreachable flag-off; every other
  mode's assembly byte-identical — proven by the oracle).

## Phase 4 — snapshot serializer

`python_backend/app/services/inventory_service.py` (new).
`build_inventory_snapshot(scope) -> str` — deterministic, zero LLM calls.
Pipe-delimited sections per spec: ITEMS (id|name|qty|out|avail|…), OPEN LOANS
(with computed `overdue_days`), RESERVATIONS (next 30d, Denied hidden),
PLAXIS SEATS (2 concurrent, held sessions), ALERTS. Scope inference is pure
keyword membership (`infer_inventory_scope`). Cap 4000 tokens (len//4, the
repo's history-cap heuristic): whole **sections** dropped in priority order
`PLAXIS → RESERVATIONS → ITEMS → OPEN LOANS → ALERTS` (alerts + open loans
survive longest), then whole **rows** with an explicit `(+N more rows
omitted)` marker — never a partial row. `alertsFor()` ported server-side from
the spec list: overdue loans, consumables ≤ `minStock`, expiry ≤ 45d,
maintenance due ≤ 30d (`lastMaint` + `maintDays`), damaged/missing, PLAXIS
seats held past end, reservations pending approval. The scope note
(timestamp + included/omitted sections) is assembled from the serializer's
own bookkeeping (`SnapshotResult.scope_note()`), never from model prose.

**Observed sizes** (cap 4000): the REAL Lin Lab data — demo-activity variant
≈ **1724 tokens**, clean ledger ≈ **1281 tokens**, both all-sections with
nothing dropped or trimmed. Synthetic stress points: 15 items/5 loans/5 res
≈ 854; 60/20/20 ≈ 3149 (still whole); 300/80/120 → full snapshot would be
~11k, cap cascade kept ALERTS (~2025 tokens) and named the four dropped
sections in the note.

## Phase 5 — feasibility engine

`check_feasibility(requests, start, end)` walks open loans (`inv_tx` type
checkout, `actualReturn` null-or-absent) + **Approved** reservations
overlapping the half-open window (`res.start < end and res.end > start`).
Per item: `available | short_by(n) | conflicts_with(user, dates)` (+
`unknown_item`), overall verdict, and the earliest date the full set becomes
available (candidate instants = loan expectedReturns + reservation ends; a
loan with no expectedReturn never frees in projection). Consumables use the
consumption model (checkout decremented `qty` at tx time, so open loans are
not double-counted). Pure core `compute_feasibility(...)` + async shell; the
LLM's only role is `extract_feasibility_request` (router-pattern JSON parse,
temperature 0, defensively validated — any failure degrades to a
snapshot-only answer). **AI routes, Python calculates.**

Unit tests (`tests/unit/test_inventory_feasibility.py`, 22 tests, all green):
exact-boundary overlap both edges, one-minute overlap, pending-doesn't-block,
partial shortfall (short_by 1), returned-loans-don't-count, multi-item where
one blocks (+ earliest = blocking reservation's end), earliest from loan due
date, no-expectedReturn → never schedulable, consumable vs equipment (both
directions), consumable shortfall, zero-qty (trivially available), unknown
item, rendered-report content, extraction parser accept/reject matrix.
Snapshot/alerts/scope/router-gating tests in
`tests/unit/test_inventory_snapshot.py` (19 tests, all green).

## Phase 6 — frontend

**STILL BLOCKED — the `linlab_bench.jsx` COMPONENT does not exist.** The
owner supplied the seed *data* (2026-08-21, now in
`data/linlab_seed.json`, unblocking Phase 2), but Phase 6's instructions
operate on the bench component itself (`window.storage` → API rewire while
keeping `commit()` so "component code is untouched", the user `<select>` →
JWT session user, optimistic update/rollback) — none of which can be done
without the component source. The backend half of the contract is ready for
it: CRUD keeps the loose object shapes (unknown keys dropped, not errored),
`expectedUpdatedAt` → 409 gives the conflict toast its signal, and the
session user comes from the existing JWT. The ODiSI 6104 fix is already
applied in the supplied data. `rm -rf .next` + full build was still run to
verify the Phase-1 frontend edits.

---

## Verification

- **Backend imports clean** — `import app.main` OK after every phase.
- **Unit suite**: 595 passed, 6 skipped (was 594+1 env-broken: see below).
- **Frontend**: `rm -rf .next` → `next build` ✓ compiled; vitest 75/75.
- **/health**: throwaway uvicorn on :8002 (flag off) → HTTP 200; and
  `/api/inventory/items` → 404 (routes absent). Server stopped, port freed.
- **Flag-on route check** (import-only, no server, no DB writes): 4
  `/api/inventory` paths register; total paths 58 → 62.
- **Integration suite**: 125 passed, 6 failed — **all 6 reproduce identically
  with my changes stashed** (verified by running the same files on pristine
  HEAD): they are pre-existing environmental failures (`.env` now sets
  ROUTER_ENABLED / WEB_INGEST_ENABLED / ROUTER_UNCERTAIN_RETRIEVES = true,
  which the older test expectations + stale mocks don't pin; live Ollama
  leaks through). Zero regressions from this branch.
- **Test fix included**: `test_intent_router.py`'s two byte-identity prompt
  tests now pin ALL prompt-extra flags (they broke the moment `.env` gained
  `ROUTER_UNCERTAIN_RETRIEVES=true`, before this branch).

### Parity proof (flag-off ≡ production behavior)

Live prod LLM responses cannot be byte-compared (temperature 0.3, sampling),
so the proof follows the repo's established git-archive-oracle pattern:
every **deterministic stage that fully determines a response** was hashed in
a pristine `HEAD` (e90b584b) worktree and in this working tree, both with
`INVENTORY_ENABLED=false` and the prod-like `.env` (router ON, web-ingest ON,
uncertain ON):

- router system prompt; all 4 mode prompts; both fallback prompts;
- 32 full answer-prompt assemblies (4 fixed KB_QUERY/GENERAL/MIXED/THREAD_DOC
  prompts × 4 modes × with/without context+history);
- the complete 58-route OpenAPI route table;
- `classify()` dispatch via scripted client for all labels — including a
  stray `"INVENTORY"` label, which resolves to the KB_QUERY safe default
  exactly as any unknown label does today.

Result: **4317-byte hash manifests byte-identical** (`diff` clean). With the
flag off there is no code path by which a chat response can differ. The one
intended flag-off delta is Phase 1's PII-gate removal (upload endpoints, not
chat), listed above.

## Phase 6 (revised) — Inventory as a fourth tab · built 2026-08-21

**Provenance:** `linlab_bench.jsx` was still absent (name / git history / every
remote branch tree / whole-filesystem / symbol grep all empty), so per the
owner's explicit choice the tab was **built from scratch** on the app's own
conventions + the seed data. Consequences reported honestly below: every
"match the prototype byte-for-byte" requirement is inapplicable.

**Backend additions (flag-gated, parity re-proven):**
- `GET /api/inventory/status` — nav probe (kb/workspace pattern; flag-off the
  route is absent → 404 → tab hidden).
- `GET /api/inventory/alerts` — the server-side `alerts_for` list; the UI
  renders THIS, never recomputes client-side, so UI and assistant can never
  drift (the Step-7 client/server comparison is moot: one implementation).
- `GET /api/inventory/audit` — read-only listing (newest first); audit stays
  server-written (POST/PUT/DELETE on it remain 404).
- `next.config.mjs`: `/api/inventory/:path*` rewrite (nothing covered it).

**Frontend (new `app/inventory/`):** `page.tsx` (shell: AuthProvider +
AuthGuard + shared Header; six sub-pages as the workspace-style in-tab
tablist), `lib.ts` (types, API client, pure logic), `use-inventory.ts` (store:
distinct loading / error-with-retry / disabled states — a failed load never
renders an empty lab), `actions.ts` (mutation map), `inventory.module.css`,
`components/` (ui, modals, dashboard, items, reservations, plaxis, reports,
people). `Header.tsx` gains the fourth probe + segment (Package icon).

**Mutation → endpoint map** (each optimistic-with-rollback via `runMutation`;
`expectedUpdatedAt` on every PUT):
checkout / return(+`closesTxId`) / adjust / damage → `POST /tx`;
item create/edit/delete → `POST|PUT|DELETE /items`; reserve → `POST /res`;
approve/deny → `PUT /res/{id}`; cancel → `DELETE /res/{id}`; user CRUD →
`/users`; PLAXIS start → `POST /plaxis`; PLAXIS log-out → `PUT /plaxis/{id}
{loggedOut:true}`. On success the touched collections + audit + alerts are
refetched — qtyOut/status always end up the SERVER's values. A 409 rolls
back, toasts the item name via the app toast, and refetches so a retry runs
against current state; never auto-retried. (The shared toast is string-only,
so "offer refresh" is delivered as automatic refetch + toast text.)

**Design tokens:** every surface uses globals.css tokens (s1–s4 elevation,
t1–t3 text, line/line-2, e1–e3, r1–r3, Plex families; eyebrow = the app's
mono-uppercase treatment). Eight status chips as fg/bg pairs, labels always
rendered (status never color-alone); severity crim>rust>amber maps natively
to `--danger` > `--oxide` > `--warn`; 4px strata rail kept on item rows.
**One derived color:** `--oxide` ships without the `-a` fill variant its
siblings have — chip/rail fills derive it with `color-mix(… var(--oxide) 13%,
transparent)` instead of a hex (the one "no clean token" case).

**Session user:** `useAuth()` (id/email/full_name/role); `isManager` =
admin|professor, `isPI` = professor (presentation-only; JWT server-side is
the gate). Modal prefill joins the `inv_users` roster by email for
group/studentId; all fields stay editable for on-behalf transactions.
Client-side note: `studentId` is validated (V-number `^[Vv]\d{8}$`) but the
tx whitelist server-side drops it (tx schema carries no studentId column).

**Verification:** vitest 99/99 (24 new: rollback-on-failure, 409-no-auto-
retry, role-flag derivation, CSV quoting + byte-stability, PLAXIS half-open
grid math incl. stale-seat detection, status mapping, roster join, V-number);
`rm -rf .next` → build clean with `/inventory` route; `tsc --noEmit` — zero
errors in new code (5 pre-existing in `app/api/assistants/files/route.tsx` +
a Playwright spec, untouched); backend 601 unit tests green; flag-on = 6
inventory paths; **flag-off parity oracle re-run: still byte-identical**.
Flag-off UX: probe 404 → tab hidden; direct `/inventory` → calm "not
enabled" panel (no crash, no hanging fetch); Chat/GeoPilot/KB untouched.

**Not verifiable from this session** (browser automation is banned by
standing rule; no local Mongo; live-Atlas seeding deliberately left to the
owner): the seeded-lab manual pass — 36 items/20 users load, checkout
write-through showing the server's qtyOut, the two-tab conflict toast, and
the visual pass. To run it: `INVENTORY_ENABLED=true`, restart backend, seed
(`python -m app.scripts.inv_seed`), open `/inventory`. Also inapplicable, not
skipped silently: prototype `toCSV` byte-match, client-vs-server `alertsFor`
comparison, and prototype-verbatim modal/validation preservation — there is
no prototype code in existence to compare against.

## Phase 6 cleanup — open access + roster-sourced studentId · 2026-08-21

**Step 0 key finding:** before this cleanup the backend had **zero role
checks** — every inventory route was authenticated-only, and delete/approve
were "enforced" by hidden buttons alone. The two retained gates are now real
(server-side 403s); everything else is open by construction.

**Gates removed (frontend; there was nothing to remove server-side):**
"New item" (`items.tsx:111`), item "Edit" (`items.tsx:~213`), "Add member"
(`people.tsx:34`), roster "Edit" + the manager-only actions column
(`people.tsx:45,50,65`). Checkout/return/reserve/adjust/damage were already
open. `isPI` removed from `roleFlags` — Step 0 confirmed zero call sites
beyond its own tests (now updated; a test asserts the flag is gone).

**Gates retained — now enforced server-side (`inventory.py`):**
1. `DELETE /items|/users` → 403 unless admin/professor (`_require_manager`,
   checked before any DB access) — the one action the audit log can't
   reverse. Client keeps hiding the buttons (presentation only).
2. Reservation approval → 403 unless manager on BOTH paths: `PUT /res/{id}`
   carrying a `status` key, and `POST /res` created with any status other
   than Pending (else pre-approved bookings would bypass the queue).
   Non-status res edits and res deletes (cancel) stay open; cancel keeps its
   owner-or-manager client affordance. PLAXIS log-out kept its
   mine-or-manager affordance (not in the ungated list; flagged below).

**studentId (and group — same one-source treatment):** `inv_tx` schema +
whitelist gain `studentId`; on every tx create the server resolves BOTH
fields from `inv_users` joined on the SESSION user's email
(case-insensitive; `_roster_identity`), overwriting any client value; no
roster match → null, transaction proceeds. The checkout modal no longer
collects either — it shows a read-only "Recorded from the roster: …" line
(or a plain note when nothing resolves). The V-number regex survives in
exactly ONE place, the roster (People) modal — deliberate deviation from
"remove from all modals": `inv_users` is now the single entry point for a
student id, so that is where the format gate belongs; removing it there
would leave the field un-fillable forever (all seed rows have "" today).

**Tests:** backend +15 (`test_inventory_authz.py`, fake collections, no
Mongo): 403s for non-manager delete/approve on every path, managers pass,
res cancel + pending-creation + item/user create/edit open to plain users,
tx carries roster studentId with the client's spoofed value ignored,
unresolvable email → null without error. Frontend `roleFlags` suite rewritten
for the isPI removal. Totals: backend **616 passed / 6 skipped**; vitest
**100/100**; `rm -rf .next` → build clean with `/inventory`; `tsc` — zero
errors in this code (same 5 pre-existing elsewhere); **flag-off parity
oracle: byte-identical**, flag-off route table carries no inventory paths
(tab hidden; `/inventory` renders the not-enabled panel, unchanged).

**For human review:**
- All seed roster rows carry `studentId: ""` → every tx records null until
  V-numbers are entered via the People modal.

### Follow-up fixes (owner-requested, 2026-08-21)

Both earlier review flags resolved:
1. **Borrower-keyed identity.** The tx roster join now keys on the FORM's
   email (the borrower), falling back to the session email only when the
   form field is blank — an on-behalf checkout records the actual borrower's
   studentId/group. Unresolvable still writes null without failing. The
   checkout modal's read-only "Recorded from the roster" line now resolves
   LIVE against the typed email (`rosterIdentityLine`, client-side mirror of
   the server rule).
2. **One-source `group` on inv_res / inv_plaxis.** Creation now resolves
   `group` server-side from the roster too. These schemas carry no email
   column, so the join keys on the NAMED person (`user`, case-insensitive
   exact) — client-supplied group is overwritten; unknown name → null. The
   Group inputs left the Reserve and PLAXIS modals.
   (`_roster_identity` became `_roster_lookup(email=…, name=…)`.)

Tests: backend 621 green (+5: borrower-email join asserted by captured query,
blank-email fallback, res/plaxis name-keyed resolution with spoofed client
group ignored, unknown-name null); vitest 102 green (+2: rosterIdentityLine).
`rm -rf .next` rebuild clean; tsc clean for this code; **flag-off parity
oracle re-run: byte-identical**.

## Prototype parity pass — 2026-08-21 (reference: docs/reference/linlab_bench.jsx)

The prototype finally arrived (pasted; now stored verbatim at
`docs/reference/linlab_bench.jsx`). Behavior reference only — styling,
shell and CSS untouched; gaps closed, working pages left alone.

**Gap audit (Step 0):**

| Feature | Prototype | Build before | Outcome |
|---|---|---|---|
| Dashboard KPIs | Items on record · Available now · On loan · Overdue · Low/out of stock · In maintenance | Items · Out on loan · Overdue · Reservations·7d · PLAXIS seats · Low stock | **Gap** — added Available now + In maintenance (kept the two extras) |
| Needs-attention feed, severity-sorted | client `alertsFor()` | server `/alerts` (server-sorted) | present; rows were inert → **deep-link added** |
| Checked out to you | yes | no | **Gap → added** (email match, name fallback) |
| PLAXIS seat status line | "Both seats free today" / holders + Not logged out | no | **Gap → added** |
| Open board + pending count | rail badges + Open board button | no | **Gap → added** (subnav badges: high alerts / pending reservations) |
| Inventory search name/id/serial/location; category + status filters | yes | yes | present (prototype's type/location/condition/sort extras out of scope) |
| Drawer fields (id, category, make, serial, stock triple, condition, location, custodian, last maint, expiry) | yes | yes | present; **per-item "Reserved" block added** |
| Six actions | checkout/return/reserve/report/edit/adjust | same (+ manager delete) | present |
| Modals ×8 | all | all | present |
| Export modal (preview + row count) | yes | direct download | **Gap → added** (Copy + Download) |
| Reservations approve/deny queue · cancel | "Waiting on you" section | inline buttons only | **queue section added**; cancel present |
| Per-item reservation grouping | drawer "Upcoming reservations" | no | **added to drawer** |
| PLAXIS grid 07:00–22:00, 2 seats, week nav, stale detection | yes | 08:00–18:00 | **hours fixed**; rest present |
| Reports: five sections, each with own export | yes | one panel, 5 generic exports | **Gap → rebuilt** |
| Overdue columns | Item/User/Email/Group/Qty/Out since/Due/Days late | — | per spec: item, user, email, group, qty, taken, due, days overdue — **days from the server clock** via the alerts payload |
| People roster CRUD · audit log | yes | yes | present |

**Implemented:** `lib.ts` report builders (`inventoryReport`, `mostBorrowedReport`,
`overdueReport`, `lowStockReport`, `serviceReport`) + `checkoutFrequency`,
`availabilityByCategory`, `overdueRows`, `lowStockItems`, `serviceItems`,
`nextMaint`, `buildExport`; `ExportModal`; Dashboard + Reports rebuilt on the
existing primitives/tokens (new CSS classes are token-only); item-drawer
selection lifted to the tab shell so alerts/reports/reservations deep-link;
nav badges. All writes still go through the optimistic runner (no new
mutations were needed). One **additive** backend change: alert records now
carry `itemId` / `refId` / `days` (snapshot text unchanged; `/alerts` payload
gains fields) — required for deep-links and server-clock overdue days.
Bug found by the tests and fixed: date-only strings parsed as UTC midnight
(a day early in this timezone vs the backend's naive-date math) — `asDate`
now treats them as local dates.

**Prototype ↔ backend divergences (backend wins, not changed):**
- Reservation states: prototype Rejected/Cancelled; backend Denied + delete.
- Low-stock rule: prototype `minStock > 0 && available <= minStock`; server
  flags `qty <= minStock` for any numeric minStock — the report follows the
  server so it matches the alerts.
- Alerts windows (45 d expiry / 30 d maintenance) are server-only; the
  client never recomputes them.
- Adjust: prototype sets absolute qty + status + "serviced today"; backend is
  a delta transaction (service date via Edit).
- Checkout: prototype "return in N days" select + reservation-overlap warning
  + client V-number regex; build uses a date field and roster-resolved
  identity (the overlap warning was not ported — not in the gap list).
- PLAXIS: prototype auto-assigns the free seat, books from a cell click, and
  shows a session log with Done/Overdue/Active/Upcoming; build asks for the
  seat and shows active sessions — not in the gap list, left as is.
- Reserve: prototype refuses overlapping bookings client-side; backend
  accepts overlapping Pending requests (the feasibility engine reasons about
  Approved ones).
- `toCSV`: prototype quotes every field with LF rows; build is RFC 4180
  (quote-when-needed, CRLF) — kept; existing quoting tests extended per report.
- Roles: prototype gates member management to the PI; build is open access
  with manager-only delete/approve (owner decision, Phase 6 cleanup).
- Prototype seed still reads ODiSI 6100; the seed file is 6104.

**Tests:** vitest **117/117** (+15: frequency ordering, category grouping,
server-vs-browser overdue days, exact overdue columns, per-report CSV
quoting incl. comma/quote/newline cells, export row counts incl. empty);
backend **621** green (alert-record metadata asserted); `rm -rf .next` →
build clean; `tsc` clean for this code; **flag-off parity oracle byte-
identical**; flag-off route table carries no inventory paths.

## Fix — sub-nav vanished on tall pages · 2026-08-21

**Symptom:** the six-page tablist showed on Dashboard but was gone on the
Inventory page (no way back). **Root cause was layout, not a header:** no
sub-page renders its own heading. `.main` is a fixed-height flex column
(`100vh`, overflow hidden), `.wrap` a flex-column scroll container, and
`.subnav` a flex child with `overflow-x: auto`. Flexbox gives a scroll-
container child an automatic minimum size of **0**, so whenever a page's
content was taller than the viewport the column squeezed the tablist to
**0px** — still in the DOM, invisible. Dashboard fit the viewport; Inventory
(36 rows + drawer), Reports (five tables), People & log (roster + audit) and
the 15-row PLAXIS grid did not — all four were affected, not just Inventory.

**Fix:** `.subnav { flex: none }` (and `.pageHead`), with a comment naming
the mechanism. The shell (`InventoryTab` + `SUBPAGES`) moved from `page.tsx`
into `components/inventory-tab.tsx` so it can be rendered in tests (Next
rejects extra exports from a page file); `page.tsx` is now the thin route.

**Tests (`inventory-tab.test.tsx`, +3):** renders the shell with mocked
store/auth and walks all six tabs — the tablist (6 tabs, correct order) is
present after every navigation, selection follows, each page's landmark
content renders inside the shell, and the Dashboard/Reservations badges keep
their server-derived counts; exactly one `<h1>` exists on every page (no
sub-page draws its own heading); and a layout guard asserts the `flex: none`
rule on `.subnav`/`.pageHead`, since jsdom does no layout and the DOM test
alone could not have caught this. Totals: vitest **120/120**, `rm -rf .next`
→ build clean, `tsc` clean for this code, **flag-off parity oracle byte-
identical**, flag-off route table has no inventory paths.

## Needs human review / follow-up

1. **Phase 6 frontend still needs the `linlab_bench.jsx` component source**
   (the seed *data* arrived 2026-08-21 and unblocked Phase 2; the component
   to rewire still doesn't exist anywhere findable). Paste/drop the component
   and the `window.storage` → API rewire can be completed — or say the word
   and a fresh inventory page can be built against the ready API instead.
2. **Seeding + first flag-on start are deliberately left to you** (no live-DB
   writes from this session, per standing practice). To go live:
   `INVENTORY_ENABLED=true` (creates the idempotent `inv_*` indexes on
   startup), then from `python_backend/`:
   `python -m app.scripts.inv_seed` for the clean ledger, or `--demo` if you
   want the bench's demo activity in the live DB. Both variants dry-run
   validated clean. Note the demo tx/res/plaxis rows attribute fabricated
   activity to real lab members — the file's own note labels them demo.
3. The 6 pre-existing integration failures (stale mocks vs the now-on `.env`
   flags + the honest-KB-fallback behavior) deserve their own cleanup pass.
4. `SECTION_DROP_ORDER` follows the spec exactly (alerts/open-loans last);
   at very large item counts this drops ITEMS before OPEN LOANS — revisit if
   the real lab catalog outgrows the 4000-token cap.
5. Feasibility projection treats an overdue loan as freeing at its (past)
   `expectedReturn`, i.e. "as soon as it comes back"; loans with no
   expectedReturn never free. Confirm this matches lab expectations.
