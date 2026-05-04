"""
Products API - Mock product catalog.
In a real system this would be a database. We keep it in-memory 
so the focus stays on payment + localization, not catalog management.
"""

from flask import Blueprint, jsonify

products_bp = Blueprint("products", __name__)

# Mock product catalog — prices in USD cents
PRODUCTS = [
    {
        "id": "prod_001",
        "name": "Developer Toolkit",
        "description": "Essential tools for modern development",
        "price": 4999,         # $49.99
        "currency": "usd",
        "image": "/images/toolkit.png",
    },
    {
        "id": "prod_002",
        "name": "API Access — Pro",
        "description": "Unlimited API calls, priority support",
        "price": 9900,         # $99.00
        "currency": "usd",
        "image": "/images/api-pro.png",
    },
    {
        "id": "prod_003",
        "name": "Cloud Storage Add-on",
        "description": "50 GB encrypted cloud storage",
        "price": 1500,         # $15.00
        "currency": "usd",
        "image": "/images/storage.png",
    },
]


@products_bp.route("/products", methods=["GET"])
def list_products():
    return jsonify({"products": PRODUCTS})


@products_bp.route("/products/<product_id>", methods=["GET"])
def get_product(product_id):
    product = next((p for p in PRODUCTS if p["id"] == product_id), None)
    if not product:
        return jsonify({"error": "Product not found"}), 404
    return jsonify(product)
