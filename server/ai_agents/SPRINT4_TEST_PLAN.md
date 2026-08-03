# Sprint 4 Test Plan — Auth, Roles & Deployment Hardening

Validates the four Sprint 4 deliverables (see `sprint4-handover.md` and
`KNOWN_ISSUES.md` #1 and #2):

1. Bearer-token auth on registry-mutating endpoints (`server/routes/fraud.py`)
2. Cross-process lock on `registry.json` writes (`server/ai_agents/tool_registry.py`)
3. Dashboard role gating driven by a real auth source (`client/src/pages/ToolDashboard.jsx`, `client/src/utils/api.js`)
4. Status filter disabled on the "All Categories" overview (`KNOWN_ISSUES.md` #3)

Re-run this whenever `fraud.py`, `tool_registry.py`, or `ToolDashboard.jsx`
change, to catch regressions.

## Phase 0 — Setup

```bash
# From server/, with the venv active and PAYMENTLAB_ADMIN_KEY set (see .env)
python app.py
```

Note the current registry stats before touching anything, so test data can
be reverted afterward:

```bash
curl -s http://localhost:5000/api/fraud/tools | python -c "
import json,sys
d = json.load(sys.stdin)
print(d['statistics'])
"
```

As of Sprint 4 sign-off: `active=9, candidate=24, proposed=4, rejected=1, total=38`.

Set the key as a shell variable for the rest of this plan:

```bash
KEY=$(python -c "import os; print(os.environ.get('PAYMENTLAB_ADMIN_KEY',''))")
# or just paste the value from server/.env directly:
# KEY=your_dev_key_here
```

## Phase 1 — Server-side auth (curl)

```bash
# 1.1 — no header on all three mutating routes -> 401 "Missing bearer token"
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/fraud/tools/propose \
  -H "Content-Type: application/json" -d '[]'
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/fraud/tools/anything/approve
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/fraud/tools/anything/reject

# 1.2 — wrong key -> 401 "Invalid admin key"
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/fraud/tools/anything/approve \
  -H "Authorization: Bearer wrong_key"

# 1.3 — correct key -> not 401 (404 for an unknown tool name is correct/expected here)
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/fraud/tools/anything/approve \
  -H "Authorization: Bearer $KEY"

# 1.4 — reads stay public
curl -s -o /dev/null -w 'GET /tools -> %{http_code}\n' http://localhost:5000/api/fraud/tools
curl -s -o /dev/null -w 'GET /tools/get_transaction_details -> %{http_code}\n' \
  http://localhost:5000/api/fraud/tools/get_transaction_details
```

**Pass criteria:** 1.1 all three return 401; 1.2 returns 401; 1.3 returns
404 (not 401) with body `{"error": "Unknown tool: anything"}`; 1.4 both
return 200.

**1.5 — safe default with no key configured at all:**

```bash
# Stop the server, restart without PAYMENTLAB_ADMIN_KEY set, then:
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/fraud/tools/anything/approve \
  -H "Authorization: Bearer anything-at-all"
```

**Pass criteria:** 401 `{"error": "Admin auth is not configured on this server"}`
— no key ever validates. Restart the server with the key set again before continuing.

## Phase 2 — Registry mutation correctness (curl)

```bash
# 2.1 — propose a valid tool
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/fraud/tools/propose \
  -H "Content-Type: application/json" -H "Authorization: Bearer $KEY" \
  -d '[{"name":"zz_test_tool_a","category":"transaction_context","status":"proposed","source":"external","description":"test","detects":"test","input_schema":{"type":"object","properties":{}},"references":[]}]'

# 2.2 — approve it, then try approving again (should 409 — already candidate)
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/fraud/tools/zz_test_tool_a/approve \
  -H "Authorization: Bearer $KEY"
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/fraud/tools/zz_test_tool_a/approve \
  -H "Authorization: Bearer $KEY"

# 2.3 — propose + reject a second tool, then try rejecting again (should 409)
curl -s -X POST http://localhost:5000/api/fraud/tools/propose \
  -H "Content-Type: application/json" -H "Authorization: Bearer $KEY" \
  -d '[{"name":"zz_test_tool_b","category":"transaction_context","status":"proposed","source":"external","description":"test","detects":"test","input_schema":{"type":"object","properties":{}},"references":[]}]' > /dev/null
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/fraud/tools/zz_test_tool_b/reject \
  -H "Authorization: Bearer $KEY"
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/fraud/tools/zz_test_tool_b/reject \
  -H "Authorization: Bearer $KEY"

# 2.4 — missing required fields -> HTTP 200 at the top level (batch
# endpoint), with the missing-fields error listed in the response's
# "errors" array, not a top-level 400
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/fraud/tools/propose \
  -H "Content-Type: application/json" -H "Authorization: Bearer $KEY" \
  -d '[{"name":"zz_incomplete"}]'

# 2.5 — duplicate name -> error
curl -s -X POST http://localhost:5000/api/fraud/tools/propose \
  -H "Content-Type: application/json" -H "Authorization: Bearer $KEY" \
  -d '[{"name":"zz_test_tool_a","category":"transaction_context","status":"proposed","source":"external","description":"test","detects":"test","input_schema":{"type":"object","properties":{}},"references":[]}]'

# 2.6 — unknown tool -> 404
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/fraud/tools/does_not_exist/approve \
  -H "Authorization: Bearer $KEY"
```

**Pass criteria:** 2.1 → 200, stored/proposed; 2.2 first call 200 →
candidate, second call 409; 2.3 first call 200 → rejected, second call
409; 2.4 → **200** at the top level (batch endpoint — per-item failures
don't fail the request) with `"Missing required fields"` inside the
`errors` array; 2.5 → `"already exists"` in the `errors` array; 2.6 → 404
`"Unknown tool"`.

**2.7 — cleanup** (remove the two test tools so the demo dataset isn't left mutated):

```bash
cd server/ai_agents
python -c "
import json
with open('registry.json', encoding='utf-8') as f:
    data = json.load(f)
before = len(data['tools'])
data['tools'] = [t for t in data['tools'] if not t['name'].startswith('zz_test_tool')]
with open('registry.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2)
print(f'removed {before - len(data[\"tools\"])} test tools, {len(data[\"tools\"])} remain')
"
# Restart the server afterward so its in-memory copy picks up the cleaned file.
```

Confirm stats match the Phase 0 baseline after restart.

## Phase 3 — Cross-process lock

The in-process `threading.Lock` in `fraud.py` is easy to check (two rapid
requests to the same running server — the loser gets a fast 409). The
cross-process case is the one that actually motivated this deliverable
(KNOWN_ISSUES.md #1: two gunicorn workers, each with their own in-memory
`ToolRegistry`, silently losing one worker's write) and needs a real
concurrent-process run to prove.

**3.1 — same-process fast-fail (regression check, unchanged from Sprint 3):**

**This requires a threaded server** — the default `python app.py` dev
server (`app.run(debug=True, port=5000)`, no `threaded=True`) handles one
request at a time, so two curl calls fired back-to-back get processed
*sequentially*, not concurrently. Against that config, both calls will
land after the first has already fully committed, and the second 409s
via the invalid-transition path (`"Cannot transition ... from 'candidate'
to 'candidate'"`) instead of the lock-contention path — a false negative
for this specific check (the registry is still safe either way, just via
a different code path than 3.1 is meant to exercise).

To actually reach the `threading.Lock` fast-fail message, run a throwaway
instance with `threaded=True`:

```python
# server/_phase31_runner.py (throwaway — delete after use)
from app import create_app
app = create_app()
app.run(port=5552, debug=False, threaded=True)
```

Then fire two requests that are genuinely concurrent (not just
back-to-back) — e.g. two Python threads released by a shared
`threading.Barrier`, both `POST`ing `/api/fraud/tools/<a-proposed-tool>/approve`
at the same instant. Expect one 200 and one 409
`"Registry is being updated by another request, try again"`. Revert the
tool's status and delete the throwaway script/log afterward.

**3.2 — actual cross-process test:**

Run `_test_concurrent_lock.py` (in this directory) with the server
**stopped** (it operates on `registry.json` directly, not through Flask):

```bash
cd server/ai_agents
python _test_concurrent_lock.py
```

The script spawns two separate OS processes, each importing `ToolRegistry`
independently and calling `propose_tool()` for a distinct dummy tool name
at roughly the same time, then reloads the file fresh and asserts both
entries survived and the JSON is well-formed. See script docstring for
exact mechanics. It cleans up its own test tools and exits non-zero with a
diagnostic if anything is lost or corrupted.

**Pass criteria:** exits 0, both dummy tools present, `registry.json`
still valid JSON, `.lock` sidecar file was created next to it.

## Phase 4 — Dashboard role gating (browser)

With the server running and the client dev server up (`npm run dev` in
`client/`, or via the project's preview tooling):

1. Open `/tools` in a browser with no `paymentlab_admin_key` in
   localStorage. **Expect:** "Admin sign-in" button; stat pills show
   Active/Candidate/Total only (no "Proposed"); drilling into any category
   shows a "Details" column (not "Action"), no Approve/Dismiss buttons,
   and proposed/rejected tools absent from both the table and the sidebar
   per-category counts.
2. On the "All Categories" overview, confirm the status filter `<select>`
   is disabled. Click into a category — confirm it becomes enabled, and
   that its options are only All/Active/Candidate (no Proposed/Rejected).
3. Click "Admin sign-in", enter the correct key (from `server/.env`).
   **Expect:** button becomes "Admin ✓ (log out)"; "Proposed" stat pill
   appears; proposed tools become visible with Approve/Dismiss buttons.
4. Click Approve on a proposed tool. **Expect:** it moves to Candidate,
   counts update, no full page reload. Revert it afterward (see Phase 2
   cleanup pattern — flip its status back to `proposed` directly in
   `registry.json` if you don't want to leave it changed).
5. Log out via localStorage manually to simulate a bad key
   (`localStorage.setItem('paymentlab_admin_key', 'wrong')`), reload, then
   attempt Approve. **Expect:** 401, an alert, and the dashboard reverting
   to the viewer view (key cleared from localStorage).
6. Click "Admin ✓ (log out)" while validly signed in. **Expect:**
   immediate revert to viewer state, no reload needed.
7. Sign in again, refresh the page. **Expect:** stays admin (key persisted
   in localStorage).

## Phase 5 — Regression checks

1. `server/ai_agents/fraud_investigator.py` and `tool_scanner.py` were not
   touched this sprint — spot check that an investigation still runs
   end-to-end (`POST /api/fraud/investigate/<txn_id>`) and the scanner CLI
   still works, if either is easy to exercise in the current environment.
2. Sidebar proposed-count per category (Sprint 3 feature, referenced in
   the handover as "already done, confirm no regression") still renders
   correctly for admin — covered incidentally by Phase 4 step 3.
3. After Phases 2 and 3, confirm `registry.json` is still valid JSON and
   `python -c "import json; json.load(open('registry.json'))"` doesn't
   raise.

## Phase 6 — Deployment note (Render)

Not executable locally:

1. `PAYMENTLAB_ADMIN_KEY` must be set as its own Render environment
   variable — the value in local `server/.env` is dev-only and should not
   be reused there.
2. The POSIX branch of `_CrossProcessLock` (`fcntl.flock`) only runs on
   Linux — Windows dev exercises the `msvcrt` branch instead (covered by
   Phase 3). Sanity-check the Render deployment specifically once it's
   live, e.g. by firing two near-simultaneous approve clicks against the
   deployed dashboard and confirming both survive.
