# Known Issues — Tool Registry / Scanner (Sprint 3)

Acknowledged but not fixed as of the Sprint 3 commit. Found during a full
code review before commit; the fixes below were deferred as lower priority
than the correctness bugs that were fixed in the same pass (see git log for
that commit's message).

**Issues 1–3 below were fixed in Sprint 4** (commit `726e780`,
`server/ai_agents/SPRINT4_TEST_REPORT.md` has the validation results) —
left in place as a historical record of what the fix addressed. See
"Known Limitations" at the bottom: #4 is an accepted design trade-off,
#5 was a real testing gap that's since been closed (2026-08-02, via
WSL2).

## 1. `_registry_lock` doesn't synchronize across multiple processes

`server/routes/fraud.py` uses a `threading.Lock` to serialize writes to
`registry.json` from the `/propose`, `/approve`, and `/reject` routes. This
only protects against races between threads in one process. `requirements.txt`
ships `gunicorn`, which is meant to run multiple worker *processes* — each
worker would have its own lock and its own in-memory `ToolRegistry` instance.
Two concurrent registry-mutating requests landing on different workers can
each read the same on-disk file, mutate their own copy, and write it back —
the second write silently overwrites the first (lost update), even though
the API's 409 response implies mutual exclusion is guaranteed.

**Fix direction:** move the locking into `ToolRegistry` itself (e.g. a file
lock around `_persist()`, or switch the registry to a real datastore with
its own concurrency control) so every caller — routes, future scripts, the
scanner if it's ever wired to write directly — gets serialization
automatically instead of each call site needing to remember a
threading.Lock that doesn't actually help under gunicorn's process model.

**Until fixed:** don't run this behind more than one gunicorn worker, or
accept that concurrent registry mutations can lose data.

## 2. No authentication/authorization on registry-mutating endpoints

`POST /fraud/tools/propose`, `/approve`, and `/reject` have no auth check at
all. The only "admin" gate anywhere in the system is a hardcoded frontend
constant (`isAdmin = true` in `client/src/pages/ToolDashboard.jsx`) that
controls whether the Approve/Dismiss *buttons render* — it has no bearing on
whether the underlying API calls succeed. Anyone who can reach the Flask
server can mutate `registry.json` directly via curl/fetch, bypassing the UI
entirely.

**Fix direction:** add real request-level auth (session, API key, or
whatever auth mechanism the app eventually adopts) to these three routes,
and wire the frontend `isAdmin` flag to the same source of truth so the UI
and the API agree about who's allowed to do what.

## 3. Category-overview status filter has no visible effect

In `client/src/pages/ToolDashboard.jsx`, the status filter dropdown
(All/Active/Candidate/Proposed/Rejected) only applies when a specific
category is selected (drilled into `CategoryTable`). The default "All
Categories" overview (`CategoryOverviewGrid`) renders from the raw,
unfiltered `registry.statistics` object, so changing the status filter while
on that view has no visible effect — it looks broken until the user clicks
into a category.

**Fix direction:** either compute a filtered-statistics variant for the
overview grid cards (count only tools matching `selectedStatus` per
category), or disable/hide the status filter while on the "All Categories"
view so it doesn't imply functionality that isn't there.

---

# Known Limitations — Sprint 4 (auth, roles, registry lock)

Not bugs — deliberate simplifications per the "keep it simple, this is a
portfolio project, not a production SaaS platform" design goal in
`sprint4-handover.md`. Documented here so they read as known trade-offs
if rediscovered later, not as surprises.

## 4. Single shared admin key, no rotation or per-user identity

`PAYMENTLAB_ADMIN_KEY` is one shared secret for anyone who should have
admin access — there's no per-user identity, no ability to revoke one
person's access without rotating the key for everyone, and no audit
trail of *which* admin approved/rejected a given tool. This matches the
handover's explicit "no user management system" scope for this sprint.

The key is also **stored in `localStorage` on whatever browser signs
in** (`client/src/utils/api.js`), readable by anything with devtools
access to that browser/machine — script injection, a shared/public
computer, or browser extensions with broad permissions could read it.
Given the threat model here (closing the "anyone with curl" gap from
issue #2, not defending against a compromised admin's own machine), this
is an accepted trade-off, not a defect.

**If this ever needs to change:** move toward per-user sessions/API keys
(even a simple one-key-per-admin lookup table would remove the "rotate
for everyone" problem) before this app handles anything more sensitive
than a demo tool registry.

## 5. [RESOLVED 2026-08-02] POSIX lock branch verified via WSL2

`_CrossProcessLock` in `server/ai_agents/tool_registry.py` has two
branches: `msvcrt.locking` (Windows, exercised locally via
`_test_concurrent_lock.py`) and `fcntl.flock` (POSIX/Render). Originally
only the Windows branch had been run — the `fcntl` branch was untested
code as of the Sprint 4 commit.

**Update:** ran `_test_concurrent_lock.py` for real under WSL2 Ubuntu
(`sys.platform != "win32"`, so the actual `fcntl.flock` path executes,
not a Windows fallback). Result: both concurrent writes from separate OS
processes survived, `registry.json` stayed valid JSON, the `.lock`
sidecar was created — same pass criteria as the Windows run, now
confirmed on the code path Render actually runs. See
`SPRINT4_TEST_REPORT.md` for the full log.

**Residual caveat, worth a lightweight sanity check post-deploy (no
longer a hard gate, but not nothing):** WSL2 is a single Linux VM running
two OS processes — closer to gunicorn's real multi-worker-process model
than the Windows test was, but still not identical to Render's actual
topology (separate worker processes under one gunicorn master, behind
Render's own scheduling). If it's ever worth the extra confidence, two
genuinely concurrent gunicorn workers is the closest local approximation
to production. Two near-simultaneous `curl` calls against a *live*
Render URL, by contrast, remain a weak test on their own — network
latency and dyno scheduling can serialize what looks like a race without
ever contending the lock, so a "pass" there shouldn't be read as strong
evidence either way.
