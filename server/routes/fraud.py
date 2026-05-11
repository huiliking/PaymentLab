"""
fraud_routes.py
===============
Flask blueprint for fraud investigation API.
Add to your existing PaymentLab server:

    from routes.fraud import fraud_bp
    app.register_blueprint(fraud_bp, url_prefix='/api')
"""

from flask import Blueprint, request, jsonify
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_agents.fraud_investigator import (
    FraudInvestigator, 
    InvestigationTools, 
    RuleEngine,
    list_flagged_transactions
)

fraud_bp = Blueprint('fraud', __name__)

# Config — uses same DB path as models/database.py
DB_PATH = os.environ.get(
    "PAYMENT_LAB_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "payment_lab.db")
)
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")


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
    investigator = FraudInvestigator(
        db_path=DB_PATH,
        ollama_url=OLLAMA_URL,
        model=OLLAMA_MODEL
    )
    
    try:
        report = investigator.investigate(txn_id)
        return jsonify(report.to_dict())
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


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
    
    cursor.execute("""
        SELECT r.*, t.card_last4, t.amount_cents, t.currency, t.customer_email
        FROM investigation_reports r
        LEFT JOIN transactions t ON r.transaction_id = t.id
        ORDER BY r.created_at DESC
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    # Parse JSON fields
    for row in rows:
        for field in ["evidence", "steps"]:
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
    for field in ["evidence", "steps"]:
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
    
    # Investigation reports
    cursor.execute("SELECT COUNT(*) as count FROM investigation_reports")
    total_reports = cursor.fetchone()["count"]
    
    cursor.execute("SELECT verdict, COUNT(*) as count FROM investigation_reports GROUP BY verdict")
    verdict_breakdown = {row["verdict"]: row["count"] for row in cursor.fetchall()}
    
    conn.close()
    
    return jsonify({
        "total_transactions": total_txns,
        "total_amount_usd": total_amount / 100,
        "status_breakdown": status_breakdown,
        "total_investigations": total_reports,
        "verdict_breakdown": verdict_breakdown
    })
