"""
Config API - Exposes non-secret configuration to the frontend.
Only the PUBLISHABLE key is sent — the secret key never leaves the server.
"""

from flask import Blueprint, jsonify, current_app

config_bp = Blueprint("config", __name__)


@config_bp.route("/config", methods=["GET"])
def get_config():
    return jsonify({
        "stripe_publishable_key": current_app.config["STRIPE_PUBLISHABLE_KEY"],
        "supported_currencies": ["usd", "cad", "eur", "gbp", "jpy", "mxn"],
        "supported_locales": ["en", "fr", "es", "ja", "de"],
    })
