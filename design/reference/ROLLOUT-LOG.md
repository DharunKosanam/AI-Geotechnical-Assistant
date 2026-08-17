# Dark redesign — rollout log

Branch: `ui/dark-redesign` (off `main`; `for_pass` was already merged).
Run mode: unattended, Phases 1–6, decisions pre-answered by Dharun (2026-08-15).

## ⚠ Missing mockup

`design/reference/geotech-ai-v3.html` was **not on this VM** when the run started
(no `~/Downloads` exists; filesystem search found no copy). Per the "make the
call, log it, keep going" rule the run proceeded from the written spec in the
rollout prompt, which fully defines tokens, type, spacing, radii, elevation,
easing, control heights, and per-phase structure. Judgment calls made without
the mockup are logged per phase below. When the file lands, drop it in this
directory and diff the details (grain texture, exact interaction states,
icon details) against what was built.

---

## Phase 1 — token layer

**Files touched:** `app/globals.css` (rewritten), `app/layout.tsx` (rewritten).

**What changed**

- All spec tokens landed under `:root`: surfaces `--bg/--s1..--s4`, alpha lines
  `--line/--line-2/--hi`, text `--t1..--t3`, `--accent/--accent-2/--accent-a`,
  `--oxide`, shadows `--e1..--e3`, radii `--r1/--r2/--r3` (6/8/12px), easing
  `--ease`.
- Legacy tokens (`--bg-base`, `--bg-sidebar`, `--bg-surface`, `--text-primary`,
  `--text-secondary`, `--border-subtle`) are now **aliases onto the dark scale**,
  so every module already consuming them went dark for free.
- Fonts: Inter dropped; IBM Plex Sans (400/500/600), Plex Sans Condensed
  (600/700), Plex Mono (400/500) via the existing `next/font/google` — no new
  dependency. Exposed as `--font-sans/--font-cond/--font-mono`; body set to
  13.5px / -0.006em on Plex Sans.
- Base layer: body bg/color, `color-scheme: dark` (darkens native selects,
  checkboxes, scrollbars), `accent-color: var(--accent)` for native
  checkbox/radio, `::selection`, double-ring `:focus-visible`
  (`0 0 0 2px var(--bg), 0 0 0 4px var(--accent)`), thin tokenized scrollbars,
  global `prefers-reduced-motion` kill switch.
- Grain overlay: fixed, `pointer-events: none`, opacity 0.028, `z-index` max,
  SVG `feTurbulence` data-URI, mounted once in `layout.tsx` after `{children}`.
  Cannot intercept clicks (pointer-events none + no children).
- Global light remnants removed: `a { color: blue }`, `pre` light colors
  (retokenized to `--s1`/`--t1`/mono; its structural negative margins kept —
  Phase 3 owns code blocks), orphan `.logo` and `.warnings` classes (verified
  unreferenced by any live component).

**Judgment calls (no mockup to check against)**

- Added `--danger/--danger-2/--danger-a` and `--warn/--warn-2/--warn-a` token
  families. The spec defines no error/warning colors but the app has real error
  and acknowledgement surfaces; values chosen muted, same saturation register
  as `--accent`/`--oxide`.
- `::selection` uses accent at 0.28 alpha (spec names selection styling but no
  value; `--accent-a`'s 0.14 was illegibly subtle).
- Grain is generated `feTurbulence` noise at 140px tile — the mockup's actual
  texture asset was unavailable.

**Skipped / left as-is:** dead files `file-viewer.*` and `warnings.*` untouched
per decision. `KB_UPLOAD_PROGRESS.md`, `update-assistant.js` untouched.

**Build:** `rm -rf .next && npm run build` → **pass** (all routes compile;
Google Fonts fetch for Plex succeeded).

**Hex grep on touched files:** `globals.css` contains hex only inside `:root`
token definitions (by design — this is the one place hex is allowed).
`layout.tsx` contains none. Every other module still carries its old hex — that
is the expected "broken-but-dark" state; phases 2–5 burn them down.

---

## Phase 2 — app shell

**Files touched:** `app/components/sidebar.tsx` (extracted verbatim from
chat.tsx in its own commit, then restyled), new `sidebar.module.css`,
`thread-list.tsx` + `thread-list.module.css` (rewritten presentation, logic
byte-identical), `Header.tsx` + `header.module.css` (rewritten), new
`account-menu.tsx` + `account-menu.module.css`, `page.tsx` + `page.module.css`,
`chat.tsx` (sub-header + collapse props + title state), `chat.module.css`
(shell classes only), `__tests__/thread-list.test.tsx` (menu-open step added).

**What changed**

- **Sidebar**: brand + collapse toggle, "New thread" + search button (client-
  side title filter), Mine / Lab shared segmented filter (client-side over the
  existing `isGroup` field — real data, no endpoint invented), day-grouped
  thread list (Today / Yesterday / short date, computed from
  `updatedAt||createdAt`), 30px rows with a hover-revealed ⋯ menu (Rename /
  Share with lab / Delete — all calling the exact pre-existing functions),
  footer with Join team chat. Collapse state lives in `page.tsx`; both the
  top-bar toggle and the sidebar's own toggle drive it (width 264→0,
  160ms `--ease`).
- **Top bar**: 48px, `rgba(12,11,10,.72)` + backdrop blur, collapse toggle
  left (chat page; brand shows on other pages), segmented nav absolutely
  centred (28px segments, icons drop labels below 720px), account avatar
  right. All flag fetches, links and `aria-current` logic untouched.
- **Account menu**: popover from the avatar — initials avatar, name, email
  (mono), role badge (real: `AuthUser.role` exists), Account / Settings /
  Shortcuts / Invite disabled with "Not available yet" tooltips, Sign out
  wired to the existing `signOut()` + `/login` redirect. Closes on outside
  click and Escape; focus returns to the avatar on Escape-close.
- **Thread sub-header** (40px, only when a thread is open): title (fed by the
  list on select and kept fresh on rename via a new `onThreadRenamed`
  callback), user-turn count in mono, LAB SHARED chip when the thread is a
  group thread, `⋯` menu with a working Copy thread ID.
- Share-thread-ID and Join modals restyled (--s2, e3 shadow, Escape +
  backdrop close on the share modal; Escape close on the join modal).

**Disabled controls (no backend), rendered with tooltips**

- Sub-header **Share with lab** button (the real share action lives in the
  thread row's ⋯ menu, where `toggleGroupStatus` already existed; the tooltip
  says so). Shown only for non-shared threads; shared threads show the LAB
  SHARED chip instead.
- Sub-header **Export** — no export endpoint exists.
- Account menu **Account / Settings / Shortcuts / Invite** — no backends.

**Skipped / omitted, and why**

- **Sidebar footer (model + on-premise + doc count)**: `/api/kb/status`
  returns exactly `{"enabled": bool}` (python_backend/app/routers/kb.py:558)
  and no endpoint anywhere exposes a model name or library doc count — the
  whole block is omitted rather than faked. If a status endpoint ever grows
  `{model, docCount}`, the footer slot in `sidebar.tsx` is the place.
- **Team chat button in the top bar** (mockup position): the join-modal state
  lives inside `chat.tsx`; hoisting it out is state surgery, not styling.
  Join team chat stays in the sidebar footer, wired as before.
- `SidebarAccount` is now unmounted (identity + sign-out moved to the account
  popover per the mockup). File left untouched per the dead-files decision.

**Judgment calls**

- The Mine/Lab filter defaults to **Mine**, matching the mockup's two-segment
  control; lab-shared threads are one click away. Note this changes the
  default view for users who had group threads mixed into the list.
- The extraction discipline produced **two commits this phase** (verbatim move,
  then restyle) so the move diffs as a move; both use the phase-N message
  format.
- Unit test updated only to open the ⋯ menu before clicking Rename/Delete —
  the pinned refresh contract itself is unchanged and all 7 tests pass.

**Build:** `rm -rf .next && npm run build` → **pass**. `npm run test:unit` →
**7/7 pass**.

**Hex grep on touched files:** sidebar/thread-list/header/account-menu/page
modules: zero hardcoded colors (all tokens). `chat.module.css` still has 47
hex values — all in conversation/composer classes owned by Phases 3–4.

---

## Phase 3 — conversation view

**Files touched:** `app/components/message-list.tsx` (extracted verbatim from
chat.tsx in its own commit, then rewritten), new `message-list.module.css`,
`chat.tsx` (sources routing + retry), `chat.module.css` (conversation classes
removed), `globals.css` (global `pre` reduced to a baseline).

**What changed**

- **Turns as documents**: mono uppercase role label (user in `--t3`,
  Assistant in `--accent-2`), user question at 15px/500, assistant prose at
  14.5px/1.72, measure capped at 720px + 28px rail gutter. The old
  bubble/row/label classes (including the four class references that rendered
  `class="undefined"` since forever) are gone.
- **Vertical rail**: per-turn 1px `--line` segments join into a continuous
  rail; a 9px hairline tick marks each user turn, a 5px `--accent` dot each
  assistant turn.
- **Markdown**: all 25 inline `style={{}}` overrides deleted; element styling
  now lives in CSS scoped under `.assistantBody` (list markers `--t3`, strong
  `--t1`, links `--accent-2`, blockquote hairline, tables with hairline rules
  in an `overflow-x` wrap). This also fixes the react-markdown v9 `inline`
  regression: inline code (`--s3` chip) and block code (`<pre>` on `--s1`,
  mono 12.5px) are now styled by element, not by the dead `inline` prop.
  KaTeX display math gets its own overflow scroll. The global
  `pre { background:#e4e4e4; margin:-4px -16px }` bleed is gone.
- **Sources panel per answer ("Grounded in")**: sources are no longer
  flattened into the message text as a `**Sources:**` markdown block —
  `formatSourcesBlock`/`visionMarker` are deleted and each assistant message
  carries the retrieval payload's `sources` array as structured data
  (`MessageProps.sources`). All five attachment points converted: chat-SSE
  `done`, formats-SSE `done`, JSON fallback, history load, group-poll load.
  The panel renders index (mono, `--oxide`), title (Scholar link only when
  the payload has a URL — user uploads stay link-less by design), provenance
  meta (project · version · uploader when present), and the vision
  disclaimer (`AI vision · p. N — not verbatim`, in `--warn-2`) exactly when
  `visionDerived` is set. History rows whose stored text already contains the
  legacy `**Sources:**` block keep it and skip the panel, so nothing renders
  twice.
- **Streaming state**: three accent dots + a single generic status line with
  the existing (real, client-side) time escalation. No fake retrieving/
  generating stages — the wire has none. The indicator shares the assistant
  turn's structure so the first token replaces it without layout shift.
- **Message actions on hover**: Copy (real, copies the raw markdown), Retry
  (real — re-sends the last user question through the same send path; the
  duplicate user turn is hidden by the existing consecutive-dedup; the server
  thread records it as a re-ask, which is exactly what it is), Helpful
  (disabled + tooltip — no feedback endpoint exists).

**Omitted (payload has no such data) — decisions pre-answered**

- **Relevance bar + score column**: dropped per decision (unbounded
  cross-encoder logits can't honestly render as a 0–1 bar).
- **Inline `.ref` citation chips**: the SSE path carries no span-level
  citation data, so there is nothing honest to anchor inline chips to. The
  per-answer panel is the citation surface. The legacy OpenAI-annotation path
  (`【n:m†source】`) still renders as `(Source: filename)` italic text.
- **Per-source page numbers (non-vision)**: page indices ARE stored at
  ingestion — `python_backend/app/services/file_processing.py` chunk metadata
  carries `page_number` ("1-indexed logical page (sheet, slide, or PDF
  page)"). They are dropped when `python_backend/app/routers/chat.py`
  (~line 766) builds each `source_entry`. To forward them, aggregate the cited
  chunks' `chunk["metadata"]["page_number"]` values per source into e.g.
  `source_entry["pages"] = sorted({...})`; the frontend normalizer in
  `message-list.tsx` is the single place to then render them. Not added —
  backend change.
- **Router mode chip**: omitted. Exact backend diff needed to land it later,
  in `python_backend/app/routers/chat.py` (the streaming generator's `done`
  event, ~line 1131):

  ```diff
           yield _sse(
               "done",
               {
                   "sources": result.sources,
                   "no_high_confidence_sources": result.no_high_confidence_sources,
  +                # Router verdict for the UI chip: KB_QUERY | GENERAL |
  +                # MIXED | THREAD_DOC (None when ROUTER_ENABLED is off).
  +                "route": getattr(result, "route", None),
               },
           )
  ```

  (plus threading `route` onto the turn result object where the router's
  verdict is currently consumed, and the JSON `/api/chat` response body for
  the non-streaming path). Frontend side once it exists: read
  `payload.route` in the `done` handler in `chat.tsx`, store it on the
  message, render as spaced uppercase mono in the panel header.
- **User-turn timestamps** (mockup shows them): messages in frontend state
  carry no timestamps on any path (SSE tokens, history load), and stamping
  them client-side at render would show load time, not send time. Omitted.

**Judgment calls**

- Retry renders only on the **last** message when it's an assistant turn, and
  is disabled in group conversations (a shared thread re-ask from the retry
  button would surprise the other participants mid-poll).
- Exported diagram PNGs (white canvas) render as framed figures with a white
  ground (`background:#fff` on `.assistantBody img` — the one intentional hex
  in the phase, a light artifact on dark ground, not UI chrome).

**Build:** pass. **Unit tests:** 7/7 pass.

**Hex grep on touched files:** `message-list.module.css` — one intentional
`#fff` (see above). `message-list.tsx` — none. `chat.module.css` — 14 hex
remain, all in composer/chip/format classes owned by Phase 4.

---

## Phase 4 — empty state and composer

**Files touched:** `app/components/composer.tsx` (extracted verbatim from
chat.tsx in its own commit, then rewritten), new `composer.module.css`,
`chat.tsx` (welcome restyle + Composer mount), `chat.module.css` (rewritten
down to what chat.tsx still owns: shell, sub-header, diagram overlay,
welcome).

**What changed**

- **Composer**: a raised `--s2` card at the 748px measure; `focus-within`
  swaps the hairline border for the accent ring. Auto-growing textarea
  (40→200px, height driven by content, resets when the send path clears the
  input; the old user-draggable `resize: vertical` is gone). Left: attach `+`
  (both feature-flag branches and the two-item menu preserved, Escape now
  dismisses the menu) and the scope toggle. Right: mono keyboard hint
  (`⏎ send · ⇧⏎ newline`, hidden under 560px) and a 32px accent send button
  (icon, `aria-label="Send"`). **Send is now disabled when the input is
  empty** (was: enabled but a no-op).
- **Attachment chips**: `--s3` chips with token status colors — the
  `color="#4ade80"` / `color="#f87171"` icon props are gone (accent-2 /
  danger-2 classes). Stage line in mono. Error/warning text in
  danger-2/warn-2. Upload-progress spinner unchanged in behavior.
- **Sources bar / format pills / progress notice** (composer-area features):
  retokenized, control heights per spec (26px small), same handlers,
  tooltips and disabled-reason copy preserved verbatim.
- **Empty state**: mono eyebrow, condensed 26px heading, one line of subtext,
  starter prompts as hairline-separated rows — mono verb label (accent on
  hover), title, subtitle, and a chevron that slides in on hover/focus. Same
  two handlers (prompt select / attach picker).
- Join modal: buttons now `type="button"` — previously they were default
  submit buttons inside the form and only worked because `handleSubmit`
  no-ops on empty input.

**Disabled controls (no backend), rendered with tooltips**

- **Scope toggle** ("Knowledge base", left of the composer): retrieval scope
  is the backend router's decision; no endpoint exists for a user override.
  Tooltip says so.

**Skipped / omitted, and why**

- **Empty-state eyebrow live data** (document count + index time): no
  endpoint exposes either (`/api/kb/status` is `{enabled}` only) — the
  eyebrow is static text rather than a fake number.
- **Upload progress percentage on chips** (mockup shows progress): the ingest
  poll exposes stages (extracting/ocr/chunking/embedding), not percentages —
  the chip shows the real stage in mono instead of a fake bar.

**Build:** pass. **Unit tests:** 7/7 pass.

**Hex grep on touched files:** two intentional `#fff` remain (diagram iframe
canvas in `chat.module.css`, diagram PNG thumbnail ground in
`composer.module.css`) — light artifacts on dark ground, not UI chrome.
Remaining `color="#4ade80"` hits are all in `kb-upload.tsx` → Phase 5.

---

## Phase 5 — every remaining surface

**Files touched:** `auth.module.css` + `auth-guard.module.css` (rewritten;
auth page TSX untouched — CSS-only restyle), `kb-upload.module.css`
(rewritten) + `kb-upload.tsx` (icon color props → token class, alert→toast),
`workspace/workspace.module.css` (rewritten) + `workspace/page.tsx` (icon
color prop → class, 3× alert→toast), new `toaster.tsx` + `toaster.module.css`
+ mount in `layout.tsx`, `chat.tsx` (3× alert→toast), `composer.tsx`
(diagram-error alert→toast), `diagram-editor-modal.tsx` (embed skin
`ui=atlas` → `ui=dark`).

**What changed**

- **Auth pages** (login / signup / forgot / reset): card on `--s2` with `--e2`
  + top highlight, condensed heading, mono eyebrow/labels, inputs on `--s1`
  with accent focus glow, accent primary button (32px), accent links,
  danger-token error blocks, accent-token success notice. The stratigraphic
  "borehole log" column is kept as the signature motif, retuned one step
  darker to sit on `--s2` — five intentional hex values, documented in-file.
  All four pages share the one stylesheet, so no TSX changed; every state
  (registered notice, invalid-link, 400/429 branches, loading labels) renders
  the new language automatically. AuthGuard gate: accent spinner on `--bg`.
- **Knowledge Base**: `kb-upload.module.css` rewritten on tokens — panels
  `--s2`, dropzone dashed `--line-2` on `--s1` with accent hover,
  acknowledgement warnings in warn tokens, fields on `--s1` with accent
  focus, accent primary / ghost secondary buttons, mono meta and sample-chunk
  block, danger error box, delete button with danger hover. **The
  `prefers-color-scheme: light` override block is deleted outright** per
  decision — the app is single-theme dark. The three `color="#4ade80"` icon
  props became a `.okIcon` token class.
- **GeoPilot / workspace**: full token rewrite — `--s1` doc panel with 26px
  tabs / 30px doc rows / mono history meta, hover-revealed remove (now also
  `:focus-within`), welcome state with condensed heading and mono `run CPT`
  chip, bubbles retokenized (`--s3` user / `--s2` assistant). **CPT result
  card**: `--s2` card with hairline table rules, mono numeric cells, zone
  chips in mono on `--s3`, the citation/reference line in `--oxide`, "AI
  draft — for engineer review" badge in warn tokens (mono uppercase), flagged-
  concerns box in warn tokens, error box in danger tokens, Export as a 28px
  raised control. Composer row mirrors the chat composer (accent send,
  hairline card input with accent focus). Per decision: **no confidence
  indicators, no source spans, no override step** — see the payload proposal
  below.
- **Toast layer**: new `toaster.tsx` (event-based `toast()` + `<Toaster/>`
  mounted once in `layout.tsx`; `--s3` cards bottom-right, `aria-live`,
  6s auto- or click-dismiss, no dependency). All 8 live `alert()` call sites
  swapped (chat ×3, composer diagram-error ×1, workspace ×3, kb-upload ×1).
  Handler logic around them unchanged. The dead `file-viewer.tsx` alerts are
  untouched (dead file).
- **Diagram editor chrome**: overlay darkened; embed skin switched
  `ui=atlas` → `ui=dark`. The postMessage save protocol is skin-independent
  and unknown `ui` values fall back to the default skin, but this is
  **unverified in a real browser** (no browser automation per standing rule)
  — worth one manual open-and-save when you next deploy. The iframe interior
  is cross-origin and otherwise unstylable; the white canvas stays a framed
  light artifact by design.

**GeoPilot payload proposal (logged, not built)**

The screen the rollout brief describes needs data the workspace payload does
not carry. `Layer` (app/workspace/page.tsx) is
`{layer_index, depth_from, depth_to, thickness, soil_type, zone, qc_avg, ic_avg}`
and `Interpretation` is `{narrative, concerns[], error?}`. To support the
review-grade screen, the CPT interpret response would need, per layer:

- `confidence: {qc_avg: float, ic_avg: float, soil_type: float}` — per-field
  0–1 confidences from the calculator (deterministic fields can carry 1.0;
  boundary-adjacent Ic classifications are where this earns its keep);
- `source_span: {row_start: int, row_end: int}` — the raw CPT-file row range
  each layer was aggregated from (the calculator already knows its
  depth-window; forwarding row indices lets the UI link a value back to the
  sounding data);
- and thread-level `review: {status: "draft"|"accepted"|"overridden", ...}`
  with an endpoint to persist an engineer's accept/override per layer —
  today no such endpoint exists, which is why the only human-in-the-loop
  affordance remains the advisory draft badge and concerns list.

Producers: `python_backend/app/workspace/calculators/cpt.py` (confidence +
spans) and a new small routes.py endpoint (review state). Until then the card
deliberately shows only what the payload proves.

**Skipped / left as-is**

- `file-viewer.*`, `warnings.*`, `sidebar-account.*` untouched (dead /
  unmounted; `sidebar-account.module.css` still carries 2 old hex values —
  irrelevant while unmounted, flagged for the delete branch).
- Native `<select>` popup chrome and checkboxes: OS-rendered; `color-scheme:
  dark` + `accent-color` from Phase 1 are the practical limit.

**Build:** pass. **Unit tests:** 7/7 pass.

**Hex grep (whole live app):** zero inline colors in live TSX. Remaining hex
in live CSS: the 5 documented strata colors and the 3 documented `#fff`
light-artifact grounds. No `prefers-color-scheme` blocks, no `lightgrey`, no
light-palette values anywhere live.

---

## Phase 6 — QA pass

**Files touched:** `sidebar.module.css`, `page.tsx`, `workspace.module.css`,
`kb-upload.module.css`, `chat.module.css`, `composer.module.css`,
`composer.tsx`, `thread-list.tsx`, `message-list.module.css` — targeted fixes
only.

No browser automation is permitted in this project, so QA is code-level
verification plus fixes; items needing a real browser are called out.

**Responsive to 380px**

- Below 720px the chat sidebar becomes a fixed overlay (same collapse state,
  `--e3` shadow) and `page.tsx` starts it collapsed via `matchMedia`, so
  narrow screens open onto the conversation.
- Workspace stacks vertically below 720px (doc panel becomes a 180px row).
- KB metadata `.row` fields wrap (min 160px each).
- Header segments already drop labels below 720px; composer keyboard hint
  hides below 560px; the sub-header's two disabled placeholder buttons hide
  below 560px (title, LAB SHARED chip and ⋯ stay).
- Horizontal overflow: all wide content (markdown tables, KaTeX display math,
  code blocks, CPT table, KB sample block) sits in its own `overflow-x`/
  `overflow` container; turns cap at the measure; long tokens break via
  `overflow-wrap`. **Needs one real-browser spot check at 380px.**

**Keyboard + focus**

- Every interactive element is a real `<button>`/`<a>`/`<input>`; the global
  double focus ring (`--bg` + `--accent`) applies app-wide via
  `:focus-visible`.
- Hover-revealed controls are also revealed by `:focus-within` (thread-row ⋯
  menu, message actions, workspace doc-remove) and the starter chevron by
  `:focus-visible`.
- **Modals** (Share thread ID, Join team chat): Escape closes, backdrop
  closes (share), Tab is trapped and cycles, focus moves in on open and
  returns to the opener on close.
- **Popover menus** (account, thread-row ⋯, sub-header ⋯, attach): Escape and
  outside-click close all four; the account menu returns focus to the avatar
  on Escape. Popovers are light-dismiss by design, not focus-trapped — Tab
  walks on and the outside-click handler closes behind it.

**Reduced motion**: global kill switch (Phase 1) zeroes every animation and
transition; the thinking dots additionally keep their explicit static
fallback. The two remaining keyframe users (spinners, toast entry) are
covered by the global rule.

**Contrast (WCAG, computed against the surfaces actually used)**

- `--t1` 12.4–17.0:1, `--t2` 5.6–7.7:1, `--accent-2` ≥8.3:1, `--oxide`
  ≥5.5:1, `--warn-2` ≥8.5:1, `--danger-2` ≥6.4:1, send-button text on
  `--accent` 7.5:1 — all AA or better everywhere they appear.
- `--t3` measures 3.47:1 on `--bg` falling to 2.83:1 on `--s3`. It is
  spec-defined for tertiary metadata and placeholders and is used only
  there; the one informative case on a raised surface (chip ingest stage)
  was bumped to `--t2`. Disabled-control labels in `--t3` are
  WCAG-exempt. If AA-for-everything is wanted later, raising `--t3` to
  ~#7d766f clears 4.5:1 on `--bg`/`--s1`.

**Layout shift**

- Stream start: the thinking indicator shares the assistant turn's exact
  structure (same rail marker, label, padding) — the first token swaps
  content, not layout.
- Sidebar collapse: width animates under `--ease` (and is overlay below
  720px, zero reflow); no snap.
- Sources panel: renders inside the scroll container below the finished
  answer; composer geometry untouched; `scrollbar-gutter: stable` added to
  the conversation scroller so the scrollbar's appearance no longer nudges
  content.

**Final greps**: zero hardcoded colors in live TSX; live CSS carries only the
8 documented intentional hex values (5 strata + 3 white artifact grounds); no
`prefers-color-scheme`, `lightgrey`, or light-palette remnants outside dead
files; no references to removed CSS classes.

**Build:** pass (final clean rebuild). **Unit tests:** 7/7 pass.

**Left broken / not fully verifiable here**

1. The mockup never reached this VM — fidelity to it is untested by
   definition; the build follows the written spec.
2. `ui=dark` on the diagrams.net embed is unverified in a browser (save
   round-trip is skin-independent, but check once).
3. KaTeX got only baseline dark treatment (inherits `currentColor`, display
   math scrolls); exotic constructs (`\colorbox`) may need targeted overrides
   once seen in real content.
4. Dead files (`file-viewer.*`, `warnings.*`) and the now-unmounted
   `sidebar-account.*` remain light-themed on purpose — delete branch.
5. A frontend rebuild + restart on the test instance is needed to see any of
   this: `stop → rm -rf .next → npm run build → start` (build already run
   here; the running instance still serves the old bundle until restarted).

---

## Post-run review fixes (Dharun's pre-deploy checks, 2026-08-15)

- `--t3` raised `#6C6660` → `#878079` per review (second pass): measured
  against `--s2`, the lightest surface `--t3` carries readable text on (the
  composer placeholder). Ratios: 4.51:1 on `--s2`, 4.81 on `--s1`, 5.05 on
  `--bg` — AA everywhere it renders informative text. (The first-pass
  `#7d766f` measured only 4.39 on `--bg` and was superseded.) Deviation from
  the spec value, requested explicitly.
- Sources-panel producer hardening (chat.tsx), per review:
  - The legacy-block guard no longer text-sniffs the whole message: rows
    created after the 2026-08-16 cutover always get the panel (`createdAt` is
    returned by the python history endpoint; no backend or frontend since the
    redesign persists the appended block), and only older/undated rows fall
    back to a tail-anchored match, so a model-written "**Sources:**" heading
    mid-prose can't suppress the panel.
  - `attachSourcesToLastMessage` attaches only when the last message is an
    assistant turn.
  - A `done` event with no streamed tokens no longer appends an empty
    assistant message with a floating panel (it rendered as a role label over
    an empty body) — it is skipped on both the chat and formats streams.
- Verified: legacy threads do NOT double-render sources (the
  `!text.includes('**Sources:**')` guard suppresses the structured panel on
  rows whose stored text carries the old baked-in block; the backend never
  bakes the block into persisted text, so only genuinely old rows have it).
- Verified: the `【n:m†source】` annotation rewrite survived the
  `formatSourcesBlock` deletion — it lives in `message-list.tsx` and still
  runs on every assistant message.
- Verified: zero `confirm()` calls were converted to toasts in Phase 5 — the
  only `confirm()` in the tree is dead `file-viewer.tsx:137`, untouched. All
  nine conversions were post-failure `alert()` notifications.

---

## Deploy-test fixes (first build on the test instance, 2026-08-15)

1. **Conversation column bottom-pinned** — the bubble-era
   `.messages > *:first-child { margin-top: auto }` bottom-pinning rule had
   been carried into `message-list.module.css`; removed. Turns are now
   top-aligned and grow downward; the column stays max-720px and centred
   (there was no right-anchoring rule — the "right" in the report was the
   column centring within the pane to the right of the 264px sidebar,
   compounded by the bottom pinning).
2. **Serif body text** — the next/font variable classes sat on `<body>` while
   `--font-sans/--font-cond/--font-mono` are declared on `:root`. A custom
   property's var() references resolve at the element that declares it, so
   `--font-plex-*` didn't exist at `:root`, `--font-sans` computed to invalid,
   and `font-family` fell through to the browser serif default. The three
   variable classes moved to `<html>`. Verified in the built output:
   prerendered `<html>` carries all three classes; the built CSS defines
   `--font-plex-sans` on that class and `body{font-family:var(--font-sans)}`
   resolves to "IBM Plex Sans" + its size-adjusted fallback. Message bodies
   inherit it (only code/pre switch to Plex Mono).
3. **Duplicate sources UI** — the composer's SOURCE_SETS "Sources (n)" toggle
   + panel removed (JSX, ten props, 109 lines of CSS). ⚠ Caveat flagged at
   removal time: that panel was NOT reading the per-answer citation payload —
   it listed the THREAD's uploaded documents (ingest status, section counts)
   and hosted the only UI for the source-set **Remove** flow
   (`requestRemoveSource`/`confirmRemoveSource` + dry-run confirm). The
   backend endpoints and the chat.tsx state/handlers remain wired and
   untouched (data layer unchanged, presentation removed); until a new home
   is chosen (thread sub-header ⋯ menu is the natural one), removing a
   document from a source set has no UI entry point.

---

## Post-deploy tasks (2026-08-15) — Thread documents re-home + branch sweep

### Task 1 — Thread documents drawer

**New:** `app/components/thread-documents.tsx` + `.module.css`. **Edited:**
`chat.tsx` (⋯ menu item, drawer mount, import), `chat.module.css` (menu
item layout + count badge).

**Form: right-edge drawer, not an inline panel.** Reasons: (a) the shell's
column-reverse chat container has no spare vertical real estate — an inline
panel would either push the composer or eat conversation height; (b) the
Remove flow is destructive and modal-shaped (preview → confirm) and deserves
its own focus-trapped surface; (c) a fixed drawer over the conversation is
the most visually distinct treatment available from the in-message
"Grounded in" inset, which was the second hard requirement.

**Distinctness (hard requirement 2):** drawer on `--s1` with a `--line-2`
left rule and `--e3`, condensed "Thread documents" title + explanatory
subtitle, rows on `--s2` cards, no citation indices, mono status/provenance
line. "Grounded in" is an `--s1` inset inside the answer with `--oxide`
indices and a mono "GROUNDED IN" eyebrow. Different container, different
header language, different position, different information.

**Preview stays mandatory (hard requirement 1):** the only control per row is
"Remove…" → `requestRemoveSource` (dry-run, `confirm:false`). The destructive
`confirmRemoveSource` is reachable ONLY from the Delete button inside the
preview block that the dry-run response produces; Cancel and Escape/close
clear the preview. No other path calls it. Remove is disabled (with a
tooltip) while a stream or format generation is running — same locking the
old panel had.

**Wiring:** identical state/handlers from chat.tsx (`threadSources`,
`removePreview`, `removeBusy`, `requestRemoveSource`, `confirmRemoveSource`,
`showSources`/`setShowSources` reused as the open flag). Menu item is
flag-gated on `sourceSetsEnabled` and shows the doc count in mono. Escape,
backdrop click, Tab trap, focus-restore to the ⋯ button.

### Task 2 — branch sweep

**Fixed**
- `message-list.tsx` referenced `s.sourcesCount`, which had no class in the
  module (would render `class="undefined"` on the "N sources" count) —
  class added. This was the ONLY dead-class reference on the branch (checked
  every `alias.class` in every live TSX against its module); the Phase 0
  `messageRow/messageContent/messageLabel/clearfix` set is confirmed gone.
- Duplicate `.threadMenuItem` rule in `chat.module.css` merged.

**Flagged (judgment calls, left as-is)**
- **Spacing grid:** colors, font-family and radii are 100% clean across every
  live module. Spacing is not: the build uses a de-facto **2px** grid
  (6/10/14/26/30/34px are common), not the spec's strict multiples of 4. Two
  of the spec's own control heights (26px small, 30px list rows) are off a
  4px grid, and 6/10/14px paddings pair naturally with those. Full list is
  reproducible with the sweep grep in this session; ~130 declarations across
  12 modules. Snapping them all to 4px is a mechanical pass but changes the
  feel of every control — wants a decision, not a silent sweep.
- **Focus rings on text inputs:** buttons/links/menu items everywhere inherit
  the global `:focus-visible` double ring untouched. Text inputs (auth, KB
  fields, thread rename, workspace input, composer textarea) deliberately
  set `outline:none` and use an accent border/glow on `:focus` instead —
  consistent app-wide, and the composer's ring is carried by the card's
  `focus-within`. If the double ring is wanted on inputs too, remove those
  five `:focus` overrides.
- **`--t3` on `--s3/--s4`:** 4.12 / 3.67 — used there only for disabled
  labels and hover-revealed icons that brighten on interaction.
- Thread Delete and GeoPilot New session proceed without confirmation —
  pre-existing behaviour, unchanged.

**Disabled-with-tooltip audit:** every permanently-disabled control (Share
with lab, Export, Helpful, scope toggle, Account/Settings/Shortcuts/Invite,
Remove-while-locked) sits inside a `<span title="…">` wrapper — the reliable
carrier since disabled buttons don't fire hover for `title` in every browser
— and each tooltip says why / what to do instead, not just that it's off.

**Empty / loading / error states — restyled vs never seen firing**
Restyled by construction (CSS reaches them) but NOT observed firing in this
session (no browser): thread list empty/search-empty/lab-empty; thread
documents loading/empty/failed-doc; KB every phase (reading, needs-input
warnings, indexing, done, error box, bulk queued/processing/skipped/failed,
"Nothing yet."); workspace doc error icon, interpretation error box,
concerns box, history empty; auth 401 error, generic error, registered
notice, forgot confirmation, reset invalid-link/400/429/done; AuthGuard gate;
composer chip error/warning text; toasts; the "Still working…" thinking
escalation (15 s/45 s). Observed only via build/type-check: all of the above.
Genuinely never exercised end-to-end anywhere: vision-derived citation row,
diagram chip thumbnail, format-generation progress notice, group-thread
poll path. These are why DEPLOY-CHECKLIST.md exists.

**Orphans created by this branch (listed, not deleted):**
`sidebar-account.tsx/.module.css` (unmounted since Phase 2; module still
carries 2 legacy hex). Pre-existing dead files untouched: `file-viewer.*`,
`warnings.*`, `handleSSEStream` in chat.tsx. Nothing else was orphaned by
the extractions — every lucide import in chat.tsx is used, `showSources`
was re-purposed as the drawer flag rather than left dangling.

**Checklist:** `design/reference/DEPLOY-CHECKLIST.md` written (8 sections,
~30 checks, failure-first ordering, written for a non-internals reader).

**Build:** pass. **Unit tests:** 7/7 pass.

### Thread documents → persistent side column (2026-08-15, follow-up)

**Files:** `thread-documents.tsx` + `.module.css` (rewritten), `chat.tsx`
(mount moved out of `.chatContainer` to a sibling column in `.container`;
open state persisted; no longer reset on thread switch; menu item toggles),
`DEPLOY-CHECKLIST.md` §2 rewritten.

- **Column, not modal**: a real flex child after `.chatContainer`, which
  already has `flex:1; min-width:0` and so narrows automatically. No
  backdrop, no focus trap, no Escape, `role="complementary"`. Chat and
  composer untouched and fully usable. Only the ⋯ item ("Thread documents" /
  "Hide thread documents", `aria-pressed`) and the header × toggle it.
- **Persistence**: `localStorage` `geotech.threadDocs.open` (`"1"/"0"`) and
  `geotech.threadDocs.width`; both restored in client-only effects (no
  hydration mismatch) and written on change. Thread switch keeps the panel;
  only an in-flight removal preview is dropped.
- **Resize — done (it was straightforward)**: 6px left-edge handle,
  pointer-capture drag (drag left = wider), clamped **280–560px and to
  viewport − 480px** so the chat column keeps room; re-clamped on window
  resize; keyboard ←/→ in 16px steps (`role="separator"` with aria value
  attrs); width persisted on pointer-up / key.
- **Destructive step is the only modal region**: `RemoveConfirm` is
  `role="alertdialog"`, focus lands on Cancel, Tab cycles Cancel↔Delete,
  Escape cancels. Other rows' Remove… buttons disable while a preview is
  showing (one preview at a time). `confirmRemoveSource` reachable only from
  its Delete.
- **Breakpoint 1100px** — 264px sidebar + 280px panel min + ~500px usable
  chat ≈ 1044, rounded up for slack and kept well clear of the 720px
  sidebar-overlay breakpoint so behaviour changes at most once between phone
  and desktop. Below it: `position: fixed` right overlay (400px, max
  100vw−32), backdrop, Escape/backdrop close, no handle. Mode follows a
  `matchMedia` listener live.
- **Overflow fix**: `.filename` was `nowrap + ellipsis` inside a flex column
  lacking `min-width:0`, so long names widened the whole panel. Now
  `overflow-wrap: anywhere` with `min-width:0` on the row/column and
  `overflow-x: hidden` on the body; full name in `title=`.
- Distinctness from "Grounded in" unchanged (see the component header).
