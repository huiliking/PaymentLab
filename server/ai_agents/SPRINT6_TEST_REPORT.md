# Sprint 6 Test Report — Retry-Recovery for Hallucinated Tool Calls

**Run date:** 2026-08-10
**Plan executed:** `SPRINT6_TEST_PLAN.md` (all phases)
**Scope:** `KNOWN_ISSUES.md` #6
**Base commit:** `5d3b255` (`master`), working tree uncommitted
**Environment:** Windows 11, Python 3.14.3, pytest 9.1.1, Ollama `llama3.2`

## Verdict

**Pass.** All 10 handover validation criteria met. One previously unknown defect
was discovered by the live phases, misdiagnosed once, then correctly diagnosed,
fixed, and re-verified live before commit — `KNOWN_ISSUES.md` #12, now marked
`[RESOLVED]`. Test counts below (49 → 50) reflect that fix and its regression
test landing after the initial pass.

| Phase | Result |
|---|---|
| 0 — Setup & baseline | ✅ 9 active tools, toolchain confirmed |
| 1 — Automated suite | ✅ 50 passed |
| 2 — Classifier by hand | ✅ 6/6 |
| 3 — Ollama correction path, live | ✅ finding → fixed → re-verified (#12) |
| 4 — Claude correction path, live | ⏭️ skipped — no `ANTHROPIC_API_KEY` |
| 5 — API surface | ✅ (cheap variant; live Flask not run) |
| 6 — Threshold re-validation | ✅ 9/9, narrowest clearance +0.019 |
| 7 — Regression & boundary | ✅ 40 + 25 passed, gate unchanged |
| 8 — Cleanup | ✅ dev DB untouched, runner deleted |

## What changed

| File | Change |
|---|---|
| `server/ai_agents/fraud_investigator.py` | +312/−9 — the only production change (includes the #12 fix and its comment) |
| `server/tests/test_sprint6_retry_recovery.py` | new, 1038 lines, 50 tests |
| `server/ai_agents/KNOWN_ISSUES.md` | #6 closed, #12 opened and resolved within this same sign-off |
| `server/ai_agents/SPRINT6_TEST_PLAN.md` | new |
| `server/ai_agents/SPRINT6_TEST_REPORT.md` | this file |

`tool_registry.py` and `registry.json` untouched, as the handover expected —
`get_tool(name)` already returned `None` for unknown names, which is the entire
hook the classifier needed.

---

## Phase 0 — Setup & baseline

```
Python 3.14.3 | pytest 9.1.1
active tools: 9
```

Matches the documented baseline.

## Phase 1 — Automated suite

```
50 passed in 1.90s
```

(47 at the initial pass; +3 after the #12 fix — see Phase 3 below.)

Group-by-group, summing to 50:

| Group | Tests |
|---|---|
| `TestDispatchClassification` | 7 |
| `TestPerNameRepeatCap` | 3 |
| `TestFuzzyResolutionThreshold` | 4 |
| `TestReportCaveats` | 8 |
| `TestOllamaLoop` | 11 (8 original + 3 from the #12 fix) |
| `TestClaudeLoop` | 4 |
| `TestMaxSteps` | 6 |
| `TestSprint5BackwardsCompat` + `TestNoRegression` + `TestAutoEscalation` | 7 |

Zero skips — confirming the fixtures found the real `payment_lab.db` and
`registry.json` to copy from, i.e. the run happened in the correct working copy.

## Phase 2 — Classifier by hand

| Step | Observed | Pass |
|---|---|---|
| 2.1 hallucination | `error_class: 'unknown_tool'`, message lists **only** the two granted tools; `check_velocity` absent | ✅ no grant leak |
| 2.2 out-of-grant | `Tool 'check_velocity' is not granted for this merchant's tier`, `error_class: 'not_granted'` | ✅ byte-identical to Sprint 5 |
| 2.3a inactive, unrestricted | `is not active (status: candidate)`, **no** `error_class` | ✅ real name, not a fumble |
| 2.3b inactive, off-grant | `error_class: 'not_granted'` | ✅ grant check precedes execute |
| 2.4 ordering | `execute called? False` | ✅ the load-bearing guarantee |
| 2.5 repeat cap | attempt 1 → `Available tools: …`; attempt 2 (**different params**) → `already corrected` | ✅ name-sensitive, not params-sensitive |
| 2.6 `allowed_tools=None` | `unknown_tool`, then `None` | ✅ no out-of-grant class exists |

## Phase 5 — API surface

`InvestigationReport.to_dict()` keys:

```
['confidence', 'created_at', 'evidence', 'risk_level', 'steps',
 'summary', 'tool_results', 'transaction_id', 'unresolved_checks', 'verdict']
```

Additive only — nothing removed or renamed, so `routes/fraud.py` (which returns
`report.to_dict()` verbatim) carries the new field to the frontend with no route
change.

**Not run:** the live-Flask variant. The cheap variant proves serialization; the
live one would only additionally prove the route still returns 200, which
Phase 7's Sprint 5 route tests already cover.

## Phase 6 — Threshold re-validation

`HALLUCINATION_MATCH_CUTOFF = 0.75`, measured against the live registry:

| Fumble | Target ratio | Next-best (unrelated) |
|---|---|---|
| `check_transaction_details` | 0.875 | 0.408 |
| `get_locale_consistency` | 0.870 | 0.579 |
| `check_address_history` | 0.850 | 0.595 |
| `check_device_history` | 0.842 | 0.649 |
| `get_identity_graph` | 0.842 | 0.457 |
| `check_email_history` | 0.833 | 0.606 |
| `check_ip_reputation` | 0.833 | 0.513 |
| `check_card_history` | 0.824 | 0.649 |
| `get_velocity` | **0.769** | 0.552 |

9/9 OK. **Narrowest clearance: +0.019** (`get_velocity` → `check_velocity`).
That case is the reason the threshold is pinned by a test rather than trusted as
a constant — it would not survive much drift in the active tool names.

## Phase 7 — Regression & boundary

| Check | Result |
|---|---|
| 7.1 Sprint 5 suite | **40 passed** |
| 7.1b `test_dispatch_blocks_out_of_grant_tool_and_never_calls_underlying_method` | **1 passed** — the load-bearing backwards-compat proof |
| 7.2 Sprint 5 test file unmodified | no diff output ✅ |
| 7.3 no new snapshot source | attrs = `['_correction_message', '_hallucination_attempts', 'allowed_tools', 'execute', 'get', 'get_tool', 'registry', 'tools']`; banned (`load`/`reload`/`open`) = **none** ✅ `KNOWN_ISSUES` #10 stays satisfied |
| 7.4 `test_claude_provider.py` | **25 passed** |

## Phase 4 — Claude path (skipped)

`ANTHROPIC_API_KEY` is not set in this environment, so the live Claude loop could
not run. The `anthropic` SDK is installed (0.84.0), so this is a credentials gap,
not a dependency one.

**Coverage impact: low.** `TestClaudeLoop` (4 tests) exercises the same code path
deterministically with the client mocked — including the parallel-batch case, the
hedged-batch case, and the `tool_result`-per-`tool_use`-id API contract. What
remains unverified is only that a real Sonnet response object has the attribute
shape the loop expects, and that path is unchanged from Sprint 5.

---

## Phase 3 — Ollama correction path, live

The automated suite mocks `_llm_plan()`, so it cannot show that a correction
reaches a prompt a live model actually reads. These runs keep every real Ollama
HTTP call and inject a fumble only at the `_parse_plan` boundary; every other
turn is a genuine model decision. Transaction `b79fe282-122`, temp copy of the
DB, grant restricted to keep runs short.

### 3.1 — Unresolved fumble → caveat and cap

```
unresolved_checks : [{'tool_name': 'check_email_history', 'attempts': 4,
                      'error_class': 'unknown_tool'}]
confidence        : 0.85
summary           : …[1 intended check(s) could not be completed due to tool name errors.]
tools_called      : ['check_locale_consistency', 'get_ip_reputation', 'get_transaction_details']
GATHER steps for the bad name (must be none): []
```

Console: `[CORRECTION] Confidence capped 0.9 → 0.85 (1 unresolved check(s))`.

| Criterion | Result |
|---|---|
| Corrective message on attempt 1, terminal thereafter | ✅ |
| Exactly one `unresolved_checks` entry despite 4 attempts | ✅ tracker is keyed by name |
| Cap 0.85 (not 0.7 — one *name*, not one per attempt) | ✅ |
| Summary self-documents the gap | ✅ |
| Zero evidence pollution | ✅ `GATHER … []`, bad name absent from `tools_called` |
| Investigation completed, never aborted | ✅ |

**`attempts: 4`, not the 2 the runner injects.** See the finding below.

### 3.2 — The correction reaches the next real prompt

```
TOOL NAME CORRECTIONS present : True
bad name present              : True
iteration ceiling line        : ['4. ITERATION: 2 of 8.']
```

### 3.3 — Self-correction → no caveat

Same injection, but with `get_email_history` in the grant, and the corrected
name forced on turn 2:

```
unresolved_checks : []
confidence        : 0.9
tools_called      : ['check_locale_consistency', 'get_email_history',
                     'get_ip_reputation', 'get_transaction_details']
CORRECTION steps  : 1  (attempt 1, corrective list)
GATHER steps for the bad name (must be none): []
```

Console: `[CORRECTION] 'check_email_history' resolved by a later call — no caveat`.

**This is the live proof that the `difflib` resolution mechanism works
end-to-end.** The fumble is logged as a `CORRECTION` step but not penalized;
confidence stays at the model's own 0.9 rather than being capped to 0.85. A
recovered investigation and a failed one now produce different confidence
signals — which was the entire point of the design argument that shaped this
sprint.

### FINDING → FIX → RE-VERIFIED — `KNOWN_ISSUES` #12

**Finding.** 3.1 injects **two** fumbles and recorded **four**. Iterations 6 and
7 show `llama3.2` choosing `TOOL: check_email_history` *unprompted* — a name
absent from its `AVAILABLE TOOLS` list, present in the prompt only inside the
`TOOL NAME CORRECTIONS:` block that names it in order to correct it.

**First fix attempt (insufficient) — caught before commit.** The obvious fix
filters `context["corrections"]` to drop `terminal is True` entries before
rendering. A live re-run of the identical scenario made it *worse*: 5 attempts,
not 2, with the corrective (non-terminal) message present in **7 of 8** prompts
— unchanged from before the filter. A prompt-dumping diagnostic explained why:
`context["corrections"]` was never pruned, only appended to. Because
`HALLUCINATION_REPEAT_CAP = 1`, attempt 1 for any name is *always* non-terminal
by construction, so the terminal filter never touched it — that first entry sat
in the list and was re-rendered into every subsequent prompt for the rest of the
investigation. The filter removed the wrong entries.

**Actual fix.** `_llm_plan()` now clears `context["corrections"] = []`
immediately after building the block. Each entry can appear in at most one
prompt — the turn right after it was appended — never again. Verified with a
regression test that a single-call test couldn't have caught:
`test_correction_shown_at_most_once_across_calls` calls `_llm_plan()` three
times over one unconsumed correction and asserts the name appears in the first
prompt only.

**Re-verified live**, same transaction, same model, same two injected fumbles,
every prompt logged and compared:

| Variant | Bad name in prompt | `attempts` |
|---|---|---|
| No fix | 7 of 8 iterations | 4 |
| Terminal-filter only | 7 of 8 iterations | **5 — worse** |
| Consumed-on-read (shipped) | **1 of 8** iterations | **2** |

With the real fix, the name appears exactly once (the deliberate display at
iteration 2) and the model did not re-hallucinate on its own for the remaining
six turns. `attempts: 2` matches the injected count precisely — zero spontaneous
repeats. That's strong evidence the earlier echo was driven by persistent prompt
content rather than pure model bias, though it's one run, not a statistical
claim across seeds or models.

**Not a safety or correctness defect at any point** — true of the unfixed
version, the insufficient fix, and the real fix alike: the gate refused every
attempt, accounting stayed correct (one name → one `unresolved_checks` entry →
0.85 cap, never 0.7, regardless of attempt count), evidence stayed clean
(`GATHER … []` in every variant), and the run always completed and produced a
verdict.

### 3.4 — Turn ceiling and override

Plain run, no injection, unrestricted grant:

- **8** `[PLAN]` LLM calls — the new Ollama ceiling. The same run at `5d3b255`
  would have stopped at 6.
- No `[CORRECTION]` lines (the model didn't fumble), `unresolved_checks` empty,
  confidence untouched at 0.90, verdict `CRITICAL` / `FRAUDULENT`.
- Auto-escalation to `check_identity_graph` fired on address reuse (6 cards);
  the dedup `[SKIP]` guard still caught a repeated `get_email_history`. Both
  Sprint 5 behaviors intact.

Override, `--max-steps 3`:

- **3** `[PLAN]` LLM calls — an explicit value wins over the Ollama default of 8,
  confirming the `None`-sentinel design distinguishes "caller passed 3" from
  "caller passed nothing".
- Still reached a verdict (`FRAUDULENT`, 90%), so a tighter budget degrades
  evidence depth without breaking the loop.

## Phase 8 — Cleanup

- Throwaway runner `_phase3_hallucination_runner.py` deleted.
- All live phases ran against **temp copies** of `payment_lab.db`;
  `git status -- server/payment_lab.db` is empty, so the dev database was never
  written to.
- Working tree contains only the five intended files.

---

## Criteria coverage

| # | Handover criterion | Evidence |
|---|---|---|
| 1 | Hallucination gets corrective feedback | Phase 2.1, 3.1 |
| 2 | Out-of-grant unchanged | Phase 2.2, 7.1b |
| 3 | Per-name repeat cap | Phase 2.5, 3.1 |
| 4 | Corrections don't pollute evidence | Phase 3.1, 3.3 (`GATHER … []`) |
| 5 | Report carries unresolved checks | Phase 3.1, 5 |
| 6 | Confidence capped | Phase 3.1 (0.9 → 0.85), 3.3 (uncapped when resolved) |
| 7 | Out-of-grant does NOT caveat | Phase 1 `test_out_of_grant_does_not_caveat`, Phase 2.2 |
| 8 | Ollama 8 / Claude 6, override wins | Phase 3.4 (8 and 3) |
| 9 | Both backends handle correction | Phase 3.1–3.3 live (Ollama); Phase 1 `TestClaudeLoop` mocked (Claude) |
| 10 | No regression | Phase 3.4, 7.1–7.4 |

## Open items from this run

1. **`KNOWN_ISSUES` #12 — resolved before commit.** Found, misdiagnosed once
   (a terminal-only filter made it worse — 5 attempts, not fewer), correctly
   diagnosed (the corrections channel was never pruned), fixed (consume on
   read), and re-verified live: bad name in prompt dropped from 7-of-8
   iterations to 1-of-8, `attempts` dropped from 4–5 to 2 (matching the
   injected count exactly, zero spontaneous repeats). Worth a second live run
   against a different model/transaction before treating the single
   measurement as representative rather than indicative.
2. **Phase 4 unrun** — no `ANTHROPIC_API_KEY`. Worth one live Claude run when a
   key is available, purely to confirm the response-object shape; the logic is
   already covered by mocks.
3. **Pre-existing, unfixed:** `python ai_agents/fraud_investigator.py` fails with
   `ImportError` (relative import at line 23) at `5d3b255` and still does. Use
   `python -m ai_agents.fraud_investigator`. Out of scope for Sprint 6, but the
   CLI advertises an entry point that doesn't work.
4. **`get_velocity` threshold margin** — +0.019 above the cutoff, the narrowest
   of the nine. Any rename near `check_velocity` should be treated as requiring
   a Phase 6 re-run.
