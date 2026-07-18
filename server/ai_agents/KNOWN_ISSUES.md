# Known Issues — Tool Registry / Scanner (Sprint 3)

Acknowledged but not fixed as of the Sprint 3 commit. Found during a full
code review before commit; the fixes below were deferred as lower priority
than the correctness bugs that were fixed in the same pass (see git log for
that commit's message).

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
