"""
AI Fraud / Security Agent (Phase 3-4)

This agent will analyze transactions for fraud signals,
with a special focus on locale-based anomalies:

- IP geolocation vs billing country mismatch
- Browser locale vs card issuing country mismatch
- Shipping address in different country than billing
- Unusual currency/locale combinations
- Velocity checks (many attempts from same locale pattern)

For now, this is a stub with the interface we'll implement.
"""


class FraudAgent:
    """AI-powered fraud detection using locale signals."""

    def __init__(self, llm_backend="anthropic"):
        self.backend = llm_backend

    def assess_risk(self, transaction_data):
        """
        Assess fraud risk for a transaction.
        
        Args:
            transaction_data: {
                "amount": 4999,
                "currency": "usd",
                "customer_locale": "ja-JP",
                "billing_country": "US",
                "shipping_country": "BR",
                "ip_country": "NG",
                "card_country": "US",
                "user_agent": "...",
            }
        
        Returns:
            {
                "risk_score": 0-100,
                "risk_level": "low" | "medium" | "high",
                "signals": [
                    {"type": "locale_mismatch", "detail": "...", "weight": 30},
                ],
                "recommendation": "allow" | "review" | "block"
            }
        """
        # TODO: Implement with Claude API for contextual reasoning
        signals = []
        score = 0

        # Basic rule: locale vs billing country mismatch
        locale = transaction_data.get("customer_locale", "")
        billing = transaction_data.get("billing_country", "")

        if locale and billing:
            locale_country = locale.split("-")[-1].upper() if "-" in locale else ""
            if locale_country and locale_country != billing.upper():
                signals.append({
                    "type": "locale_billing_mismatch",
                    "detail": f"Browser locale '{locale}' vs billing country '{billing}'",
                    "weight": 20,
                })
                score += 20

        # Basic rule: shipping vs billing country
        shipping = transaction_data.get("shipping_country", "")
        if shipping and billing and shipping.upper() != billing.upper():
            signals.append({
                "type": "shipping_billing_mismatch",
                "detail": f"Shipping to '{shipping}' but billing in '{billing}'",
                "weight": 15,
            })
            score += 15

        risk_level = "low" if score < 30 else "medium" if score < 60 else "high"
        recommendation = "allow" if score < 30 else "review" if score < 60 else "block"

        return {
            "risk_score": min(score, 100),
            "risk_level": risk_level,
            "signals": signals,
            "recommendation": recommendation,
        }
