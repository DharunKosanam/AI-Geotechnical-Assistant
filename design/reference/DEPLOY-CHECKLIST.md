# Deploy checklist — dark redesign (`ui/dark-redesign`)

Manual checks against the deployed test build. Everything here can only be
verified in a real browser; each check says what to do, what correct looks
like, and what a failure means. Ordered so the failures that would invalidate
later checks surface first. Tick each box; note anything odd next to it.

You need: a desktop browser (Chrome or Firefox) with DevTools, a phone or the
DevTools device toolbar, one account, and one **thread created before
2026-08-16** (an "old thread"). Sangam: "DevTools" = press F12; the
"Elements/Inspector" tab shows the page structure, the "Computed" sub-tab
shows the final CSS values.

---

## 0. Foundation (if these fail, stop and report — everything else is moot)

- [ ] **0.1 The page loads dark, not white.**
  Open the app. Correct: near-black background (`#0C0B0A`), no white flash
  longer than a blink. Failure: the new bundle isn't being served — the
  instance was not restarted after the rebuild.

- [ ] **0.2 Body text is IBM Plex Sans, not a serif.**
  Open any thread with an assistant reply. In DevTools, right-click a
  sentence in the reply → Inspect → Computed tab → search `font-family`.
  Correct: begins with `"IBM Plex Sans"` (a fallback like `"IBM Plex Sans
  Fallback"` after it is fine). Also glance at the page: text should look
  like a clean geometric sans, not Times/Georgia.
  Failure: the font variable classes are not on `<html>` (Elements tab: the
  `<html>` element should have three classes ending in `__variable`) — the
  layout fix didn't ship.

- [ ] **0.3 Mono where it should be.**
  Look at any timestamp/day header ("TODAY"), a thread turn count ("3 turns"),
  the keyboard hint in the composer. Correct: monospaced (IBM Plex Mono).
  Failure: same root cause as 0.2, or a token miss — report which element.

- [ ] **0.4 The grain overlay doesn't block clicks.**
  Click a sidebar thread, a starter prompt, the send button. Correct: all
  respond. Failure (nothing clickable anywhere): the grain layer is
  intercepting input — report immediately.

---

## 1. Conversation layout and citations

- [ ] **1.1 Conversation column is centred and grows downward.**
  Open a thread with 2–3 turns on a wide window. Correct: a single column,
  ~720px wide, centred in the space to the right of the sidebar; the first
  turn sits at the **top** of the pane; a short thread leaves empty space
  **below** it, not above. Failure (thread pinned to the bottom, or hugging
  the right edge): the layout fix didn't ship.

- [ ] **1.2 Turns read as documents on a rail.**
  Correct: no chat bubbles. Each turn has a small uppercase mono label
  ("YOU" grey, "ASSISTANT" green) and a thin vertical line down the left with
  a tick at each of your turns and a green dot at each assistant turn.
  Failure: old bubble CSS is still winning — report a screenshot.

- [ ] **1.3 Old thread renders sources exactly once.**
  Open the old (pre-2026-08-16) thread; find an assistant answer that had
  sources. Correct: **either** a "**Sources:**" numbered list at the end of
  the answer text **or** a "GROUNDED IN" box under it — never both. Scroll the
  whole thread; count. Failure (both visible on one answer): the legacy-row
  guard is misfiring — report the thread ID and the answer's date.

- [ ] **1.4 Fresh retrieval question shows the panel while streaming AND
  after a hard refresh.**
  New thread → ask a knowledge-base question ("What causes soil
  liquefaction?"). Correct during streaming: text appears token by token
  under an "ASSISTANT" label with three pulsing green dots first; when the
  answer finishes, a "GROUNDED IN · N SOURCES" box appears under it listing
  numbered sources (orange `01`, `02` indices). Then hard-refresh
  (Ctrl+Shift+R / Cmd+Shift+R) and reopen the thread. Correct: the same box
  is still there. Failure during streaming: sources not attaching to the
  streamed answer. Failure after refresh only: the history-load path isn't
  attaching the structured sources — report which of the two failed.

- [ ] **1.5 A knowledge-base source is a link; a thread upload is not.**
  In a "GROUNDED IN" box, a source from the shared library should be
  **underlined and clickable** (opens Google Scholar in a new tab). Now attach
  your own PDF to a thread, ask a question about it, and look at its
  "GROUNDED IN" entry. Correct: the **filename appears as plain text, not a
  link** (by design — your file's title must never be sent to an external
  search). Failure (your upload's title is a link): report immediately, this
  is a privacy rule.

- [ ] **1.6 A vision citation shows the "not verbatim" disclaimer.**
  Attach a scanned/image-only PDF or a PNG (only offered if the deployment
  has vision enabled — if the file picker won't accept images, mark N/A).
  Ask about it. Correct: its "GROUNDED IN" row shows, in small yellow-ish
  mono text on the right, `AI vision · p. N — not verbatim` (or `AI vision
  description — not verbatim` for a bare image). Failure (missing): the
  vision provenance flag isn't reaching the panel — report the filename.

- [ ] **1.7 Message actions.**
  Hover an assistant turn. Correct: Copy / Retry / Helpful appear under it.
  Copy → paste somewhere → the answer text. Retry (last answer only) → the
  same question is re-asked and a new answer streams in. Helpful is greyed
  with a tooltip "Feedback is not available yet" on hover. Failure: report
  which button.

- [ ] **1.8 Streaming has no layout jump.**
  Watch the moment the first token replaces the pulsing dots. Correct: text
  starts exactly where the dots were; nothing above it shifts. Failure:
  report — the pending indicator and the answer turn have drifted apart.

---

## 2. Thread documents panel (source sets)

Only present if source sets are enabled on this deployment — if the ⋯ menu
in the thread's top bar has no "Thread documents" item, mark this whole
section N/A and say so.

- [ ] **2.1 It's a side column, not a popup — and looks different from
  "Grounded in".**
  On a wide window (>1100px), open a thread with at least one uploaded
  document. Click the `⋯` at the right of the thin bar above the conversation
  → "Thread documents". Correct: a column appears on the **right edge**, and
  the conversation **narrows** to make room (it does not get covered). No
  dark backdrop. Titled **"Thread documents"** with a subtitle about files
  uploaded into this thread; documents listed with a status line like
  `4 sections · verbatim text`. It must NOT look like the in-answer
  "GROUNDED IN" box (no orange numbers, not inside a message). Failure:
  report a screenshot.

- [ ] **2.2 The chat stays fully usable with the panel open.**
  With the panel open: scroll the conversation, type in the composer, send a
  question. Correct: all work; clicking anywhere in the conversation does
  **not** close the panel; pressing Escape does **not** close it. Only the
  `×` in the panel header and the ⋯ menu item ("Hide thread documents")
  close it. Failure: report which.

- [ ] **2.3 It remembers open/closed and width.**
  Leave it open, switch to another thread → still open. Reload the page →
  still open (on the thread you land on). Drag its **left edge** (the cursor
  turns to ↔) to make it wider, reload → same width. Close it, reload →
  still closed. Failure: report which didn't persist.

- [ ] **2.4 Long filenames wrap, never spill.**
  Upload a file with a very long name (60+ characters, no spaces). Correct:
  the name wraps onto multiple lines inside the panel; hovering shows the
  full name in a tooltip; the panel never grows wider or scrolls sideways.
  Failure: report a screenshot.

- [ ] **2.5 Remove requires the dry-run preview — and Cancel works.**
  Click **Remove…** on a document. Correct: a red-tinted confirm block
  appears **inside that row** saying "Delete N sections and N document
  record(s) from this thread? Other conversations and the knowledge base are
  not affected." with **Cancel** and **Delete** — **nothing has been deleted
  yet**, and the keyboard focus has jumped to **Cancel** (visible ring).
  Press **Escape**. Correct: the confirm disappears, the document is still
  listed. Do it again and click **Cancel** — same result. Failure (document
  gone after Remove… without the confirm, or after Cancel/Escape): STOP and
  report — the mandatory preview is broken.

- [ ] **2.6 Delete actually removes it (use a throwaway upload).**
  Upload a small test PDF to a scratch thread, then Remove… → Delete.
  Correct: the row disappears; if that file had an attachment chip in the
  composer, the chip disappears too. Failure: report.

- [ ] **2.7 Narrow window: it overlays instead.**
  Shrink the browser under ~1100px wide (or use a tablet). Open the panel.
  Correct: it now slides **over** the conversation from the right with a
  dark backdrop; Escape closes it; clicking the backdrop closes it; there is
  no drag handle. Widen the window again → it snaps back to a side column.
  Failure: report the window width and a screenshot.

---

## 3. Shell: sidebar, top bar, account menu

- [ ] **3.1 Sidebar collapse, both toggles.**
  Click the panel icon at the far left of the top bar; then the one at the
  top-right of the sidebar. Correct: sidebar slides closed/open smoothly
  either way; the conversation re-centres; nothing jumps.

- [ ] **3.2 Day groups, search, Mine/Lab shared.**
  Correct: threads grouped under mono headers TODAY / YESTERDAY / dates.
  Click the magnifier, type part of a thread name → list filters live;
  Escape clears. Click "Lab shared" → only shared threads (or "No lab-shared
  threads yet…"). Note: the default view is **Mine** — shared threads are
  one click away; if that surprises anyone, say so.

- [ ] **3.3 Row ⋯ menu: Rename / Share with lab / Delete.**
  Hover a thread row → `⋯` appears at its right → click. Rename → inline
  edit → Enter → new name shows in the list AND in the bar above the
  conversation if that thread is open. Share with lab → "Share thread ID"
  dialog with a Copy button. Delete → thread is removed (⚠ no confirmation —
  this is unchanged behaviour, use a scratch thread).

- [ ] **3.4 Account menu.**
  Click the round avatar (top-right, your initials). Correct: popover with
  your name, email (mono), a green role badge, four greyed items (Account /
  Settings / Shortcuts / Invite — hover shows "Not available yet"), and Sign
  out. Click outside → closes. Reopen, press Escape → closes and the avatar
  shows a focus ring. Sign out → login page.

- [ ] **3.5 Thread top bar.**
  With a thread open: title, "N turns" in mono, greyed "Share with lab" and
  "Export" (hover each for a tooltip), `⋯`. Open a lab-shared thread: a green
  "LAB SHARED" chip replaces the Share button.

---

## 4. Composer

- [ ] **4.1 Send is disabled when empty; Enter sends; Shift+Enter newlines.**
  Correct: send arrow greyed with nothing typed; type → turns green; Enter
  sends; Shift+Enter adds a line and the box grows (up to ~200px, then
  scrolls). Failure: report which.

- [ ] **4.2 Focus ring on the whole card.** Click into the textarea. Correct:
  the entire composer card gets a green border. Tab away → gone.

- [ ] **4.3 Attach flow and chips.** `+` → (menu with "Upload document" /
  "Draw a diagram" if the diagram editor is enabled, else the file picker
  directly). Attach a PDF. Correct: a chip appears above the textarea with a
  spinner and a small mono stage word (Extracting… / Chunking… / Embedding…),
  then a green check, then a file icon; a notice under it explains you can
  keep chatting. Attach a `.exe` or a >50 MB file. Correct: a small dark
  **toast bottom-right** explains why it was rejected (NOT a browser
  alert box). The `×` on a chip removes it.

- [ ] **4.4 Generate-from-sources pills** (only if source formats are
  enabled). With a ready document: a row "Generate from sources:" with pills.
  Hover a greyed pill while an answer is streaming → tooltip says why.

---

## 5. Diagram editor (only if enabled — else N/A)

- [ ] **5.1 Dark skin + save round-trip.** `+` → Draw a diagram. Correct:
  the editor opens **full-screen with a dark UI** (its own dark toolbar and
  panels; the canvas itself stays white — that's expected). Draw one shape,
  use the editor's own Save/Exit. Correct: the editor closes and a chip with
  a small **thumbnail of your diagram** appears in the composer; clicking the
  thumbnail opens the PNG full size in a new tab. Failure (editor opens light
  / never opens / save does nothing): report exactly which — the `ui=dark`
  parameter is unverified until this passes.

---

## 6. Other routes

- [ ] **6.1 Login / Sign up / Forgot / Reset.** Log out; view each. Correct:
  dark card with the earth-tone stratigraphic stripe down its left, green
  primary button, mono field labels. Submit a wrong password → red error box
  (no browser alert). Sign-up hint "Use at least 8 characters." Forgot →
  after submitting any email, the same neutral confirmation shows (that's
  intentional). Reset with a bad/expired link → "invalid link" state with a
  "Request a new reset link" link.

- [ ] **6.2 Knowledge Base page.** Correct: two dark panels; dashed drop
  zone turns green-bordered on hover; native `<select>` and checkboxes look
  dark (they'll still be OS-styled — acceptable). Upload a small PDF end to
  end: "Reading & checking…" → form → "Indexing…" → success block with a
  green check and a mono sample. Recent uploads list at the bottom.

- [ ] **6.3 GeoPilot page** (only if enabled). Correct: dark two-column
  layout, Documents/History tabs, welcome text with a mono `run CPT` chip.
  Upload a `.CPT` file → doc row with spinner → file icon. Send "run CPT".
  Correct: a dark result card with a table (mono numbers), an orange
  reference line, an "AI INTERPRETATION" section with a yellow "AI DRAFT — FOR
  ENGINEER REVIEW" badge, and an Export to Excel button that downloads a
  file. Errors (e.g. open a run that no longer exists from History) show as
  a **toast**, not an alert.

---

## 7. Phone / narrow viewport (do this on a real phone if you can)

- [ ] **7.1 380px width.** Open the app on a phone in portrait, or in
  DevTools set the device toolbar to 380 × 800. Correct: the sidebar starts
  **closed**; tapping the top-left panel icon slides it **over** the
  conversation (with a shadow) rather than squeezing it; tapping the icon
  again closes it. The composer fits the width; the keyboard-hint text is
  hidden; the send button is reachable. The top-bar segments show icons only.
  A long answer with a table: the table scrolls sideways **inside its own
  box** — the page as a whole must never scroll sideways (try dragging left
  and right on the body). Failure: report a screenshot with the width.

- [ ] **7.2 GeoPilot and Knowledge Base at 380px.** GeoPilot: the document
  panel becomes a short strip on top and the chat sits below it. KB: the
  Project/Type/Year fields wrap onto separate lines rather than squishing.

---

## 8. Motion and accessibility spot checks

- [ ] **8.1 Reduced motion.** OS setting → Reduce motion (macOS:
  Accessibility → Display; Windows: Settings → Accessibility → Visual
  effects → Animation effects off). Reload. Correct: the pulsing dots hold
  still, the sidebar snaps instead of sliding, toasts appear without a
  slide-in.

- [ ] **8.2 Keyboard-only lap.** Put the mouse down. Tab through: sidebar
  New thread → search → Mine/Lab → thread rows → into the conversation →
  composer → send. Correct: every stop shows the **double ring** (thin dark
  gap then a green ring, not the browser's default blue/white outline). Text
  boxes show a green border instead of the ring — that's intended. Failure:
  report any element that gets focus but shows no ring, or shows a
  browser-blue outline.

---

When done: send the ticked list plus screenshots for anything marked failed.
The two checks that block release outright are **2.5** (deletion without
preview) and **1.5** (an upload's title rendered as an external link).
