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
