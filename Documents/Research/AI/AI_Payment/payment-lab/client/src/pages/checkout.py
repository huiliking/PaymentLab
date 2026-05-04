"""
Checkout API - Creates Stripe PaymentIntents and manages the payment lifecycle.

This is the core of the payment flow:
1. Client sends cart items + locale info
2. Server calculates total, creates a Stripe PaymentIntent
3. Returns client_secret so frontend can confirm payment via Stripe.js
4. Webhook (future) or polling confirms completion

LOCALIZATION HOOKS (to be expanded):
- Currency selection based on locale
- Amount calculation respecting currency precision (JPY has no decimals)
- Locale metadata stored for audit/analysis
"""

import stripe
from flask import Blueprint, jsonify, request, current_app
from models.database import create_order, log_audit_event
from ai_agents.transaction_auditor import TransactionAuditor

checkout_bp = Blueprint("checkout", __name__)


# Currency precision: most currencies use 2 decimal places,
# but some (JPY, KRW) use 0. This affects how amounts are sent to Stripe.
ZERO_DECIMAL_CURRENCIES = {"jpy", "krw", "vnd", "clp", "pyg", "ugx", "rwf"}


@checkout_bp.route("/create-payment-intent", methods=["POST"])
def create_payment_intent():
    """
    Create a Stripe PaymentIntent for the checkout.
    
    Expected JSON body:
    {
        "items": [{"id": "prod_001", "quantity": 1}, ...],
        "currency": "usd",
        "customer_email": "user@example.com",
        "locale": "en-US",           # browser locale
        "billing_country": "US"       # from address form
    }
    """
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    items = data.get("items", [])
    currency = data.get("currency", "usd").lower()
    customer_email = data.get("customer_email")
    locale = data.get("locale", "en-US")
    billing_country = data.get("billing_country")

    if not items:
        return jsonify({"error": "Cart is empty"}), 400

    # Calculate total from product catalog
    from routes.products import PRODUCTS
    total = 0
    resolved_items = []

    for item in items:
        product = next((p for p in PRODUCTS if p["id"] == item["id"]), None)
        if not product:
            return jsonify({"error": f"Unknown product: {item['id']}"}), 400

        qty = item.get("quantity", 1)
        subtotal = product["price"] * qty
        total += subtotal
        resolved_items.append({
            "id": product["id"],
            "name": product["name"],
            "price": product["price"],
            "quantity": qty,
            "subtotal": subtotal,
        })

    # Convert from USD cents to the target currency's minor units.
    # convert_amount handles both exchange rates AND zero-decimal currencies
    # (e.g., JPY 154.5x rate + no-cents = whole yen; EUR 0.92x rate + cents).
    # USD needs no conversion — it's already in cents.
    if currency != "usd":
        from services.currency import convert_amount
        total = convert_amount(total, "usd", currency)

    # ================================================================
    # AI TRANSACTION AUDITOR
    # Pre-submission check: ask LLM if this transaction looks right.
    # The auditor has NO knowledge of specific bugs — it uses general
    # reasoning about commerce and currencies to spot anomalies.
    # ================================================================
    auditor = TransactionAuditor()
    audit_result = auditor.audit_transaction({
        "items": [
            {
                "name": item["name"],
                "unit_price_usd_cents": item["price"],
                "quantity": item["quantity"],
                "subtotal_usd_cents": item["subtotal"],
            }
            for item in resolved_items
        ],
        "target_currency": currency,
        "final_amount": total,
        "customer_locale": locale,
        "billing_country": billing_country,
    })

    # Log the audit result regardless of outcome
    log_audit_event(None, "ai_transaction_audit", {
        "risk_level": audit_result["risk_level"],
        "concerns": audit_result["concerns"],
        "approved": audit_result["approved"],
        "amount": total,
        "currency": currency,
    })

    # If CRITICAL risk, block the transaction
    if not audit_result["approved"]:
        print(f"\n  [AI AUDITOR] *** TRANSACTION BLOCKED ***")
        print(f"  [AI AUDITOR] Risk: {audit_result['risk_level']}")
        for concern in audit_result["concerns"]:
            print(f"  [AI AUDITOR]   - {concern}")

        return jsonify({
            "error": "Transaction flagged by AI auditor. Please review the amount and currency.",
            "audit": {
                "risk_level": audit_result["risk_level"],
                "concerns": audit_result["concerns"],
            }
        }), 400
    elif audit_result["risk_level"] == "WARNING":
        print(f"\n  [AI AUDITOR] *** WARNING (transaction allowed) ***")
        for concern in audit_result["concerns"]:
            print(f"  [AI AUDITOR]   - {concern}")

    try:
        # Create Stripe PaymentIntent
        intent = stripe.PaymentIntent.create(
            amount=total,
            currency=currency,
            metadata={
                "customer_locale": locale,
                "billing_country": billing_country or "unknown",
            },
            receipt_email=customer_email,
            # Future: add payment_method_types based on locale
            # e.g., ["card", "ideal"] for Netherlands
            automatic_payment_methods={"enabled": True},
        )

        # Save order to database
        order_id = create_order(
            payment_intent_id=intent.id,
            amount=total,
            currency=currency,
            customer_email=customer_email,
            customer_locale=locale,
            billing_country=billing_country,
            items=resolved_items,
            metadata={"user_agent": request.headers.get("User-Agent", "")},
        )

        log_audit_event(order_id, "payment_intent_created", {
            "stripe_id": intent.id,
            "amount": total,
            "currency": currency,
            "locale": locale,
        })

        return jsonify({
            "client_secret": intent.client_secret,
            "payment_intent_id": intent.id,
            "amount": total,
            "currency": currency,
            "order_id": order_id,
        })

    except stripe.error.StripeError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Server error: {str(e)}"}), 500


@checkout_bp.route("/payment-status/<payment_intent_id>", methods=["GET"])
def payment_status(payment_intent_id):
    """Check payment status — used for confirmation page."""
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    try:
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        return jsonify({
            "status": intent.status,
            "amount": intent.amount,
            "currency": intent.currency,
        })
    except stripe.error.StripeError as e:
        return jsonify({"error": str(e)}), 400
