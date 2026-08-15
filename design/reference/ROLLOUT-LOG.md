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
