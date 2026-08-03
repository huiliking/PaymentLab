# Sprint 4 Test Report — Auth, Roles & Deployment Hardening

**Run date:** 2026-07-28
**Executed against:** `master` @ working tree with Sprint 4 changes (uncommitted at time of test)
**Environment:** Windows 11 dev machine, Flask dev server (`python app.py`), Vite dev client, curl + browser automation
**Procedure followed:** `SPRINT4_TEST_PLAN.md`, all 6 phases

## Result: PASS

All 12 validation criteria from `sprint4-handover.md` hold. One design nuance and one environment quirk are called out below — neither is a defect in the Sprint 4 changes themselves.

---

## Phase 0 — Setup

Baseline registry stats recorded and confirmed restored at the end of every phase:

```
active=9  candidate=24  proposed=4  rejected=1  total=38
```

## Phase 1 — Server-side auth

| # | Check | Result |
|---|---|---|
| 1.1 | `/propose`, `/approve`, `/reject` with no `Authorization` header | **PASS** — all three 401 `"Missing bearer token"` |
| 1.2 | Same three with `Authorization: Bearer wrong_key_12345` | **PASS** — 401 `"Invalid admin key"` |
| 1.3 | Same with correct key, unknown tool name | **PASS** — 404 `"Unknown tool: anything"` (not 401 — auth passed, then normal 404) |
| 1.4 | `GET /api/fraud/tools` and `GET /api/fraud/tools/<name>` with no auth | **PASS** — both 200 |
| 1.5 | Server started with `PAYMENTLAB_ADMIN_KEY` unset entirely | **PASS** — 401 `"Admin auth is not configured on this server"`, with or without an `Authorization` header |

**Note on 1.5 methodology:** the primary dev server on port 5000 couldn't be cleanly restarted mid-test (see Appendix A), so this check ran against an isolated throwaway instance on port 5551, started with `PAYMENTLAB_ADMIN_KEY` commented out of `server/.env`. Same code path, same result. `.env` was restored to its original value immediately after.

## Phase 2 — Registry mutation correctness

| # | Check | Result |
|---|---|---|
| 2.1 | Propose a valid tool | **PASS** — 200, stored with `status: "proposed"` |
| 2.2 | Approve it, then approve again | **PASS** — first 200 → `candidate`; second 409 `"Cannot transition 'zz_test_tool_a' from 'candidate' to 'candidate'. Allowed from 'candidate': none"` |
| 2.3 | Propose + reject a second tool, then reject again | **PASS** — first 200 → `rejected`; second 409 with the equivalent message |
| 2.4 | Propose with missing required fields | **PASS** (see nuance below) — `errors: [{"name": "zz_incomplete", "error": "Missing required fields: category, status, source, description, detects, input_schema, references"}]` |
| 2.5 | Propose a duplicate name | **PASS** — `errors: [{"name": "zz_test_tool_a", "error": "Tool 'zz_test_tool_a' already exists in registry"}]` |
| 2.6 | Approve/reject an unknown tool name | **PASS** — 404 `"Unknown tool: does_not_exist"` |
| 2.7 | Cleanup | **PASS** — both test tools removed, registry back to baseline |

**Design nuance found (update test plan wording):** `/propose` always returns **HTTP 200** at the top level, even when every proposal in the batch fails — errors are reported per-item in the `errors` array (`stored: []`, `errors: [...]`). This is correct batch-endpoint behavior (some proposals in a batch can succeed while others fail), just not literally "400" as originally described in the test plan. No code change needed; the plan doc's wording was imprecise, not the endpoint.

## Phase 3 — Cross-process lock

| # | Check | Result |
|---|---|---|
| 3.1 | Two near-simultaneous requests to the same running process | **PASS, with a caveat** — one 200, one 409, but via the *invalid-transition* path (`"Cannot transition ... from 'candidate' to 'candidate'"`), not the *"Registry is being updated by another request"* lock-contention message. The Flask dev server (`app.run(debug=True, port=5000)`, no `threaded=True`) processes requests one at a time by default, so the two curl calls landed sequentially rather than truly overlapping inside the same process — the `threading.Lock` fast-fail path in `fraud.py` was never actually contended. **Either way, the outcome is safe** (no double-approve, no corruption) because `update_status()` reloads fresh state before checking the transition. This is a property of the dev server's concurrency model, not a defect; the fast-fail 409 message would be reachable under `threaded=True` or gunicorn with multiple threads/workers. |
| 3.2 | Real cross-process concurrency (`_test_concurrent_lock.py`) | **PASS** — run twice for confirmation. Two independent OS processes, synchronized via `multiprocessing.Barrier`, called `propose_tool()` simultaneously for distinct dummy tools. Both writes survived, `registry.json` stayed valid JSON, `.lock` sidecar file was created. Exit code 0 both runs. **This is the test that actually proves the KNOWN_ISSUES.md #1 lost-update race is fixed** — reproduces the exact scenario (separate processes, each with their own in-memory `ToolRegistry`) that a same-process test cannot. |
| 3.3 | `.lock` sidecar file gitignored | **PASS** — confirmed present in `.gitignore` (`*.lock`), file removed from working tree after each test run |

## Phase 4 — Dashboard role gating (browser)

Ran against a fresh client (`npm run dev`) + fresh server pair, localStorage cleared before the viewer checks.

| # | Check | Result |
|---|---|---|
| 4.1 | Fresh browser, no stored key → viewer view | **PASS** — "Admin sign-in" button; stat pills show Active(9)/Candidate(24)/Total(38) only, no "Proposed" pill; sidebar per-category counts show only active/candidate |
| 4.1b | Drill into a category with proposed tools | **PASS** — "DETAILS" column header (not "Action"); no Approve/Dismiss buttons; `ddos_attack` and `app_fraud` (both proposed) absent from the 4-row table (category total badge still says 6, table shows 4 — confirms server-side count vs. client-filtered list are intentionally different, per the documented design decision) |
| 4.2 | Status dropdown disabled on "All Categories" overview | **PASS** — confirmed via `document.querySelector('select').disabled === true`; became enabled after selecting a category; dropdown options limited to All/Active/Candidate for a viewer (no Proposed/Rejected options) |
| 4.3 | Sign in with correct key | **PASS** — button becomes "Admin ✓ (log out)"; "Proposed" stat pill (4) appears; dropdown gains Proposed/Rejected options; sidebar shows proposed counts per category; drilling into Transaction Context now shows 6 rows including `ddos_attack`/`app_fraud` as "Proposed" with Approve/Dismiss, and the header reads "ACTION" |
| 4.4 | Click Approve on `ddos_attack` | **PASS** — `POST /api/fraud/tools/ddos_attack/approve` → 200; row updates to "Candidate" with only "See details"; stat pills update live (candidate 24→25, proposed 4→3); **no page reload** (confirmed via unchanged URL/scroll state and immediate DOM update). Reverted afterward. |
| 4.5 | Attempt Approve with a stale/wrong key | **PASS** — set `localStorage` to a wrong key without reloading (so the still-rendered admin UI sends the wrong key on the next request, simulating a key that stopped being valid); `POST .../approve` → 401; `alert("Admin key is missing or invalid. Please re-enter it.")` fired; `localStorage` key cleared (`null`); dashboard **immediately reverted to the viewer view with no reload** — "Admin sign-in" button back, "DETAILS" header, proposed tools dropped back out of the visible list |
| 4.6 | Explicit "Admin ✓ (log out)" click | **PASS** — immediate revert to viewer state, `localStorage` cleared, no reload |
| 4.7 | Refresh with a valid key stored | **PASS** — reloading with a valid key in `localStorage` re-entered the admin view automatically |

## Phase 5 — Regression checks

| # | Check | Result |
|---|---|---|
| 5.1 | `fraud_investigator.py` untouched | **PASS** — `git diff --stat` shows zero changes |
| 5.2 | `tool_scanner.py` untouched | **PASS** — `git diff --stat` shows zero changes |
| 5.3 | Sidebar proposed-count per category (Sprint 3 feature) still renders | **PASS** — confirmed incidentally in Phase 4.3 |
| 5.4 | `registry.json` still valid JSON, matches Phase 0 baseline exactly after all test mutations | **PASS** — `active=9 candidate=24 proposed=4 rejected=1 total=38`, byte-for-byte consistent with Phase 0 (no leftover diff in `git status`) |

## Phase 6 — Deployment note

Not executable locally, as expected — flagged for a post-deploy check:
- `PAYMENTLAB_ADMIN_KEY` needs its own value set as a Render env var (the dev `.env` value must not be reused there).
- The POSIX (`fcntl.flock`) branch of `_CrossProcessLock` only runs on Linux; Phase 3 here only exercised the Windows (`msvcrt`) branch. Recommend firing two near-simultaneous approve clicks against the live Render URL once deployed, as a real-world sanity check of the untested branch.

---

## Appendix A — Environment quirk encountered during testing

Mid-test, the dev server process bound to port 5000 became unresponsive to process management: `Get-Process`, `tasklist /FI`, and `Get-CimInstance Win32_Process` all reported the PID netstat listed as owning port 5000 (`25124`) did not exist, yet the port was actively serving correct HTTP responses. `Stop-Process -Force` and `taskkill /F` both failed with "process not found" against that PID. This is a pre-existing sandbox/dev-environment characteristic (likely a process-namespace boundary between the PowerShell tool and the actual listener), not something introduced by Sprint 4.

**Workaround that worked:** touching a `.py` file already imported by the running app (`server/app.py`) triggered Werkzeug's stat-based auto-reloader (the app runs with `debug=True`), which cycled the process internally and released the port cleanly. Worth remembering if this recurs.

## Appendix B — Cleanup performed

- All `zz_test_*` / `zz_concurrent_test_*` / `zz_incomplete` tool entries removed from `registry.json`.
- `ddos_attack` reverted from `candidate` back to `proposed`.
- `malware_infection` reverted from `candidate` back to `proposed` (touched during a same-process lock test).
- `registry.json.lock` sidecar removed after each concurrency test run.
- `server/.env`'s `PAYMENTLAB_ADMIN_KEY` line restored after the Phase 1.5 isolated-instance test.
- Final state verified to exactly match the Phase 0 baseline; `git status` shows no diff on `registry.json` or `.env`.
