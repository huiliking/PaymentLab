# Sprint 7 Test Plan — Scanner Pipeline Expansion + Frontend Refresh

Validates the Sprint 7 deliverables (see `SPRINT7_HANDOVER.md` in Downloads and
the approved implementation plan) against report-profile extraction,
scan-provenance tracking, and the two frontend dashboard changes.

**This plan is split deliberately into two parts with different status.**
Part A was already executed during implementation, in the same session that
wrote the code — the automated suite and a full round of in-browser
verification, both with evidence captured below. It is **not** re-run by this
plan; it's documented here so the full verification picture lives in one
place, the same way `SPRINT6_TEST_PLAN.md` documents phases that don't need
repeating on every read. Part B is the one thing that verification round
deliberately deferred — a live, non-mocked scan against a real Ollama
endpoint — and **is** re-run as part of executing this plan, because
`strip_json_repair()` and the real Ollama response shape are never exercised
by the mocked test suite or by the stubbed-fetch browser checks.

If you're re-running this plan later (e.g. after a further change to
`tool_scanner.py`, `tool_registry.py`, or either dashboard file), re-run
**both** parts — Part A's "already verified" status is a snapshot of this
session, not a standing guarantee.

---

## Part A — Already Verified (evidence below, not re-run by this plan)

### A.1 Automated suite

```bash
cd server
python -m pytest tests/ -q
```

**Result:** 136 passed (21 new in `test_sprint7_scanner_provenance.py`, 115
from the pre-existing Sprint 5/6/Claude-provider suites — zero regressions).

The 21 new tests cover, per `test_sprint7_scanner_provenance.py`:

| Group | What it proves |
|---|---|
| `TestReportProfiles` (7) | Each profile extracts differently; `narrative` lands on the real body header, not the TOC (verified via `SequenceMatcher`-free direct string checks, not visual inspection); the unclipped match range contains subsection markers 3.1.1–3.1.7; the capped extraction respects `max_chars`; unknown profile and an unmatchable `start_pattern` both raise; `--profile` defaults to `narrative`. |
| `TestScanHistoryWrite` (3) | `add_scan_history()` persists and reloads correctly; missing required fields return `{"error": ...}` without mutating state; repeated writes are append-only, never overwritten. |
| `TestDuplicateDetection` (3) | A duplicate `(report_name, profile)` scan is blocked before `call_ollama` is ever invoked (`assert_not_called()` — no wasted LLM call); `--force` proceeds and appends rather than replacing; scanning the same report under a **different** profile is correctly *not* treated as a duplicate. |
| `test_backfill_entry_is_well_formed` (1) | Loads the real, unmodified `registry.json` and asserts the backfilled EPC entry has the right `report_name`, `profile == "narrative"`, `proposals_generated == 8`. |
| `TestGetScanHistoryCounts` (2) | Live-computed counts against the real registry pin to `approved=3, rejected=1, pending=4`; a status change via `update_status()` shifts the counts on the next read, proving they're computed live, not cached. |
| `TestPromptContext` (2) | `build_prompt()` uses the default wording with no third arg, and the profile's `prompt_context` string with one. |
| `TestDashboardPayload` (1) | `to_dashboard_payload()["scan_history"]` is present with all eight expected keys per entry. |
| `TestRunScanOrchestration` (2) | `run_scan()` returns the built proposal and a scan_history entry; critically, `registry.list_tools()` count is **unchanged** afterward — proves scan-history recording never bypasses the human-review step the propose/approve workflow exists to enforce. |

This suite mocks `call_ollama` throughout via
`patch("ai_agents.tool_scanner.call_ollama")` — no live network calls, no
running Ollama required. That's precisely the gap Part B closes.

### A.2 Browser verification

Both dev servers (`server` on :5000, `client` on :5173) were started via the
project's `.claude/launch.json` configs. Zero console errors and zero server
errors were observed at every checkpoint below.

**ToolDashboard (`/tools`):**

- `ScanHistoryPanel` rendered the backfill row exactly as expected:
  `EPC162-24 v2.0 2025 Payments Threats and Fraud Trends Report_0 |
  2026-07-18T16:47:25Z | narrative | 8 generated · 3 approved · 1 rejected · 4 pending`.
- KNOWN_ISSUES #3 fix confirmed end-to-end, not just by inspection: drilled
  into "Transaction Context," set the status filter to "Candidate," navigated
  back to "All Categories." Every category card switched from the
  active/candidate/total triple to a single `"N Candidate"` line, and the
  numbers matched `CategorySidebar`'s own (always-correct) per-category
  candidate tallies exactly — Transaction Context 3, Identity History 2, Card
  & Velocity 2, Geographic & Locale 2, Address & Shipping 3, Behavioral &
  Account 6, Merchant & Product 2, External Intelligence 4. Setting the filter
  back to "All Status" restored the original triple on every card.

**FraudDashboard (`/fraud`):**

Verifying the `CORRECTION` step color and the `unresolved_checks` warning
strip live is nondeterministic (requires a real model to hallucinate a tool
name, which can't be induced on demand) and slow (~1–2 minutes per live
investigation on this machine). To verify the *rendering logic* deterministically
without waiting on a live LLM, `window.fetch` was intercepted in the browser
for exactly one `POST /api/fraud/investigate/<id>` call, returning a synthetic
but schema-accurate `InvestigationReport.to_dict()` payload (one `CORRECTION`
step, one `unresolved_checks` entry) — then the app's own unmodified React
code (not test code) rendered it through the real component tree. This tests
the same code path a live investigation would, without depending on
nondeterministic model output. A second stub with an empty `unresolved_checks`
array and no `CORRECTION` step verified the negative case the same way.

- The `CORRECTION` step's dot and badge, and the `unresolved_checks` warning
  strip, were checked via `getComputedStyle()` — not just DOM presence — and
  matched `RISK_COLORS.MEDIUM` exactly: `background-color: rgb(255, 243, 224)`
  (`#fff3e0`), `border-color: rgb(255, 167, 38)` (`#ffa726`),
  `color: rgb(230, 81, 0)` (`#e65100`).
- The warning strip's text read exactly:
  `⚠ 1 intended check could not be completed due to invalid tool name: check_email_history. Confidence may be capped as a result.`
- The investigation-steps timeline rendered all five phases present in the
  synthetic report — `PRE-SCREEN`, `PLAN`, `CORRECTION`, `GATHER`, `VERDICT` —
  confirming `CORRECTION` doesn't fall through to the gray default.
- **Negative case:** with an empty `unresolved_checks` array, `warningPresent`
  evaluated to `false` — the guard (`report.unresolved_checks &&
  report.unresolved_checks.length > 0`) correctly suppresses the strip, and
  the phase list for that report showed only `PRE-SCREEN, PLAN, GATHER,
  VERDICT` (no stray `CORRECTION`).

**Working tree check:** `git status --short -- server/payment_lab.db
server/ai_agents/registry.json` showed only the intentional backfill to
`registry.json` — the stubbed-fetch browser session made no real network
writes and touched neither file further.

---

## Part B — Still Needs Verification (re-run by this plan)

### B.1 — Live scan against a real Ollama endpoint

**Why this specific gap matters.** Every other code path in `run_scan()` —
duplicate detection, extraction, scan-history persistence, proposal
validation — is covered by A.1's mocked suite. The one thing that suite
cannot exercise is what a *real* Ollama response looks like: whether
`llama3.2`'s actual output for the narrative-profile prompt parses cleanly
through `strip_json_repair()`, whether the model stays within the JSON-array
contract the prompt asks for, and whether `build_registry_entry()`'s
category/field validation drops or accepts what a real model actually
produces (as opposed to the synthetic `MOCK_CANDIDATES_JSON` used everywhere
in A.1, which is hand-written to be well-formed by construction).

**Setup — operates on a temp copy, never the real registry:**

```bash
cd server
python -c "
import shutil, tempfile, os
d = tempfile.mkdtemp()
dest = os.path.join(d, 'registry.json')
shutil.copy('ai_agents/registry.json', dest)
print(dest)
" > /tmp/scan_target_path.txt
```

**Run:**

```bash
REGISTRY_PATH=$(cat /tmp/scan_target_path.txt)
python -m ai_agents.tool_scanner \
  --report "ai_agents/EPC162-24 v2.0 2025 Payments Threats and Fraud Trends Report_0.pdf" \
  --registry "$REGISTRY_PATH" \
  --profile narrative \
  --force
```

`--force` is required because the temp copy carries the real backfill entry
(`report_name` = the same PDF, `profile` = `narrative`) — without it,
duplicate detection correctly refuses to run, which is itself a pass (proven
already by A.1's `TestDuplicateDetection`), not the thing this run is for.

**Pass criteria:**

1. The process exits 0 and prints a JSON array to stdout (or writes it via
   `--out` if that flag is added to the command).
2. Console shows `[SCAN] Extracted N raw candidates, validating...` followed
   by one `[OK]`/`[DROP]` line per candidate — confirms `strip_json_repair()`
   successfully parsed the real Ollama response into a list, and
   `build_registry_entry()` ran its validation against real (not synthetic)
   candidate shapes.
3. At least one proposal is `[OK]` (a total-failure run — 0 valid proposals —
   would still exit cleanly per the code, but signals either a bad prompt or
   a `strip_json_repair()` gap that A.1's synthetic-JSON tests can't surface,
   and should be investigated before treating this phase as a pass).
4. Every `[OK]` proposal's `name` matches `NAME_RE` (snake_case), `category`
   is one of the eight real category IDs, and `description`/`detects` are
   non-empty — i.e. `build_registry_entry()`'s validation actually held
   against live model output, not just the hand-written mock.
5. Confirm the temp registry (not the real one) received the scan_history
   write:
   ```bash
   python -c "
   import sys; sys.path.insert(0, '.')
   from ai_agents.tool_registry import ToolRegistry
   import os
   reg = ToolRegistry(os.environ.get('REGISTRY_PATH') or open('/tmp/scan_target_path.txt').read().strip())
   entries = [s for s in reg.get_scan_history() if s['profile'] == 'narrative']
   print(len(entries), 'narrative entries')
   print(entries[-1])
   "
   ```
   Expect **two** entries for the EPC report under `narrative` — the original
   backfill plus this run's `--force` append (proves append-only persistence
   against a real, not synthetic, write).
6. **The real dev registry is untouched:**
   ```bash
   git status --short -- server/ai_agents/registry.json
   ```
   Must show only the pre-existing backfill diff from earlier in this
   session — no new entry from this run leaking into the tracked file.

**What a failure here would mean, and what it would NOT mean:** if
`strip_json_repair()` fails to parse the real response, or zero proposals
survive validation, that's a **prompt-engineering or extraction-quality**
finding, not evidence the Sprint 7 code is broken — the classifier, the
lock/persist path, and the duplicate-detection logic are already proven
correct by A.1 independent of what the LLM says. Report it as a finding, not
a blocking defect, unless it also throws an unhandled exception (which A.1's
mocked-failure-path tests don't cover either, since they mock a
well-formed response).

---

## Out of Scope for This Plan

- Re-running Part A's browser checks (already verified, evidence above stands
  until the frontend code changes again).
- Actually scanning EBA/ECB or OWASP PDFs (no such files exist in this repo —
  `regulatory`/`ranked_list` remain unverified placeholders by design, per
  the Sprint 7 handover's "pipeline, not content" scope).
- Load/concurrency testing of the cross-process lock under simultaneous scans
  (inherited, unaddressed gap — `KNOWN_ISSUES.md` #7 territory, not new to
  Sprint 7).
