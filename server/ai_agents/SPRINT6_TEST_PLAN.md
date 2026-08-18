# Sprint 6 Test Plan — Retry-Recovery for Hallucinated Tool Calls

Validates the Sprint 6 deliverables (see `SPRINT6_HANDOVER.md`) against all 10
validation criteria: the `_dispatch()` classifier, per-name attempt tracking,
both loop integrations, `InvestigationReport.unresolved_checks`, the confidence
cap, and per-backend `max_steps`. Everything lives in one production file,
`server/ai_agents/fraud_investigator.py`.

Re-run this whenever `_dispatch()`, either investigation loop, `_llm_plan()`'s
prompt assembly, `InvestigationReport`, or `registry.json`'s **active tool
names** change. The last one matters more than it looks — Phase 6 exists
because renaming an active tool can silently move the fuzzy-resolution
threshold.

**The pytest suite (`server/tests/test_sprint6_retry_recovery.py`) needs no
live server, no Ollama, and no API key, and is the primary source of truth.**
It covers all 10 criteria deterministically. If you only have time for one form
of verification, run Phase 1. Phases 3–5 are the supplementary
closer-to-real-usage pass; they are slower, need a live backend, and are
non-deterministic in exactly the way the automated suite is designed to
compensate for.

**Inherited fragility (Sprint 4/5):** phases that drive the live Flask server
on port 5000 can hit the state where `Get-Process`/`taskkill` no longer see the
PID while the port stays live. The fix that works: touch `server/app.py` so
Werkzeug's reloader cycles the process. Only Phase 5 is exposed to this.

**Pre-existing CLI break — not a Sprint 6 regression.** Line 23 of
`fraud_investigator.py` is a relative import (`from .identity_graph import
...`), so `python ai_agents/fraud_investigator.py` fails with `ImportError:
attempted relative import with no known parent package`. This is true at
`5d3b255` too. Every command below uses the module form,
`python -m ai_agents.fraud_investigator`.

---

## Phase 0 — Setup & baseline

All commands run **from `server/`**.

```bash
cd server
python -c "import sys, pytest; print('Python', sys.version.split()[0], '| pytest', pytest.__version__)"
```

Record the baseline so later phases can prove nothing drifted:

```bash
python -c "
import json
d = json.load(open('ai_agents/registry.json', encoding='utf-8'))
active = [t['name'] for t in d['tools'] if t['status'] == 'active']
print('active tools:', len(active))
for n in sorted(active): print('  ', n)
"
```

**Baseline as of this sprint:** 9 active tools —
`check_identity_graph`, `check_locale_consistency`, `check_velocity`,
`get_address_history`, `get_card_history`, `get_device_history`,
`get_email_history`, `get_ip_reputation`, `get_transaction_details`.

**Pass criteria:** command runs; note the count. If it is no longer 9, Phase 6
is mandatory rather than optional.

---

## Phase 1 — Automated suite (primary evidence)

```bash
python -m pytest tests/test_sprint6_retry_recovery.py -v
```

**Pass criteria:** 47 passed, 0 failed, 0 skipped.

A **skip** here is a failure of this plan, not a pass — the module-level
`pytestmark` skips the whole file when `payment_lab.db` or `registry.json` is
missing. If you see "47 skipped", you are in the wrong directory or the wrong
working copy (see the note at the end of this file).

Group-by-group, so a partial failure localizes fast:

```bash
python -m pytest tests/test_sprint6_retry_recovery.py -v -k "TestDispatchClassification"
python -m pytest tests/test_sprint6_retry_recovery.py -v -k "TestPerNameRepeatCap"
python -m pytest tests/test_sprint6_retry_recovery.py -v -k "TestFuzzyResolutionThreshold"
python -m pytest tests/test_sprint6_retry_recovery.py -v -k "TestReportCaveats"
python -m pytest tests/test_sprint6_retry_recovery.py -v -k "TestOllamaLoop"
python -m pytest tests/test_sprint6_retry_recovery.py -v -k "TestClaudeLoop"
python -m pytest tests/test_sprint6_retry_recovery.py -v -k "TestMaxSteps"
python -m pytest tests/test_sprint6_retry_recovery.py -v -k "TestSprint5BackwardsCompat or TestNoRegression or TestAutoEscalation"
```

---

## Phase 2 — Classifier, by hand (no server, no LLM)

Proves the gate classifies correctly and — critically — that it never executes
before classifying.

```bash
python -c "
import shutil, tempfile, os, sys
sys.path.insert(0, '.')
from unittest.mock import MagicMock
from ai_agents.fraud_investigator import FraudInvestigator

d = tempfile.mkdtemp()
db = os.path.join(d, 'p.db'); rg = os.path.join(d, 'r.json')
shutil.copy('payment_lab.db', db); shutil.copy('ai_agents/registry.json', rg)

inv = FraudInvestigator(db_path=db, registry_path=rg,
                        allowed_tools=['get_transaction_details', 'check_locale_consistency'])

inv_open = FraudInvestigator(db_path=db, registry_path=rg)   # no merchant resolved

print()
print('2.1 hallucination        ', inv._dispatch('check_email_history', {}))
print('2.2 out-of-grant         ', inv._dispatch('check_velocity', {'card_last4':'4242'}))
print('2.3a inactive, no grant  ', inv_open._dispatch('check_amount_anomaly', {}))
print('2.3b inactive, off-grant ', inv._dispatch('check_amount_anomaly', {}))

# 2.4 — the ordering rule: a hallucinated name must never reach execute()
inv.registry.execute = MagicMock()
inv._dispatch('check_email_history', {})
print('2.4 execute called?      ', inv.registry.execute.called)

# 2.5 — repeat cap
inv2 = FraudInvestigator(db_path=db, registry_path=rg, allowed_tools=['get_transaction_details'])
first = inv2._dispatch('check_email_history', {}); inv2._note_hallucination('check_email_history')
second = inv2._dispatch('check_email_history', {'different':'params'})
print('2.5 first  ', first['error'])
print('2.5 second ', second['error'])

# 2.6 — no merchant resolved
print('2.6 CLI halluc           ', inv_open._dispatch('nope_not_real', {})['error_class'])
print('2.6 CLI real tool        ', inv_open._dispatch('check_velocity', {'card_last4':'4242'}).get('error_class'))
" 2>&1 | grep -v "BOOT\|====\|FRAUD INVEST\|Active tools\|Registry:\|Merchant tool\|ToolRegistry"
```

**Pass criteria:**

| Step | Expected |
|---|---|
| 2.1 | `error_class: 'unknown_tool'`; message lists **only** `get_transaction_details, check_locale_consistency` — `check_velocity` must **not** appear (grant leak) |
| 2.2 | `error_class: 'not_granted'`; message exactly `Tool 'check_velocity' is not granted for this merchant's tier` |
| 2.3a | `error_class` **absent**; error says `is not active (status: candidate)` |
| 2.3b | `error_class: 'not_granted'` — see the note below; this is correct, not a misclassification |
| 2.4 | `False` — the load-bearing ordering guarantee |
| 2.5 | first contains `Available tools:`; second contains `already corrected` — and note the params differed, proving the cap is name-sensitive |
| 2.6 | `unknown_tool`, then `None` — with `allowed_tools=None` there is no out-of-grant class at all |

**Why 2.3 is split.** An inactive tool is a *real name*, so it is never
classified as a hallucination — that is the invariant being tested. But which
of the two remaining outcomes you get depends on the grant, because the grant
check (step 2) runs before execution (step 3):

- **outside the grant** → `not_granted`, and `registry.execute()` is never
  reached, so the `not active` message never appears (2.3b);
- **unrestricted, or explicitly granted** → falls through to
  `registry.execute()`, which reports `not active` (2.3a).

Both are correct. The thing that would be a bug is either one coming back as
`unknown_tool`. Asserting `not active` against a *restricted* investigator is a
mistake — it will fail, and the failure is in the expectation, not the code.

---

## Phase 3 — Ollama correction path, live (the real gap-closer)

Phase 1 mocks `_llm_plan()`. This phase keeps the **real** Ollama HTTP calls and
the **real** prompt-rebuild path, injecting a fumble only at the parse boundary.
That is what proves the correction actually reaches the next prompt a live model
sees — the one thing the automated suite cannot show.

Requires Ollama running with `llama3.2`. Takes several minutes per run.

Create the throwaway runner (delete it after — it is not part of the suite):

```bash
cat > _phase3_hallucination_runner.py <<'PY'
"""Throwaway — Sprint 6 Phase 3. Delete after use.

Injects a hallucinated tool name at the _parse_plan boundary so the live
Ollama loop exercises the correction path deterministically. Every other
turn is a real model decision against a real Ollama endpoint.

Usage: python _phase3_hallucination_runner.py <txn_id> <mode>
  mode=unresolved  fumble twice, never recover -> caveat + cap expected
  mode=resolved    fumble once, then force the correct name -> no caveat
"""
import os, shutil, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ai_agents.fraud_investigator import FraudInvestigator

txn_id = sys.argv[1]
mode = sys.argv[2] if len(sys.argv) > 2 else "unresolved"

tmp_db = os.path.join(tempfile.mkdtemp(), "payment_lab.db")
shutil.copy("payment_lab.db", tmp_db)

# Restricted grant keeps the run short and, in unresolved mode, guarantees
# the model cannot stumble onto get_email_history and resolve the fumble.
grant = ["get_transaction_details", "check_locale_consistency", "get_ip_reputation"]
if mode == "resolved":
    grant.append("get_email_history")

inv = FraudInvestigator(db_path=tmp_db, backend="ollama", model="llama3.2",
                        allowed_tools=grant)

real_parse = inv._parse_plan
state = {"n": 0}

def parse_with_injection(answer, context):
    plan = real_parse(answer, context)
    if plan is None or plan.get("tool") == "CONCLUDE":
        return plan
    state["n"] += 1
    if mode == "unresolved" and state["n"] <= 2:
        return {"reasoning": "INJECTED fumble", "tool": "check_email_history", "params": {}}
    if mode == "resolved":
        if state["n"] == 1:
            return {"reasoning": "INJECTED fumble", "tool": "check_email_history", "params": {}}
        if state["n"] == 2:
            return {"reasoning": "INJECTED self-correction", "tool": "get_email_history",
                    "params": {"email": context["transaction"].get("customer_email")}}
    return plan

inv._parse_plan = parse_with_injection
report = inv.investigate(txn_id)

print("\n" + "=" * 60)
print("PHASE 3 RESULT  mode =", mode)
print("=" * 60)
print("unresolved_checks :", report.unresolved_checks)
print("confidence        :", report.confidence)
print("summary           :", report.summary)
print("tools_called      :", sorted(report.tool_results.keys()))
print("CORRECTION steps  :")
for s in report.steps:
    if s.phase == "CORRECTION":
        print("   -", s.action, "|", s.result[:90])
print("GATHER steps for the bad name (must be none):",
      [s.action for s in report.steps
       if s.phase == "GATHER" and "check_email_history" in (s.tool_used or "")])
PY
```

Pick a flagged transaction:

```bash
python -m ai_agents.fraud_investigator --db payment_lab.db --list | head -5
```

### 3.1 — Unresolved fumble → caveat and cap

```bash
python _phase3_hallucination_runner.py <txn_id> unresolved 2>&1 | tee phase3_unresolved.log
```

**Pass criteria:**

- Console shows `[CORRECTION]` exactly **twice**: the first listing
  `Available tools:`, the second saying `already corrected and remains
  unavailable`.
- `unresolved_checks` has exactly **one** entry, `tool_name:
  'check_email_history'`, `attempts: 2`, `error_class: 'unknown_tool'`.
- `confidence` ≤ **0.85**.
- `summary` ends with `[1 intended check(s) could not be completed due to tool name errors.]`
- `GATHER steps for the bad name` is `[]` — the fumble produced no evidence.
- `check_email_history` is absent from `tools_called`.

**This phase doubles as the regression check for `KNOWN_ISSUES.md` #12.** An
earlier version of `_llm_plan()` re-displayed a hallucinated name on every
subsequent turn because `context["corrections"]` was never pruned, so the
runner's two injected fumbles could balloon into 4–5 recorded attempts as the
model echoed the name back from its own prompt. The fix makes each correction
visible for exactly one turn (`_llm_plan()` clears the channel after reading
it), so `attempts` should now equal the injected count precisely.

**If `attempts` exceeds 2, treat it as a regression, not noise** — verify:

- whether the bad name appears in more than one prompt (dump prompts as in
  3.2 if so — that means the consume-on-read fix broke);
- if prompts are clean but the model still fumbled independently (no
  reinforcement, genuine repeat), the entry count in `unresolved_checks` must
  still be **1**, not one per attempt (the tracker is keyed by name — 4+
  entries or a 0.7 cap here is a real bug regardless of cause).

### 3.2 — The correction reaches the next real prompt

```bash
grep -n "TOOL NAME CORRECTIONS" phase3_unresolved.log
```

The prompt itself is not echoed to stdout, so confirm it directly:

```bash
python -c "
import shutil, tempfile, os, sys
sys.path.insert(0, '.')
from unittest.mock import MagicMock
import ai_agents.fraud_investigator as mod
from ai_agents.fraud_investigator import FraudInvestigator

d = tempfile.mkdtemp(); db = os.path.join(d,'p.db'); rg = os.path.join(d,'r.json')
shutil.copy('payment_lab.db', db); shutil.copy('ai_agents/registry.json', rg)
inv = FraudInvestigator(db_path=db, registry_path=rg, allowed_tools=['get_transaction_details'])

txn = inv.tools.get_transaction_details(
    __import__('sqlite3').connect(db).execute('SELECT id FROM transactions LIMIT 1').fetchone()[0]
).get('result', {})
ctx = {'transaction': txn, 'pre_screen_triggers': [], 'evidence_gathered': [],
       'tools_called': [], 'corrections': [
           {'tool_name': 'check_email_history',
            'message': \"'check_email_history' is not a valid tool. Available tools: get_transaction_details\",
            'terminal': False}]}

seen = {}
def fake_post(url, json=None, timeout=None):
    seen['prompt'] = json['prompt']
    r = MagicMock(); r.status_code = 200
    r.json.return_value = {'response': 'REASONING: x\nTOOL: CONCLUDE\nPARAMS: none'}
    return r
mod.requests.post = fake_post
inv._llm_plan(ctx, 1)
p = seen['prompt']
print('TOOL NAME CORRECTIONS present :', 'TOOL NAME CORRECTIONS:' in p)
print('bad name present              :', 'check_email_history' in p)
print('iteration ceiling line        :', [l for l in p.splitlines() if 'ITERATION:' in l])
" 2>&1 | grep -v "BOOT\|====\|FRAUD INVEST\|Active tools\|Registry:\|Merchant tool\|ToolRegistry"
```

**Pass criteria:** both `True`; the ceiling line reads `ITERATION: 2 of 8`.

### 3.3 — Self-correction → no caveat

```bash
python _phase3_hallucination_runner.py <txn_id> resolved 2>&1 | tee phase3_resolved.log
```

**Pass criteria:**

- One `[CORRECTION]` in the console, and a `CORRECTION` step still present in
  the report — the fumble is *logged*, just not *penalized*.
- `get_email_history` appears in `tools_called` / `tool_results`.
- `unresolved_checks` is `[]`.
- `confidence` is whatever the model returned, **not** capped at 0.85.
- Console shows `resolved by a later call — no caveat`.

### 3.4 — Turn ceiling is 8, and the clean path is unchanged

A plain run, no injection:

```bash
cp payment_lab.db /tmp/live.db
python -m ai_agents.fraud_investigator --db /tmp/live.db --backend ollama --txn <txn_id> 2>&1 | tee phase34.log
grep -c "\[PLAN\] LLM response" phase34.log
```

**Pass criteria:** the count is **8** (it was 6 before Sprint 6), unless the
model chose `CONCLUDE` earlier — in which case check the step trace shows a
deliberate conclusion rather than a truncation. `unresolved_checks` empty,
confidence untouched, `[CORRECTION]` absent, auto-escalation and the dedup
`[SKIP]` guard still behaving as in Sprint 5.

And the override:

```bash
python -m ai_agents.fraud_investigator --db /tmp/live.db --backend ollama --max-steps 3 --txn <txn_id> 2>&1 | grep -c "\[PLAN\] LLM response"
```

**Pass criteria:** 3 — an explicit `--max-steps` overrides both backends.

---

## Phase 4 — Claude correction path, live (optional)

Needs `ANTHROPIC_API_KEY` and spends real tokens. Skip unless the Claude path
changed; `TestClaudeLoop` covers the same ground deterministically.

```bash
python -m ai_agents.fraud_investigator --db /tmp/live.db --backend claude --txn <txn_id> 2>&1 | tee phase4.log
grep -n "CORRECTION\|Turn \|TOKENS" phase4.log | head -20
```

**Pass criteria:** at most 6 turns (Claude's ceiling is unchanged). If a
`[CORRECTION]` appears — Sonnet rarely fumbles a name, so this may not trigger —
the corrective string must have gone back as that `tool_use` id's `tool_result`,
and the fumble must not appear in the evidence trail.

**Do not treat the absence of a correction as a failure.** It means the model
didn't hallucinate, which is the normal case.

---

## Phase 5 — API surface (live Flask)

Proves `unresolved_checks` actually reaches the HTTP response, since the React
frontend reads `report.to_dict()` verbatim.

```bash
# terminal 1
python app.py

# terminal 2 — slow (a full Ollama investigation)
curl -s -X POST http://localhost:5000/api/fraud/investigate/<txn_id> \
  | python -c "import json,sys; d=json.load(sys.stdin); print('unresolved_checks' in d, d.get('unresolved_checks'))"
```

**Pass criteria:** `True` and a list (empty is fine — presence of the key is
what's being tested). A `KeyError`/`False` means `to_dict()` regressed.

Cheaper equivalent if you don't want to run a live investigation:

```bash
python -c "
import sys; sys.path.insert(0,'.')
from ai_agents.fraud_investigator import InvestigationReport
d = InvestigationReport(transaction_id='t').to_dict()
print(sorted(d.keys()))
print('unresolved_checks' in d)
"
```

**Pass criteria:** the key set is exactly `created_at, confidence, evidence,
risk_level, steps, summary, tool_results, transaction_id, unresolved_checks,
verdict` — additive only, nothing removed.

---

## Phase 6 — Threshold re-validation (mandatory after any tool rename)

The fuzzy-resolution cutoff is a **property of the current tool names**, not a
universal constant. Re-run whenever `registry.json`'s active names change.

```bash
python -m pytest tests/test_sprint6_retry_recovery.py -v -k "TestFuzzyResolutionThreshold"
```

To see the actual margins rather than just pass/fail:

```bash
python -c "
import json, difflib, sys
sys.path.insert(0,'.')
from ai_agents.fraud_investigator import HALLUCINATION_MATCH_CUTOFF as CUT
active = [t['name'] for t in json.load(open('ai_agents/registry.json', encoding='utf-8'))['tools']
          if t['status']=='active']
def swap(n):
    return 'check_'+n[4:] if n.startswith('get_') else ('get_'+n[6:] if n.startswith('check_') else None)
print(f'cutoff = {CUT}')
worst = 1.0
for t in active:
    f = swap(t)
    if not f or f in active: continue
    hit = difflib.SequenceMatcher(None, f, t).ratio()
    nxt = max(difflib.SequenceMatcher(None, f, o).ratio() for o in active if o != t)
    worst = min(worst, hit - CUT)
    flag = 'OK  ' if hit >= CUT and nxt < CUT else 'FAIL'
    print(f'{flag} {f:28s} target={hit:.3f}  next={nxt:.3f}')
print(f'\nnarrowest clearance above the cutoff: {worst:+.3f}')
"
```

**Pass criteria:** every line `OK`. **Known-tight case:** `get_velocity` →
`check_velocity` clears by only **0.019** (0.769 vs. 0.75). If the narrowest
clearance goes negative, do not just lower the cutoff — a fumble that no longer
resolves means recovered investigations get penalized, and a cutoff low enough
to catch it may start resolving fumbles to the *wrong* tool. Re-derive the
corridor from the printed table first.

**Deliberately not asserted:** similarity between two *real* active tools. Five
such pairs already exceed 0.75 (`get_email_history`/`get_device_history` =
0.800). That is not a defect — the matcher only ever takes a hallucinated name
as its left-hand argument, so real names never appear in that position.

---

## Phase 7 — Regression & boundary checks

```bash
# 7.1 — the load-bearing Sprint 5 gate test, unchanged. Run this file ALONE
# (its isolated_env fixture asserts routes.fraud isn't already imported).
python -m pytest tests/test_sprint5_business_layer.py -v
```

**Pass criteria:** 40 passed. In particular
`test_dispatch_blocks_out_of_grant_tool_and_never_calls_underlying_method`
passes with the same assertions and the same fixture as at `5d3b255`.

```bash
# 7.2 — the Sprint 5 test file was not edited
git diff --name-only -- server/tests/test_sprint5_business_layer.py
```

**Pass criteria:** no output.

```bash
# 7.3 — no new snapshot source (KNOWN_ISSUES #10 must stay satisfied).
# Classification may read self.registry / self.allowed_tools and nothing else.
python -c "
import ast, sys
src = open('ai_agents/fraud_investigator.py', encoding='utf-8').read()
tree = ast.parse(src)
fn = next(n for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and n.name == '_dispatch')
attrs = sorted({n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)})
print('attributes touched by _dispatch:', attrs)
banned = [a for a in attrs if a in ('load', 'reload', '_load', 'open')]
print('reload/IO calls inside the gate:', banned or 'none')
"
```

**Pass criteria:** the attribute list is exactly

```
['_correction_message', '_hallucination_attempts', 'allowed_tools',
 'execute', 'get', 'get_tool', 'registry', 'tools']
```

and the banned list is `none`. The point is what's *absent*: no `load`,
`reload`, or file read inside the gate. `self.registry` is the same snapshot
the schemas came from, so classification introduces no second source of tool
names and `KNOWN_ISSUES` #10 stays satisfied. A new entry appearing in this
list is not automatically a failure, but it needs justifying — if it reads tool
names from anywhere other than `self.registry` / `self.allowed_tools`, that is
a regression.

```bash
# 7.4 — the other suites still pass
python -m pytest tests/test_claude_provider.py -q
```

**Pass criteria:** 25 passed.

```bash
# 7.5 — only the intended files changed
git status --short
```

**Pass criteria:** modified `server/ai_agents/fraud_investigator.py` and
`server/ai_agents/KNOWN_ISSUES.md`; new `server/tests/test_sprint6_retry_recovery.py`,
`server/ai_agents/SPRINT6_TEST_REPORT.md`, `server/ai_agents/SPRINT6_TEST_PLAN.md`.
Pre-existing untracked `.log`/`.png` files and `.claude/` are expected noise.

---

## Phase 8 — Cleanup

```bash
rm -f _phase3_hallucination_runner.py phase3_unresolved.log phase3_resolved.log phase34.log phase4.log
rm -f /tmp/live.db
git status --short
```

**Pass criteria:** matches the Phase 7.5 list exactly — no throwaway runner, no
phase logs. Note that Phases 3–5 all operate on **temp copies** of
`payment_lab.db`, so the dev database should be untouched; confirm with:

```bash
git status --short -- payment_lab.db
```

**Pass criteria:** no output (the dev DB is tracked and must be unmodified).

---

## Exit criteria

| # | Handover criterion | Proven by |
|---|---|---|
| 1 | Hallucination gets corrective feedback | Phase 2.1, Phase 3.1 |
| 2 | Out-of-grant unchanged | Phase 2.2, Phase 7.1 |
| 3 | Per-name repeat cap | Phase 2.5, Phase 3.1 |
| 4 | Corrections don't pollute evidence | Phase 3.1 (`GATHER steps ... []`), Phase 1 `TestOllamaLoop` |
| 5 | Report carries unresolved checks | Phase 3.1, Phase 5 |
| 6 | Confidence capped | Phase 3.1, Phase 1 `TestReportCaveats` |
| 7 | Out-of-grant does NOT caveat | Phase 1 `test_out_of_grant_does_not_caveat`, Phase 2.2 |
| 8 | Ollama 8 / Claude 6, override wins | Phase 3.4, Phase 4 |
| 9 | Both backends handle correction | Phase 3.1–3.3 (Ollama, live), Phase 1 `TestClaudeLoop` |
| 10 | No regression | Phase 3.4, Phase 7.1–7.4 |

Sign-off requires **Phase 1 + Phase 2 + Phase 7** at minimum. Phases 3–6 are
required for a full sprint-close sign-off; Phase 4 is optional (costs tokens),
and Phase 6 is mandatory only when tool names changed.

---

## What this plan does not prove

Stated plainly so the gaps aren't mistaken for coverage:

- **That a real model, unprompted, recovers from its own fumble.** Every live
  correction in Phase 3 is *injected*. Hallucination can't be induced on demand,
  so "the model reads the correction and picks the right tool next turn" remains
  inferred from the prompt containing the correction (Phase 3.2), not observed
  end-to-end. Watching production traces for `CORRECTION` steps is the only real
  evidence here.
- **Whether 0.75 is the right cutoff for names that don't yet exist.** Phase 6
  validates the current registry only.
- **Concurrency.** No phase exercises two investigations racing. Unchanged from
  Sprint 5 (`KNOWN_ISSUES` #7) — Sprint 6 added no shared mutable state beyond
  `_hallucination_attempts`, which is per-instance and reset per investigation,
  but "reasoned safe" is not "tested safe."
- **Frontend rendering.** `unresolved_checks` is serialized (Phase 5) but no UI
  consumes it; that was explicitly out of scope.

## See also

- `server/tests/test_sprint6_retry_recovery.py` — the automated suite; Phases 1
  and 6 are just entry points into it.
- `server/ai_agents/SPRINT6_TEST_REPORT.md` — results of the actual sign-off run,
  including the measured similarity table and the live-run caveats.
- `server/ai_agents/KNOWN_ISSUES.md` #6 — the issue this sprint closed, with the
  implementation note explaining why resolution is a similarity test.

**Working-copy warning.** There is a stale pre-Sprint-4 copy of this project at
`C:\Users\Administrator\server\` (inside a git repo rooted at the home
directory). It has no `_dispatch()` and no `business/` layer. Everything above
assumes
`C:\Users\Administrator\Documents\Research\AI\AI_Payment\payment-lab`.
If Phase 1 reports "47 skipped" or an `ImportError` on `business.service`, check
which copy you are in before debugging anything else.
