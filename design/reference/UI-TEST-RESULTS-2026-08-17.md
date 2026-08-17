# UI test results — dark redesign, 2026-08-17

Automated run of `DEPLOY-CHECKLIST.md` in headless Chromium (Chrome for
Testing 145 headless shell via the repo's @playwright/test library; scripts
kept in the session scratchpad, nothing added to `tests/`). Explicitly
authorised by Dharun for this run — the standing no-browser-automation rule
still applies afterwards.

**Two targets.** §0/§1(GENERAL answer)/§3/§4 ran against the **deployed build
on :3000** (production service, restarted by Dharun 15:38 after the last
build). Everything after the first fixes ran against an **isolated copy of the
working tree on :3005** (same backend, own `.next`, webpack build) so
production was never rebuilt or restarted. Anything marked *(fix)* is in the
working tree only and needs a redeploy.

Account used: `ui.redesign.test.20260817@example.com` (throwaway, created via
the real signup page). Viewport 1440×900 unless noted; 380×800 for §7.

Legend: ✅ pass · ❌ fail (product) · ⚠ probe artifact (product OK on
inspection) · ➖ not testable here

## §0 Foundation
| # | Check | Result | Evidence |
|---|---|---|---|
| 0.1 | Page loads dark | ✅ | body `rgb(12,11,10)` |
| 0.2 | Message body font | ✅ | `.assistantBody` computed `"IBM Plex Sans", "IBM Plex Sans Fallback"…` 14.5px |
| 0.3 | Mono where it should be | ✅ | role labels, day header, turn count, kbd hint all `"IBM Plex Mono"` |
| 0.4 | Grain doesn't intercept | ✅ | `pointer-events:none`; every click in the run landed |

## §1 Conversation
| # | Check | Result | Evidence |
|---|---|---|---|
| 1.1 | Column ≤720 centred, top-aligned | ✅ | turn 748px (720+rail), left/right gap 209/219 in the pane, first turn 8px from scroller top |
| 1.2 | Rail: accent dot / hairline tick | ✅ | `::after` dot `rgb(99,174,147)`, tick `rgba(255,255,255,.1)` |
| 1.3 | Old thread renders sources once | ➖ | this account has no pre-cutover thread; a fresh answer showed 0 legacy blocks + 1 panel. **Run manually on a real pre-2026-08-16 thread.** |
| 1.4 | Fresh retrieval question → panel while streaming + after hard refresh | ✅ | KB question: first text 16s, panel "GROUNDED IN · 4 SOURCES"; after reload+reopen: exactly 1 panel. Note: a GENERAL-routed question ("What causes soil liquefaction?") correctly shows **no** panel — its `sources` is `[]`. |
| 1.5 | KB source linked; upload NOT linked | ✅ | 4/4 KB rows → `scholar.google.com`; upload cited as plain "test doc short" (`link:false`) |
| 1.6 | Vision citation disclaimer | ➖ | not exercised (no image/scanned upload in this run; vision IS enabled — picker accepts PNG/JPG/WEBP). **Manual.** |
| 1.7 | Copy / Retry / Helpful | ✅ | hover reveals; Copy → clipboard 3765 chars; Helpful disabled with "Feedback is not available yet"; Retry present on last answer (not clicked — would re-ask) |
| 1.8 | No layout shift at stream start | ✅ | ASSISTANT label y 181 → 181 |
| — | Streaming indicator | ✅ | `role=status` visible; three `--accent` dots (colour re-probed with exact class) |
| — | Panel treatment | ✅ | `--s1` inset, `--oxide` indices, mono GROUNDED IN eyebrow, meta "legacy · v1 · Legacy KB Import" |

## §2 Thread documents (side column)
| # | Check | Result | Evidence |
|---|---|---|---|
| 2.1 | Column, not popup; distinct from Grounded-in | ✅ | panel x1080–1440 (360px), chat narrows 1176→816, no backdrop; header "Thread documents", `--s1`, zero citation indices |
| 2.2 | Chat usable; click/Escape don't close; × closes | ✅ | typed in composer with panel open; conversation click + Escape kept it open; header × closed it |
| 2.3 | Open + width persist (reload AND thread switch) | ✅ | open on other thread after reload; drag 360→483, reload → 483; `localStorage` `open="1"`, `width="483"`; keyboard ← → 499 |
| 2.4 | Long filename wraps | ✅ | 122-char name wraps to 2 lines, right edge 1416 ≤ 1440, no horizontal scroll, `title` = full name |
| 2.5 | Remove… → mandatory preview; Cancel/Escape | ✅ | "Delete 3 sections and 1 document record…" appears in-row; focus on Cancel; Tab cycles Cancel↔Delete; Escape cancels; Cancel cancels; doc still listed both times; other Remove disabled meanwhile |
| 2.6 | Delete removes | ✅ | row gone; matching chip gone |
| 2.7 | <1100px overlay | ✅ | at 1000px: `position:fixed`, backdrop, no handle; Escape closes; backdrop click closes; back to column at 1440 |

## §3 Shell
| # | Check | Result | Evidence |
|---|---|---|---|
| 3.1 | Collapse both toggles | ✅ | 264→0→264→0→264 |
| 3.2 | Day groups / search / Mine–Lab | ✅ | "Today"; search filters + empty copy; Escape closes; Lab shared empty copy |
| 3.2e | Shared thread stays in Mine *(fix)* | ✅ (fix) | was: sharing your own thread made it vanish from the default list |
| 3.3 | Row ⋯: Rename / Share / Delete | ✅ | rename updated list AND open-thread title; Share opened dialog with `thread_…` id; Escape closed |
| 3.4 | Account menu | ✅ | identity, role badge, 4 disabled+tooltips, Sign out; outside click closes; Escape → focus on avatar |
| 3.4d | …with the double ring *(fix)* | ✅ (fix) | was: `--hi` box-shadow on raised controls beat the global ring |
| 3.5 | Sub-header | ✅ | title, "1 turn" mono, Share/Export disabled with tooltips |
| 3.5d | LAB SHARED chip right after sharing *(fix)* | ✅ (fix) | was: chip only after re-selecting the thread |

## §4 Composer
| # | Check | Result | Evidence |
|---|---|---|---|
| 4.1 | Send disabled empty; Enter sends; Shift+Enter grows | ✅ | 40→55px on Shift+Enter; disabled while awaiting |
| 4.2 | Card accent border on focus | ✅ | visible in screenshot (probe measured mid-transition — artifact) |
| 4.3 | Chips + rejected type → toast | ✅ | `.exe` → toast "…not a supported file type. Supported: PDF, DOCX, XLSX, XLS, CSV, PPTX, PNG, JPG, WEBP", zero native dialogs all run; PDF chip: mono stage → ready |
| 4.4 | Format pills | ✅ (seen) | "Generate from sources: Study guide · Briefing doc · FAQ · Timeline · Key terms" rendered once a doc was ready (not clicked — multi-minute generation) |

## §5 Diagram editor
| # | Check | Result | Evidence |
|---|---|---|---|
| 5.1 | ui=dark + save round-trip | ✅ | iframe src `…embed=1&ui=dark&…`; no init-timeout toast; protocol export (load XML → export xmlpng) → editor closed → chip with white-ground PNG thumbnail "UI test". *Actual in-editor Save click not driven (cross-origin UI); the app-side round-trip is proven.* |

## §6 Other routes
| # | Check | Result | Evidence |
|---|---|---|---|
| 6.1 | Auth pages | ✅ | signup short-password inline error, registered notice, wrong-password inline error, login — all styled, no alerts |
| 6.2 | KB page + upload end-to-end | ✅ | `--s2` panels, dashed dropzone, condensed heading; test PDF → form → indexed → success block (mono sample, accent check) → **deleted via its delete button** |
| 6.3 | GeoPilot page | ✅ render / ➖ run | tabs, empty states, condensed heading, mono chip verified; **`run CPT` not exercised (no .CPT fixture) — manual.** |

## §7 380px
| # | Check | Result | Evidence |
|---|---|---|---|
| 7.1 | Chat at 380 | ✅ | sidebar starts collapsed → toggles as fixed overlay; page scrollWidth 380; kbd hint hidden; nav icons-only; disabled header buttons hidden; thread with panel: no overflow |
| 7.2a | GeoPilot stacks *(fix)* | ✅ (fix) | was: doc panel stacked but stayed 260px (media block preceded base rule) |
| 7.2b | KB at 380 | ✅ | no overflow |

## §8 Motion + keyboard
| # | Check | Result | Evidence |
|---|---|---|---|
| 8.1 | Reduced motion | ✅ | transitions computed `1e-05s` (=0.01ms) |
| 8.2 | Keyboard lap | ✅ | 18 Tab stops, every non-text stop shows the double ring, zero browser outlines |

## Bugs found by this run (all fixed in the working tree, uncommitted)
1. **Focus ring lost on raised controls** — components' `box-shadow: var(--hi)` beat the global `:focus-visible` ring by cascade order. `globals.css` now uses `html :focus-visible:not(input,textarea,select) { …!important }`.
2. **LAB SHARED chip didn't appear after sharing from the row menu** — new `onThreadShared` callback (thread-list → sidebar → chat) sets `isGroupConversation` for the open thread.
3. **Sharing your own thread removed it from "Mine"** — filter changed: Mine = all own threads, Lab shared = shared subset.
4. **GeoPilot at 380px: doc panel stacked but stayed 260px wide** — media block moved to the end of `workspace.module.css`.

## Not covered (manual)
Pre-cutover thread (1.3), vision citation (1.6), Retry click, format generation, GeoPilot `run CPT`, in-editor Save click, forgot/reset-password flows beyond render.

## Test artefacts to clean up (prod Mongo)
- user `ui.redesign.test.20260817@example.com`
- threads `thread_bfa3c8f8b38f4de29bf511c77321d020` ("Renamed by UI test", lab-shared) and `thread_84f7f834c6984d1387359e786874ad2b` (lab-shared; docs `test-doc-short.pdf`, diagram `page-1-8mhy3q.png`)
- KB: the test upload was deleted through the UI (verified gone from Recent uploads).
