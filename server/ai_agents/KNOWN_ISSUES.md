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

## 3. [RESOLVED Sprint 7] Category-overview status filter has no visible effect

In `client/src/pages/ToolDashboard.jsx`, the status filter dropdown
(All/Active/Candidate/Proposed/Rejected) only applies when a specific
category is selected (drilled into `CategoryTable`). The default "All
Categories" overview (`CategoryOverviewGrid`) renders from the raw,
unfiltered `registry.statistics` object, so changing the status filter while
on that view has no visible effect — it looks broken until the user clicks
into a category.

**Precise reachable scenario** (found during the Sprint 7 fix, sharper than
the original description above): the status `<select>` is already
`disabled={!selectedCategory}`, so a user can't change the filter while
*looking* at the overview at all. The actual bug: drill into a category,
change `selectedStatus`, navigate back to "All Categories" — the value is
never reset, the (now-disabled) dropdown still displays it, but the overview
grid silently ignores it and shows unfiltered counts.

**Fix applied:** `CategoryOverviewGrid` now takes `tools`/`selectedStatus`/
`isAdmin` props (the last was previously accepted but unused — a
pre-existing dead prop now genuinely consumed) and computes per-category
counts filtered by `selectedStatus` client-side, from `registry.tools`
(already fully present in client state — no backend query needed). When
`selectedStatus === 'all'`, the original active/candidate/total three-stat
row renders unchanged; otherwise each card shows a single
`"{N} {StatusLabel}"` line. Verified in-browser: setting the filter to
"Candidate" inside a category, then returning to "All Categories," produces
per-card counts that exactly match `CategorySidebar`'s own (always-correct)
per-category candidate tallies.

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

---

# Known Issues / Limitations — Sprint 5 (business layer, tool profiles)

From the Sprint 5 test-review walkthrough — a mix of real gaps and
accepted trade-offs. See `server/business/SPRINT5_TEST_REPORT.md` for the
run that surfaced them and `SPRINT5_TEST_PLAN.md` for how to re-verify.

## 6. [RESOLVED 2026-08-10] Dispatch drops hallucinated/invalid tool calls instead of feeding them back

`FraudInvestigator._dispatch()` correctly refuses any tool not in the
merchant's grant — including tool names the LLM *hallucinates* (observed
live: the model requested `check_email_history`, which doesn't exist; the
real tool is `get_email_history`). The gate is a **safety** guarantee (no
wrong tool runs), but it is not a **correctness** guarantee: if the model
*meant* to perform a legitimate check and merely got the name wrong, the
call is dropped and that investigation step silently never happens. A
verdict can be reached with an intended check missing.

**Fix direction:** on a rejected call, return the error *to the model*
(e.g. "`check_email_history` is not a valid tool; available tools: …") so
it can retry with the correct name, converting a silently-skipped step
into a self-correcting loop. Prompt-hardening can lower the hallucination
rate but must never be relied on as the control — the gate stays; the
feedback-retry is the addition.

**Priority:** medium. Affects investigation *quality*, not safety.

**Resolved in Sprint 6.** `_dispatch()` now classifies rather than merely
refusing. It checks the registry *before* the grant, so the two rejection
classes are distinguishable and each carries an `error_class`:

- `"unknown_tool"` — name absent from the registry. A model fumble. Both
  loops feed a corrective message listing the granted tool names back to
  the model, so it can retry with the right one.
- `"not_granted"` — real tool, outside the merchant's tier. Terminal and
  unchanged, message byte-identical to Sprint 5. No retry: the model
  already has the granted schemas, so re-prompting costs turns for no
  expected benefit, and a legitimately excluded tool is correct scope, not
  a gap.

The gate did not weaken. Classification is a read-only `registry.get_tool()`
lookup on the same snapshot the schemas came from (so #10 stays satisfied),
and it happens strictly before any execution path — the Sprint 5
`assert_not_called` test passes unchanged.

Bounds and accounting:

- Per-name repeat cap of 1, name-sensitive not params-sensitive. A second
  attempt at the same bad name gets a terminal message, so one stubborn
  fixation costs at most two turns.
- Corrections consume the loop's own `max_steps`; there is no separate
  budget that extends an investigation past its ceiling.
- Hallucinations never enter `evidence_gathered`, `tools_called`, or
  `tool_results`. They produce a `phase="CORRECTION"` step, not `"GATHER"`.
- Names still unresolved at investigation end land in
  `InvestigationReport.unresolved_checks` and cap confidence at 0.85 (one)
  or 0.7 (two or more) — capped, never raised. Out-of-grant refusals
  populate nothing and cap nothing.
- An investigation is never aborted for this. Give up = log + caveat.

**Note on "resolved".** A hallucinated name can never itself appear in
`tools_called` — it isn't a real tool — so "did the model recover?" is a
correspondence question, not a membership test, and a literal membership
check would collapse to "always caveat." Resolution therefore uses
`difflib.SequenceMatcher` against the names actually called, at
`HALLUCINATION_MATCH_CUTOFF = 0.75`. Measured on the live registry,
prefix-swap fumbles score 0.769–0.875 against their intended target and
≤0.649 against every other active tool. The margin is real but not
enormous — `get_velocity` → `check_velocity` clears by only 0.019 — so
`test_fuzzy_resolution_threshold` pins the invariant against the actual
registry contents and fails loudly if a future tool name narrows the gap.
See `SPRINT6_TEST_REPORT.md`.

## 7. Stress test proved no corruption at volume, not graceful behavior under contention

`server/business/_stress_test_business_layer.py` ran 50 concurrent
processes with 0 `database is locked` errors — but every operation was a
millisecond-long single statement, so no writer ever held the lock long
enough to force another into the 5-second `busy_timeout` retry path. The
test proves the data model doesn't *logically* corrupt under concurrent
access; it does **not** prove the lock-contention/retry mechanism actually
recovers a writer that hits a genuinely held lock, because that condition
was never manufactured.

**Fix direction (only if concurrency ever matters at scale):** a
deliberately adversarial concurrency test — either lower the SQLite
`timeout` to near-zero to force clean fast-fail, or hold a write
transaction open artificially so other writers must wait — plus enabling
WAL mode (`PRAGMA journal_mode=WAL`) so readers and writers stop blocking
each other (PaymentLab's many-readers/occasional-writer shape is exactly
WAL's sweet spot).

**Priority:** low at current (hobby) load; the gap is real but unlikely to
manifest below dozens of concurrent admin writes.

## 8. "default" tier conflates a safety fallback with a commercial free tier

The seeded `default` tier grants *all 9 active tools*. This currently
serves two distinct purposes that should be separated once real
pricing/tiers are defined: (a) a **safety fallback** so a merchant with no
valid tier still gets a working investigation, and (b) an eventual
**commercial free/starter tier**, which should be *restrictive* (a few
essential tools), not more privileged than a paid tier.

**Fix direction:** when pricing tiers land (deferred roadmap item), split
these — a restrictive starter/free tier for commercial packaging, and a
separate explicit policy for the "unassigned/fallback" case (minimal
tools, or fail loudly rather than silently granting everything).

**Priority:** low now (mechanism-only sprint); revisit with pricing work.

## 9. No commercial-admin UI — tier/grant management is API-only

Sprint 5 delivered the endpoints and auth seam for managing tiers and
grants, but no dashboard for the commercial-admin persona to drive them.
All management is via curl/API today. This is by design for the sprint
(deliverable 5 was explicitly "endpoints + auth seam," not a UI) but is
worth naming so the absence reads as scoped-out, not overlooked — it's why
the sprint is "invisible from the UI."

**Fix direction:** a commercial-admin dashboard, most naturally alongside
the Sprint 6 IAM work (the two personas — ops-admin, commercial-admin —
want distinct UIs gated by distinct identities).

**Priority:** medium; blocks non-technical use of the tiering feature.

## 10. Registry-reload atomicity relies on one-snapshot-per-investigation

The `tool_id`/`name` split is safe today partly because an investigation
builds its allowed-list, its LLM-facing schemas, and its dispatch gate all
from the *same* in-memory registry snapshot — so even a stale snapshot is
internally consistent (schemas and gate agree on names). A latent
fragility: if a future refactor reused a long-lived `FraudInvestigator`
across a registry reload, the schemas shown to the LLM and the gate's
allowed-list could be built from *different* snapshots, reintroducing
exactly the "LLM uses one name, gate expects another" failure that's
currently avoided.

**Fix direction:** if investigator objects are ever cached/shared across
requests or reloads, make registry reads within a single investigation an
atomic snapshot explicitly, rather than depending on construction timing
to keep them aligned.

**Priority:** low (latent, not currently triggerable); note before any
investigator-reuse refactor.

## 11. `models/database.py`'s `DB_PATH` is a hardcoded module constant

Unlike `routes/fraud.py`'s env-var-configurable path (`PAYMENT_LAB_DB`),
`models/database.py` resolves `DB_PATH` relative to its own file location
with no environment override. Consequence: `app.create_app()` can't be
called in an isolated test without `init_db()` hitting the *real*
`payment_lab.db` regardless of env vars. Found while building the Sprint 5
pytest fixtures, which work around it by constructing a minimal Flask app
that registers `fraud_bp`/`business_bp` directly and skips `create_app()`
(and its metering-service side effect) entirely.

**Fix direction:** make `DB_PATH` read from the environment the same way
`routes/fraud.py` does, so `create_app()` itself becomes safely testable
and the two path resolutions stop disagreeing.

**Priority:** medium (test-safety — it constrains how the app can be
tested, not how it runs).

---

# Known Issues — Sprint 6 (retry-recovery)

## 12. [RESOLVED] The corrections channel was cumulative, not consumed

Found during the Sprint 6 Phase 3 live run (`SPRINT6_TEST_PLAN.md`), not
predicted by the handover. Root cause misdiagnosed on the first pass; the
correct diagnosis and fix are below.

**Symptom.** The `TOOL NAME CORRECTIONS:` block `_llm_plan()` adds to the
prompt names the hallucinated tool in order to correct it — *"You requested
'check_email_history' — this tool does not exist."* On `llama3.2`, two
injected fumbles at iterations 1–2 were followed by the model
*spontaneously* producing `check_email_history` again at iterations 6 and
7 — 4 of 8 turns spent on a name that cannot execute.

**First attempt (insufficient).** The initial fix filtered
`context["corrections"]` to exclude entries where `terminal is True` before
rendering the block, on the theory that repeating the terminal message
("already corrected and remains unavailable") was the contamination
source. A live re-run of the identical scenario showed this made things
*worse* — 5 attempts, not 2 — with the corrective (non-terminal) message
present in **7 of 8** prompts, unchanged from before the filter.

**Actual root cause.** `context["corrections"]` was never pruned; entries
only ever accumulated. Because `HALLUCINATION_REPEAT_CAP = 1`, attempt 1
for any name is *always* non-terminal by construction — so the terminal
filter never touched it. That first entry stayed in the list for the rest
of the investigation and was re-rendered into `_llm_plan()`'s prompt on
every subsequent turn, terminal-filter notwithstanding. The filter removed
attempts 2+; it never had a chance to remove the one entry that actually
mattered.

**Fix applied.** Corrections are now consumed on read: `_llm_plan()` clears
`context["corrections"] = []` immediately after building the block, so any
given entry can appear in at most one prompt — the turn right after it was
appended — never again. The terminal filter is kept as well (a terminal
message still isn't worth a second display), but the clear is what closes
the gap; the filter alone did not.

**Verified live**, same transaction, same model, same injected fumbles,
prompts logged and compared line-by-line pre/post fix:

| | bad name present in prompt | `attempts` recorded |
|---|---|---|
| Before any fix | 7 of 8 iterations | 4 |
| Terminal-filter only (insufficient) | 7 of 8 iterations | 5 |
| Consumed-on-read (applied) | **1 of 8** iterations | **2** |

With the fix, the bad name appears exactly once — at iteration 2, the
single deliberate display — and the model did not re-hallucinate it on its
own for the rest of the run. `attempts: 2` matches the two *injected*
fumbles precisely, with zero spontaneous repeats. That is strong evidence
the echo was driven by the persistent prompt content, not (solely) an
intrinsic model bias toward that name — though this is one run, not a
statistical guarantee across seeds.

What stayed correct throughout, including during the insufficient first
attempt:

- The gate refused every attempt; nothing ungranted ever executed.
- No evidence pollution: `GATHER` steps for the bad name = `[]` in every
  variant.
- Accounting stayed correct: `_hallucination_attempts` is keyed by name, so
  4–5 attempts still yielded exactly **one** `unresolved_checks` entry and
  a 0.85 cap, not 0.7.
- The investigation always completed and produced a verdict, bounded by
  `max_steps`.

So even the insufficient first attempt was a quality/efficiency defect, not
a safety or correctness one — the same category as the #6 this descends
from. It just wasn't the fix.

**Regression coverage:**
`test_correction_shown_at_most_once_across_calls`
(`test_sprint6_retry_recovery.py`) calls `_llm_plan()` three times over one
unconsumed correction and asserts the bad name appears in the first prompt
only — the multi-call behavior the original single-call tests
(`test_terminal_corrections_are_not_shown_in_prompt`, etc.) couldn't catch,
since a single `_llm_plan()` call can't observe whether state persists
across calls.

**Rejected alternative** (unaffected by the correction above): counting
terminal repeats toward `consecutive_skips` so the loop bails early.
`SPRINT6_HANDOVER.md` §4 explicitly forbids it ("conflating them could
terminate the investigation prematurely").

**Status:** resolved in `fraud_investigator.py` (`_llm_plan()`), verified
against both the mocked suite and a live Ollama run. Not yet re-confirmed
against a second model (`llama3.2:1b` or larger) or a second transaction —
worth doing before treating the live measurement above as representative
rather than indicative.

---

# Known Issues — Sprint 7 (scanner pipeline expansion)

## 13. `narrative` profile's section-3.1 extraction reproducibly breaks Ollama's JSON formatting

Found during Sprint 7 sign-off, live-scan verification
(`SPRINT7_TEST_PLAN.md` Part B) — not caught by the mocked test suite, since
every mocked test supplies hand-written, well-formed JSON as the "Ollama
response."

**Symptom.** Two live scans of the EPC report with `--profile narrative`,
same model (`llama3.2:latest`), both produced the same malformed shape —
each candidate as its own top-level `[...]` array containing bare
`"key": "value"` pairs with no enclosing `{}`:

```
["name": "phishing_email", "category": "external_intel", "description": "...", ...]
["name": "malware_infection", "category": "external_intel", "description": "...", ...]
```

This isn't truncation — it's structurally invalid JSON from the first
character (an array literal can't contain bare key:value pairs). `main()`
caught it cleanly both times (`[SCAN] FAILED: ... Cannot find a repair
point in truncated JSON`, exit 1, no traceback), and no corrupt or partial
`scan_history` entry was written on either failed run (`add_scan_history()`
is only reached after successful parsing) — the Sprint 7 code introduced by
this sprint behaved exactly as designed on this failure path.

**Root cause is the new prompt content, not the parsing/extraction code.**
`strip_json_repair()` is byte-for-byte unchanged by Sprint 7 — its repair
heuristic (patch a truncated tail, close unbalanced brackets) was never
designed to recover from a missing object wrapper, which is a different
failure mode than what it exists to handle. Confirmed by a direct
side-by-side test against the *same live model, same session*:

- **Old (pre-Sprint-7) hardcoded window** (`full_text[8000:8000+6000]`) →
  clean output, `{` `}`-wrapped objects, parsed successfully into 8
  candidates — consistent with how the original 8 EPC-sourced tools in the
  registry were produced back in Sprint 3.
- **New `narrative` profile's section-3.1 window** (same model, same day) →
  malformed on 2/2 attempts.

So the regex-based section extraction is doing its job correctly — it
finds the real content, not the TOC, and the section it targets (3.1
"Cyber threats, attack techniques and fraud enablers") is exactly the
right material — but that content's own heavy nested-numbering structure
(3.1, 3.1.1, 3.1.1.1, ...) plausibly confuses a small local model into
echoing bracket/list conventions from the source text into its own output
format. Unconfirmed hypothesis; not verified further this sprint.

**Not a Sprint 7 regression in the code-correctness sense** — every code
path Sprint 7 actually added (duplicate detection, `run_scan()`
orchestration, `scan_history` read/write, the dashboard extension) is
independently proven correct by the mocked suite and browser verification,
none of which depend on what the LLM says. This is a content/prompt-quality
finding surfaced *by* Sprint 7's live-verification requirement, not a defect
introduced *by* Sprint 7's changes to `tool_registry.py` or the frontend.

**Fix direction (not applied — a prompt/parsing decision, not a Sprint 7
architecture change):**
- Harden the prompt with an explicit one-shot example showing the required
  `{"name": ..., ...}` object shape, since the current prompt states the
  contract in prose only ("output an object with these exact fields") and
  llama3.2 isn't reliably inferring the brace requirement for this
  particular content.
- Extend `strip_json_repair()` to detect and repair the "flat key:value
  pairs directly inside `[]`" pattern specifically, as a second repair
  strategy alongside the existing truncation repair.
- Or: reduce `max_chars` for the `narrative` profile, since the numbered
  substructure gets denser (more sub-sub-sections) later in the section —
  a shorter, shallower excerpt may reduce the model's exposure to nested
  numbering.

**Priority:** medium. Blocks the `narrative` profile from producing usable
proposals against this specific report with this specific model right now,
but doesn't block anything Sprint 7 itself shipped — the scanner's
provenance-tracking and profile infrastructure work correctly regardless of
whether a given scan's LLM output happens to parse.
