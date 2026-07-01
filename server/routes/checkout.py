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
import uuid
import sqlite3
import json
from datetime import datetime
from flask import Blueprint, jsonify, request, current_app
from models.database import create_order, log_audit_event, DB_PATH
from ai_agents.transaction_auditor import TransactionAuditor

checkout_bp = Blueprint("checkout", __name__)


def record_live_transaction(intent, order_id, locale, billing_country, billing_address):
    """
    Write a completed Stripe payment into the transactions table
    and run the fraud rule pre-screen. Called after payment succeeds.

    Returns (txn_id, triggers).
    """
    # Stripe objects support both attribute and dict-style access via .get()
    # Normalise to plain dict so the rest of the function is unambiguous.
    if not isinstance(intent, dict):
        intent = dict(intent)

    # Pull card details — payment_method may be an expanded object or a string ID
    pm = intent.get("payment_method")
    card = {}
    if isinstance(pm, dict):
        card = pm.get("card", {}) or {}
    elif pm and isinstance(pm, str):
        try:
            pm_obj = stripe.PaymentMethod.retrieve(pm)
            card = dict(pm_obj.get("card") or {})
        except Exception:
            card = {}
    elif pm:
        # Already an expanded Stripe object
        try:
            card = dict(getattr(pm, "card", None) or pm.get("card") or {})
        except Exception:
            card = {}

    txn_id = str(uuid.uuid4())
    shipping_address = None
    if billing_address:
        parts = [
            billing_address.get("line1", ""),
            billing_address.get("line2", ""),
            billing_address.get("city", ""),
            billing_address.get("state", ""),
            billing_address.get("postalCode", ""),
            billing_country or billing_address.get("country", ""),
        ]
        shipping_address = ", ".join(p for p in parts if p)

    # ip_country: derive from locale as proxy (real app would use GeoIP)
    ip_country = locale.split("-")[-1] if "-" in locale else locale.upper()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT OR IGNORE INTO transactions
        (id, merchant_id, stripe_payment_intent, card_last4, card_brand, card_country,
         amount_cents, currency, status, customer_email, customer_name,
         billing_country, billing_postal, shipping_address,
         browser_locale, ip_country, device_fingerprint, created_at, metadata)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        txn_id,
        "demo_merchant",
        intent.get("id", ""),
        card.get("last4") or "0000",
        card.get("brand") or "unknown",
        card.get("country") or "US",
        intent.get("amount", 0),
        intent.get("currency", "usd"),
        intent.get("status", "succeeded"),
        intent.get("receipt_email") or "",
        "",
        billing_country or "",
        billing_address.get("postalCode", "") if billing_address else "",
        shipping_address or "",
        locale,
        ip_country,
        request.headers.get("User-Agent", "")[:64],
        datetime.now().isoformat(),
        json.dumps({"order_id": order_id, "live": True})
    ))
    conn.commit()
    conn.close()

    # Run rule pre-screen
    from ai_agents.fraud_investigator import RuleEngine
    triggers = RuleEngine(DB_PATH).pre_screen(txn_id)

    if triggers:
        log_audit_event(order_id, "fraud_flagged", {
            "txn_id": txn_id,
            "triggers": triggers
        })
        risk_levels = [t["risk"] for t in triggers]
        max_risk = max(risk_levels,
                       key=lambda r: ["LOW", "MEDIUM", "HIGH", "CRITICAL"].index(r)
                       if r in ["LOW", "MEDIUM", "HIGH", "CRITICAL"] else 0)
        print(f"\n  [FRAUD] Live txn {txn_id[:12]}... flagged — {len(triggers)} triggers, max risk: {max_risk}")
        for t in triggers:
            print(f"  [FRAUD]   [{t['risk']}] {t['rule']}: {t.get('detail', '')}")
    else:
        print(f"\n  [FRAUD] Live txn {txn_id[:12]}... clean")

    return txn_id, triggers


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
    billing_address = data.get("billing_address") or {}

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
    # convert_amount handles exchange rates + zero-decimal currencies.
    # USD stays as-is (already in cents); all other currencies go through conversion.
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
                "billing_line1": (billing_address or {}).get("line1", ""),
                "billing_city": (billing_address or {}).get("city", ""),
                "billing_state": (billing_address or {}).get("state", ""),
                "billing_postal": (billing_address or {}).get("postalCode", ""),
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
    """
    Check payment status — called by ConfirmationPage.
    On first successful call, records the transaction in the fraud pipeline
    and runs rule pre-screen. Subsequent calls are read-only (idempotent).
    """
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    try:
        intent = stripe.PaymentIntent.retrieve(
            payment_intent_id,
            expand=["payment_method"]   # get card details in one call
        )

        response = {
            "status": intent.status,
            "amount": intent.amount,
            "currency": intent.currency,
        }

        # Only record + screen on success, and only if not already recorded
        if intent.status == "succeeded":
            # Check if already recorded to keep idempotent
            conn = sqlite3.connect(DB_PATH)
            row = conn.execute(
                "SELECT id FROM transactions WHERE stripe_payment_intent = ?",
                (payment_intent_id,)
            ).fetchone()
            conn.close()

            if not row:
                # Pull billing info stored in PaymentIntent metadata
                locale = intent.metadata.get("customer_locale", "en-US")
                billing_country = intent.metadata.get("billing_country", "")

                # Billing address: prefer payment_method, fall back to metadata
                billing_address = None
                pm = intent.payment_method
                if isinstance(pm, stripe.PaymentMethod):
                    ba = pm.billing_details.address
                    if ba and ba.line1:
                        billing_address = {
                            "line1": ba.line1 or "",
                            "line2": ba.line2 or "",
                            "city": ba.city or "",
                            "state": ba.state or "",
                            "postalCode": ba.postal_code or "",
                            "country": ba.country or billing_country,
                        }

                # Fall back to metadata stored at PaymentIntent creation
                if not billing_address:
                    meta = intent.metadata
                    billing_address = {
                        "line1": meta.get("billing_line1", ""),
                        "city": meta.get("billing_city", ""),
                        "state": meta.get("billing_state", ""),
                        "postalCode": meta.get("billing_postal", ""),
                        "country": billing_country,
                    }

                order_id = None  # we don't have it here; look it up if needed
                txn_id, triggers = record_live_transaction(
                    intent, order_id, locale, billing_country, billing_address
                )
                response["fraud_txn_id"] = txn_id
                response["fraud_triggers"] = len(triggers)
            else:
                response["fraud_txn_id"] = row[0]

        return jsonify(response)

    except stripe.error.StripeError as e:
        return jsonify({"error": str(e)}), 400
