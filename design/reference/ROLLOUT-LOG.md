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
