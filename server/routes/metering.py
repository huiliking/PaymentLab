"""
Metering API Routes
===================
Exposes metering data for the usage dashboard (future) and health checks.

Endpoints:
    GET /api/metering/health                  → system status
    GET /api/metering/usage?customer_id=X     → raw events for a customer
    GET /api/metering/summary?customer_id=X   → aggregated summary (dashboard)
    GET /api/metering/breakdown?customer_id=X → cost over time (charts)
    GET /api/metering/session/<session_id>    → per-turn detail for one investigation

All endpoints are read-only. Writing happens through meter.record()
inside business logic, not through the API.
"""

from flask import Blueprint, request, jsonify

metering_bp = Blueprint('metering', __name__)

# The MeteringService instance is injected at registration time
# (see integration instructions in INTEGRATION.md)
_meter = None


def init_metering_routes(metering_service):
    """Call this from app.py to inject the MeteringService instance"""
    global _meter
    _meter = metering_service


@metering_bp.route('/api/metering/health', methods=['GET'])
def metering_health():
    """System health — is metering working?"""
    if not _meter:
        return jsonify({"status": "not_initialized"}), 503
    return jsonify(_meter.health())


@metering_bp.route('/api/metering/usage', methods=['GET'])
def metering_usage():
    """
    Raw usage events for a customer.
    
    Query params:
        customer_id  (required)
        start        ISO datetime (optional)
        end          ISO datetime (optional)
        event_type   filter by type (optional)
    """
    if not _meter:
        return jsonify({"error": "Metering not initialized"}), 503

    customer_id = request.args.get('customer_id')
    if not customer_id:
        return jsonify({"error": "customer_id required"}), 400

    events = _meter.get_usage_by_customer(
        customer_id=customer_id,
        start_date=request.args.get('start'),
        end_date=request.args.get('end'),
        event_type=request.args.get('event_type'),
    )

    return jsonify({
        "customer_id": customer_id,
        "count": len(events),
        "events": events,
    })


@metering_bp.route('/api/metering/summary', methods=['GET'])
def metering_summary():
    """
    Aggregated usage summary — this is what the merchant dashboard shows.
    
    Query params:
        customer_id  (required)
        days         billing period length (default: 30)
    """
    if not _meter:
        return jsonify({"error": "Metering not initialized"}), 503

    customer_id = request.args.get('customer_id')
    if not customer_id:
        return jsonify({"error": "customer_id required"}), 400

    days = int(request.args.get('days', 30))
    summary = _meter.get_usage_summary(customer_id=customer_id, period_days=days)

    return jsonify(summary)


@metering_bp.route('/api/metering/breakdown', methods=['GET'])
def metering_breakdown():
    """
    Cost breakdown over time — feeds charts in the dashboard.
    
    Query params:
        customer_id  (required)
        group_by     "day" | "hour" | "event_type" (default: "day")
        start        ISO datetime (optional)
        end          ISO datetime (optional)
    """
    if not _meter:
        return jsonify({"error": "Metering not initialized"}), 503

    customer_id = request.args.get('customer_id')
    if not customer_id:
        return jsonify({"error": "customer_id required"}), 400

    breakdown = _meter.get_cost_breakdown(
        customer_id=customer_id,
        start_date=request.args.get('start'),
        end_date=request.args.get('end'),
        group_by=request.args.get('group_by', 'day'),
    )

    return jsonify({
        "customer_id": customer_id,
        "group_by": request.args.get('group_by', 'day'),
        "data": breakdown,
    })


@metering_bp.route('/api/metering/session/<session_id>', methods=['GET'])
def metering_session(session_id):
    """
    Per-turn detail for a single investigation session.
    Shows exactly how many tokens each turn consumed.
    """
    if not _meter:
        return jsonify({"error": "Metering not initialized"}), 503

    detail = _meter.get_session_detail(session_id)
    return jsonify(detail)
