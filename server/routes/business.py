"""
routes/business.py
===================
Business-layer admin API: assign a merchant's tier, manage a tier's tool
grants. These are commercial-admin mutations — a different persona than
the tooling ops-admin gate in routes/fraud.py (_require_admin), though
both currently check the same shared PAYMENTLAB_ADMIN_KEY (see
business/auth.py). Reads (list tiers/merchants) stay public/unauthenticated,
matching the Sprint 4 precedent for GET /api/fraud/tools.

    from routes.business import business_bp
    app.register_blueprint(business_bp, url_prefix='/api')
"""

from flask import Blueprint, request, jsonify

from business.service import BusinessError
from business.auth import current_principal
from routes.fraud import business_layer

business_bp = Blueprint('business', __name__)


def _require_commercial_admin():
    """Returns None if authorized, or a (response, status) tuple to return immediately."""
    principal = current_principal()
    if principal.role != "commercial_admin":
        return jsonify({"error": "Requires commercial_admin role"}), 403
    return None


@business_bp.route('/business/tiers', methods=['GET'])
def list_tiers():
    return jsonify({"tiers": business_layer.list_tiers()})


@business_bp.route('/business/tiers', methods=['POST'])
def create_tier():
    auth_error = _require_commercial_admin()
    if auth_error:
        return auth_error

    body = request.get_json(silent=True) or {}
    name = body.get("name")
    if not name:
        return jsonify({"error": "'name' is required"}), 400

    try:
        tier = business_layer.create_tier(name)
        return jsonify({"tier": tier}), 201
    except BusinessError as e:
        return jsonify({"error": str(e)}), 409


@business_bp.route('/business/tiers/<int:tier_id>/tools', methods=['POST'])
def grant_tier_tool(tier_id):
    """Body: {"tool_id": <int>} — tool_id is the registry's immutable PK, not the tool name."""
    auth_error = _require_commercial_admin()
    if auth_error:
        return auth_error

    body = request.get_json(silent=True) or {}
    tool_id = body.get("tool_id")
    if tool_id is None:
        return jsonify({"error": "'tool_id' is required"}), 400

    try:
        grant = business_layer.grant_tool(tier_id, tool_id)
        return jsonify({"grant": grant}), 201
    except BusinessError as e:
        return jsonify({"error": str(e)}), 409


@business_bp.route('/business/tiers/<int:tier_id>/tools/<int:tool_id>', methods=['DELETE'])
def revoke_tier_tool(tier_id, tool_id):
    auth_error = _require_commercial_admin()
    if auth_error:
        return auth_error

    business_layer.revoke_tool(tier_id, tool_id)
    return '', 204


@business_bp.route('/business/merchants', methods=['GET'])
def list_merchants():
    return jsonify({"merchants": business_layer.list_merchants()})


@business_bp.route('/business/merchants/<registration_id>/tier', methods=['POST'])
def assign_merchant_tier(registration_id):
    """Body: {"tier_id": <int>}"""
    auth_error = _require_commercial_admin()
    if auth_error:
        return auth_error

    body = request.get_json(silent=True) or {}
    tier_id = body.get("tier_id")
    if tier_id is None:
        return jsonify({"error": "'tier_id' is required"}), 400

    merchant = business_layer.get_merchant_by_registration_id(registration_id)
    if not merchant:
        return jsonify({"error": f"Unknown merchant: {registration_id}"}), 404

    try:
        updated = business_layer.assign_tier(merchant["id"], tier_id)
        return jsonify({
            "merchant": {
                "registration_id": updated["registration_id"],
                "tier_id": updated["tier_id"],
            }
        })
    except BusinessError as e:
        return jsonify({"error": str(e)}), 409
