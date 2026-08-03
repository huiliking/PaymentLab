"""
fraud_routes.py
===============
Flask blueprint for fraud investigation API.
Add to your existing PaymentLab server:

    from routes.fraud import fraud_bp
    app.register_blueprint(fraud_bp, url_prefix='/api')
"""

from flask import Blueprint, request, jsonify, current_app
import sys
import os
import threading
import hmac

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_agents.fraud_investigator import (
    FraudInvestigator, 
    InvestigationTools, 
    RuleEngine,
    list_flagged_transactions
)
from ai_agents.tool_registry import ToolRegistry

fraud_bp = Blueprint('fraud', __name__)

# Config — uses same DB path as models/database.py
DB_PATH = os.environ.get(
    "PAYMENT_LAB_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "payment_lab.db")
)
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")

# Shared secret gating the registry-mutating endpoints (propose/approve/reject).
# Not set locally by default -- until PAYMENTLAB_ADMIN_KEY is exported, those
# three routes reject every request, which is the safe default.
PAYMENTLAB_ADMIN_KEY = os.environ.get("PAYMENTLAB_ADMIN_KEY")

# Tool registry — shared instance, loaded once
REGISTRY_PATH = os.environ.get(
    "TOOL_REGISTRY_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai_agents", "tools", "registry.json")
)
# Fallback: check ai_agents/ directory directly
if not os.path.exists(REGISTRY_PATH):
    REGISTRY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai_agents", "registry.json")

tool_registry = ToolRegistry(REGISTRY_PATH)

# Serializes investigations: Ollama and sqlite writes aren't safe under
# concurrent investigate() calls, so only one runs at a time per process.
_investigation_lock = threading.Lock()
_investigation_state = {"txn_id": None}

# Serializes registry.json writes (scanner proposals, dashboard approvals) —
# same rationale as _investigation_lock, applied to the shared JSON file.
_registry_lock = threading.Lock()


def _require_admin():
    """
    Checks the 'Authorization: Bearer <key>' header on registry-mutating
    requests against PAYMENTLAB_ADMIN_KEY. Returns None if authorized, or a
    (response, status_code) tuple to return immediately if not.

    Uses hmac.compare_digest instead of == so a mistyped key doesn't leak
    timing information about how many leading characters matched.
    """
    if not PAYMENTLAB_ADMIN_KEY:
        return jsonify({"error": "Admin auth is not configured on this server"}), 401

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "Missing bearer token"}), 401

    token = auth_header[len("Bearer "):]
    if not hmac.compare_digest(token, PAYMENTLAB_ADMIN_KEY):
        return jsonify({"error": "Invalid admin key"}), 401

    return None


@fraud_bp.route('/fraud/transactions', methods=['GET'])
def get_transactions():
    """List all transactions with optional filters"""
    tools = InvestigationTools(DB_PATH)
    
    limit = request.args.get('limit', 50, type=int)
    status = request.args.get('status')
    
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    query = "SELECT * FROM transactions ORDER BY created_at DESC LIMIT ?"
    params = [limit]
    
    if status:
        query = "SELECT * FROM transactions WHERE status = ? ORDER BY created_at DESC LIMIT ?"
        params = [status, limit]
    
    cursor.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify({"transactions": rows, "count": len(rows)})


@fraud_bp.route('/fraud/flagged', methods=['GET'])
def get_flagged():
    """List transactions flagged by rule engine"""
    flagged = list_flagged_transactions(DB_PATH)
    
    results = []
    for f in flagged:
        results.append({
            "transaction": f["transaction"],
            "triggers": f["triggers"],
            "trigger_count": len(f["triggers"]),
            "max_risk": max(
                (t["risk"] for t in f["triggers"]),
                key=lambda r: ["LOW", "MEDIUM", "HIGH", "CRITICAL"].index(r)
                if r in ["LOW", "MEDIUM", "HIGH", "CRITICAL"] else 0
            )
        })
    
    # Sort by risk level
    risk_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "ERROR": 4}
    results.sort(key=lambda r: risk_order.get(r["max_risk"], 5))
    
    return jsonify({"flagged": results, "count": len(results)})


@fraud_bp.route('/fraud/investigate/<txn_id>', methods=['POST'])
def investigate_transaction(txn_id):
    """Run full FAA-style investigation on a transaction"""
    if not _investigation_lock.acquire(blocking=False):
        return jsonify({
            "error": "An investigation is already in progress",
            "in_progress_txn_id": _investigation_state["txn_id"],
        }), 409

    try:
        _investigation_state["txn_id"] = txn_id
        metering_service = current_app.config.get('METERING_SERVICE')

        investigator = FraudInvestigator(
            db_path=DB_PATH,
            ollama_url=OLLAMA_URL,
            model=OLLAMA_MODEL,
            registry_path=REGISTRY_PATH,
            metering_service=metering_service,
        )

        try:
            report = investigator.investigate(txn_id)
            return jsonify(report.to_dict())
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500
    finally:
        _investigation_state["txn_id"] = None
        _investigation_lock.release()


@fraud_bp.route('/fraud/pre-screen/<txn_id>', methods=['GET'])
def pre_screen_transaction(txn_id):
    """Quick rule-based pre-screen without LLM"""
    engine = RuleEngine(DB_PATH)
    triggers = engine.pre_screen(txn_id)
    
    return jsonify({
        "transaction_id": txn_id,
        "triggers": triggers,
        "should_investigate": len(triggers) > 0
    })


@fraud_bp.route('/fraud/reports', methods=['GET'])
def get_reports():
    """Get all investigation reports"""
    import sqlite3, json
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Only the latest report per transaction — a transaction can be
    # re-investigated multiple times, and the frontend indexes this list by
    # transaction_id, so stale duplicate rows would silently overwrite the
    # current report.
    cursor.execute("""
        SELECT r.*, t.card_last4, t.amount_cents, t.currency, t.customer_email
        FROM investigation_reports r
        LEFT JOIN transactions t ON r.transaction_id = t.id
        WHERE r.created_at = (
            SELECT MAX(r2.created_at) FROM investigation_reports r2
            WHERE r2.transaction_id = r.transaction_id
        )
        ORDER BY r.created_at DESC
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    # Parse JSON fields
    for row in rows:
        for field in ["evidence", "steps", "tool_results"]:
            if row.get(field):
                try:
                    row[field] = json.loads(row[field])
                except:
                    pass

    return jsonify({"reports": rows, "count": len(rows)})


@fraud_bp.route('/fraud/reports/<txn_id>', methods=['GET'])
def get_report(txn_id):
    """Get investigation report for a specific transaction"""
    import sqlite3, json
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT r.*, t.card_last4, t.amount_cents, t.currency, t.customer_email,
               t.billing_country, t.ip_country, t.browser_locale, t.shipping_address
        FROM investigation_reports r
        LEFT JOIN transactions t ON r.transaction_id = t.id
        WHERE r.transaction_id = ?
        ORDER BY r.created_at DESC
        LIMIT 1
    """, (txn_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return jsonify({"error": "Report not found"}), 404
    
    result = dict(row)
    for field in ["evidence", "steps", "tool_results"]:
        if result.get(field):
            try:
                result[field] = json.loads(result[field])
            except:
                pass

    return jsonify(result)


@fraud_bp.route('/fraud/stats', methods=['GET'])
def get_stats():
    """Dashboard statistics"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Total transactions
    cursor.execute("SELECT COUNT(*) as count FROM transactions")
    total_txns = cursor.fetchone()["count"]
    
    # Total amount
    cursor.execute("SELECT SUM(amount_cents) as total FROM transactions WHERE currency = 'usd'")
    total_amount = cursor.fetchone()["total"] or 0
    
    # Status breakdown
    cursor.execute("SELECT status, COUNT(*) as count FROM transactions GROUP BY status")
    status_breakdown = {row["status"]: row["count"] for row in cursor.fetchall()}
    
    # Investigation reports — count distinct transactions, using only the latest
    # report per transaction (a transaction can be re-investigated multiple
    # times; earlier attempts shouldn't inflate these counts).
    cursor.execute("""
        SELECT COUNT(*) as count FROM (
            SELECT transaction_id FROM investigation_reports GROUP BY transaction_id
        )
    """)
    total_reports = cursor.fetchone()["count"]

    cursor.execute("""
        SELECT verdict, COUNT(*) as count FROM investigation_reports ir
        WHERE ir.created_at = (
            SELECT MAX(ir2.created_at) FROM investigation_reports ir2
            WHERE ir2.transaction_id = ir.transaction_id
        )
        GROUP BY verdict
    """)
    verdict_breakdown = {row["verdict"]: row["count"] for row in cursor.fetchall()}
    
    conn.close()
    
    return jsonify({
        "total_transactions": total_txns,
        "total_amount_usd": total_amount / 100,
        "status_breakdown": status_breakdown,
        "total_investigations": total_reports,
        "verdict_breakdown": verdict_breakdown
    })


# ── Tool Registry Endpoints ───────────────────────────────────────────────

@fraud_bp.route('/fraud/tools', methods=['GET'])
def get_tools():
    """
    Full tool registry for the dashboard.
    Returns categories, tools (with status/source/references), and statistics.

    Optional query params:
        ?category=card_velocity    — filter by category
        ?status=active             — filter by status (active, candidate, proposed)
    """
    category = request.args.get('category')
    status = request.args.get('status')

    if category or status:
        return jsonify({
            "tools": tool_registry.list_tools(category=category, status=status),
            "categories": tool_registry.list_categories(),
            "statistics": tool_registry.get_statistics(),
        })

    # No filters — return full dashboard payload
    return jsonify(tool_registry.to_dashboard_payload())


@fraud_bp.route('/fraud/tools/<tool_name>', methods=['GET'])
def get_tool_detail(tool_name):
    """Get details for a single tool by name"""
    tool = tool_registry.get_tool(tool_name)
    if not tool:
        return jsonify({"error": f"Tool '{tool_name}' not found"}), 404

    # Include the category metadata alongside the tool
    category = next(
        (c for c in tool_registry.list_categories() if c["id"] == tool["category"]),
        None
    )
    return jsonify({"tool": tool, "category": category})


@fraud_bp.route('/fraud/tools/propose', methods=['POST'])
def propose_tools():
    """
    Accept a list of scanner-proposed tool entries and write them to the
    registry with status="proposed". Body: JSON array of tool dicts
    (see ai_agents/tool_scanner.py output format).
    """
    auth_error = _require_admin()
    if auth_error:
        return auth_error

    proposals = request.get_json(silent=True)
    if not isinstance(proposals, list):
        return jsonify({"error": "Request body must be a JSON array of tool proposals"}), 400

    if not _registry_lock.acquire(blocking=False):
        return jsonify({"error": "Registry is being updated by another request, try again"}), 409

    try:
        stored = []
        errors = []
        for proposal in proposals:
            if not isinstance(proposal, dict):
                errors.append({"name": "?", "error": f"Proposal must be an object, got {type(proposal).__name__}"})
                continue
            result = tool_registry.propose_tool(proposal)
            if "error" in result:
                errors.append({"name": proposal.get("name", "?"), "error": result["error"]})
            else:
                stored.append(result)
        return jsonify({"stored": stored, "errors": errors})
    finally:
        _registry_lock.release()


def _status_transition_response(result):
    """Shared error-status mapping for approve/reject: a missing tool is a
    404, but a disallowed transition (e.g. tool isn't 'proposed') is a 409
    conflict, not a 404 — the tool exists, the request is just invalid."""
    if "error" not in result:
        return jsonify({"tool": result})
    code = 404 if result["error"].startswith("Unknown tool") else 409
    return jsonify(result), code


@fraud_bp.route('/fraud/tools/<tool_name>/approve', methods=['POST'])
def approve_tool(tool_name):
    """Approve a proposed tool: flips status from 'proposed' to 'candidate'."""
    auth_error = _require_admin()
    if auth_error:
        return auth_error

    if not _registry_lock.acquire(blocking=False):
        return jsonify({"error": "Registry is being updated by another request, try again"}), 409

    try:
        result = tool_registry.update_status(tool_name, "candidate")
        return _status_transition_response(result)
    finally:
        _registry_lock.release()


@fraud_bp.route('/fraud/tools/<tool_name>/reject', methods=['POST'])
def reject_tool(tool_name):
    """Dismiss a proposed tool: flips status to 'rejected' (non-destructive)."""
    auth_error = _require_admin()
    if auth_error:
        return auth_error

    if not _registry_lock.acquire(blocking=False):
        return jsonify({"error": "Registry is being updated by another request, try again"}), 409

    try:
        result = tool_registry.update_status(tool_name, "rejected")
        return _status_transition_response(result)
    finally:
        _registry_lock.release()
