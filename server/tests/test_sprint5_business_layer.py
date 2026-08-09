"""
Automated test suite for Sprint 5 (business layer + tool profiles).

Run from server/:
    python -m pytest tests/test_sprint5_business_layer.py -v

Isolation is the whole design point: nothing here may touch the real
server/payment_lab.db or server/ai_agents/registry.json. Every fixture
operates on a temp copy. The Flask-route fixtures deliberately do NOT call
app.create_app() — that calls models.database.init_db(), whose DB_PATH is
a hardcoded module constant (not env-var-configurable, unlike
routes/fraud.py's), so it would touch the real dev DB regardless of the
PAYMENT_LAB_DB env var set below. Building a minimal Flask app with just
the two blueprints under test avoids that entirely (see `app` fixture).

The migration itself IS tested (group A) — just by calling
database._migrate_business_layer(conn) directly against a temp copy of the
pre-migration backup, rather than through init_db()/create_app().
"""
import json
import os
import shutil
import sqlite3
import sys
import uuid
from unittest.mock import MagicMock

import pytest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SERVER_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _SERVER_DIR)

REAL_DB = os.path.join(_SERVER_DIR, "payment_lab.db")
REAL_REGISTRY = os.path.join(_SERVER_DIR, "ai_agents", "registry.json")
PRISTINE_DB = os.path.join(_SERVER_DIR, "payment_lab.db.pre-sprint5-backup")

pytestmark = pytest.mark.skipif(
    not (os.path.exists(REAL_DB) and os.path.exists(REAL_REGISTRY)),
    reason="requires the real dev payment_lab.db and registry.json to copy from",
)


# ── Isolated per-test fixtures (BusinessLayer / ToolRegistry directly) ──────

@pytest.fixture()
def tmp_paths(tmp_path):
    tmp_db = tmp_path / "payment_lab.db"
    tmp_registry = tmp_path / "registry.json"
    shutil.copy(REAL_DB, tmp_db)
    shutil.copy(REAL_REGISTRY, tmp_registry)
    return {"db": str(tmp_db), "registry": str(tmp_registry)}


@pytest.fixture()
def registry(tmp_paths):
    from ai_agents.tool_registry import ToolRegistry
    return ToolRegistry(tmp_paths["registry"])


@pytest.fixture()
def business(tmp_paths, registry):
    from business.service import BusinessLayer
    return BusinessLayer(tmp_paths["db"], registry)


def _merchant_id(business_layer, registration_id):
    m = business_layer.get_merchant_by_registration_id(registration_id)
    assert m is not None, f"fixture DB missing expected merchant {registration_id}"
    return m["id"]


# ── Group A: migration ──────────────────────────────────────────────────────

@pytest.fixture()
def pristine_db_path(tmp_path):
    if not os.path.exists(PRISTINE_DB):
        pytest.skip(f"pre-migration backup not found at {PRISTINE_DB}")
    dest = tmp_path / "pristine.db"
    shutil.copy(PRISTINE_DB, dest)
    return str(dest)


def _run_migration(db_path):
    from models import database
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    database._migrate_business_layer(conn)
    conn.close()


def _counts(db_path):
    conn = sqlite3.connect(db_path)
    out = {
        "tiers": conn.execute("SELECT COUNT(*) FROM tiers").fetchone()[0],
        "merchants": conn.execute("SELECT COUNT(*) FROM merchants").fetchone()[0],
        "tier_tools": conn.execute("SELECT COUNT(*) FROM tier_tools").fetchone()[0],
        "transactions": conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
    }
    conn.close()
    return out


def _transaction_count(db_path):
    """Unlike _counts(), safe to call before migration — transactions is
    the one table that exists pre- and post-migration."""
    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    conn.close()
    return n


class TestMigration:
    def test_migration_idempotent_from_pristine(self, pristine_db_path):
        before_txns = _transaction_count(pristine_db_path)
        _run_migration(pristine_db_path)
        first = _counts(pristine_db_path)
        _run_migration(pristine_db_path)
        second = _counts(pristine_db_path)
        assert first == second, "re-running the migration must not duplicate rows"
        assert first["transactions"] == before_txns, "migration must not touch transaction rows"

    def test_migration_zero_orphans(self, pristine_db_path):
        _run_migration(pristine_db_path)
        conn = sqlite3.connect(pristine_db_path)
        orphans = conn.execute("""
            SELECT COUNT(*) FROM transactions t
            LEFT JOIN merchants m ON t.merchant_ref = m.id
            WHERE t.merchant_id IS NOT NULL AND m.id IS NULL
        """).fetchone()[0]
        conn.close()
        assert orphans == 0

    def test_migration_no_duplicate_registration_id(self, pristine_db_path):
        _run_migration(pristine_db_path)
        conn = sqlite3.connect(pristine_db_path)
        dups = conn.execute("""
            SELECT COUNT(*) FROM (
                SELECT registration_id FROM merchants GROUP BY registration_id HAVING COUNT(*) > 1
            )
        """).fetchone()[0]
        conn.close()
        assert dups == 0

    def test_migration_fk_present_after_rebuild(self, pristine_db_path):
        _run_migration(pristine_db_path)
        conn = sqlite3.connect(pristine_db_path)
        fks = conn.execute("PRAGMA foreign_key_list(transactions)").fetchall()
        conn.close()
        assert any(fk[2] == "merchants" and fk[3] == "merchant_ref" for fk in fks)

    def test_migration_preserves_transaction_count(self, pristine_db_path):
        before = _transaction_count(pristine_db_path)
        _run_migration(pristine_db_path)
        after = _transaction_count(pristine_db_path)
        assert before == after

    def test_migration_default_tier_seeded_with_active_snapshot(self, pristine_db_path):
        with open(REAL_REGISTRY, encoding="utf-8") as f:
            registry_data = json.load(f)
        active_ids = {t["tool_id"] for t in registry_data["tools"] if t["status"] == "active"}

        _run_migration(pristine_db_path)
        conn = sqlite3.connect(pristine_db_path)
        default_tier_id = conn.execute("SELECT id FROM tiers WHERE name = 'default'").fetchone()[0]
        granted = {r[0] for r in conn.execute(
            "SELECT tool_id FROM tier_tools WHERE tier_id = ?", (default_tier_id,)
        ).fetchall()}
        conn.close()
        assert granted == active_ids

    def test_migration_registry_untouched(self, pristine_db_path):
        before = json.load(open(REAL_REGISTRY, encoding="utf-8"))
        _run_migration(pristine_db_path)
        after = json.load(open(REAL_REGISTRY, encoding="utf-8"))
        assert before == after, "the DB migration must never write to registry.json"


# ── Group B: tool_registry.py ────────────────────────────────────────────────

class TestToolRegistry:
    def test_tool_id_unique_across_all_tools(self, registry):
        ids = [t["tool_id"] for t in registry.list_tools()]
        assert len(ids) == len(set(ids))
        assert len(ids) == 38

    def test_get_tool_by_id_roundtrip(self, registry):
        tool = registry.get_tool_by_id(1)
        assert tool is not None
        assert tool["name"] == "get_transaction_details"
        assert registry.get_tool_by_id(999999) is None

    def test_propose_tool_autoassigns_tool_id(self, registry):
        max_before = max(t["tool_id"] for t in registry.list_tools())
        result = registry.propose_tool({
            "name": "zz_pytest_tool",
            "category": "transaction_context",
            "status": "proposed",
            "source": "external",
            "description": "test",
            "detects": "test",
            "input_schema": {"type": "object", "properties": {}},
            "references": [],
        })
        assert "error" not in result
        assert result["tool_id"] == max_before + 1

    def test_propose_tool_ignores_caller_supplied_tool_id(self, registry):
        result = registry.propose_tool({
            "name": "zz_pytest_tool_2",
            "category": "transaction_context",
            "status": "proposed",
            "source": "external",
            "description": "test",
            "detects": "test",
            "input_schema": {"type": "object", "properties": {}},
            "references": [],
            "tool_id": 1,  # collides with an existing tool — must be overridden, not honored
        })
        assert result["tool_id"] != 1

    def test_get_schemas_allowed_happy_path(self, registry):
        schemas = registry.get_schemas(allowed=["get_transaction_details", "get_card_history"])
        assert sorted(s["name"] for s in schemas) == ["get_card_history", "get_transaction_details"]

    def test_get_schemas_allowed_unknown_raises(self, registry):
        with pytest.raises(ValueError, match="unknown tool"):
            registry.get_schemas(allowed=["not_a_real_tool"])

    def test_get_schemas_allowed_inactive_raises(self, registry):
        with pytest.raises(ValueError, match="inactive tool"):
            registry.get_schemas(allowed=["check_amount_anomaly"])  # status=candidate


# ── Group C: BusinessLayer ───────────────────────────────────────────────────

class TestBusinessLayer:
    def test_resolve_allowed_tools_default_tier_all_active(self, business, registry):
        merchant_id = _merchant_id(business, "merchant_alpha")
        active_names = {t["name"] for t in registry.get_active_tools()}
        assert set(business.resolve_allowed_tools(merchant_id)) == active_names

    def test_resolve_allowed_tools_unknown_merchant_raises(self, business):
        from business.service import BusinessError
        with pytest.raises(BusinessError, match="Unknown merchant_id"):
            business.resolve_allowed_tools(999999)

    def test_resolve_allowed_tools_stale_grant_raises(self, business, tmp_paths):
        from business.service import BusinessError
        merchant_id = _merchant_id(business, "merchant_alpha")
        conn = sqlite3.connect(tmp_paths["db"])
        conn.execute("INSERT INTO tier_tools (tier_id, tool_id) VALUES (1, 999999)")
        conn.commit()
        conn.close()
        with pytest.raises(BusinessError, match="missing"):
            business.resolve_allowed_tools(merchant_id)

    def test_grant_tool_active_ok(self, business):
        tier = business.create_tier("zz_pytest_tier_a")
        grant = business.grant_tool(tier["id"], 1)
        assert grant["name"] == "get_transaction_details"

    def test_grant_tool_inactive_rejected(self, business):
        from business.service import BusinessError
        tier = business.create_tier("zz_pytest_tier_b")
        with pytest.raises(BusinessError, match="not active"):
            business.grant_tool(tier["id"], 2)  # check_amount_anomaly = candidate

    def test_grant_tool_unknown_tool_id_rejected(self, business):
        from business.service import BusinessError
        tier = business.create_tier("zz_pytest_tier_c")
        with pytest.raises(BusinessError, match="Unknown tool_id"):
            business.grant_tool(tier["id"], 999999)

    def test_grant_tool_unknown_tier_rejected(self, business):
        from business.service import BusinessError
        with pytest.raises(BusinessError, match="Unknown tier_id"):
            business.grant_tool(999999, 1)

    def test_revoke_tool_idempotent(self, business):
        tier = business.create_tier("zz_pytest_tier_d")
        business.grant_tool(tier["id"], 1)
        business.revoke_tool(tier["id"], 1)
        business.revoke_tool(tier["id"], 1)  # second revoke must not raise
        assert business.get_tier_tools(tier["id"]) == []

    def test_assign_tier_unknown_merchant_rejected(self, business):
        from business.service import BusinessError
        with pytest.raises(BusinessError, match="Unknown merchant_id"):
            business.assign_tier(999999, 1)

    def test_assign_tier_unknown_tier_rejected(self, business):
        from business.service import BusinessError
        merchant_id = _merchant_id(business, "merchant_alpha")
        with pytest.raises(BusinessError, match="Unknown tier_id"):
            business.assign_tier(merchant_id, 999999)

    def test_tool_id_stable_across_rename(self, business, registry, tmp_paths):
        merchant_id = _merchant_id(business, "merchant_alpha")
        before = set(business.resolve_allowed_tools(merchant_id))
        assert "get_transaction_details" in before

        with open(tmp_paths["registry"], encoding="utf-8") as f:
            data = json.load(f)
        for t in data["tools"]:
            if t["tool_id"] == 1:
                t["name"] = "zz_renamed"
        with open(tmp_paths["registry"], "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        registry.load()

        after = set(business.resolve_allowed_tools(merchant_id))
        assert "zz_renamed" in after
        assert "get_transaction_details" not in after

    def test_service_module_does_not_import_tool_registry(self):
        """
        Boundary check (criterion #8): business/service.py must stay
        decoupled from ai_agents.tool_registry — it takes a registry object
        via constructor injection instead. Checks actual import statements
        (via the ast module), not just a substring — the module's own
        docstring legitimately *mentions* tool_registry.py by name in prose,
        which a naive substring check would misflag.
        """
        import ast

        service_path = os.path.join(_SERVER_DIR, "business", "service.py")
        with open(service_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=service_path)

        imported_names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imported_names.append(module)
                imported_names += [f"{module}.{alias.name}" for alias in node.names]

        offenders = [n for n in imported_names if "tool_registry" in n or "ToolRegistry" in n]
        assert offenders == [], f"business/service.py must not import the tool registry directly: {offenders}"


# ── Group D: Flask routes ────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def isolated_env(tmp_path_factory):
    tmp_dir = tmp_path_factory.mktemp("sprint5_flask")
    tmp_db = tmp_dir / "payment_lab.db"
    tmp_registry = tmp_dir / "registry.json"
    shutil.copy(REAL_DB, tmp_db)
    shutil.copy(REAL_REGISTRY, tmp_registry)

    assert "routes.fraud" not in sys.modules, (
        "routes.fraud was already imported elsewhere with the real DB/registry paths bound — "
        "env var isolation below won't take effect. Run this test file on its own."
    )

    os.environ["PAYMENT_LAB_DB"] = str(tmp_db)
    os.environ["TOOL_REGISTRY_PATH"] = str(tmp_registry)
    os.environ.setdefault("PAYMENTLAB_ADMIN_KEY", "test-admin-key-sprint5")

    return {
        "db_path": str(tmp_db),
        "registry_path": str(tmp_registry),
        "admin_key": os.environ["PAYMENTLAB_ADMIN_KEY"],
    }


@pytest.fixture(scope="session")
def flask_app(isolated_env):
    """
    A minimal Flask app with just fraud_bp + business_bp — NOT
    app.create_app(), which calls models.database.init_db() against a
    hardcoded (non-env-configurable) DB_PATH and would touch the real dev
    payment_lab.db. See module docstring.
    """
    from flask import Flask
    import routes.fraud as fraud_mod
    import routes.business as business_mod

    test_app = Flask(__name__)
    test_app.config["TESTING"] = True
    test_app.register_blueprint(fraud_mod.fraud_bp, url_prefix="/api")
    test_app.register_blueprint(business_mod.business_bp, url_prefix="/api")
    return test_app


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture()
def auth_headers(isolated_env):
    return {"Authorization": f"Bearer {isolated_env['admin_key']}"}


class TestBusinessRoutesAuth:
    def test_get_tiers_public_no_auth(self, client):
        resp = client.get("/api/business/tiers")
        assert resp.status_code == 200

    def test_get_merchants_public_no_auth(self, client):
        resp = client.get("/api/business/merchants")
        assert resp.status_code == 200
        for m in resp.get_json()["merchants"]:
            assert "id" not in m  # never expose the internal integer PK

    @pytest.mark.parametrize("headers", [
        {},
        {"Authorization": "Bearer wrong-key"},
    ])
    def test_create_tier_rejected_without_valid_auth(self, client, headers):
        resp = client.post("/api/business/tiers", json={"name": str(uuid.uuid4())}, headers=headers)
        assert resp.status_code == 403

    def test_create_tier_ok_with_auth(self, client, auth_headers):
        resp = client.post("/api/business/tiers", json={"name": f"zz_{uuid.uuid4().hex[:8]}"}, headers=auth_headers)
        assert resp.status_code == 201

    def test_grant_requires_auth(self, client, auth_headers):
        tier_id = client.post(
            "/api/business/tiers", json={"name": f"zz_{uuid.uuid4().hex[:8]}"}, headers=auth_headers
        ).get_json()["tier"]["id"]
        assert client.post(f"/api/business/tiers/{tier_id}/tools", json={"tool_id": 1}).status_code == 403
        assert client.post(
            f"/api/business/tiers/{tier_id}/tools", json={"tool_id": 1}, headers=auth_headers
        ).status_code == 201

    def test_revoke_requires_auth(self, client, auth_headers):
        tier_id = client.post(
            "/api/business/tiers", json={"name": f"zz_{uuid.uuid4().hex[:8]}"}, headers=auth_headers
        ).get_json()["tier"]["id"]
        client.post(f"/api/business/tiers/{tier_id}/tools", json={"tool_id": 1}, headers=auth_headers)
        assert client.delete(f"/api/business/tiers/{tier_id}/tools/1").status_code == 403
        assert client.delete(f"/api/business/tiers/{tier_id}/tools/1", headers=auth_headers).status_code == 204

    def test_assign_tier_requires_auth(self, client, auth_headers):
        assert client.post(
            "/api/business/merchants/merchant_alpha/tier", json={"tier_id": 1}
        ).status_code == 403

    def test_assign_tier_unknown_merchant_404(self, client, auth_headers):
        resp = client.post(
            "/api/business/merchants/zz_does_not_exist/tier", json={"tier_id": 1}, headers=auth_headers
        )
        assert resp.status_code == 404


class _StubInvestigator:
    """Records constructor kwargs and investigate() args instead of running a real LLM loop."""
    last_ctor_kwargs = None
    last_investigate_args = None

    def __init__(self, **kwargs):
        _StubInvestigator.last_ctor_kwargs = kwargs

    def investigate(self, txn_id, customer_id=None):
        _StubInvestigator.last_investigate_args = {"txn_id": txn_id, "customer_id": customer_id}

        class _R:
            def to_dict(self):
                return {"stub": True}

        return _R()


def _pick_transaction(db_path, merchant_id):
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT id FROM transactions WHERE merchant_id = ? LIMIT 1", (merchant_id,)
    ).fetchone()
    conn.close()
    assert row is not None, f"fixture DB missing a transaction for {merchant_id}"
    return row[0]


class TestInvestigateRouteOrchestration:
    """
    NOTE: these stub FraudInvestigator, so they prove the route resolves the
    right allowed_tools/customer_id and PASSES them in — not that the
    investigator actually enforces them. That's proven separately, without
    a stub, by TestDispatchEnforcement below. Both halves are required to
    make the "server-enforced, not just wired" claim (criterion #1).
    """

    def test_restricted_tier_merchant_gets_only_granted_tools(self, client, auth_headers, isolated_env, monkeypatch):
        import routes.fraud as fraud_mod
        monkeypatch.setattr(fraud_mod, "FraudInvestigator", _StubInvestigator)

        tier = client.post(
            "/api/business/tiers", json={"name": f"zz_{uuid.uuid4().hex[:8]}"}, headers=auth_headers
        ).get_json()["tier"]
        client.post(f"/api/business/tiers/{tier['id']}/tools", json={"tool_id": 1}, headers=auth_headers)
        client.post(f"/api/business/tiers/{tier['id']}/tools", json={"tool_id": 13}, headers=auth_headers)
        client.post(
            "/api/business/merchants/merchant_gamma/tier", json={"tier_id": tier["id"]}, headers=auth_headers
        )

        txn_id = _pick_transaction(isolated_env["db_path"], "merchant_gamma")
        resp = client.post(f"/api/fraud/investigate/{txn_id}")

        assert resp.status_code == 200
        allowed = _StubInvestigator.last_ctor_kwargs["allowed_tools"]
        assert sorted(allowed) == ["check_locale_consistency", "get_transaction_details"]
        assert _StubInvestigator.last_investigate_args["customer_id"] == "merchant_gamma"

        # revert merchant_gamma so later tests aren't affected by ordering
        client.post("/api/business/merchants/merchant_gamma/tier", json={"tier_id": 1}, headers=auth_headers)

    def test_default_tier_merchant_unrestricted_no_regression(self, client, isolated_env, monkeypatch):
        import routes.fraud as fraud_mod
        monkeypatch.setattr(fraud_mod, "FraudInvestigator", _StubInvestigator)

        txn_id = _pick_transaction(isolated_env["db_path"], "merchant_alpha")
        resp = client.post(f"/api/fraud/investigate/{txn_id}")

        assert resp.status_code == 200
        allowed = _StubInvestigator.last_ctor_kwargs["allowed_tools"]
        assert len(allowed) == 9  # all active tools — matches pre-Sprint-5 behavior
        assert _StubInvestigator.last_investigate_args["customer_id"] == "merchant_alpha"

    def test_stale_grant_returns_409_not_silent_degradation(self, client, auth_headers, isolated_env, monkeypatch):
        import routes.fraud as fraud_mod
        monkeypatch.setattr(fraud_mod, "FraudInvestigator", _StubInvestigator)

        conn = sqlite3.connect(isolated_env["db_path"])
        conn.execute("INSERT OR IGNORE INTO tier_tools (tier_id, tool_id) VALUES (1, 999999)")
        conn.commit()
        conn.close()

        txn_id = _pick_transaction(isolated_env["db_path"], "merchant_beta")
        resp = client.post(f"/api/fraud/investigate/{txn_id}")
        assert resp.status_code == 409
        assert "missing" in resp.get_json()["error"]

        conn = sqlite3.connect(isolated_env["db_path"])
        conn.execute("DELETE FROM tier_tools WHERE tier_id = 1 AND tool_id = 999999")
        conn.commit()
        conn.close()


# ── Group E: dispatch enforcement — load-bearing, not stubbed ──────────────
#
# This is the only test proving enforcement is real rather than cosmetic:
# it constructs a real FraudInvestigator (no stub) and calls the actual
# _dispatch() gate. Do not simplify this away — the route-level tests above
# only prove the right list gets *passed in*, not that it's honored.

class TestDispatchEnforcement:
    @pytest.fixture()
    def investigator(self, tmp_paths):
        from ai_agents.fraud_investigator import FraudInvestigator
        return FraudInvestigator(
            db_path=tmp_paths["db"],
            registry_path=tmp_paths["registry"],
            allowed_tools=["get_transaction_details", "check_locale_consistency"],
        )

    def test_dispatch_allows_in_grant_tool(self, investigator, tmp_paths):
        conn = sqlite3.connect(tmp_paths["db"])
        txn_id = conn.execute("SELECT id FROM transactions LIMIT 1").fetchone()[0]
        conn.close()
        result = investigator._dispatch("get_transaction_details", {"txn_id": txn_id})
        assert "error" not in result

    def test_dispatch_blocks_out_of_grant_tool_and_never_calls_underlying_method(self, investigator):
        mock_method = MagicMock(return_value={"tool": "check_velocity", "risk": "LOW"})
        investigator.tools.check_velocity = mock_method

        result = investigator._dispatch("check_velocity", {"card_last4": "4242"})

        assert "error" in result
        assert "not granted" in result["error"]
        mock_method.assert_not_called()
