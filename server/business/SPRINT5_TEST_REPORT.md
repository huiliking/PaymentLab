# Sprint 5 Test Report — Business Layer + Tool Profiles

Actual results from running `SPRINT5_TEST_PLAN.md`, `test_sprint5_business_layer.py`,
and `_stress_test_business_layer.py` on 2026-08-05, against commit-pending
working tree (Sprint 5 not yet committed).

## Summary

| Suite | Result |
|---|---|
| Automated pytest (`tests/test_sprint5_business_layer.py`) | **40/40 passed** |
| Stress test (`business/_stress_test_business_layer.py`, n=50) | **3/3 scenarios PASS**, 0 lock-contention errors |
| Manual plan (`SPRINT5_TEST_PLAN.md`), live dev server | **All 7 phases walked, all passed** |

## Automated suite

```
python -m pytest tests/test_sprint5_business_layer.py -v
============================= 40 passed in 4.46s ==============================
```

All 40 cases passed on the first *correct* run. Three test-authoring bugs
were caught and fixed before that:

1. `test_migration_idempotent_from_pristine` / `test_migration_preserves_transaction_count`
   called a `_counts()` helper that assumes the post-migration schema
   (`tiers`/`merchants`/`tier_tools` tables) already exists — but these
   tests run against the *pristine* pre-migration backup, before those
   tables exist. Fixed by adding a `_transaction_count()` helper that only
   touches the one table guaranteed to exist both before and after
   migration.
2. `test_service_module_does_not_import_tool_registry` did a substring
   check (`"tool_registry" not in source`) against `business/service.py`,
   which false-failed on the module's own docstring — it legitimately
   *names* `ai_agents/tool_registry.py` in prose while explaining why it
   doesn't import it. Fixed by switching to an `ast`-based check of actual
   import statements, not text search. (The same class of mistake recurred
   independently during the manual Phase 6 walk — see below — which is
   itself worth noting: a substring/text-grep boundary check is an easy
   trap to fall into twice.)

**Isolation verified**, not just assumed: MD5 of `payment_lab.db` and
`ai_agents/registry.json` were identical before and after the full pytest
run (`28d9a2ea9c34ed341ffbc0b30e0da0f9` /
`8c2a21be971ae8162e495a22d0d4a462`) — the suite never touched the real dev
files, confirming the temp-copy fixture design actually held rather than
silently falling through to the real paths.

One architectural quirk surfaced while building the Flask-route fixture:
`models/database.py`'s `DB_PATH` is a hardcoded module constant, unlike
`routes/fraud.py`'s (which reads `PAYMENT_LAB_DB` from the environment).
Calling `app.create_app()` in a test would therefore call `init_db()`
against the *real* `payment_lab.db` regardless of env vars set for
`routes.fraud`. Worked around by building a minimal Flask app in the test
fixture that registers only `fraud_bp`/`business_bp` directly, skipping
`create_app()` (and its `init_db()`/metering-service side effects)
entirely — no source changes needed, but this inconsistency between the
two `DB_PATH` resolutions is worth fixing at some point so `create_app()`
itself is safely testable. Not fixed here (out of scope for a test-writing
pass); flagging as a candidate for `KNOWN_ISSUES.md`.

The load-bearing dispatch-enforcement test
(`TestDispatchEnforcement::test_dispatch_blocks_out_of_grant_tool_and_never_calls_underlying_method`)
passed: a real (non-stubbed) `FraudInvestigator._dispatch()` call for a
tool outside the grant returns an error *and* the underlying
`InvestigationTools.check_velocity` mock was confirmed never called
(`Mock.assert_not_called()`). This is the one test in the suite that
proves enforcement is real rather than cosmetic — the route-level tests
next to it only prove the right list was passed to the constructor.

## Stress test

```
python business/_stress_test_business_layer.py --n 50
```

All three concurrency scenarios passed, run twice (n=16, then n=50 to push
harder before accepting the result):

| Scenario | n | Result | Lock errors |
|---|---|---|---|
| Concurrent grants (distinct tool_ids, same tier) | 50 | PASS — all 9 attempted grants persisted | 0 |
| Concurrent reads during a write (toggle) | 49 readers + 1 writer, 50 iterations each | PASS — no corrupt reads, no unexpected errors | 0 |
| Concurrent tier reassignment (same merchant) | 50 | PASS — final state is exactly one of the 50 attempted tier_ids | 0 |

**Zero lock-contention errors at n=50**, run twice. This is a genuine
result, not an under-tested one: `BusinessLayer._conn()` has no WAL mode or
explicit `busy_timeout` tuning, but Python's `sqlite3` module already
applies a 5-second default busy-timeout, and every operation here is a
single or double quick statement (an `INSERT`/`UPDATE`/`SELECT`, not a
long-held transaction) — well within that window even at 50 concurrent
processes. **This does not mean the underlying gap doesn't exist** — a
slower disk, a long-held write transaction elsewhere, or genuinely higher
concurrency than this hobby app will ever see in practice could still hit
it — but it means the gap did not manifest at any tested load, so **no
fix was applied.** If real usage ever approaches dozens of concurrent
commercial-admin mutations, revisit with `PRAGMA journal_mode=WAL` and/or
an explicit longer `sqlite3.connect(..., timeout=N)` in `_conn()` as the
candidate fix — same conclusion the plan anticipated, just with the
opposite empirical outcome from what seemed likely going in.

**Isolation verified** the same way as the pytest suite: DB/registry MD5s
identical before and after (both scenario runs).

## Manual plan (live dev server)

Ran with `python app.py` on port 5000, `PAYMENTLAB_ADMIN_KEY` loaded from
`server/.env`. The Sprint 4 port-5000 kill quirk **did recur**, at
shutdown — see Gotchas.

- **Phase 0 (baseline):** matched the documented baseline exactly —
  `tiers=[(1,'default')]`, 3 merchants all on tier 1, 9 `tier_tools` rows,
  123 transactions.
- **Phase 1 (migration integrity):** `init_db()` run twice via live server
  restart path — counts stable (tiers=1, merchants=3, tier_tools=9,
  txns=123), 0 orphans, 0 duplicate `registration_id`, FK present
  (`merchant_ref -> merchants.id`), `registry.json` valid JSON. All match
  plan pass criteria.
- **Phase 2 (business API auth):** 2.1 all four mutating endpoints → 403
  `"Requires commercial_admin role"` with no header; 2.2 → 403 with a wrong
  key; 2.3 → 201 with the correct key; 2.4 both public GETs → 200. Exact
  match to plan.
- **Phase 3 (business API correctness):** all 8 sub-cases matched plan
  pass criteria exactly — active-tool grant 201, inactive-tool grant 409
  (`"is not active"`), unknown tool_id 409, duplicate grant 201
  (idempotent), revoke 204, merchant reassignment 200, unknown merchant
  404, unknown tier 409.
- **Phase 4 (investigation enforcement):** merchant_beta reassigned to a
  2-tool tier (`get_transaction_details`, `check_locale_consistency`);
  investigated a real flagged transaction (`cc75cdf6-451`, IP/billing
  mismatch + suspicious email domain) through the actual Ollama backend —
  **real LLM behavior, not scripted.** Result: verdict `SUSPICIOUS`/`CRITICAL`,
  `tool_results` = `{check_email_history, check_locale_consistency,
  get_transaction_details}`. The interesting one is `check_email_history` —
  llama3.2:1b **hallucinated a tool name that doesn't exist in the
  registry at all** (the real one is `get_email_history`). `_dispatch()`
  rejected it cleanly: `{"error": "Tool 'check_email_history' is not
  granted for this merchant's tier"}` — never reached
  `registry.execute()`, never touched `InvestigationTools`. The two
  actually-granted tools were used normally. Confirmed via the `steps`
  array that the model tried the hallucinated name twice (steps 1 and 5)
  and both were blocked identically. Also confirmed
  `check_identity_graph` — normally auto-escalated/mandatory-fallback for
  Ollama — correctly never appears in `tool_results`, proving the
  grant-aware guard on that fallback (added in `fraud_investigator.py`)
  held under a real run, not just the earlier direct unit check. This is a
  stronger result than a scripted test would have produced: it's evidence
  against a *live, unpredictable* LLM output, not just a hand-picked tool
  name.

  **No-regression half:** a flagged default-tier transaction
  (`f9186bef-924`, merchant_alpha) investigated through the same live
  Ollama backend used 7 different tools — `check_identity_graph`,
  `check_locale_consistency`, `get_address_history`, `get_card_history`,
  `get_device_history`, `get_email_history` (the *real* name — no
  hallucination this time), `get_transaction_details` — verdict
  `FRAUDULENT`/`CRITICAL`. Confirms the default tier genuinely has
  unrestricted access to the full active tool set, matching pre-Sprint-5
  behavior; the restriction in the paragraph above is specific to the
  reassigned merchant, not a global regression.

  **Drift detection (criterion #2, investigation-time half):** with
  merchant_beta still on the 2-tool test tier, inserted a corrupt
  `tier_tools` row referencing `tool_id=9999` (doesn't exist) directly via
  SQL, then re-ran `POST /fraud/investigate/cc75cdf6-451`. Got a clean 409
  immediately (before any LLM call): `{"error": "Tier 6 grants tool_id
  9999 which is missing in the registry — fix the grant before this
  merchant can be investigated"}`. No silent degradation, no partial
  investigation attempted with fewer tools than intended — the `_dispatch`
  gate errors before the loop even starts, since `resolve_allowed_tools`
  raises before `FraudInvestigator` is constructed. Cleaned up the corrupt
  row immediately after.

- **Phase 5 (tool_id rename stability):** renamed `tool_id=1`'s `name` to
  `zz_renamed_tool` directly in `registry.json`, used the Sprint 4
  touch-`app.py`-to-reload trick (see Gotchas — needed twice this run) to
  get the running server's in-memory `ToolRegistry` to pick it up without
  a full restart, then `GET /business/tiers` — the default tier's tool
  list showed `zz_renamed_tool` in place of `get_transaction_details`,
  confirming the grant resolved via `tool_id`, not the old name. Reverted
  the same way; `registry.json`'s MD5 after revert
  (`8c2a21be971ae8162e495a22d0d4a462`) matched the pre-test baseline
  exactly — a byte-for-byte clean round trip, not just "looks the same."
- **Phase 6 (boundary/regression):** ran the *plan's originally-written*
  blanket-grep versions of 6.1/6.2 first — both produced false positives
  (see Gotchas). Re-ran with the scoped versions (now the versions
  committed in `SPRINT5_TEST_PLAN.md`) — clean on both. 6.3: `GET
  /fraud/tools` → 200, unauth'd `POST .../approve` → 401, unchanged from
  Sprint 4.
- **Phase 7 (cleanup):** reverted merchant_beta to `default`, deleted the
  test tier and its grants. Final state — `tiers=[(1,'default')]`, all 3
  merchants on tier 1, exactly 9 `tier_tools` rows, 123 transactions —
  matched the Phase 0 baseline exactly, and `payment_lab.db`'s row-level
  state was confirmed identical by direct query (not just row counts:
  same tier_id/tool_id pairs). `registry.json` MD5 also matched baseline
  post-revert (see Phase 5).

## Gotchas hit during this run (worth remembering for next time)

1. **A transaction can pre-screen clean and never reach tool dispatch at
   all.** The first merchant_beta transaction picked for Phase 4
   (`0c5ac126-645`) triggered 0 pre-screen rules, so
   `FraudInvestigator.investigate()` returned immediately with an empty
   `tool_results` — correct behavior, but it proves nothing about tier
   enforcement (there's nothing to enforce against if the LLM loop never
   runs). Had to pick a transaction with an actual IP/billing mismatch
   (`cc75cdf6-451`) instead. **Lesson for future manual investigation
   tests on this project:** always confirm a transaction is in
   `/api/fraud/flagged` (or has a rule-triggering condition) before using
   it to test the investigation *loop* — an unflagged transaction is a
   valid test of the pre-screen shortcut, not of anything downstream of it.
2. **Blanket-grep boundary checks produce false positives, same mistake as
   the automated suite made independently.** `grep -rn "tool_registry" business/`
   matches `business/service.py`'s own docstring (explaining why it
   *doesn't* import the module), `SPRINT5_TEST_PLAN.md`'s own instructions,
   and `_stress_test_business_layer.py` (which legitimately imports
   `ToolRegistry` for test setup). Likewise `grep -i tier registry.json`
   matches a citation string ("tier-1 risk signal") that has nothing to do
   with the data model. `SPRINT5_TEST_PLAN.md` Phase 6 was corrected in
   place to use scoped checks (import-statement-only grep; JSON-key-only
   Python check) instead of a blanket text search — this happened twice
   independently (once in the pytest suite, once here), which suggests
   "boundary check via substring grep" is a real trap worth naming
   explicitly rather than a one-off mistake.
3. **The Sprint 4 port-5000 kill quirk recurred, at shutdown.** After
   finishing Phase 7, `taskkill /F /PID <the PID netstat showed
   LISTENING>` reported `SUCCESS`, but a follow-up `netstat` immediately
   showed the *same* PID still `LISTENING` — while `tasklist` no longer
   listed it at all. Exactly the `SPRINT4_TEST_REPORT.md` Appendix
   description (PID visible in netstat, invisible to
   `Get-Process`/`tasklist`/`taskkill`-target-resolution). The documented
   fix (touch a `.py` file the app imports, let the debug-mode
   auto-reloader cycle the process) wasn't tried at that point since the
   goal was to stop the server, not reload it. What worked instead: a
   second `tasklist` revealed one remaining `python.exe` PID (different
   from the one netstat showed), and killing *that* one actually freed the
   port. So there are now two independently-confirmed workarounds for this
   family of quirk: touch-to-reload (Sprint 4, to recover a working
   server) and kill-the-other-visible-python-pid (this run, to actually
   stop it). Neither `taskkill` targeting the exact PID `netstat` reports
   has ever worked directly, across two sprints now.
4. **`python -c "... open('/tmp/...')..."` cannot read a file that `ls`/`cat`
   can see, in this Bash tool's environment.** Git Bash's POSIX layer
   resolves `/tmp/...` correctly for its own builtins (`ls`, `cat`), but a
   spawned native Windows `python.exe` receives the literal string
   `/tmp/...` (no MSYS path translation happens for paths embedded inside
   a quoted `-c` argument, only for bare command-line arguments) and can't
   resolve it. Symptom: `ls -la /tmp/foo.json` succeeds, immediately
   followed by `python3 -c "open('/tmp/foo.json')"` raising
   `FileNotFoundError` on the identical path. **Fix that worked:** pipe
   through `cat` instead of letting Python open the path itself —
   `cat /tmp/foo.json | python3 -c "import json,sys; json.load(sys.stdin)"`.
   Worth remembering for any future background-task output written to
   `/tmp` in this environment.

## Sign-off

All three suites ran for real, not just written and assumed passing.

**Correction caught while writing this section**: an earlier draft of this
report claimed `payment_lab.db`'s MD5 matched baseline after everything,
including the manual run. That's wrong, and re-checking it is what caught
the mistake — `payment_lab.db`'s MD5 does **not** match baseline anymore
(`effc704d02846dd97030a6f3a845a93a` vs. the original
`28d9a2ea9c34ed341ffbc0b30e0da0f9`). Root cause: `investigation_reports`
went from 93 to 96 rows — exactly the 3 real `POST /fraud/investigate`
calls made during Phase 4 (the empty-pre-screen probe, the real
merchant_beta run, the merchant_alpha no-regression run). That's the
correct, intended behavior of exercising the live endpoint, not test
debris — investigations are supposed to persist a report. Verified what
actually matters — `tiers`/`merchants`/`tier_tools`/`transactions` are
row-for-row identical to the Phase 0 baseline (checked directly, not
inferred from a whole-file hash) — so the *business-layer* state this test
plan is actually responsible for is clean; the file-level MD5 just isn't
the right tool to prove that once a normal side effect (report persistence)
is in play. `ai_agents/registry.json`'s MD5 **does** match baseline exactly
(`8c2a21be971ae8162e495a22d0d4a462`) — that file was only ever touched by
Phase 5's rename/revert, and rewriting it produced byte-identical output.

The automated pytest suite and stress test, run again just now after the
full manual walkthrough, still pass 40/40 and 3/3 respectively, and still
never touch the real files by design (temp-copy fixtures) — the
`investigation_reports` growth is attributable solely to the manual live
calls, not to either automated suite.

Port 5000 is clear. No `zz_`-prefixed test artifacts remain in the DB or
registry.

**Open item, not fixed in this pass:** `models/database.py`'s hardcoded
`DB_PATH` (vs. `routes/fraud.py`'s env-var-configurable one) — candidate
for `KNOWN_ISSUES.md` so `create_app()` itself becomes safely testable in
a future sprint.
