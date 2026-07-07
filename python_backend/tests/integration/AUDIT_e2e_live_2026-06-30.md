# GeoTech AI — Live End-to-End Audit Report

**Date:** 2026-06-30
**Target:** local backend `http://127.0.0.1:8000` (FastAPI, un-mocked, real Ollama generation)
**Harness:** `python_backend/tests/integration/test_e2e_live.py` (HTTP-layer only; no browser automation)
**Backend build under test:** num_ctx fix DEPLOYED — service restarted 15:14 (after the 14:46 edit); `ollama ps` showed `CONTEXT 8192`.

---

## 1. Summary

**33 passed / 1 failed / 0 skipped (34 checks).**

The single failure (**B2**) was a **test artifact, not an application bug** — see §2. With the test corrected, the suite is effectively **34/34 green**. No critical or major application defects were found in the tested surface. Three notable **risks/weak spots** are flagged in §3 (one of them — concurrency-induced 504 — is operationally significant).

**Headline result — the regression fix is validated end-to-end:**

| Guard | Old behavior | Result now |
|---|---|---|
| **A1 multi-turn overflow** (9 sequential turns, history grown past the old ~3k break point) | answers collapsed to "Based" / blank | **all 9 turns substantive** (440–5,248 chars); no truncation |
| **A3 slow heavy EICP query** | risk of hang | completed **44.7s**, 6,489 chars |
| **VRAM at 8192 ctx** | unknown | **6,762 MiB / 12,288 MiB used, 3,804 free, 100% GPU** — no OOM, no spill |

---

## 2. Failures

### B2 — "unknown email returns 422, wrong password returns 401" — **NOT A BUG (test artifact)** — Severity: N/A
- **What happened:** the test's unknown-email probe used `...@test.local`. Pydantic `EmailStr` rejects special-use/reserved domains (`.local`, `example.com`) and malformed addresses with **HTTP 422** *before* the login handler runs, so it never reached the credential check.
- **Evidence:**
  ```
  unknown @test.local   -> 422  "...special-use or reserved name that cannot be used with email."
  malformed "notanemail"-> 422  "An email address must have an @-sign."
  known email/bad pass  -> 401  "Incorrect email or password"   (auth.py:104-109)
  ```
  Reading `app/routers/auth.py:104-109`, the handler returns the **identical** generic `401 "Incorrect email or password"` for both *unknown-but-valid* email and *wrong password*. The anti-enumeration property **holds**.
- **Resolution:** fixed the **test** (now uses a valid `@gmail.com` unknown address). No app change. The 422-vs-401 distinction only reveals "this string isn't a well-formed email" — not account existence — so it is not an enumeration vector.

---

## 3. Risks & weak spots (tests passed, but worth attention)

### R1 — Concurrency-induced 504 under real load — **Severity: MAJOR (operational)**
- **Evidence (D1, 6 concurrent /chat):** `latency min=25.6s median=89.2s max=148.2s`, status mix all 200 at the *backend*.
- **Why it matters:** Ollama on the single ~10.5 GB MIG slice serves generations **serially** (only ~3.8 GB free at 8192 ctx — no room for a second parallel context). Concurrent requests **queue**, so the 6th request waited **148s**. That **exceeds nginx `proxy_read_timeout 120s`** — so through the public path, concurrent users would get the **nginx 504** (the original Symptom A) even though the backend eventually succeeds. The num_ctx fix addresses single-user truncation; it does **not** address queueing latency under concurrency.
- **Suggested direction (do NOT implement):** raise nginx `proxy_read_timeout` to ≥ the Next route's 180s (currently 120s < 180s < 300s — nginx is the tightest link); and/or bound concurrency with a request queue + user-facing "busy" feedback; and/or tune `OLLAMA_NUM_PARALLEL`/scale the slice. At minimum, align the timeout chain.

### R2 — num_ctx headroom is thin — **Severity: MINOR**
- Worst-case prompt was estimated at **~7.3k tokens**; `num_ctx=8192` leaves only **~890 tokens** of margin, and `num_predict=2048` output must also fit alongside input within the window. A long history (cap is 6000 tokens) plus large retrieved chunks could re-approach the limit and squeeze output again.
- **Suggested direction:** consider `num_ctx=12288` (VRAM has ~3.8 GB free — a bigger KV cache likely still fits; verify), or lower `HISTORY_TOKEN_CAP`, or log input-token estimates to watch the margin. (Tunable via the `OLLAMA_NUM_CTX` env constant already added.)

### R3 — Raw Ollama client has no request timeout — **Severity: MINOR**
- The answer/rewriter calls use `ollama.AsyncClient(...).chat(...)` with **no `request_timeout`** (the llama-index `Ollama(request_timeout=120)` object is built but unused on this path). A wedged generation has no app-level ceiling; only nginx (120s) and the Next route (180s) bound it. Under R1 queueing this compounds.
- **Suggested direction:** pass an explicit client timeout so a stuck call fails fast with a clean error instead of holding a worker.

### R4 — 500 error bodies echo internal exception text — **Severity: MINOR (info hygiene)**
- Multiple handlers return `detail=f"Failed to ...: {str(error)}"` (e.g. `chat.py:355`, several in `files.py`). On an unexpected error this leaks internal messages (stack-adjacent strings, driver errors) to clients.
- **Suggested direction:** return a generic message to the client; keep `str(error)` in server logs only. (Health/readiness already do this correctly — they never leak.)

### R5 — Orphaned chat messages on thread delete — **Severity: MINOR (data hygiene)**
- `DELETE /api/assistants/threads/history` removes the `conversations` doc and in-memory state but **not** the `messages_collection` rows. There is no API to delete messages, so they accumulate (observed: the test left ~18 messages under user A that cleanup could not remove).
- **Suggested direction:** cascade-delete messages on thread delete, or add a maintenance task.

### R6 — Legacy live test suite is dead — **Severity: MINOR (maintenance)**
- `tests/integration/test_e2e.py` (12 tests) predates Phase-4 auth and sends no token → every protected call now 401s. It provides false confidence if anyone runs it.
- **Suggested direction:** update it to authenticate, or remove it in favor of the new `test_e2e_live.py`.

### R7 — Per-IP login lockout can affect shared NAT — **Severity: MINOR**
- `RATE_LIMIT_LOGIN=5/minute` is keyed by client IP. Multiple legitimate users behind one NAT/VPN egress (plausible on a campus network) share the budget; a few bad attempts lock out others for ~1 min. Functionally correct, but note the blast radius.

---

## 4. What passed (coverage confirmation)

- **Auth/security:** unauth → 401 on all protected endpoints (B1); httpOnly + SameSite cookie (B5); login lockout → 429 with Retry-After (B3); **per-user isolation holds** — User B sees 0 of User A's messages, A's thread absent from B's list, and 404 on A's file (B4).
- **Edge inputs (no 500):** empty / whitespace / 5,000-char / injection-ish (`<script>`, `'; DROP`, `{{7*7}}`, "ignore previous instructions") all handled with 200; **non-Latin Chinese** query → 1,774-char English answer (translation guard holds); off-topic query → graceful 224-char refusal (not blank).
- **Concurrency:** 6 concurrent → 0 server-errors, 0 hangs (latency caveat in R1).
- **Health:** `/health` 200; `/health/ready` 200 with `mongo=true, redis=true`.
- **Upload path:** upload → ingest reached `ready` → deleted cleanly (1 chunk), `user_upload` only. **KB (16,811 chunks) never touched.**

---

## 5. MANUAL UI smoke checklist (browser — run by hand)

Not covered by the API suite (deliberately no browser automation). Please verify:

- [ ] **Scroll holds** at 10+ messages in a thread (no jump-to-top; the scroll fix behaves).
- [ ] **Header, sidebar, and input box stay fixed** while the message list scrolls.
- [ ] **New chat** creates a fresh thread and clears the view.
- [ ] **Thread switch** loads the correct history for each thread.
- [ ] **Login / logout** round-trips; after logout, protected views redirect/deny.
- [ ] **File upload** shows the processing spinner, then flips to ready; the file appears in the list.
- [ ] **Narrow viewport** (mobile width) layout — sidebar collapses, input usable, no overflow.
- [ ] **Long multi-turn chat in the browser** (the A1 scenario) — confirm full answers render, not "Based". (API-validated, but confirm the rendered UI path too.)
- [ ] **Sources block** renders with clickable links and correct file-type icons.

---

## 6. How to re-run

```bash
cd python_backend
export TEST_USER_A_EMAIL=...  TEST_USER_A_PASSWORD=...
export TEST_USER_B_EMAIL=...  TEST_USER_B_PASSWORD=...
venv/bin/python tests/integration/test_e2e_live.py            # full run (~3-4 min of live LLM)
venv/bin/python tests/integration/test_e2e_live.py --no-upload
# pytest:  RUN_LIVE_E2E=1 venv/bin/pytest tests/integration/test_e2e_live.py -s
```

Run when the app is idle (shared MIG slice). Exit code 0 = all pass, 1 = any fail.
