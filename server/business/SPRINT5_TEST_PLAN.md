# Sprint 5 Test Plan — Business Layer + Tool Profiles

Validates the Sprint 5 deliverables (see `SPRINT5_HANDOVER.md` and
`project_sprint5_business_layer.md` in memory) against all 10 validation
criteria: merchants/tiers/tier_tools data model + migration
(`server/models/database.py`), `tool_id` on the registry
(`server/ai_agents/tool_registry.py`), the `BusinessLayer` resolver
(`server/business/service.py`), the `commercial_admin` auth seam
(`server/business/auth.py`), the admin API (`server/routes/business.py`),
and server-side dispatch enforcement (`server/ai_agents/fraud_investigator.py`).

Re-run this whenever `database.py`'s migration, `business/service.py`,
`routes/business.py`, `routes/fraud.py`, or `fraud_investigator.py`'s
`_dispatch`/`allowed_tools` handling change, to catch regressions.

**Known fragility, inherited from Sprint 4** (`SPRINT4_TEST_REPORT.md`
Appendix): the dev server on port 5000 can end up in a state where
`Get-Process`/`tasklist`/`taskkill` no longer see the PID while the port
stays live. The fix that worked there: touch `server/app.py` so Werkzeug's
debug-mode auto-reloader cycles the process — don't fight process managers.
Phases 2–5 below drive a live server and inherit this risk.

**The pytest suite (`server/tests/test_sprint5_business_layer.py`) needs no
live server and is the more reliable source of truth.** If you only have
time for one form of verification, run that one — it covers criteria
1–3, 5, 8–10 deterministically. These manual phases are the supplementary,
closer-to-real-usage pass (curl against an actual running Flask process),
not the primary evidence.

## Phase 0 — Setup & baseline

```bash
# From server/, with the venv active and PAYMENTLAB_ADMIN_KEY set (see .env)
python app.py
```

Record the baseline before touching anything, so test data can be reverted:

```bash
python -c "
import sqlite3
conn = sqlite3.connect('payment_lab.db')
print('tiers:', conn.execute('SELECT * FROM tiers').fetchall())
print('merchants:', conn.execute('SELECT * FROM merchants').fetchall())
print('tier_tools:', conn.execute('SELECT * FROM tier_tools').fetchall())
print('txn count:', conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0])
"
```

As of Sprint 5 sign-off: `tiers=[(1,'default')]`,
`merchants=[(1,'merchant_alpha',1),(2,'merchant_beta',1),(3,'merchant_gamma',1)]`,
`tier_tools` has exactly 9 rows (tier_id=1, tool_id in
`{1,4,5,8,9,10,13,14,17}`), `txn count=123`. Registry stats unchanged from
Sprint 4: `active=9, candidate=24, proposed=4, rejected=1, total=38`.

Note: a pre-migration snapshot of the DB exists at
`server/payment_lab.db.pre-sprint5-backup` — if a phase below leaves the DB
in a bad state and cleanup (Phase 7) can't recover it, this is the fallback.

Set the admin key as a shell variable for the rest of this plan:

```bash
KEY=$(python -c "import os; print(os.environ.get('PAYMENTLAB_ADMIN_KEY',''))")
```

## Phase 1 — Migration integrity

```bash
# 1.1 — idempotency: re-run init_db() twice, row counts must not grow
python -c "
from models.database import init_db
init_db()
init_db()
import sqlite3
conn = sqlite3.connect('payment_lab.db')
print('tiers:', conn.execute('SELECT COUNT(*) FROM tiers').fetchone()[0])
print('merchants:', conn.execute('SELECT COUNT(*) FROM merchants').fetchone()[0])
print('tier_tools:', conn.execute('SELECT COUNT(*) FROM tier_tools').fetchone()[0])
print('txns:', conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0])
"

# 1.2 — zero orphans
python -c "
import sqlite3
conn = sqlite3.connect('payment_lab.db')
n = conn.execute('''SELECT COUNT(*) FROM transactions t
    LEFT JOIN merchants m ON t.merchant_ref = m.id
    WHERE t.merchant_id IS NOT NULL AND m.id IS NULL''').fetchone()[0]
print('orphans:', n)
"

# 1.3 — no duplicate registration_id
python -c "
import sqlite3
conn = sqlite3.connect('payment_lab.db')
n = conn.execute('''SELECT COUNT(*) FROM (
    SELECT registration_id FROM merchants GROUP BY registration_id HAVING COUNT(*) > 1)''').fetchone()[0]
print('duplicates:', n)
"

# 1.4 — FK constraint present
python -c "
import sqlite3
conn = sqlite3.connect('payment_lab.db')
print(conn.execute('PRAGMA foreign_key_list(transactions)').fetchall())
"

# 1.5 — registry.json still valid JSON
python -c "import json; json.load(open('ai_agents/registry.json', encoding='utf-8')); print('valid JSON')"
```

**Pass criteria:** 1.1 all four counts identical across both runs (tiers=1,
merchants=3, tier_tools=9, txns=123); 1.2 → 0; 1.3 → 0; 1.4 shows a row with
`from='merchant_ref', table='merchants'`; 1.5 prints `valid JSON` without
raising.

## Phase 2 — Business API auth (curl)

```bash
# 2.1 — mutating endpoints, no header -> 403
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/business/tiers \
  -H "Content-Type: application/json" -d '{"name":"zz_test_tier"}'
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/business/tiers/1/tools \
  -H "Content-Type: application/json" -d '{"tool_id":1}'
curl -s -w '\n%{http_code}\n' -X DELETE http://localhost:5000/api/business/tiers/1/tools/1
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/business/merchants/merchant_alpha/tier \
  -H "Content-Type: application/json" -d '{"tier_id":1}'

# 2.2 — wrong key -> 403
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/business/tiers \
  -H "Content-Type: application/json" -H "Authorization: Bearer wrong_key" -d '{"name":"zz_test_tier"}'

# 2.3 — correct key -> succeeds
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/business/tiers \
  -H "Content-Type: application/json" -H "Authorization: Bearer $KEY" -d '{"name":"zz_test_tier"}'

# 2.4 — reads stay public, no auth needed
curl -s -o /dev/null -w 'GET /business/tiers -> %{http_code}\n' http://localhost:5000/api/business/tiers
curl -s -o /dev/null -w 'GET /business/merchants -> %{http_code}\n' http://localhost:5000/api/business/merchants
```

**Pass criteria:** 2.1 all four return 403 `{"error": "Requires
commercial_admin role"}`; 2.2 → 403 (same message — the stub doesn't
distinguish "wrong key" from "no key", both just fail to resolve to
`commercial_admin`); 2.3 → 201 with the created tier (note its `id` for
Phase 3); 2.4 both → 200.

## Phase 3 — Business API correctness (curl)

Uses `zz_test_tier`'s id from Phase 2.3 — call it `$TIER_ID` below.

```bash
TIER_ID=<id from 2.3>

# 3.1 — grant an active tool -> 201
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/business/tiers/$TIER_ID/tools \
  -H "Content-Type: application/json" -H "Authorization: Bearer $KEY" -d '{"tool_id": 1}'

# 3.2 — grant an inactive (candidate) tool -> 409
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/business/tiers/$TIER_ID/tools \
  -H "Content-Type: application/json" -H "Authorization: Bearer $KEY" -d '{"tool_id": 2}'

# 3.3 — grant an unknown tool_id -> 409
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/business/tiers/$TIER_ID/tools \
  -H "Content-Type: application/json" -H "Authorization: Bearer $KEY" -d '{"tool_id": 9999}'

# 3.4 — duplicate grant of the same tool -> idempotent, still 201 (composite PK + INSERT OR IGNORE)
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/business/tiers/$TIER_ID/tools \
  -H "Content-Type: application/json" -H "Authorization: Bearer $KEY" -d '{"tool_id": 1}'

# 3.5 — revoke -> 204
curl -s -w '\n%{http_code}\n' -X DELETE http://localhost:5000/api/business/tiers/$TIER_ID/tools/1 \
  -H "Authorization: Bearer $KEY"

# 3.6 — assign merchant_beta to the test tier -> 200
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/business/merchants/merchant_beta/tier \
  -H "Content-Type: application/json" -H "Authorization: Bearer $KEY" -d "{\"tier_id\": $TIER_ID}"

# 3.7 — assign an unknown merchant -> 404
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/business/merchants/zz_nonexistent/tier \
  -H "Content-Type: application/json" -H "Authorization: Bearer $KEY" -d "{\"tier_id\": $TIER_ID}"

# 3.8 — assign to an unknown tier -> 409
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/business/merchants/merchant_beta/tier \
  -H "Content-Type: application/json" -H "Authorization: Bearer $KEY" -d '{"tier_id": 9999}'
```

**Pass criteria:** 3.1 → 201; 3.2 → 409 with `"is not active"` in the
message; 3.3 → 409 `"Unknown tool_id"`; 3.4 → 201 (no error — the grant
already existing is not a failure); 3.5 → 204; 3.6 → 200 with
`merchant_beta`'s `tier_id` now `$TIER_ID`; 3.7 → 404; 3.8 → 409.

## Phase 4 — Investigation enforcement end-to-end

```bash
# 4.1 — merchant_beta is now on $TIER_ID with zero grants (revoked in 3.5) —
# reassign it to default first, this phase needs a controlled subset instead.
curl -s -X POST http://localhost:5000/api/business/tiers/$TIER_ID/tools \
  -H "Content-Type: application/json" -H "Authorization: Bearer $KEY" -d '{"tool_id": 1}' > /dev/null
curl -s -X POST http://localhost:5000/api/business/tiers/$TIER_ID/tools \
  -H "Content-Type: application/json" -H "Authorization: Bearer $KEY" -d '{"tool_id": 13}' > /dev/null

# find a merchant_beta transaction id
python -c "
import sqlite3
conn = sqlite3.connect('payment_lab.db')
print(conn.execute(\"SELECT id FROM transactions WHERE merchant_id='merchant_beta' LIMIT 1\").fetchone())
"

# 4.2 — investigate it; tool_results keys must be a subset of {get_transaction_details, check_locale_consistency}
curl -s -X POST http://localhost:5000/api/fraud/investigate/<txn_id_from_above> | python -c "
import json, sys
r = json.load(sys.stdin)
print('tools used:', sorted(r.get('tool_results', {}).keys()))
"

# 4.3 — no-regression check: a default-tier merchant (merchant_alpha or merchant_gamma)
# investigation still has access to all 9 active tools
python -c "
import sqlite3
conn = sqlite3.connect('payment_lab.db')
print(conn.execute(\"SELECT id FROM transactions WHERE merchant_id='merchant_alpha' LIMIT 1\").fetchone())
"
curl -s -X POST http://localhost:5000/api/fraud/investigate/<txn_id_from_above> | python -c "
import json, sys
r = json.load(sys.stdin)
print('tools used (should be drawn from all 9 active, not just 2):', sorted(r.get('tool_results', {}).keys()))
"

# 4.4 — drift detection: corrupt merchant_beta's grant to reference a missing tool_id
python -c "
import sqlite3
conn = sqlite3.connect('payment_lab.db')
conn.execute('INSERT INTO tier_tools (tier_id, tool_id) SELECT tier_id, 9999 FROM merchants WHERE registration_id=\"merchant_beta\"')
conn.commit()
"
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/fraud/investigate/<merchant_beta_txn_id>
```

**Pass criteria:** 4.2 tool names are a subset of the 2 granted (may be
fewer if the investigation concluded early, but never anything outside the
set); 4.3 confirms the default-tier merchant is NOT restricted to 2 tools
(no regression from pre-Sprint-5 behavior); 4.4 → 409 with the
"is missing in the registry" `BusinessError` message, not a 200 with a
silently degraded tool set.

**Cleanup for 4.4:**

```bash
python -c "
import sqlite3
conn = sqlite3.connect('payment_lab.db')
conn.execute('DELETE FROM tier_tools WHERE tool_id = 9999')
conn.commit()
"
```

## Phase 5 — tool_id stability across a rename

```bash
# 5.1 — rename tool_id=1's name, confirm resolution still works by id
python -c "
import json
with open('ai_agents/registry.json', encoding='utf-8') as f:
    data = json.load(f)
for t in data['tools']:
    if t['tool_id'] == 1:
        t['name'] = 'zz_renamed_tool'
with open('ai_agents/registry.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2)
print('renamed')
"

curl -s http://localhost:5000/api/business/tiers | python -c "
import json, sys
r = json.load(sys.stdin)
default = next(t for t in r['tiers'] if t['name'] == 'default')
print('default tier tool names:', sorted(t['name'] for t in default['tools']))
"

# 5.2 — revert
python -c "
import json
with open('ai_agents/registry.json', encoding='utf-8') as f:
    data = json.load(f)
for t in data['tools']:
    if t['tool_id'] == 1:
        t['name'] = 'get_transaction_details'
with open('ai_agents/registry.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2)
print('reverted')
"
```

**Note:** the running server's in-process `ToolRegistry` won't see the
rename until it reloads — either restart the server between 5.1 and the
curl call, or call `tool_registry.load()` directly in a one-off script
against `routes.fraud.tool_registry` instead of going through HTTP.

**Pass criteria:** 5.1's tier listing shows `zz_renamed_tool` (not
`get_transaction_details`) among the default tier's tools — the grant
followed the `tool_id`, not the old name. After 5.2 and a server
restart/reload, the name is back to `get_transaction_details`.

## Phase 6 — Boundary/regression checks

```bash
# 6.1 — business/service.py never imports the tool registry directly
# (duck-typed via ctor arg). Scoped to service.py's own import lines, not
# the whole business/ directory — a blanket grep also matches this file's
# own docstring (which legitimately *names* tool_registry.py in prose),
# SPRINT5_TEST_PLAN.md itself, and _stress_test_business_layer.py (which
# imports ToolRegistry for its own setup, not a boundary violation).
grep -n "^import\|^from" business/service.py | grep -i "tool_registry\|ToolRegistry"

# 6.2 — registry.json has no tier-related JSON *keys*. A blanket text grep
# for "tier" also matches legitimate prose (e.g. a references[].relevance
# string citing "tier-1 risk signal") — check keys specifically instead.
python -c "
import json
with open('ai_agents/registry.json', encoding='utf-8') as f:
    data = json.load(f)
def find_tier_keys(obj, path=''):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if 'tier' in k.lower():
                print(f'FOUND KEY: {path}.{k}')
            find_tier_keys(v, f'{path}.{k}')
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            find_tier_keys(v, f'{path}[{i}]')
find_tier_keys(data)
print('done - no FOUND KEY line above means clean')
"

# 6.3 — Sprint 3/4 tool-registry endpoints still work unchanged
curl -s -o /dev/null -w 'GET /fraud/tools -> %{http_code}\n' http://localhost:5000/api/fraud/tools
curl -s -w '\n%{http_code}\n' -X POST http://localhost:5000/api/fraud/tools/anything/approve
```

**Pass criteria:** 6.1 → no matches; 6.2 → no `FOUND KEY` lines; 6.3 → 200,
then 401 (auth unchanged from Sprint 4).

**Gotcha hit during the actual sign-off run:** the naive blanket-grep
versions of 6.1/6.2 (matching the bare string `tool_registry`/`tier`
anywhere in the directory or file) produce false positives — service.py's
own docstring mentions `tool_registry.py` by name, and a registry entry's
citation text says "tier-1 risk signal". Neither is an actual boundary
violation. Use the scoped commands above, not a blanket grep.

## Phase 7 — Cleanup

```bash
python -c "
import sqlite3
conn = sqlite3.connect('payment_lab.db')
# revert merchant_beta to default
conn.execute(\"UPDATE merchants SET tier_id = 1 WHERE registration_id = 'merchant_beta'\")
# remove the test tier and its grants
row = conn.execute(\"SELECT id FROM tiers WHERE name = 'zz_test_tier'\").fetchone()
if row:
    conn.execute('DELETE FROM tier_tools WHERE tier_id = ?', (row[0],))
    conn.execute('DELETE FROM tiers WHERE id = ?', (row[0],))
conn.commit()
print('tiers:', conn.execute('SELECT * FROM tiers').fetchall())
print('merchants:', conn.execute('SELECT * FROM merchants').fetchall())
print('tier_tools:', conn.execute('SELECT * FROM tier_tools').fetchall())
"
```

**Pass criteria:** matches the Phase 0 baseline exactly.

## See also

- `server/tests/test_sprint5_business_layer.py` — automated equivalent of
  Phases 1, 2, 3, 4 (minus the live-server dependency), plus the
  dispatch-enforcement proof that this manual plan doesn't attempt (would
  require a live LLM backend to exercise realistically — the automated
  suite proves it by calling `_dispatch()` directly instead).
- `server/business/_stress_test_business_layer.py` — concurrent
  grant/read/reassign load, not covered by any phase above.
- `server/business/SPRINT5_TEST_REPORT.md` — results of an actual run.
