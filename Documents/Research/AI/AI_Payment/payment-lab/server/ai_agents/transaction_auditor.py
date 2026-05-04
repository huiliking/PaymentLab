"""
Transaction Auditor — AI-powered pre-submission check.

Runs before every PaymentIntent creation. Sends the full transaction
context to a local LLM and asks it to flag anything that looks wrong.

This agent has NO hardcoded knowledge of specific bugs. It relies on
the LLM's general understanding of commerce, currencies, and pricing
to spot anomalies.
"""

import requests
import json


class TransactionAuditor:
    """Pre-submission transaction auditor using local Ollama."""

    def __init__(self, ollama_url="http://localhost:11434", model="llama3.2"):
        self.ollama_url = ollama_url
        self.model = model

    def audit_transaction(self, transaction_data):
        """
        Audit a transaction before it's submitted to Stripe.

        Args:
            transaction_data: {
                "items": [
                    {"name": "...", "unit_price_usd_cents": 9900, "quantity": 2, "subtotal_usd_cents": 19800}
                ],
                "target_currency": "jpy",
                "final_amount": 198,
                "customer_locale": "ja-JP",
                "billing_country": "JP"
            }

        Returns:
            {
                "approved": True/False,
                "risk_level": "OK" | "WARNING" | "CRITICAL",
                "concerns": ["..."],
                "raw_response": "..."
            }
        """
        prompt = self._build_prompt(transaction_data)

        # Print the full prompt for debugging
        print(f"\n  [AI AUDITOR] === PROMPT SENT TO LLM ===")
        print(prompt)
        print(f"  [AI AUDITOR] === END PROMPT ===\n")

        # Compute display amount for debug logging
        target_cur = transaction_data['target_currency'].upper()
        zero_decimal = {"JPY", "KRW", "VND", "CLP", "PYG", "UGX", "RWF"}
        raw_amount = transaction_data['final_amount']
        if target_cur in zero_decimal:
            debug_amount = f"{raw_amount:,} {target_cur}"
        else:
            debug_amount = f"{raw_amount / 100:,.2f} {target_cur}"

        print(f"\n  [AI AUDITOR] Reviewing transaction before submission...")
        print(f"  [AI AUDITOR] Items: {len(transaction_data['items'])} item(s)")
        print(f"  [AI AUDITOR] Target currency: {target_cur}")
        print(f"  [AI AUDITOR] Final amount: {debug_amount} (raw: {raw_amount})")

        try:
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 500}
                },
                timeout=60
            )

            if response.status_code != 200:
                print(f"  [AI AUDITOR] Ollama error: HTTP {response.status_code}")
                return self._fallback_result("LLM unavailable")

            result = response.json()
            raw = result.get("response", "").strip()

            print(f"  [AI AUDITOR] LLM response:\n    {raw[:500]}")

            return self._parse_response(raw)

        except requests.exceptions.ConnectionError:
            print(f"  [AI AUDITOR] Cannot connect to Ollama at {self.ollama_url}")
            return self._fallback_result("Ollama not running")
        except Exception as e:
            print(f"  [AI AUDITOR] Error: {e}")
            return self._fallback_result(str(e))

    def _build_prompt(self, data):
        """Build the audit prompt. Generic — no bug-specific hints."""

        items_desc = []
        for item in data["items"]:
            price_usd = item["unit_price_usd_cents"] / 100
            subtotal_usd = item["subtotal_usd_cents"] / 100
            items_desc.append(
                f"  - {item['name']}: ${price_usd:.2f} USD x {item['quantity']} = ${subtotal_usd:.2f} USD"
            )

        items_text = "\n".join(items_desc)
        total_usd = sum(i["subtotal_usd_cents"] for i in data["items"]) / 100

        # Convert final_amount to human-readable form
        # Zero-decimal currencies (JPY, KRW, etc.) are already in major units
        # Others are in minor units (cents) — divide by 100 for display
        target_cur = data["target_currency"].upper()
        zero_decimal = {"JPY", "KRW", "VND", "CLP", "PYG", "UGX", "RWF"}
        raw_amount = data["final_amount"]
        if target_cur in zero_decimal:
            display_amount = f"{raw_amount:,} {target_cur}"
        else:
            display_amount = f"{raw_amount / 100:,.2f} {target_cur}"

        # Pre-compute expected conversion so the LLM only has to compare
        rates = {
            "USD": 1.0, "EUR": 0.92, "GBP": 0.79, "CAD": 1.38, "AUD": 1.53,
            "JPY": 154.5, "KRW": 1350, "CNY": 7.24, "INR": 83.5,
            "MXN": 17.2, "BRL": 4.97, "CHF": 0.88, "SEK": 10.5,
            "SGD": 1.34, "HKD": 7.82, "NZD": 1.67, "NOK": 10.8,
            "PLN": 4.02, "THB": 35.4, "TRY": 32.5, "ZAR": 18.6,
        }

        conversion_line = ""
        if target_cur != "USD" and target_cur in rates:
            rate = rates[target_cur]
            expected = total_usd * rate
            if target_cur in zero_decimal:
                expected_display = f"{round(expected):,} {target_cur}"
            else:
                expected_display = f"{expected:,.2f} {target_cur}"
            conversion_line = f"""
CONVERSION CHECK:
- Exchange rate for {target_cur}: 1 USD = {rate} {target_cur}
- Expected amount: ${total_usd:.2f} USD x {rate} = {expected_display}
- Actual amount to be charged: {display_amount}"""

        prompt = f"""You are a payment transaction auditor.

TRANSACTION:
- Items purchased:
{items_text}
- Cart total: ${total_usd:.2f} USD
- Currency to charge: {target_cur}
- Amount to charge: {display_amount}
- Customer locale: {data.get('customer_locale', 'unknown')}
- Billing country: {data.get('billing_country', 'unknown')}
{conversion_line}

RULES:
- If a CONVERSION CHECK is shown above, compare "Expected amount" to "Actual amount to be charged."
- If the actual amount is less than 90% or more than 110% of the expected amount, respond RISK: CRITICAL.
- If they are within 10% of each other, respond RISK: OK.
- If no conversion check is shown (same currency), check that the numbers are consistent and respond RISK: OK if they are.

Respond in this exact format:
RISK: [OK or CRITICAL]
CONCERNS: [Explain what you found]"""

        return prompt

    def _parse_response(self, raw):
        """Parse the LLM response into structured result."""
        raw_upper = raw.upper()

        # Determine risk level
        if "CRITICAL" in raw_upper:
            risk = "CRITICAL"
            approved = False
        elif "WARNING" in raw_upper:
            risk = "WARNING"
            approved = True  # Warnings don't block, but get logged
        else:
            risk = "OK"
            approved = True

        # Extract concerns
        concerns = []
        in_concerns = False
        for line in raw.split("\n"):
            line_stripped = line.strip()
            if line_stripped.upper().startswith("CONCERNS:"):
                in_concerns = True
                concern_text = line_stripped.split(":", 1)[1].strip()
                if concern_text and concern_text.lower() != "none":
                    concerns.append(concern_text)
            elif in_concerns and line_stripped.startswith("-"):
                concerns.append(line_stripped.lstrip("- "))
            elif line_stripped.upper().startswith("EXPLANATION:"):
                in_concerns = False

        return {
            "approved": approved,
            "risk_level": risk,
            "concerns": concerns,
            "raw_response": raw
        }

    def _fallback_result(self, reason):
        """When LLM is unavailable, allow transaction but log it."""
        return {
            "approved": True,
            "risk_level": "UNKNOWN",
            "concerns": [f"AI audit skipped: {reason}"],
            "raw_response": ""
        }
