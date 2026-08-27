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

## Gap closure — ten phases · 2026-08-21

Each phase was completed and verified before the next (vitest + tsc + clean
`rm -rf .next` build + backend unit suite + flag-off parity oracle + flag-off
route check), then staged. Finals: vitest **150**, backend **669**, oracle
**byte-identical**, flag-off route table carries **no inventory paths**.

**Step 0 findings.** KB upload discards bytes after chunking; the reusable
storage is the user-upload path: bytes inline in the Mongo `files` doc
(`content`), served by `/api/files/{id}/content` — owner-scoped, extension-
only MIME. Brevo: `email_service.get_email_sender().send_email(to, subject,
html, text)`, `EMAIL_PROVIDER/EMAIL_API_KEY/EMAIL_FROM_*`. Backend runs
**one** uvicorn worker (no `--workers`). `inv_res` had no `qty`. App
breakpoints 480/560/720 (spec uses 768). nginx `client_max_body_size` is
**50M** (not 55M).

| Phase | What changed | Tests |
|---|---|---|
| 1 Overlap prevention | `reservation_conflicts` (quantity-aware, half-open, open loans count for equipment only, Pending commits the window, Denied never) enforced server-side on create, date edits and **approval** (409 naming holder + window); client mirror pre-check in the Reserve modal + conflicted queue rows can't be approved; `CONFLICT` alert per overlapping pair; `qty` added to `inv_res` (additive — the feasibility engine already read it) | +12 backend, +3 vitest |
| 2 Mobile | `max-width: 767px` block: tablist scrolls, never collapses (`flex: none` kept + commented); card-mode tables (Inventory, Reports, People) led by name/status/available via `data-label`; drawer = full-screen sheet with Close; modals full-screen; 16px inputs; 44px targets; pinned PLAXIS hour column. **Structural assertions only — jsdom does no layout**; 390/768 visual pass is the owner's | +7 vitest |
| 3 Filters/sort/fields | location + condition filters; sortable Item/Qty/Avail/Status/Location/Custodian with aria-sort + indicator; `description` + server-derived **stored** `nextMaint` (client value ignored; recomputed on lastMaint/maintDays edits); drawer + Edit modal show both; absent → "—" | +4 vitest, +3 backend |
| 4 Calendar | list ⇄ calendar toggle (list default); item rows × 7 days on `weekDays()`/half-open math; Approved=accent, Pending=warn; span click → drawer | +2 vitest |
| 5 Archive | `DELETE /items` → **405** for everyone; manager-only `POST /items/{id}/archive|restore` (audited, `archivedFrom` kept); archived hidden by default ("Include archived" toggle), excluded from snapshot/alerts/feasibility/KPIs/reports, checkout/reserve refused (409); `DELETE /users` kept, manager-only | +8 backend, +1 vitest |
| 6 Backup/restore | manager-only `GET /backup` (schema v1, timestamp, six collections) and `POST /restore` with mandatory **dry-run diff** (added/changed/removed per collection), merge (upsert-by-id) or replace (upsert + delete-absent) — never a drop; both audited; client validates the file before any request; the confirmed write goes through the optimistic runner (identity apply, full reload) | +8 backend, +6 vitest |
| 7 Excel | SheetJS `xlsx@0.18.5`; XLSX built from the **same** `Report` builders as CSV; format toggle in the Export modal; auto-width columns. **Bold header not delivered**: the open-source SheetJS build ignores cell styles | +6 vitest (round-trip rows == CSV rows) |
| 8 Damage photos | `INVENTORY_PHOTOS_ENABLED` (default off); `POST /photos` sniffs JPEG/PNG/WebP by **magic bytes**, 10 MB cap, stores inline in `files` (category `inventory_photo`, invisible to RAG/listing filters); served by flag-gated `GET /photos/{id}` to any member (files.py's owner-scoped route left untouched); `photoId` on damage tx only, validated; thumbnails in drawer + audit log | +7 backend, +1 vitest |
| 9 Reminders | `INVENTORY_REMINDERS_ENABLED` (default off); daily digest per user (approved reservations starting ≤24 h, loans due ≤24 h or overdue) via `get_email_sender()`; sends recorded in `inv_reminders` (email, day) unique → restart-safe; **single worker ⇒ in-process hourly ticker** (fires once per day after `INVENTORY_REMINDER_HOUR`) **plus** manager-only idempotent `POST /reminders/run?dryRun=` for cron/manual; pinned-clock tests with an injected fake sender — no real email | +6 backend |
| 10 Alerts | audit vs spec: overdue ✓ damaged ✓ low-stock ✓ conflict ✓ (Ph.1) expiry ✓ maintenance ✓; **upcoming reservations was missing** → `upcoming_reservation` (approved, starts ≤48 h, low severity) added; completeness test asserts all seven fire | +2 backend |

**Spec vs backend — backend won, reported:** reservations carried no `qty`
(added additively rather than reshaping); the low-stock rule stays the
server's `qty ≤ minStock`; `restore` cannot drop collections by design;
`DELETE /items` is 405 rather than removed from the route table (the generic
`/{resource}/{id}` route serves users); breakpoint 767 vs the app's 720.

**Not met / needs review:** XLSX bold header (OSS SheetJS limitation);
`xlsx@0.18.5` on npm carries published advisories (prototype pollution /
ReDoS on crafted input — we only *write* files, but the patched 0.19+/0.20
builds are distributed from SheetJS's CDN, not npm) — decide whether to pin
that tarball; mobile is verified structurally only; the reminder ticker
assumes the single-worker unit stays single-worker (add `--workers` and the
endpoint-only mode must be used); first flag-on start creates the new
`inv_reminders` index; `nginx` is 50M, fine for 10 MB photos.

## Polish — filter-row collapse + export labels · 2026-08-21

**Search input collapsing to a sliver at desktop width.** Cousin of the
tablist bug, not the same one: the row wraps, but `.searchInput` had
`flex: 1` = **flex-basis 0**, so it contributed nothing to the wrap
decision — the four selects, checkbox and button packed the first line at
natural width and the search got only the leftover free space, split 50/50
with `.spacer` (also `flex: 1`), with `min-width: 0` inherited from
`.input` removing the floor. Fix: `.searchInput { flex: 3 1 240px;
min-width: 200px; max-width: 480px }` — a real basis makes the row WRAP
instead of squeezing, flex-grow 3 wins the free space, 200px is the floor
(mobile keeps `flex-basis: 100%`). Also: `.select { max-width: 100% }` so a
long item name can't push the Reservations picker past a 390px row, and
`.plxNav { flex-wrap: wrap }` — the PLAXIS/calendar week nav had no wrap
and overflowed on phones. Other rows (Reports, People, Dashboard) have no
competing `flex: 1` child and were fine. Mobile filters stay visible and
wrap rather than collapsing behind a disclosure (permitted, not required —
kept the smaller change). Regression guards in `mobile.test.tsx` assert the
CSS rules (jsdom does no layout).

**Export labels** now name content, never a format (the modal offers CSV
and XLSX): Export availability · Export most borrowed · Export overdue ·
Export order list · Export damaged; `exportLabel` is required (no
"Export CSV" default can reappear); a DOM test asserts all five and the
absence of the old labels. vitest 153, backend 669, build + tsc clean,
flag-off oracle byte-identical.

## Two fixes — People & Log overflow + stale RESERVED · 2026-08-24

**Step 0 findings.** (1) People roster = `.panel` → `.toolbar` + `.tableWrap`
(overflow-x auto) → 7-column `table.cardTable`; audit log same shape, 5 cols.
Inventory's `.tableWrap` sits in `.layoutMain { flex:1; min-width:0 }`; People's
in a plain `.panel`. Buttons had no `white-space: nowrap` / `flex: none`, so a
squeezed toolbar could narrow one below its label. (2) The Reservations list
was NOT scoped — it listed all of `db.res`; the picker only fed "New
reservation" and defaulted to `db.items[0]`. (3) `Reserved` was a STORED
`inv_items.status`, written only by the seed file (LL-SEN-004 is `"Reserved"`
in both seed variants) and the Edit modal; nothing derived it from `inv_res`,
nothing cleared it. (4) Live DB (read-only): LL-SEN-004 stored `Reserved`,
**zero** `inv_res` rows for it — the collection has 0 rows in total.
**Verdict: case (b)** — stale stored status; the list hid nothing.

**Step 1 — People & Log.** Same `.tableWrap` scroll pattern kept (no third
pattern). `.btn { white-space: nowrap; flex: none }` — a button never breaks or
shrinks; the (already `flex-wrap`) toolbar wraps instead. `.actionsCell`
(Edit/Remove on one line, revealed by the scroll) and `.cellWrap`
(`overflow-wrap: anywhere`, 420px cap — audit JSON has no spaces and could
push the table to thousands of px). Mobile `.cardTable td { min-width: 0;
max-width: 100%; overflow-wrap: anywhere }` so an email/JSON blob cannot push
a card past the panel; roster cards lead name → role (`cardStatus` slot),
audit cards actor → action; actions get their own line. DOM dump of the
People page shows NO stray element; the only bottom-right layer in the app is
the toast stack (6 s auto-dismiss) — if the fragment persists, it is not a
toast and needs a screenshot to identify (browser automation is off-limits
here). Guards in `mobile.test.tsx` ("people & log overflow (structural)" +
DOM hooks).

**Step 2 — Reserved is now DERIVED, never stored.**
`inventory_service.derive_reserved(items, reservations, now)`: a non-denied/
non-cancelled reservation whose `end >= now` makes an otherwise-available item
`Reserved`; none makes a stored `Reserved` read `Available`; any other status
is untouched. Applied in `_fetch_inventory_data` (snapshot/alerts/feasibility)
and `_items_view` in the router (GET /items, PUT /items/{id} response).
Write path: `strip_stored_reserved` on item create/update; `restore_item` maps
an `archivedFrom: Reserved` to `Available`. Deny/cancel/delete/expiry need no
clearing step. Edit modal drops the "Reserved" option; reservation actions
refetch `items` too. One-shot `app/scripts/inv_reconcile_status.py` (dry-run
default, `--apply` rewrites stale Reserved→Available with an audit row).
**Dry-run against live Atlas: 36 items, 0 reservation rows, 1 disagreement —
LL-SEN-004 stored=Reserved derived=Available live=0. Nothing written; `--apply`
is the owner's call** (the read path already shows it as Available). Also
done though (a) didn't apply: picker gains "All items" (default), the list
filters when an item is picked, "New reservation" needs an item, and the two
empty states differ (`reservationsEmptyMessage`). Tests:
`test_inventory_reserved_status.py` (21), `reservations.test.tsx` (4), pure
`visibleReservations`/`reservationsEmptyMessage` (2).

**Verified:** vitest 164/164, backend 690 passed / 6 skipped, `rm -rf .next`
build clean, tsc clean (legacy files only), flag-off oracle byte-identical, no
inventory routes flag-off. Not committed.

## Ownership enforcement + personal view (INVENTORY_PERSONAL_VIEW) · 2026-08-26

Trigger: a lab member could return another person's loan — a data-integrity
bug (`qtyOut` decremented for hardware still in the borrower's hands), not a
display bug. New flag `INVENTORY_PERSONAL_VIEW`, default **OFF**; flag-off
proven byte-identical (oracle below). Staged, not committed.

**Step 0 key findings.** The backend had ZERO ownership checks: any
authenticated user could close any open loan (`POST /tx` return +
`closesTxId`), cancel/edit any reservation, and release/delete any PLAXIS
row. Adjacent defects in the same class: a return with **no** `closesTxId`
still decremented `qtyOut` (orphan return); a `closesTxId` naming an
**already-closed** checkout re-applied the decrement (double return);
`PUT/DELETE /tx/{id}` were open (rewrite/delete anyone's ledger row,
bypassing side effects). `inv_audit` had a single `actor` field — it could
not distinguish who acted from whose record changed. There is **no
reservation-edit UI** (the prompt assumed one); the open `PUT /res` endpoint
was the exposure, and it is now gated. Identity: `inv_tx` carries the
borrower's email; `inv_res`/`inv_plaxis` carry only the person's NAME.

**Phase 1 — server enforcement (`inventory.py`), all flag-gated.** One
resolver: `get_current_user`'s JWT User; `_caller_identity` adds the roster
name joined by email; `_owns_row` = email match first, name fallback (the
Dashboard's existing rule, now server-side). Load-then-compare-then-mutate,
in the handler (no middleware): unknown row → 404, non-owner → 403
`{"detail": "This record belongs to another user."}`, rejected attempts
audited (`denied_*` action, actor + target row id + `owner`). Gated actions:
return (must name its loan; already-returned → 409 — kills the double
decrement), `PUT/DELETE /res` non-status (no manager bypass; managers DENY
instead), `PUT /plaxis` (manager bypass only when releasing),
`DELETE /plaxis` (release-equivalent, owner-or-manager), `PUT/DELETE /tx`
(owner-only). Open and unchanged: checkout, adjust, damage, item/user CRUD,
approval. No read query is user-scoped anywhere.

**Phase 2 — manager return-on-behalf.** Bypass on returns + PLAXIS release
only. `inv_audit` gains an additive top-level `owner` field (actor ≠ owner =
on-behalf; old rows lack the key; no migration). The closed `inv_tx` row
keeps its original user/email — ownership never reassigned.

**Phase 3 — frontend gating (flag-on only; flag-off renders byte-same).**
Return offers members their OWN loans only; managers see all, and the
confirm button reads "Return for {name}" (the chosen consistent on-behalf
treatment; PLAXIS uses "Log out for {name}"). Cancel is owner-only —
managers included (they Deny instead). 403s surface via the existing toast +
refetch recipe (stale tab trues up); never auto-retried. Audit tables render
"actor · for owner" when the field is present.

**Phase 4 — `GET /api/inventory/me` + My Bench.** Separate router registered
BEFORE the CRUD catch-all and ONLY when both flags are on → flag-off route
table byte-identical (no present-and-404ing path; the frontend probes /me
and treats 404 as flag-off, so `/status`'s payload is untouched too). Four
lists filtered from ONE full `_fetch_inventory_data` fetch: open loans with
server-clock `overdueDays`, reservations `end >= now` any status, held
seats, and the UNFORKED `alerts_for` output filtered by refId. Empty lists,
never 404. Frontend: My Bench tab first + default landing (flag-on), four
sections with reassuring empty states; layout guard re-asserted in a
stylesheet-reading test (`.subnav`/`.pageHead` flex:none, `.wrap` explicit
min-height — jsdom does no layout).

**Phase 5 — Mine only.** Inventory + Reservations tables, client-side over
the full payload already fetched (never a server filter param — the full
fetch is what `compute_feasibility` and "who has it" depend on), default ON
flag-on; distinct empty states point at the toggle.

**Verification.** Backend **717 passed / 6 skipped** (+27 in
`test_inventory_ownership.py`: owner 200 / non-owner 403 with row-unchanged
+ qtyOut-unchanged assertions, manager on-behalf with actor≠owner audit,
manager res-edit 403, 400/404/409 return edges, roster-name ownership, /me
filtering + empty lists, flag-off parity class). vitest **180/180** (+16 in
`personal-view.test.tsx`). `tsc` clean for this code (same 5 pre-existing
legacy errors); `rm -rf .next` build clean. Feasibility + snapshot test
files untouched and green. **Parity oracle** (baseline = pre-change staged
tree via `git checkout-index`; 31 hashed stages: router/mode/fallback
prompts, 16 answer-prompt assemblies, full OpenAPI schema, inventory read
endpoints over fixed data): scenario A (prod flags, INVENTORY_ENABLED=false)
and scenario B (INVENTORY_ENABLED=true, PERSONAL_VIEW=false) both
**byte-identical** — manifests
A `1b5bf416d129fd8c…`, B `fb70c06d000719c6…`. Route table: PERSONAL_VIEW
false → 71 paths (13 inventory, no /me); true → 72 (+`/api/inventory/me`).
Throwaway uvicorn on **:8001** flag-off: /health 200, /api/inventory/status
404, /me 404; server stopped, port freed; 8000/3000 untouched. Flag-on route
check import-only (no server, no live-DB index writes).

**Decisions worth review:** flag-on a return REQUIRES `closesTxId` (the UI
always sends it; an orphan return was itself a qtyOut corruption); manager
cancel of another's reservation → 403 (Deny is the manager tool);
`DELETE /tx` remains possible for the row's owner though even an owner
delete of an open checkout skews `qtyOut` — a records-are-never-deleted
rule for tx (like items' 405) would be the stricter fix, left for the owner
to call; `/me` payload key is `overdueDays` (camelCase like every inv_*
key; the prompt's `overdue_days` names the concept).

## Ownership key hardening + ledger immutability · 2026-08-26 (follow-up)

Closes the two flags from the previous section's "Decisions worth review":
name-string ownership on inv_res/inv_plaxis, and deletable tx ledger rows.
Same flag (`INVENTORY_PERSONAL_VIEW`), still default OFF; staged, not
committed.

**Step 0.** inv_tx keys its owner on the top-level **`email`** string (not an
inv_users id) — that field is mirrored, no third convention. inv_res/
inv_plaxis person fields: `user` (display name, rendered everywhere — stays)
and `group`. `list_resource`/create/update return WHOLE documents, so a new
field would echo automatically → flag-off projection required. Configured
DB: inv_res **0** rows, inv_plaxis **0** rows (clean ledger seeded since
last session: 36 items / 20 users, no activity) — the backfill has nothing
to migrate here. Seed demo variant: names all unique, but Shane Smith
(2 plaxis rows) + Cameron Schellenberg have blank roster emails.

**Key added (data-additive, ALWAYS written).** `create_resource` stamps
`doc["email"]` on res/plaxis from the JWT caller regardless of flag state
(never client-supplied — deliberately absent from the field whitelists — and
never overwritten on update: an approval or edit must not reassign
ownership; stamping legacy keyless rows on update would let any flag-off
editor or an approving manager silently claim the row, so updates PRESERVE —
a deliberate deviation from "creates or updates populate", reported). The
seeder stamps it via the variant's own roster (unique-match + non-blank
email, else a printed NOTE — never guessed). Flag-off every res/plaxis
response strips the key (`_hide_owner_key`; list + create + update);
/backup deliberately keeps whole docs so backup→restore never loses keys.

**Backfill.** `app/scripts/backfill_inventory_owner_key.py` (repo script
convention; prompt said `scripts/`) — dry-run DEFAULT (no args writes
nothing), exact case-insensitive name match only, classify
resolved/ambiguous/unresolved (blank name / no match / several matches /
matched-but-blank-roster-email), report every non-resolving row
individually (collection, id, name, item/seat, window). `--apply` writes
resolved rows only ($set email ONLY — updatedAt deliberately untouched so
no phantom 409s), audits each write as actor
`backfill_inventory_owner_key`, idempotent (second pass zero). **Dry run
against the configured Atlas DB (read-only): 0 rows lacking the key in both
collections — nothing to migrate.** `--apply` NOT run against production.

**Ownership compares the key only (flag-on).** `_require_owner` res/plaxis
branch: stored `email` vs JWT email, case-insensitive — name fallback
REMOVED (a display name is not an ownership check). Keyless row → 403
`{"detail": "This record is missing an owner reference. Ask a lab manager
to fix it."}` + `denied_no_owner_key` audit — EXCEPT where the manager
bypass applies (release/return-equivalent), which ranks above the keyless
403 so a manager can still clear a legacy stale seat ("ask a lab manager"
has to lead somewhere). tx keeps email-first/name-fallback unchanged. /me
res/plaxis filter is key-only. Frontend: `ownsRowByKey` drives Cancel,
Log out and the res side of Mine-only flag-on; flag-off keeps the
name-equality affordances byte-same.

**Ledger immutability (flag-on).** `DELETE /tx/{id}` → 405 `{"detail":
"Ledger rows cannot be deleted. Close the loan with a return, or correct
the count with a stock adjustment."}` for everyone, managers included
(even an owner delete leaves qtyOut overstated). Flag-off unchanged. **No
UI control calls DELETE /tx** (grep-verified) — nothing to remove.
`PUT /tx/{id}` untouched (owner-only as staged).

**Verification.** Backend **733 passed / 6 skipped** (+16: key-only
ownership incl. same-display-name distinct-keys, name-matches-key-doesn't,
keyless 403 + audit, manager-bypass-on-keyless-seat, stamp/strip both flag
states, spoofed-client-key ignored, tx-delete 405 ×3 roles with qtyOut
untouched, flag-off unchanged incl. keyless rows + tx delete open; 5
backfill tests: classification matrix, never-fuzzy, dry-run writes
nothing, apply-resolved-only + audits, second-pass zero). vitest
**183/183** (+3: ownsRowByKey, key-not-name Cancel with twin + keyless
rows, keyless seat manager-only). tsc clean for this code; `rm -rf .next`
build clean. Feasibility / snapshot / alerts test files untouched and
green (availability math is not owner-aware). **Parity oracle: both
scenarios byte-identical with the SAME manifests as the previous section**
— A `1b5bf416d129fd8c…`, B `fb70c06d000719c6…` (incl. `openapi_schema
b0b96aee…`, `route_paths 9e4d1d02…`). :8001 flag-off smoke: /health 200,
/status + /me 404; port freed; 8000/3000 untouched.

**Judgment calls for review:** update-preserves-never-introduces (above);
new res/plaxis rows are owned by their CREATOR (an on-behalf reservation
typed with someone else's name is cancellable by its creator, not the
named person — tx, by contrast, keys on the borrower's form email);
backfilled rows are keyed to the NAMED person via the roster (the two
provenances differ, inherent to the prompt); restore of a pre-key backup
drops keys — re-run the backfill after any such restore.

## PLAXIS overlap gate + ledger owner key + control audit · 2026-08-26 (session 3)

Flag now LIVE in prod (`INVENTORY_PERSONAL_VIEW=true`, set by the owner this
session). Staged, not committed.

**Step 0 key findings.** (0e) The two Seat-1 duplicate rows
(`57db259b7815` 14:52:15, still held; `989eee6fb7b9` 14:57:09, since logged
out) were created **4m54s apart** — a deliberate re-booking the system
permitted, NOT a double submit → per the prompt's own rule, no server-side
dedup guard (the overlap gate refuses identical windows anyway). (0c)
`inv_tx` has **0 rows**; mismatch count 0; email confirmed client-supplied
(whitelisted, form-typed). (0d) the dashboard counted plaxis session ROWS,
not distinct seats (2/2 while Seat 2 was free). (0b) the plaxis create
validated nothing: any seat int, end ≤ start, missing dates, unconditional
insert; only the client-side seatConflicts pre-check existed. (0a) full
control matrix in the session report; flagged: checkout/adjust had NO
server stock gates; every 409 detail was swallowed by the generic
"changed by someone else" toast; no modal had an in-flight guard; the
reminders endpoint has no UI (by design, cron); PUT/DELETE /tx are API-only.

**Phase 1 — seat gate.** `seat_conflicts(sessions, seat, start, end,
exclude_id)` in inventory_service (PLAXIS_SEATS = (0, 1); held = loggedOut
falsy; half-open; NOT owner-aware) + router `_reject_if_seat_conflict`
(flag-gated): 400 for bad seat / missing dates / end ≤ start; 409 via the
UNFORKED conflict_message naming holder+window. Wired into plaxis create
and any update touching seat/start/end (self-excluded). Feasibility /
snapshot / alerts untouched — availability never became owner-aware.

**Phase 2 — creator vs owner.** `createdByEmail` (always the JWT caller,
stamped on every tx/res/plaxis create regardless of flag, never
whitelisted, hidden from responses flag-off — `_hide_owner_key` extended:
res/plaxis hide email+createdByEmail, tx hides createdByEmail only since
tx.email predates the flag and the overdue report renders it). Flag-on the
OWNER key is server-resolved (`_resolve_owner_email`): the caller, unless
the form names a different person → their ROSTER email (exact unique
non-blank match) or 400 naming the problem; a client-typed email string is
never stored; returns INHERIT the closed loan's owner (best-effort roster
resolution, never a 400 that blocks a return, never reassigned to the
actor); the studentId/group roster join keys on the resolved owner.
Flag-on `PUT /tx` silently drops a client `email` (no ownership rewrite).
Flag-off tx writes byte-identical to before. Backfill extended to inv_tx
for symmetry (same never-guess classes). Consequence reported: on-behalf
writes now require the named person to be a roster member with an email.

**Phase 3 — double-submit guard.** `useSubmitOnce` in every modal
(Checkout, Return, Adjust, Damage, Item, Reserve, User, Plaxis): the
primary control disables on first fire; unmount-on-submit is the
re-enable; validation failures never arm it. Ungated (client-only defect
fix, per the prompt's unconditional order). No server dedup guard (0e).

**Phase 4 — audit fixes.** Flag-on server stock gates in
`_apply_tx_side_effects`: over-checkout → 409 "Only N of X available",
adjust below zero → 409, return qty > loan qty → 400. Conflict toast now
shows the SERVER's 409 detail flag-on (`conflictToastText`; flag-off keeps
the generic text) — the overlap gates' holder+window messages finally
reach the user. Reminders endpoint-without-UI left as designed (cron
path); no dead controls found; no permission widened.

**Phase 5 — seat counter.** `seatsInUse()` counts DISTINCT held seats
(lib.ts + dashboard); duplicate sessions on one seat read 1/2. Ungated
display-correctness fix.

**Test-environment fix.** The live `.env` flag flipped
test_inventory_authz / test_inventory_conflicts to flag-on implicitly (the
ROUTER_UNCERTAIN_RETRIEVES lesson again) — both files now pin
INVENTORY_PERSONAL_VIEW=False (they document the flag-off contract;
flag-on lives in test_inventory_ownership/plaxis_gate).

**ORACLE BUG FOUND AND FIXED (report honestly, no re-baseline).** The
session-1/2 oracle patched module attrs but `list_resource` resolves
collections via `_RESOURCES` — the six list stages silently read the LIVE
DB and matched only while the collections were empty. With the two live
plaxis rows the historical manifests (`1b5bf416…`/`fb70c06d…`) are
unreproducible by ANY tree; the only base↔work delta was the baseline
echoing the stored plaxis `email` where the current tree projects it out
flag-off — i.e. the session-2 stripping working as designed, not a leak.
Oracle made hermetic (`_RESOURCES` patched too); baseline stays the
session-1 extract. Results: scenario A `0a19362c031b422b…`, scenario B
`facb47ba09bcf44a…`, both **byte-identical** base↔work and stable across
runs; every code-determined stage (router/mode/fallback prompts, all 16
answer assemblies, `openapi_schema b0b96aee…`, `route_paths 9e4d1d02…`)
hash-unchanged from session 2 — no flag-off code path moved; only the six
formerly-live list stages differ from the old manifests.

**Verification.** Backend **760 passed / 6 skipped** (+26: 14 plaxis-gate,
7 owner-key/stock-gate + 3 flag-off pins in ownership, 1 tx backfill,
authz/conflicts pinned). vitest **187/187** (+4: seatsInUse,
conflictToastText, double-click-once ×2). tsc clean for this code;
`rm -rf .next` build clean. :8001 flag-off smoke: health 200, /status +
/me 404, port freed. Backfill dry run vs live: tx 0 rows, res 0, plaxis 2
rows **0 lacking the key** — nothing to migrate.

**Cleanup (owner's call, NOT executed):** duplicate Seat-1 rows
`57db259b7815` (held) and `989eee6fb7b9` (logged out). Suggested: remove
the logged-out duplicate after a dry look — from `python_backend/`:
`venv/bin/python -c "import asyncio; from app.core import database as db;
asyncio.run((lambda: db.inv_plaxis_collection.find_one({'id':
'989eee6fb7b9'}))()) ..."` — see the session report for the exact
dry-run + delete pair.

## Polish pass · 2026-08-26 (session 4)

Flag live; staged, not committed. No data invented — the two blank-email
roster rows (Shane Smith, Cameron Schellenberg) untouched.

**Step 0.** The three `_resolve_owner_email` 400s read "Cannot record this
for {name!r}: …" (problem, half-named person, no remedy). 400s already
surface verbatim via the onError toast (only 409s were ever swallowed —
fixed in session 3), so message TEXT was the whole fix. People-tab
client-only validations: non-blank `name` + V-number `studentId` (modal
only; server accepted anything). inv_plaxis had only the non-unique
loggedOut index; ensure_indexes is the flag-gated named-create_index
pattern. /me probe: never fires when INVENTORY_ENABLED is off (the /status
probe returns first); in the rollback state (inventory on, personal off)
our code catches the 404 silently and no console.* exists anywhere in
app/inventory — the only red line is the BROWSER's own network log of the
404 response, which JS cannot suppress short of not probing. **Phase 5:
no code change** (the "caught fetch" the prompt asks for is what shipped
in session 1); reported, parity untouched by construction.

**Phase 1 — messages that name the fix** (flag-on paths):
- not found → `No roster member named 'X'. Add them under People first.`
- duplicate → `More than one roster member is named 'X'. Ask a lab manager
  to fix the duplicate.`
- blank email → `X has no email on file. Add one under People before
  checking out on their behalf.`
Also swept (named a field, now name an action): seat 400 → "Choose Seat 1
or Seat 2."; return-without-loan 400 → "This return is not linked to an
open loan. Use the item's Return button and try again."; two "Unknown
itemId: …" 404s → "That item is no longer in the inventory." (these two
are flag-independent error texts — reported as the one flag-off delta,
error-path prose only, oracle unaffected); item dup-id 409 gains "Pick a
different id." Deliberately left: `_coerce`'s per-field 400s, the tx-type/
feasibility/restore-mode 400s (API-facing, unreachable from the UI — the
field name is the useful part there).

**Phase 2 — roster gap visible where it can be fixed.** People tab: an
email-less row shows a muted note "No email — cannot be named on a
checkout" (flag-on only — the rule it describes is flag-on; NOT in Needs
Attention: a roster data gap is neither urgent nor physical). Client-side
render from the existing payload; no endpoint.

**Phase 3 — TOCTOU backstop.** `uniq_held_seat_window` partial unique
index on inv_plaxis (seat,start,end) with partialFilterExpression
{loggedOut: false}, in ensure_indexes under the INVENTORY_ENABLED block
like its siblings. **Verified buildable against live data first** (1 held
row, 0 rows missing loggedOut, no duplicate keys). The application gate
stays; the router catches DuplicateKeyError on the plaxis insert and
re-raises the SAME 409 shape (holders re-fetched via seat_conflicts →
conflict_message), so a lost race reads like a normal conflict.
Limitations noted: exact-duplicate windows only (Mongo has no range
exclusion), and rows written without a loggedOut field would escape the
partial filter (the UI always writes it; live rows all carry it). Index
lands on next prod restart (startup-time; no live write this session).

**Phase 4 — People server validation** (flag-gated, `_validate_roster_write`):
blank name on create, or blanking it on update → 400 "A lab member needs a
name."; malformed non-blank studentId → 400 "The student ID should look
like V00891234." Blank emails deliberately STORABLE (no email-required
rule); an update not touching `name` still works on a legacy nameless row.

**Verification.** Backend **765 passed / 6 skipped** (+5 net: exact-message
matrix incl. blank-email ≠ not-found, People validation trio,
DuplicateKeyError→409). vitest **189/189** (+2: badge on/off). tsc clean
for this code; `rm -rf .next` build clean. Feasibility/snapshot/alerts
files untouched. **Oracle: both manifests byte-identical AND equal to the
session-3 values** — A `0a19362c031b422b…`, B `facb47ba09bcf44a…`. :8001
flag-off smoke clean, port freed.

**Out of scope, worth fixing (named, not fixed):** items.id race →
DuplicateKeyError still surfaces as a 500 (same translate-to-409 treatment
would fit); `_coerce` 400s remain developer-phrased; reservation windows
have no equivalent DB-level backstop (quantity-aware, so no unique index
can express it); the on-behalf 400 for a non-roster person still blocks
walk-in visitor checkouts (roster-first is the current rule by design);
audit `detail` for `update_*` is a bare key list (reads poorly in the log);
the browser's network log still shows one 404 per load in the
rollback state (unsuppressible from JS — would need the flag surfaced in a
flag-on-only field of /status or a 200-shaped probe, both parity-touching).

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
