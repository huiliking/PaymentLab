"""
fraud_investigator.py
=====================
FAA-Style Fraud Investigation Agent

Inspired by:
  - FAA Framework (arxiv.org/abs/2506.11635) — iterative investigation loop
  - LLM+GCN (arxiv.org/abs/2509.09928) — graph-based relationship analysis

Architecture:
  Transaction flagged → Rule-based pre-screen → LLM investigation loop:
    1. PLAN   — decide what to investigate next
    2. GATHER — call tool functions to query DB / check signals
    3. ANALYZE — assess gathered evidence, decide if more investigation needed
    4. REPORT — produce evidence-backed verdict with confidence score

Tool definitions are loaded from registry.json via ToolRegistry.
Adding a new tool = one .py method + one registry.json entry. No changes to
the investigation loop.

The agent runs on Claude API (claude-sonnet-4-5) with Ollama fallback.
"""
from .identity_graph import IdentityGraphBuilder
import difflib
import sqlite3
import json
import re
import os
import sys
import uuid
import requests
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional

# Ollama's free-text responses can contain arbitrary Unicode (e.g. checkmarks,
# emoji) that crashes print() on Windows consoles using non-UTF-8 codepages
# (e.g. GBK). Force UTF-8 regardless of how this module is invoked (CLI,
# Flask import, or a reused instance across a batch loop).
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
try:
    from graph_result_summarizer import summarize_for_llm
except ModuleNotFoundError:
    from ai_agents.graph_result_summarizer import summarize_for_llm

try:
    import anthropic as anthropic_sdk
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

try:
    from tool_registry import ToolRegistry
except ModuleNotFoundError:
    from ai_agents.tool_registry import ToolRegistry


# ── Retry-recovery tuning (Sprint 6) ────────────────────────────────────────

# How many times a single hallucinated tool name gets corrective feedback
# before the correction goes terminal. 1 = correct once, then give up on that
# name. Name-sensitive, not params-sensitive: one stubborn fixation costs at
# most two turns, not the whole investigation.
HALLUCINATION_REPEAT_CAP = 1

# Similarity above which a successfully-called tool counts as fulfilling the
# intent behind a hallucinated name — i.e. the model self-corrected, so the
# check is NOT unresolved and must not caveat the verdict.
#
# A hallucinated name is by definition absent from the registry, so it can
# never itself appear in tools_called; "was this resolved?" is a
# correspondence question, not a membership test, and name similarity is the
# only signal the loop records. Measured against the live registry, genuine
# near-miss fumbles (check_/get_ prefix swap on the same noun) score
# 0.83–0.87 against their intended target while scoring ≤0.65 against every
# other active tool. 0.75 sits in that corridor with margin on both sides.
#
# test_fuzzy_resolution_threshold pins this against the actual registry
# contents, so a future tool name that narrows the gap fails loudly instead
# of silently misclassifying.
HALLUCINATION_MATCH_CUTOFF = 0.75


# ── Data classes ────────────────────────────────────────────────────────────

@dataclass
class Evidence:
    source: str        # tool that produced this
    finding: str       # what was found
    risk_signal: str   # LOW, MEDIUM, HIGH, CRITICAL
    details: dict = field(default_factory=dict)


@dataclass 
class InvestigationStep:
    step_number: int
    phase: str         # PLAN, GATHER, ANALYZE
    action: str        # what the agent decided to do
    result: str        # what happened
    tool_used: Optional[str] = None


@dataclass
class InvestigationReport:
    transaction_id: str
    risk_level: str = "LOW"
    verdict: str = "LEGITIMATE"
    confidence: float = 0.0
    summary: str = ""
    evidence: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    created_at: str = ""
    tool_results: dict = field(default_factory=dict)   # keyed by tool name, stores raw results
    # Intended checks the model asked for by a name that doesn't exist and
    # never recovered from. Each entry:
    #   {"tool_name": str, "attempts": int, "error_class": "unknown_tool"}
    # Out-of-grant refusals never appear here — a tool the merchant's tier
    # legitimately excludes is correct scope, not a gap.
    unresolved_checks: list = field(default_factory=list)

    def to_dict(self):
        return {
            "transaction_id": self.transaction_id,
            "risk_level": self.risk_level,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "summary": self.summary,
            "evidence": [asdict(e) if isinstance(e, Evidence) else e for e in self.evidence],
            "steps": [asdict(s) if isinstance(s, InvestigationStep) else s for s in self.steps],
            "created_at": self.created_at,
            "tool_results": self.tool_results,
            "unresolved_checks": self.unresolved_checks,
        }


# ── Tool Functions (the agent's "hands") ────────────────────────────────────

class InvestigationTools:
    """Database-backed tool functions the agent can call"""
    
    def __init__(self, db_path="payment_lab.db"):
        self.db_path = db_path
    
    def _query(self, sql, params=()):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows
    
    def get_transaction_details(self, txn_id):
        """Get full details of a specific transaction"""
        rows = self._query("SELECT * FROM transactions WHERE id = ?", (txn_id,))
        if rows:
            row = rows[0]
            # Parse metadata
            if row.get("metadata"):
                try:
                    row["metadata_parsed"] = json.loads(row["metadata"])
                except:
                    pass
            return {"tool": "get_transaction_details", "result": row}
        return {"tool": "get_transaction_details", "result": None, "error": "Transaction not found"}
    
    def get_card_history(self, card_last4, hours=24):
        """Get all transactions for a card within time window"""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat() + "Z"
        rows = self._query(
            """SELECT id, amount_cents, currency, status, customer_email, 
                      billing_country, ip_country, browser_locale, created_at
               FROM transactions 
               WHERE card_last4 = ? AND created_at > ?
               ORDER BY created_at DESC""",
            (card_last4, cutoff)
        )
        return {
            "tool": "get_card_history",
            "card_last4": card_last4,
            "hours": hours,
            "count": len(rows),
            "transactions": rows
        }
    
    def get_address_history(self, address):
        """Find all transactions shipped to this address"""
        # Exact match first
        rows = self._query(
            """SELECT id, card_last4, customer_email, customer_name, 
                      amount_cents, currency, created_at
               FROM transactions 
               WHERE shipping_address = ?
               ORDER BY created_at DESC""",
            (address,)
        )
        # Fallback: LIKE match if exact returns nothing (handles partial addresses)
        if not rows and address:
            rows = self._query(
                """SELECT id, card_last4, customer_email, customer_name, 
                          amount_cents, currency, created_at
                   FROM transactions 
                   WHERE shipping_address LIKE ?
                   ORDER BY created_at DESC""",
                (f"%{address}%",)
            )
        unique_cards = list(set(r["card_last4"] for r in rows))
        unique_emails = list(set(r["customer_email"] for r in rows))
        return {
            "tool": "get_address_history",
            "address": address,
            "total_orders": len(rows),
            "unique_cards": len(unique_cards),
            "unique_emails": len(unique_emails),
            "card_list": unique_cards,
            "email_list": unique_emails,
            "transactions": rows[:10]  # limit for prompt size
        }
    
    def get_email_history(self, email):
        """Get transaction history for an email"""
        rows = self._query(
            """SELECT id, card_last4, amount_cents, currency, status,
                      billing_country, ip_country, created_at
               FROM transactions 
               WHERE customer_email = ?
               ORDER BY created_at DESC""",
            (email,)
        )
        return {
            "tool": "get_email_history",
            "email": email,
            "total_transactions": len(rows),
            "transactions": rows[:10]
        }
    
    def check_locale_consistency(self, txn_id):
        """Check if browser locale, billing country, IP country, and card country are consistent"""
        rows = self._query(
            "SELECT browser_locale, billing_country, ip_country, card_country, shipping_address, "
            "customer_email, device_fingerprint FROM transactions WHERE id = ?",
            (txn_id,)
        )
        if not rows:
            return {"tool": "check_locale_consistency", "error": "Transaction not found"}

        txn = rows[0]
        locale = txn["browser_locale"] or ""
        billing = txn["billing_country"] or ""
        ip = txn["ip_country"] or ""
        card = txn["card_country"] or ""
        shipping = txn["shipping_address"] or ""
        email = txn["customer_email"] or ""
        device_fingerprint = txn["device_fingerprint"] or ""
        
        # Extract country from locale (e.g., "ja-JP" -> "JP")
        locale_country = locale.split("-")[-1].upper() if "-" in locale else ""
        
        mismatches = []
        if locale_country and billing and locale_country != billing:
            mismatches.append(f"Browser locale '{locale}' suggests {locale_country} but billing country is {billing}")
        if ip and billing and ip != billing:
            mismatches.append(f"IP country {ip} differs from billing country {billing}")
        if card and billing and card != billing:
            mismatches.append(f"Card issued in {card} but billing country is {billing}")
        if locale_country and ip and locale_country != ip:
            mismatches.append(f"Browser locale suggests {locale_country} but IP is from {ip}")
        
        # Check if shipping address matches billing country
        shipping_countries = {
            "CA": ["canada", "toronto", "ontario", "quebec", " on ", " bc "],
            "JP": ["tokyo", "japan", "osaka", "shibuya"],
            "US": ["ca ", "ny ", "tx ", "wa ", "il ", "fl ", "san francisco", "new york", "chicago", "seattle"],
            "FR": ["paris", "france", "rue"],
            "DE": ["berlin", "germany", "münchen", "straße"],
        }
        
        shipping_lower = shipping.lower()
        detected_shipping_country = None
        for country_code, keywords in shipping_countries.items():
            if any(kw in shipping_lower for kw in keywords):
                detected_shipping_country = country_code
                break
        
        if detected_shipping_country and billing and detected_shipping_country != billing:
            mismatches.append(f"Shipping address appears to be in {detected_shipping_country} but billing country is {billing}")

        # Cross-transaction pattern lookup: does this SAME identity/ring repeat the
        # mismatch across merchants? Requires a shared entity (address/email/device) —
        # matching on country pair alone conflates unrelated fraud rings that happen
        # to share a common IP/billing combination.
        cross_txn_count = 0
        cross_merchant_count = 0
        pattern_description = ""
        if mismatches and ip and billing and ip != billing:
            shared_conditions = []
            shared_params = []
            if shipping:
                shared_conditions.append("shipping_address = ?")
                shared_params.append(shipping)
            if email:
                shared_conditions.append("customer_email = ?")
                shared_params.append(email)
            if device_fingerprint:
                shared_conditions.append("device_fingerprint = ?")
                shared_params.append(device_fingerprint)

            if shared_conditions:
                cross_rows = self._query(
                    "SELECT COUNT(*) as cnt, COUNT(DISTINCT merchant_id) as merchants "
                    "FROM transactions WHERE ip_country = ? AND billing_country = ? "
                    "AND ip_country != billing_country AND (" + " OR ".join(shared_conditions) + ")",
                    (ip, billing, *shared_params)
                )
                if cross_rows:
                    cross_txn_count = cross_rows[0]["cnt"]
                    cross_merchant_count = cross_rows[0]["merchants"]
                    if cross_txn_count > 1:
                        pattern_description = (
                            f"Same shipping address/email/device linked to IP={ip}, billing={billing} "
                            f"mismatch across {cross_txn_count} transactions, {cross_merchant_count} merchant(s)"
                        )

        risk = "HIGH" if len(mismatches) >= 2 else ("MEDIUM" if len(mismatches) == 1 else "LOW")
        if cross_merchant_count >= 2:
            risk = "CRITICAL" if risk == "HIGH" else ("HIGH" if risk == "MEDIUM" else risk)

        return {
            "tool": "check_locale_consistency",
            "transaction_id": txn_id,
            "browser_locale": locale,
            "billing_country": billing,
            "ip_country": ip,
            "card_country": card,
            "shipping_address": shipping,
            "mismatches": mismatches,
            "is_consistent": len(mismatches) == 0,
            "risk": risk,
            "cross_txn_count": cross_txn_count,
            "cross_merchant_count": cross_merchant_count,
            "pattern_description": pattern_description,
        }
    
    def check_velocity(self, card_last4, hours=2):
        """Check transaction velocity for a card"""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat() + "Z"
        rows = self._query(
            """SELECT id, amount_cents, currency, status, created_at
               FROM transactions 
               WHERE card_last4 = ? AND created_at > ?
               ORDER BY created_at ASC""",
            (card_last4, cutoff)
        )
        
        total_amount = sum(r["amount_cents"] for r in rows)
        failed_count = sum(1 for r in rows if r["status"] == "failed")
        
        # Calculate time span
        if len(rows) >= 2:
            first = datetime.fromisoformat(rows[0]["created_at"].replace("Z", ""))
            last = datetime.fromisoformat(rows[-1]["created_at"].replace("Z", ""))
            span_minutes = (last - first).total_seconds() / 60
        else:
            span_minutes = 0
        
        risk = "LOW"
        if len(rows) >= 5:
            risk = "HIGH"
        elif len(rows) >= 3:
            risk = "MEDIUM"
        if failed_count >= 2:
            risk = "HIGH"
        
        return {
            "tool": "check_velocity",
            "card_last4": card_last4,
            "window_hours": hours,
            "transaction_count": len(rows),
            "total_amount_cents": total_amount,
            "failed_count": failed_count,
            "time_span_minutes": round(span_minutes, 1),
            "risk": risk,
            "transactions": rows[:10]
        }
    
    def get_ip_reputation(self, ip_country):
        """Simple IP country risk assessment"""
        high_risk_countries = ["NG", "RO", "PH", "VN", "BD", "PK", "UA", "RU"]
        medium_risk_countries = ["BR", "IN", "MX", "TR", "TH", "ID"]
        
        if ip_country in high_risk_countries:
            risk = "HIGH"
            note = f"{ip_country} is frequently associated with card-not-present fraud"
        elif ip_country in medium_risk_countries:
            risk = "MEDIUM"
            note = f"{ip_country} has moderate fraud risk"
        else:
            risk = "LOW"
            note = f"{ip_country} is a low-risk jurisdiction"
        
        return {
            "tool": "get_ip_reputation",
            "ip_country": ip_country,
            "risk": risk,
            "note": note
        }
    
    def get_device_history(self, device_fingerprint):
        """Check how many different cards/emails used this device"""
        rows = self._query(
            """SELECT DISTINCT card_last4, customer_email, billing_country
               FROM transactions 
               WHERE device_fingerprint = ?""",
            (device_fingerprint,)
        )
        
        unique_cards = list(set(r["card_last4"] for r in rows))
        unique_emails = list(set(r["customer_email"] for r in rows))
        
        risk = "LOW"
        if len(unique_cards) >= 3:
            risk = "HIGH"
        elif len(unique_cards) >= 2:
            risk = "MEDIUM"
        
        return {
            "tool": "get_device_history",
            "device_fingerprint": device_fingerprint,
            "unique_cards": len(unique_cards),
            "unique_emails": len(unique_emails),
            "cards": unique_cards,
            "emails": unique_emails,
            "risk": risk
        }
    
    def check_identity_graph(self, txn_id: str = None, transaction_id: str = None, hops: int = 2) -> dict:
        """
        Build an entity relationship graph around the transaction and detect
        fraud rings or synthetic identity clusters.

        Args:
            txn_id: The transaction to investigate
            transaction_id: Accepted alias for txn_id — some LLM tool calls echo
                this name back from other tools' output fields instead of the
                registered input schema name
            hops: Graph traversal depth (1 or 2, default 2)

        Returns:
            dict with graph structure, cluster stats, risk score, and summary
        """
        txn_id = txn_id or transaction_id
        if not txn_id:
            return {"error": "check_identity_graph requires txn_id"}

        db_path = getattr(self, "db_path", None) or os.environ.get(
            "PAYMENTLAB_DB", "server/database/transactions.db"
        )

        builder = IdentityGraphBuilder(db_path=db_path)
        builder.MAX_HOPS = max(1, min(hops, 2))  # cap at 2 for performance

        result = builder.build(txn_id)

        return {
            "transaction_id": result.transaction_id,
            "seed_entities": result.seed_entities,
            "graph": {
                "node_count": len(result.nodes),
                "edge_count": len(result.edges),
                "nodes": result.nodes,
                "edges": result.edges,
            },
            "cluster": {
                "size": result.cluster_size,
                "shared_emails": result.shared_email_count,
                "shared_addresses": result.shared_address_count,
                "shared_ips": result.shared_ip_count,
                "shared_devices": result.shared_device_count,
                "known_fraud_neighbors": result.known_fraud_neighbors,
                "max_entity_reuse": result.max_card_reuse,
            },
            "risk": {
                "score": result.risk_score,
                "label": result.risk_label,
                "fraud_ring_detected": result.fraud_ring_detected,
                "synthetic_identity_signals": result.synthetic_identity_signals,
            },
            "geographic_profile": result.geographic_profile,
            "summary": result.summary,
        }


    # NOTE: TOOL_REGISTRY dict and execute_tool() method removed.
    # Tool schemas and dispatch are now handled by ToolRegistry (tool_registry.py).
    # The actual tool methods above (get_transaction_details, get_card_history, etc.)
    # remain here — ToolRegistry.execute() calls them via getattr().


# ── Rule-based pre-screen ──────────────────────────────────────────────────

class RuleEngine:
    """Fast rule-based pre-screen before LLM investigation"""
    
    def __init__(self, db_path="payment_lab.db"):
        self.tools = InvestigationTools(db_path)
    
    def pre_screen(self, txn_id):
        """
        Quick rule checks. Returns list of triggered rules with risk levels.
        This decides IF the LLM agent should investigate (saves compute).
        """
        triggers = []
        
        # Get transaction
        result = self.tools.get_transaction_details(txn_id)
        txn = result.get("result")
        if not txn:
            return [{"rule": "TRANSACTION_NOT_FOUND", "risk": "ERROR"}]
        
        # Rule 1: High amount
        amount_usd = txn["amount_cents"]
        if txn["currency"] == "jpy":
            amount_usd = txn["amount_cents"] / 154.5  # approximate conversion
        else:
            amount_usd = txn["amount_cents"] / 100
        
        if amount_usd > 500:
            triggers.append({"rule": "HIGH_AMOUNT", "risk": "MEDIUM", "detail": f"${amount_usd:.2f}"})
        
        # Rule 2: Very small amount (card testing signal)
        if amount_usd < 5:
            triggers.append({"rule": "MICRO_AMOUNT", "risk": "MEDIUM", "detail": f"${amount_usd:.2f} - possible card testing"})
        
        # Rule 3: Failed transaction
        if txn["status"] == "failed":
            triggers.append({"rule": "FAILED_TRANSACTION", "risk": "LOW", "detail": "Transaction was declined"})
        
        # Rule 4: IP/billing mismatch (quick check without full locale analysis)
        if txn["ip_country"] and txn["billing_country"] and txn["ip_country"] != txn["billing_country"]:
            triggers.append({
                "rule": "IP_BILLING_MISMATCH", 
                "risk": "HIGH",
                "detail": f"IP: {txn['ip_country']}, Billing: {txn['billing_country']}"
            })
        
        # Rule 5: Suspicious email domain
        email = txn.get("customer_email", "")
        suspicious_domains = ["tempmail", "throwaway", "guerrillamail", "mailinator", "freemail"]
        if any(d in email.lower() for d in suspicious_domains):
            triggers.append({"rule": "SUSPICIOUS_EMAIL", "risk": "HIGH", "detail": email})
        
        # Rule 6: High-risk IP country
        ip_rep = self.tools.get_ip_reputation(txn.get("ip_country", ""))
        if ip_rep["risk"] in ("HIGH", "MEDIUM"):
            triggers.append({"rule": "HIGH_RISK_IP", "risk": ip_rep["risk"], "detail": ip_rep["note"]})

        # Rule 7: Currency vs billing country mismatch
        # e.g. billing country JP but paying in USD, or billing US but paying in JPY
        CURRENCY_COUNTRY_MAP = {
            "usd": ["US", "PR", "EC", "SV", "PA"],
            "eur": ["DE", "FR", "IT", "ES", "NL", "BE", "AT", "PT", "FI",
                    "IE", "GR", "SK", "SI", "EE", "LV", "LT", "LU", "MT", "CY"],
            "gbp": ["GB"],
            "jpy": ["JP"],
            "cad": ["CA"],
            "aud": ["AU"],
            "chf": ["CH"],
            "cny": ["CN"],
            "krw": ["KR"],
            "mxn": ["MX"],
            "brl": ["BR"],
            "inr": ["IN"],
            "sgd": ["SG"],
            "hkd": ["HK"],
            "sek": ["SE"],
            "nok": ["NO"],
            "dkk": ["DK"],
        }
        currency = txn.get("currency", "").lower()
        billing = (txn.get("billing_country") or "").upper()
        if currency and billing:
            expected_countries = CURRENCY_COUNTRY_MAP.get(currency, [])
            if expected_countries and billing not in expected_countries:
                triggers.append({
                    "rule": "CURRENCY_BILLING_MISMATCH",
                    "risk": "MEDIUM",
                    "detail": f"Billing country {billing} unusual for {currency.upper()} payment"
                })

        # Rule 8: Currency hopping — same card, multiple currencies, short window
        cutoff_hop = (datetime.now() - timedelta(hours=48)).isoformat() + "Z"
        card_txns = self.tools._query(
            "SELECT DISTINCT currency FROM transactions WHERE card_last4 = ? AND created_at > ?",
            (txn["card_last4"], cutoff_hop)
        )
        distinct_currencies = {r["currency"] for r in card_txns}
        if len(distinct_currencies) >= 3:
            triggers.append({
                "rule": "CURRENCY_HOPPING",
                "risk": "HIGH",
                "detail": f"Card used in {len(distinct_currencies)} currencies within 6h: {', '.join(sorted(distinct_currencies))}"
            })

        return triggers


# ── LLM Investigation Agent ────────────────────────────────────────────────

class FraudInvestigator:
    """
    FAA-style iterative investigation agent.
    Uses Claude API for Plan→Gather→Analyze loop when ANTHROPIC_API_KEY is set,
    falls back to Ollama (llama3.2) otherwise.
    """
    
    def __init__(self, db_path="payment_lab.db", ollama_url="http://localhost:11434",
                 model="llama3.2", max_steps=None, registry_path=None, backend="ollama",
                 metering_service=None, allowed_tools=None, max_steps_ollama=None):
        """
        Args:
            backend: "auto" (Claude if key exists, else Ollama),
                     "ollama" (force Ollama even if key exists),
                     "claude" (force Claude, error if no key)
            metering_service: MeteringService instance for usage tracking (optional)
            allowed_tools: Optional list[str] of tool names this investigation
                may use — the merchant's tier grants, resolved by the business
                layer before this object is constructed. None = no restriction
                (all active tools), which preserves pre-Sprint-5 behavior for
                CLI usage and any caller that hasn't resolved a merchant.
            max_steps: Turn ceiling. None = per-backend defaults (Claude 6,
                Ollama 8). An explicit value applies to BOTH backends — the
                caller knows what they want. The asymmetry only applies to
                defaults: Ollama has no parallel tool calls, so each
                hallucination burns a whole iteration, and the smaller local
                models fumble tool names more often than Claude.
            max_steps_ollama: Overrides the Ollama ceiling specifically.
        """
        print(f"\n  {'='*55}")
        print(f"  FRAUD INVESTIGATOR - BOOT SEQUENCE")
        print(f"  {'='*55}")

        # Phase 1: Database + tools
        self.tools = InvestigationTools(db_path)
        self.rule_engine = RuleEngine(db_path)
        self.ollama_url = ollama_url
        self.model = model
        # Per-backend turn ceilings. An explicit max_steps wins for both
        # backends; the 6/8 asymmetry only applies when the caller passed
        # nothing (hence the None sentinel rather than a literal default —
        # a plain default of 6 can't distinguish "caller passed 6" from
        # "caller passed nothing").
        self.max_steps = 6 if max_steps is None else max_steps
        if max_steps_ollama is not None:
            self.max_steps_ollama = max_steps_ollama
        elif max_steps is not None:
            self.max_steps_ollama = max_steps
        else:
            self.max_steps_ollama = 8
        self.db_path = db_path
        self.meter = metering_service
        self.allowed_tools = allowed_tools
        # {tool_name: attempt_count} for names the model hallucinated.
        # Investigation-scoped state that guides loop behavior; the *outcome*
        # (which names stayed unresolved) goes on the report instead.
        self._hallucination_attempts = {}
        print(f"  [BOOT 1/3] Database: {db_path}")

        # Phase 2: Tool registry
        if registry_path is None:
            # Look for registry.json relative to this file's directory
            registry_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "registry.json")
            if not os.path.exists(registry_path):
                # Fallback: same directory as this file
                registry_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "registry.json")
        self.registry = ToolRegistry(registry_path)
        active_tools = self.registry.get_active_tools()
        print(f"  [BOOT 2/3] Registry: {registry_path}")
        print(f"             {self.registry}")
        print(f"             Active tools: {', '.join(t['name'] for t in active_tools)}")
        if allowed_tools is not None:
            print(f"             Merchant tool grant: {', '.join(allowed_tools) or '(none)'}")

        # Phase 3: Backend selection
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        
        if backend == "ollama":
            # Force Ollama regardless of API key
            self.claude = None
            self.use_claude = False
            print(f"  [BOOT 3/3] Backend: Ollama ({model}) - forced via --backend=ollama")
        elif backend == "claude":
            # Force Claude — error if no key
            if not ANTHROPIC_AVAILABLE:
                raise RuntimeError("--backend=claude requires: pip install anthropic")
            if not api_key:
                raise RuntimeError("--backend=claude requires ANTHROPIC_API_KEY environment variable")
            self.claude = anthropic_sdk.Anthropic(api_key=api_key)
            self.use_claude = True
            print(f"  [BOOT 3/3] Backend: Claude API ({self.CLAUDE_MODEL}) - forced via --backend=claude")
            print(f"             API key: {api_key[:12]}...{api_key[-4:]}")
        else:
            # Auto: Claude if key exists, else Ollama
            if api_key and ANTHROPIC_AVAILABLE:
                self.claude = anthropic_sdk.Anthropic(api_key=api_key)
                self.use_claude = True
                print(f"  [BOOT 3/3] Backend: Claude API ({self.CLAUDE_MODEL}) - auto-detected API key")
                print(f"             API key: {api_key[:12]}...{api_key[-4:]}")
            else:
                self.claude = None
                self.use_claude = False
                reason = "anthropic package not installed" if not ANTHROPIC_AVAILABLE else "ANTHROPIC_API_KEY not set"
                print(f"  [BOOT 3/3] Backend: Ollama ({model}) - {reason}")

        print(f"  {'='*55}\n")

    def _available_tool_names(self):
        """
        The tool names the model is actually allowed to use right now — the
        merchant's grant when one is resolved, otherwise every active tool.
        This is what a correction message offers as alternatives, so it must
        never leak names outside the grant (one-way dependency: the business
        layer produces policy, this layer only consumes it).
        """
        if self.allowed_tools is not None:
            return list(self.allowed_tools)
        return [t["name"] for t in self.registry.get_active_tools()]

    def _correction_message(self, tool_name, terminal):
        """Corrective feedback for a hallucinated tool name."""
        if terminal:
            return (
                f"'{tool_name}' is not a valid tool. This tool was already "
                f"corrected and remains unavailable. Proceeding without it."
            )
        return (
            f"'{tool_name}' is not a valid tool. "
            f"Available tools: {', '.join(self._available_tool_names())}"
        )

    def _dispatch(self, tool_name, params):
        """
        Execute a tool call, enforcing the merchant's tier grant server-side
        — the schemas Claude/Ollama were offered already exclude tools
        outside allowed_tools, but this is the actual gate: it's checked
        against every tool call that reaches dispatch, not just what the
        LLM was shown, so a hallucinated or out-of-grant call can't execute.

        The gate refuses two different things and the caller needs to tell
        them apart, so every refusal carries an `error_class`:

          - "unknown_tool" — the name isn't in the registry at all. A model
            fumble, not a policy refusal; the loops feed the correction back
            so the model can retry with the right name.
          - "not_granted"  — the tool exists but is outside this merchant's
            tier. The gate working as designed. Terminal, no retry.

        Both keep the `error` key, so existing `if "error" in result` checks
        stay correct; `error_class` is purely additive.

        ORDER IS LOAD-BEARING. Classification is a read-only registry lookup
        that must happen before any execution path. Never classify by
        attempting registry.execute() first — that would run a valid-but-
        ungranted tool before the gate refuses it.
        """
        # 1. Hallucination. Checked OUTSIDE the allowed_tools guard: a
        #    nonexistent name must not reach registry.execute() whether or
        #    not a merchant was resolved.
        if self.registry.get_tool(tool_name) is None:
            attempts = self._hallucination_attempts.get(tool_name, 0)
            return {
                "error": self._correction_message(
                    tool_name, terminal=attempts >= HALLUCINATION_REPEAT_CAP
                ),
                "error_class": "unknown_tool",
            }

        # 2. Out-of-grant. Unreachable when allowed_tools is None (CLI, no
        #    merchant resolved) — that case has no grant to be outside of,
        #    so it falls through to execute, exactly as before Sprint 6.
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            return {
                "error": f"Tool '{tool_name}' is not granted for this merchant's tier",
                "error_class": "not_granted",
            }

        # 3. Real name, in grant (or unrestricted). Inactive tools land here
        #    too — the name is real, so registry.execute() handles the
        #    status check with its own error rather than us calling it a
        #    hallucination.
        return self.registry.execute(tool_name, params, self.tools)

    def _note_hallucination(self, tool_name):
        """
        Record an attempt at a hallucinated name and report whether the
        correction has gone terminal.

        The cap is name-sensitive, not params-sensitive: check_email_history
        with {"email": ...} and with {"txn_id": ...} are the same bad name,
        one correction attempt total.

        Returns (attempts, terminal).
        """
        attempts = self._hallucination_attempts.get(tool_name, 0) + 1
        self._hallucination_attempts[tool_name] = attempts
        return attempts, attempts > HALLUCINATION_REPEAT_CAP

    def _apply_unresolved_caveats(self, context, report):
        """
        Turn leftover hallucinated names into report caveats, after the
        verdict has been parsed and the loop's own fallbacks have run.

        A name counts as RESOLVED — no entry, no cap — when the model
        self-corrected: some tool it actually called successfully is a close
        enough match to the name it fumbled. See HALLUCINATION_MATCH_CUTOFF
        for why this is a similarity test rather than a membership test.

        Out-of-grant refusals never reach here; they're correct scope, not a
        gap, and must not caveat the verdict.
        """
        if not self._hallucination_attempts:
            return

        # The Ollama loop records deduped calls as "<name> (skipped-dup)".
        # Strip the suffix rather than dropping the entry — a name that was
        # self-corrected and then deduped on a later iteration still proves
        # the check happened.
        called = [
            name.replace(" (skipped-dup)", "")
            for name in context.get("tools_called", [])
        ]

        for tool_name, attempts in self._hallucination_attempts.items():
            if attempts <= 0:
                continue
            resolved = any(
                difflib.SequenceMatcher(None, tool_name, name).ratio()
                >= HALLUCINATION_MATCH_CUTOFF
                for name in called
            )
            if resolved:
                print(f"  [CORRECTION] '{tool_name}' resolved by a later call — no caveat")
                continue
            report.unresolved_checks.append({
                "tool_name": tool_name,
                "attempts": attempts,
                "error_class": "unknown_tool",
            })

        if not report.unresolved_checks:
            return

        # Cap, don't set — a model that already returned 0.5 stays at 0.5.
        # The cap only prevents a high-confidence verdict hiding a gap.
        cap = 0.85 if len(report.unresolved_checks) == 1 else 0.7
        if report.confidence > cap:
            print(f"  [CORRECTION] Confidence capped {report.confidence} → {cap} "
                  f"({len(report.unresolved_checks)} unresolved check(s))")
            report.confidence = cap

        report.summary += (
            f" [{len(report.unresolved_checks)} intended check(s) could not be "
            f"completed due to tool name errors.]"
        )

    def investigate(self, txn_id, customer_id=None):
        """
        Main entry point. Runs full investigation on a transaction.
        Returns InvestigationReport.

        Args:
            customer_id: Merchant identifier for metering (the merchant's
                registration_id, resolved by the caller). Falls back to
                "unknown_merchant" for callers that haven't resolved one
                (e.g. CLI usage without merchant context).
        """
        report = InvestigationReport(
            transaction_id=txn_id,
            created_at=datetime.now().isoformat() + "Z"
        )

        # Metering: one session per investigation
        self._session_id = str(uuid.uuid4())
        self._customer_id = customer_id or "unknown_merchant"

        # Hallucination tracking is per-investigation, not per-instance —
        # the CLI's --investigate-all reuses one investigator across a batch.
        self._hallucination_attempts = {}

        print(f"\n{'='*60}")
        print(f"FRAUD INVESTIGATION: {txn_id}")
        print(f"{'='*60}")
        
        # Phase 0: Pre-screen with rules
        print(f"\n[PHASE 0] Rule-based pre-screen...")
        triggers = self.rule_engine.pre_screen(txn_id)
        
        report.steps.append(InvestigationStep(
            step_number=0,
            phase="PRE-SCREEN",
            action="Rule-based pre-screen",
            result=f"{len(triggers)} rules triggered"
        ))
        
        if not triggers:
            print(f"  [+] No rules triggered - transaction appears clean")
            report.risk_level = "LOW"
            report.verdict = "LEGITIMATE"
            report.confidence = 0.9
            report.summary = "No risk signals detected in pre-screen. Transaction appears legitimate."
            return report
        
        for t in triggers:
            print(f"  [{t['risk']}] {t['rule']}: {t.get('detail', '')}")

        # Build shared investigation context
        txn_details = self.tools.get_transaction_details(txn_id)
        txn = txn_details.get("result", {})
        context = {
            "transaction": txn,
            "pre_screen_triggers": triggers,
            "evidence_gathered": [],
            "tools_called": [],
            # Corrective feedback for hallucinated names, surfaced back into
            # the next Ollama prompt. Claude gets its corrections through
            # tool_result blocks instead, so this stays empty there.
            "corrections": []
        }

        if not txn:
            print(f"  [!] Transaction not found in database")
            report.risk_level = "ERROR"
            report.verdict = "ERROR"
            report.summary = f"Transaction {txn_id} not found."
            return report

        print(f"\n  [CONTEXT] Card: ****{txn.get('card_last4')}  Amount: {txn.get('amount_cents', 0) / 100:.2f} {txn.get('currency')}")
        print(f"  [CONTEXT] Email: {txn.get('customer_email')}  IP: {txn.get('ip_country')}  Billing: {txn.get('billing_country')}")

        # Phase 1-N: LLM investigation loop
        backend_name = "Claude API" if self.use_claude else f"Ollama ({self.model})"
        schemas = self.registry.get_schemas(allowed=self.allowed_tools)
        print(f"\n[PHASE 1] Starting {backend_name} investigation (FAA Plan→Gather→Analyze)")
        print(f"  [REGISTRY] {len(schemas)} active tools loaded: {', '.join(s['name'] for s in schemas)}")

        if self.use_claude:
            self._claude_investigate(context, report)
        else:
            self._ollama_investigate(context, report)
        
        # Save report to DB
        self._save_report(report)
        
        # Print summary
        self._print_report(report)
        
        return report
    
    def _ollama_investigate(self, context, report):
        """Original Ollama-based Plan→Gather loop (unchanged)"""
        step_count = 0
        consecutive_skips = 0

        for iteration in range(self.max_steps_ollama):
            step_count += 1
            print(f"\n  --- Iteration {step_count} ---")

            plan = self._llm_plan(context, iteration)

            if not plan:
                print(f"  [!] LLM returned no plan, ending investigation")
                break

            report.steps.append(InvestigationStep(
                step_number=step_count, phase="PLAN",
                action=plan.get("reasoning", ""),
                result=f"Tool: {plan.get('tool', 'none')}"
            ))

            if plan.get("tool") == "CONCLUDE":
                print(f"  [+] LLM concluded investigation")
                break

            tool_name = plan.get("tool")
            tool_params = plan.get("params", {})

            # check_identity_graph only ever makes sense against the transaction
            # under investigation. Ollama's free-text PARAMS parsing sometimes
            # drops the id param entirely (no "key=value" match) or uses an
            # unexpected name — default to the current transaction rather than
            # erroring out on a call that has only one sensible target anyway.
            if tool_name == "check_identity_graph" and not (
                tool_params.get("txn_id") or tool_params.get("transaction_id")
            ):
                tool_params["txn_id"] = context["transaction"].get("id")

            call_key = f"{tool_name}:{json.dumps(tool_params, sort_keys=True, default=str)}"
            if call_key in {
                f"{e['tool']}:{json.dumps(e['params'], sort_keys=True, default=str)}"
                for e in context["evidence_gathered"]
            }:
                print(f"  [SKIP] Already called {tool_name} with same params")
                consecutive_skips += 1
                if consecutive_skips >= 2:
                    print(f"  [!] 2 consecutive skips - ending investigation")
                    break
                context["tools_called"].append(tool_name + " (skipped-dup)")
                continue

            consecutive_skips = 0
            print(f"  [GATHER] Calling {tool_name}({tool_params})")
            print(f"  [REGISTRY] Dispatching → registry.execute('{tool_name}', ..., tools_instance)")
            tool_result = self._dispatch(tool_name, tool_params)

            # ── Hallucinated name: correct it, don't record it as evidence ──
            # The model fumbled a name; feed the right ones back and let it
            # retry. This is NOT a gather — it produces no evidence, no
            # tool_results entry, and no GATHER step. Deliberately does not
            # touch consecutive_skips either: that counter is for repeated
            # *valid* calls, and conflating them could end the investigation
            # early on what is really a recoverable typo.
            if tool_result.get("error_class") == "unknown_tool":
                attempts, terminal = self._note_hallucination(tool_name)
                message = self._correction_message(tool_name, terminal=terminal)
                print(f"  [CORRECTION] {message}")
                context["corrections"].append({
                    "tool_name": tool_name,
                    "message": message,
                    "terminal": terminal,
                })
                report.steps.append(InvestigationStep(
                    step_number=step_count, phase="CORRECTION",
                    action=f"Rejected hallucinated tool name '{tool_name}' (attempt {attempts})",
                    result=message,
                    tool_used=None
                ))
                continue

            if "error" in tool_result:
                print(f"  [REGISTRY] Error: {tool_result['error']}")
            else:
                result_keys = [k for k in tool_result.keys() if k != 'tool']
                print(f"  [REGISTRY] OK, keys: {', '.join(result_keys)}")

            # Store raw result so Flask/React can access it (e.g. graph data for IdentityGraphPanel)
            report.tool_results[tool_name] = tool_result

            context["tools_called"].append(tool_name)
            context["evidence_gathered"].append({
                "tool": tool_name, "params": tool_params, "result": tool_result
            })

            report.steps.append(InvestigationStep(
                step_number=step_count, phase="GATHER",
                action=f"Called {tool_name}",
                result=json.dumps(tool_result, default=str)[:500],
                tool_used=tool_name
            ))

            evidence = self._extract_evidence(tool_name, tool_result)
            if evidence:
                report.evidence.append(evidence)
                print(f"  [EVIDENCE] {evidence.risk_signal}: {evidence.finding}")

            # ── Deterministic escalation ────────────────────────────────────
            # Ollama is too small to reliably decide when to use the graph tool.
            # When address_history reveals high card reuse, auto-trigger the graph.
            if (tool_name == "get_address_history"
                    and tool_result.get("unique_cards", 0) >= 5
                    and "check_identity_graph" not in context["tools_called"]
                    and (self.allowed_tools is None or "check_identity_graph" in self.allowed_tools)):
                txn_id = context["transaction"].get("id", "")
                print(f"  [ESCALATE] Address reused by {tool_result['unique_cards']} cards "
                      f"→ auto-calling check_identity_graph")
                graph_result = self._dispatch("check_identity_graph", {"txn_id": txn_id})
                report.tool_results["check_identity_graph"] = graph_result
                context["tools_called"].append("check_identity_graph")
                context["evidence_gathered"].append({
                    "tool": "check_identity_graph",
                    "params": {"transaction_id": txn_id},
                    "result": graph_result
                })
                step_count += 1
                print(f"\n  --- Iteration {step_count} (auto-escalation) ---")
                report.steps.append(InvestigationStep(
                    step_number=step_count, phase="GATHER",
                    action=f"Auto-escalated to check_identity_graph (address shared by "
                           f"{tool_result['unique_cards']} cards)",
                    result=json.dumps(graph_result, default=str)[:500],
                    tool_used="check_identity_graph"
                ))
                graph_evidence = self._extract_evidence("check_identity_graph", graph_result)
                if graph_evidence:
                    report.evidence.append(graph_evidence)
                    print(f"  [EVIDENCE] {graph_evidence.risk_signal}: {graph_evidence.finding}")

        # ── Mandatory fallback: always run identity graph if Ollama skipped it ──
        if ("check_identity_graph" not in context["tools_called"]
                and (self.allowed_tools is None or "check_identity_graph" in self.allowed_tools)):
            txn_id = context["transaction"].get("id", "")
            print(f"  [FALLBACK] Ollama never called check_identity_graph → auto-calling")
            graph_result = self._dispatch("check_identity_graph", {"txn_id": txn_id})
            report.tool_results["check_identity_graph"] = graph_result
            context["tools_called"].append("check_identity_graph")
            context["evidence_gathered"].append({
                "tool": "check_identity_graph",
                "params": {"txn_id": txn_id},
                "result": graph_result
            })
            step_count += 1
            report.steps.append(InvestigationStep(
                step_number=step_count, phase="GATHER",
                action="Auto-called check_identity_graph (mandatory fallback)",
                result=json.dumps(graph_result, default=str)[:500],
                tool_used="check_identity_graph"
            ))
            graph_evidence = self._extract_evidence("check_identity_graph", graph_result)
            if graph_evidence:
                report.evidence.append(graph_evidence)
                print(f"  [EVIDENCE] {graph_evidence.risk_signal}: {graph_evidence.finding}")

        print(f"\n[FINAL] Generating Ollama verdict...")
        verdict = self._llm_verdict(context)
        report.risk_level = verdict.get("risk_level", "MEDIUM")
        report.verdict = verdict.get("verdict", "SUSPICIOUS")
        report.confidence = verdict.get("confidence", 0.5)
        report.summary = verdict.get("summary", "Investigation completed.")

        # After the verdict AND after the mandatory-graph fallback above, so
        # a tool called by the fallback still counts toward resolution.
        self._apply_unresolved_caveats(context, report)

        report.steps.append(InvestigationStep(
            step_number=step_count + 1, phase="VERDICT",
            action="Final analysis",
            result=f"{report.verdict} (confidence: {report.confidence})"
        ))

    # ── Claude API Investigation (FAA Plan→Gather→Analyze) ─────────────────

    # NOTE: CLAUDE_TOOLS list removed. Schemas are now loaded from registry.json
    # via self.registry.get_schemas(). To add a new tool, add it to registry.json
    # and implement the method in InvestigationTools — no changes needed here.

    CLAUDE_MODEL = "claude-sonnet-4-5"

    def _claude_investigate(self, context, report):
        """
        FAA-style investigation using Claude's native tool use.
        Each assistant turn = PLAN + tool call (GATHER).
        Each user turn with tool_result = ANALYZE input for next iteration.
        Claude decides when to stop and produce the verdict.
        Token usage is tracked and logged per turn.
        """
        txn = context["transaction"]
        triggers = context["pre_screen_triggers"]

        triggers_text = "\n".join(
            f"  - [{t['risk']}] {t['rule']}: {t.get('detail', '')}"
            for t in triggers
        )

        system_prompt = (
            "You are a senior fraud analyst conducting a structured investigation. "
            "Use the available tools to gather evidence about this transaction. "
            "Call tools one at a time. After each result, analyze what it means "
            "and decide what to investigate next. When you have enough evidence, "
            "stop calling tools and write your final verdict.\n\n"
            "Your final response MUST include these lines:\n"
            "RISK_LEVEL: <LOW|MEDIUM|HIGH|CRITICAL>\n"
            "VERDICT: <LEGITIMATE|SUSPICIOUS|FRAUDULENT>\n"
            "CONFIDENCE: <0.0-1.0>\n"
            "SUMMARY: <2-3 sentences referencing specific evidence>"
        )

        initial_message = (
            f"Investigate this transaction:\n\n"
            f"ID: {txn.get('id')}\n"
            f"Card: ****{txn.get('card_last4')} ({txn.get('card_brand')}, issued in {txn.get('card_country')})\n"
            f"Amount: {txn.get('amount_cents', 0) / 100:.2f} {txn.get('currency', '').upper()}\n"
            f"Email: {txn.get('customer_email')}\n"
            f"Billing country: {txn.get('billing_country')}\n"
            f"Browser locale: {txn.get('browser_locale')}\n"
            f"IP country: {txn.get('ip_country')}\n"
            f"Shipping address: {txn.get('shipping_address')}\n"
            f"Device: {txn.get('device_fingerprint')}\n\n"
            f"Pre-screen alerts:\n{triggers_text}"
        )

        messages = [{"role": "user", "content": initial_message}]

        total_input_tokens = 0
        total_output_tokens = 0
        step_count = 0
        verdict_text = ""

        print(f"\n  [CLAUDE] System prompt: {len(system_prompt)} chars")
        print(f"  [CLAUDE] Initial message: {len(initial_message)} chars")

        for iteration in range(self.max_steps):
            step_count += 1
            print(f"\n  ── Turn {step_count} ──────────────────────────────────────")

            # ── PLAN: Claude decides what to do ────────────────────────────
            try:
                response = self.claude.messages.create(
                    model=self.CLAUDE_MODEL,
                    max_tokens=1024,
                    cache_control={"type": "ephemeral"},
                    system=system_prompt,
                    tools=self.registry.get_schemas(allowed=self.allowed_tools),  # ← loaded from registry.json, tier-filtered
                    messages=messages
                )
            except Exception as e:
                import traceback
                print(f"  [CLAUDE ERROR] {type(e).__name__}: {e}")
                traceback.print_exc()
                raise

            # Track tokens and cache usage
            usage = response.usage
            input_tok = getattr(usage, 'input_tokens', 0)
            output_tok = getattr(usage, 'output_tokens', 0)
            cache_read = getattr(usage, 'cache_read_input_tokens', 0)
            cache_write = getattr(usage, 'cache_creation_input_tokens', 0)
            total_input_tokens += input_tok
            total_output_tokens += output_tok

            print(f"  [TOKENS] Turn {step_count}: input={input_tok} "
                f"(cache read: {cache_read}, cache write: {cache_write}), output={output_tok} "
                f"| Running total: input={total_input_tokens}, output={total_output_tokens}")
            print(f"  [PLAN]   stop_reason={response.stop_reason}")

            # Meter this turn
            if self.meter:
                tool_names = [b.name for b in response.content if hasattr(b, 'type') and b.type == 'tool_use']
                self.meter.record(
                    response=response,
                    provider="claude",
                    customer_id=self._customer_id,
                    event_type="fraud_investigation",
                    session_id=self._session_id,
                    metadata={
                        "transaction_id": txn.get("id", ""),
                        "turn": step_count,
                        "tools_called": tool_names,
                        "cache_read_input_tokens": cache_read,
                        "cache_creation_input_tokens": cache_write,
                    },
                )

            # Log any text reasoning Claude produced
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    print(f"  [PLAN]   reasoning: {block.text.strip()[:300]}")
                    report.steps.append(InvestigationStep(
                        step_number=step_count, phase="PLAN",
                        action=block.text.strip()[:300],
                        result=f"stop_reason={response.stop_reason}"
                    ))

            # ── Check if Claude is done ─────────────────────────────────────
            if response.stop_reason == "end_turn":
                # Extract verdict from final text block
                for block in response.content:
                    if block.type == "text":
                        verdict_text = block.text
                print(f"  [PLAN]   Claude concluded - writing verdict")
                break

            # ── GATHER: Execute ALL tools Claude chose ──────────────────────
            # Claude can return multiple tool_use blocks per turn (parallel calls).
            # We must provide a tool_result for EVERY tool_use id.
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

            if not tool_use_blocks:
                print(f"  [!] No tool_use block found, ending loop")
                # Treat as end_turn — extract any text as verdict
                for block in response.content:
                    if block.type == "text":
                        verdict_text = block.text
                break

            # Convert assistant content blocks to plain dicts BEFORE executing tools
            assistant_content = []
            for block in response.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input
                    })

            messages.append({"role": "assistant", "content": assistant_content})

            # Execute each tool and collect results
            tool_results_content = []
            for tool_block in tool_use_blocks:
                tool_name = tool_block.name
                tool_input = tool_block.input
                tool_use_id = tool_block.id

                print(f"  [GATHER] Tool: {tool_name}")
                print(f"  [GATHER] Params: {json.dumps(tool_input, default=str)}")
                print(f"  [REGISTRY] Dispatching → registry.execute('{tool_name}', ..., tools_instance)")
                params = tool_input

                tool_result = self._dispatch(tool_name, params)

                # ── Hallucinated name: correct it, don't record it ──────────
                # Not a gather: no evidence, no tool_results entry, no
                # GATHER step. The API still requires a tool_result for every
                # tool_use id, so the correction rides back in that block.
                # In a parallel batch this is per-block, so a valid call in
                # the same turn executes normally and is unaffected.
                if tool_result.get("error_class") == "unknown_tool":
                    attempts, terminal = self._note_hallucination(tool_name)
                    message = self._correction_message(tool_name, terminal=terminal)
                    print(f"  [CORRECTION] {message}")
                    report.steps.append(InvestigationStep(
                        step_number=step_count, phase="CORRECTION",
                        action=f"Rejected hallucinated tool name '{tool_name}' (attempt {attempts})",
                        result=message,
                        tool_used=None
                    ))
                    tool_results_content.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": message,
                        "is_error": True,
                    })
                    continue

                if "error" in tool_result:
                    print(f"  [REGISTRY] Error: {tool_result['error']}")
                else:
                    result_keys = [k for k in tool_result.keys() if k != 'tool']
                    print(f"  [REGISTRY] OK, keys: {', '.join(result_keys)}")

                # Full result → React frontend (D3 graph panel needs nodes/edges)
                report.tool_results[tool_name] = tool_result

                # Summarized result → Claude conversation history
                # (strips graph.nodes/edges from check_identity_graph, passthrough for all others)
                llm_result = summarize_for_llm(tool_name, tool_result)
                result_str = json.dumps(llm_result, default=str)

                print(f"  [GATHER] Result: {result_str[:400]}")

                context["tools_called"].append(tool_name)
                context["evidence_gathered"].append({
                    "tool": tool_name, "params": params, "result": tool_result
                })

                report.steps.append(InvestigationStep(
                    step_number=step_count, phase="GATHER",
                    action=f"Called {tool_name} with {json.dumps(params, default=str)}",
                    result=result_str[:500],
                    tool_used=tool_name
                ))

                evidence = self._extract_evidence(tool_name, tool_result)
                if evidence:
                    report.evidence.append(evidence)
                    print(f"  [EVIDENCE] [{evidence.risk_signal}] {evidence.finding}")

                tool_results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": result_str
                })

            # ANALYZE: Send ALL tool results back in a single user message
            print(f"  [ANALYZE] Returning {len(tool_results_content)} tool result(s) to Claude...")
            messages.append({"role": "user", "content": tool_results_content})

        # ── Final token summary ─────────────────────────────────────────────
        cost_input = total_input_tokens * 3 / 1_000_000    # $3/M input
        cost_output = total_output_tokens * 15 / 1_000_000  # $15/M output
        total_cost = cost_input + cost_output

        print(f"\n  [TOKENS] ═══════════════════════════════════════")
        print(f"  [TOKENS] Total input tokens  : {total_input_tokens:,}")
        print(f"  [TOKENS] Total output tokens : {total_output_tokens:,}")
        print(f"  [TOKENS] Cost (Sonnet pricing): ${total_cost:.5f}")
        print(f"  [TOKENS]   input  ${cost_input:.5f} + output ${cost_output:.5f}")
        print(f"  [TOKENS] ═══════════════════════════════════════")

        # ── Parse verdict from Claude's final text ──────────────────────────
        if verdict_text:
            verdict = self._parse_verdict(verdict_text)
        else:
            # Didn't get a clean end_turn — use last text block if any
            verdict = {"risk_level": "MEDIUM", "verdict": "SUSPICIOUS",
                       "confidence": 0.5, "summary": "Investigation ended without clean verdict."}

        report.risk_level = verdict.get("risk_level", "MEDIUM")
        report.verdict = verdict.get("verdict", "SUSPICIOUS")
        report.confidence = verdict.get("confidence", 0.5)
        report.summary = verdict.get("summary", "")

        self._apply_unresolved_caveats(context, report)

        # Store token usage in report metadata
        report.steps.append(InvestigationStep(
            step_number=step_count + 1, phase="VERDICT",
            action=f"Claude verdict (tokens: {total_input_tokens}in/{total_output_tokens}out, cost: ${total_cost:.5f})",
            result=f"{report.verdict} — confidence {report.confidence}"
        ))

    def _llm_plan(self, context, iteration):
        """Ask LLM to plan next investigation step"""
        
        # Build available tools description from registry
        tool_desc = "\n".join([
            f"- {t['name']}: {t['description']} (params: {', '.join(t['input_schema'].get('required', []))})"
            for t in self.registry.get_schemas(allowed=self.allowed_tools)
        ])
        
        # Build evidence summary — keep short to avoid timeouts on later iterations
        evidence_summary = ""
        for e in context["evidence_gathered"]:
            result = e["result"]
            # Summarize key findings instead of dumping full JSON
            tool = e["tool"]
            if tool == "get_email_history":
                evidence_summary += f"\n  {tool}: {result.get('total_transactions', 0)} transactions"
            elif tool == "get_address_history":
                evidence_summary += f"\n  {tool}: {result.get('unique_cards', 0)} cards, {result.get('total_orders', 0)} orders"
            elif tool == "get_card_history":
                evidence_summary += f"\n  {tool}: {result.get('count', 0)} transactions in {result.get('hours', '?')}h"
            elif tool == "check_velocity":
                evidence_summary += f"\n  {tool}: {result.get('transaction_count', 0)} txns in {result.get('time_span_minutes', '?')} min"
            elif tool == "check_locale_consistency":
                mm = result.get('mismatches', [])
                evidence_summary += f"\n  {tool}: {len(mm)} mismatches" + (f" ({'; '.join(mm[:2])})" if mm else "")
            elif tool == "get_ip_reputation":
                evidence_summary += f"\n  {tool}: risk={result.get('risk', '?')} ({result.get('note', '')})"
            elif tool == "get_device_history":
                evidence_summary += f"\n  {tool}: {result.get('unique_cards', 0)} cards, {result.get('unique_emails', 0)} emails"
            else:
                result_str = json.dumps(result, default=str)[:200]
                evidence_summary += f"\n  {tool}: {result_str}"
        
        tools_already_called = ", ".join(context["tools_called"]) if context["tools_called"] else "none"
        
        txn = context["transaction"]
        txn_summary = (
            f"ID: {txn.get('id')}, Card: ****{txn.get('card_last4')}, "
            f"Amount: {txn.get('amount_cents', 0) / 100:.2f} {txn.get('currency')}, "
            f"Email: {txn.get('customer_email')}, "
            f"Billing: {txn.get('billing_country')}, IP: {txn.get('ip_country')}, "
            f"Locale: {txn.get('browser_locale')}, Device: {txn.get('device_fingerprint')}, "
            f"Shipping: {txn.get('shipping_address')}"
        )
        
        triggers_text = "\n".join([
            f"  - [{t['risk']}] {t['rule']}: {t.get('detail', '')}"
            for t in context["pre_screen_triggers"]
        ])
        
        # Build tool-priority guidance for Ollama
        tools_not_called = [
            t['name'] for t in self.registry.get_schemas(allowed=self.allowed_tools)
            if t['name'] not in context["tools_called"]
        ]
        tools_not_called_str = ", ".join(tools_not_called) if tools_not_called else "all tools used"

        # Surface hallucinated names back to the model so it can retry with
        # the right one. Only rendered when something was actually fumbled.
        #
        # KNOWN_ISSUES #12, corrected: an earlier fix here filtered out
        # terminal entries but left context["corrections"] itself as a
        # cumulative, never-pruned list. That meant the FIRST correction for
        # a name — attempt 1, always non-terminal by construction (the cap
        # is 1) — was never filtered and stayed in the list for the rest of
        # the investigation, so "You requested 'check_email_history' — this
        # tool does not exist" kept re-entering the prompt on every
        # subsequent turn regardless of the terminal filter. A live
        # verification run confirmed the literal corrective string present
        # in the prompt at iterations 2 through 8 of an 8-turn investigation
        # — worse than before the "fix" (5 attempts, not 2).
        #
        # The real defect is that corrections were never consumed. Every
        # entry here is shown at most once: build the block from whatever
        # arrived since the last call, filtering out terminal entries (the
        # model already had its one non-terminal correction; re-displaying
        # "already corrected and unavailable" adds nothing and is still the
        # bad string), then clear the channel so nothing lingers into future
        # turns. Accounting (self._hallucination_attempts) and the
        # investigation trace (report.steps, phase="CORRECTION") are
        # unaffected — this list has no other reader.
        corrections = [c for c in context.get("corrections", []) if not c.get("terminal")]
        context["corrections"] = []
        if corrections:
            corrections_text = "\nTOOL NAME CORRECTIONS:\n" + "\n".join(
                f"  - You requested '{c['tool_name']}' — this tool does not exist.\n"
                f"    {c['message']}"
                for c in corrections
            ) + "\n"
        else:
            corrections_text = ""

        if iteration == 0:
            strategy_hint = (
                "STRATEGY: Start by gathering transaction details or checking the email/card history. "
                "A thorough investigation uses at least 4 different tools before concluding."
            )
        elif iteration <= 2:
            strategy_hint = (
                f"STRATEGY: You have only used {len(context['tools_called'])} tool(s) so far. "
                f"Pick a DIFFERENT tool you have NOT used yet. "
                f"Tools still available: {tools_not_called_str}. "
                "Prioritize: check_locale_consistency (geographic mismatches), "
                "check_identity_graph (fraud ring detection), get_ip_reputation (IP risk), "
                "check_velocity (transaction speed patterns)."
            )
        else:
            strategy_hint = (
                f"You have called {len(context['tools_called'])} tools. "
                f"If you have gathered enough evidence to assess risk, use CONCLUDE. "
                f"Otherwise, try one of: {tools_not_called_str}."
            )

        prompt = f"""You are a fraud investigator analyzing a suspicious transaction.
Your job is to use multiple investigation tools to gather evidence, then conclude.

TRANSACTION:
{txn_summary}

PRE-SCREEN ALERTS:
{triggers_text}

TOOLS ALREADY CALLED: {tools_already_called}

EVIDENCE GATHERED SO FAR:{evidence_summary if evidence_summary else " (none yet)"}
{corrections_text}
AVAILABLE TOOLS:
{tool_desc}
- CONCLUDE: End investigation (use when you have enough evidence from at least 4 tools)

{strategy_hint}

RULES:
1. Each iteration, pick ONE tool you have NOT called yet.
2. Never repeat a tool. Never call the same tool with different params.
3. Use at least 4 different tools before CONCLUDE.
4. ITERATION: {iteration + 1} of {self.max_steps_ollama}.

Respond in EXACTLY this format (3 lines, nothing else):
REASONING: <one sentence about why this tool>
TOOL: <tool_name>
PARAMS: <param1=value1, param2=value2>"""

        try:
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 200}
                },
                timeout=90
            )
            
            if response.status_code != 200:
                print(f"  [!] Ollama error: HTTP {response.status_code}")
                return None
            
            result = response.json()
            answer = result.get("response", "").strip()
            print(f"  [PLAN] LLM response: {answer[:200]}")

            # Meter Ollama usage
            if self.meter:
                self.meter.record(
                    response=result,
                    provider="ollama",
                    customer_id=self._customer_id,
                    event_type="fraud_investigation",
                    session_id=self._session_id,
                    metadata={"phase": "plan", "iteration": iteration},
                )
            
            return self._parse_plan(answer, context)
            
        except Exception as e:
            print(f"  [!] LLM error: {e}")
            return None
    
    def _parse_plan(self, answer, context):
        """Parse LLM plan response into tool call"""
        plan = {}
        
        # Extract reasoning
        reasoning_match = re.search(r'REASONING:\s*(.+?)(?=TOOL:|$)', answer, re.DOTALL)
        if reasoning_match:
            plan["reasoning"] = reasoning_match.group(1).strip()
        
        # Extract tool
        tool_match = re.search(r'TOOL:\s*(\S+)', answer)
        if tool_match:
            plan["tool"] = tool_match.group(1).strip()
        else:
            return None
        
        if plan["tool"] == "CONCLUDE":
            return plan
        
        # Always infer params from the transaction record in context.
        # The LLM picks the TOOL; the code supplies correct PARAMS.
        # This avoids truncated emails, comma-broken addresses, and
        # other mangled values from small LLM output windows.
        params = {}
        txn = context["transaction"]
        tool = plan["tool"]
        
        if self.registry.get_tool(tool):
            if tool == "check_locale_consistency":
                params = {"txn_id": txn.get("id")}
            elif tool == "check_velocity":
                params = {"card_last4": txn.get("card_last4"), "hours": 2}
            elif tool == "get_card_history":
                params = {"card_last4": txn.get("card_last4"), "hours": 24}
            elif tool == "get_address_history":
                params = {"address": txn.get("shipping_address")}
            elif tool == "get_email_history":
                params = {"email": txn.get("customer_email")}
            elif tool == "get_ip_reputation":
                params = {"ip_country": txn.get("ip_country")}
            elif tool == "get_device_history":
                params = {"device_fingerprint": txn.get("device_fingerprint")}
            elif tool == "get_transaction_details":
                params = {"txn_id": txn.get("id")}
        else:
            # Unknown tool — try to parse LLM params as fallback
            params_match = re.search(r'PARAMS:\s*(.+)', answer)
            if params_match:
                params_str = params_match.group(1).strip()
                for pair in re.findall(r'(\w+)\s*=\s*([^,]+)', params_str):
                    key = pair[0].strip()
                    value = pair[1].strip().strip('"').strip("'")
                    try:
                        value = int(value)
                    except ValueError:
                        pass
                    params[key] = value
        
        plan["params"] = params
        return plan
    
    def _extract_evidence(self, tool_name, result):
        """Extract an Evidence object from tool results"""
        if "error" in result:
            return Evidence(
                source=tool_name,
                finding=f"Error: {result['error']}",
                risk_signal="LOW",
                details=result
            )
        
        risk = result.get("risk", "LOW")
        
        if tool_name == "check_locale_consistency":
            mismatches = result.get("mismatches", [])
            if mismatches:
                finding = f"{len(mismatches)} locale inconsistencies: {'; '.join(mismatches)}"
                cross_merchants = result.get("cross_merchant_count", 0)
                if cross_merchants >= 2:
                    finding += f" [CROSS-MERCHANT: pattern seen across {cross_merchants} merchants, {result.get('cross_txn_count', 0)} total txns]"
                return Evidence(
                    source=tool_name,
                    finding=finding,
                    risk_signal=risk,
                    details=result
                )
            else:
                return Evidence(
                    source=tool_name,
                    finding="All locale signals are consistent",
                    risk_signal="LOW",
                    details=result
                )
        
        elif tool_name == "check_velocity":
            count = result.get("transaction_count", 0)
            failed = result.get("failed_count", 0)
            span = result.get("time_span_minutes", 0)
            return Evidence(
                source=tool_name,
                finding=f"{count} transactions in {span} minutes ({failed} failed)",
                risk_signal=risk,
                details=result
            )
        
        elif tool_name == "get_address_history":
            cards = result.get("unique_cards", 0)
            if cards > 1:
                return Evidence(
                    source=tool_name,
                    finding=f"Address used by {cards} different cards ({result.get('unique_emails', 0)} emails)",
                    risk_signal="HIGH" if cards >= 3 else "MEDIUM",
                    details=result
                )
            return Evidence(
                source=tool_name,
                finding=f"Address used by 1 card only",
                risk_signal="LOW",
                details=result
            )
        
        elif tool_name == "get_ip_reputation":
            return Evidence(
                source=tool_name,
                finding=result.get("note", ""),
                risk_signal=risk,
                details=result
            )
        
        elif tool_name == "get_device_history":
            cards = result.get("unique_cards", 0)
            if cards > 1:
                return Evidence(
                    source=tool_name,
                    finding=f"Device used with {cards} different cards",
                    risk_signal=risk,
                    details=result
                )
            return Evidence(
                source=tool_name,
                finding="Device associated with single card",
                risk_signal="LOW",
                details=result
            )
        
        elif tool_name == "get_card_history":
            count = result.get("count", 0)
            return Evidence(
                source=tool_name,
                finding=f"{count} transactions found for card in time window",
                risk_signal="HIGH" if count >= 5 else ("MEDIUM" if count >= 3 else "LOW"),
                details=result
            )
        
        elif tool_name == "get_email_history":
            count = result.get("total_transactions", 0)
            return Evidence(
                source=tool_name,
                finding=f"{count} total transactions for this email",
                risk_signal="LOW",
                details=result
            )

        elif tool_name == "check_identity_graph":
            risk_info = result.get("risk", {})
            cluster = result.get("cluster", {})
            label = risk_info.get("label", "LOW")
            fraud_ring = risk_info.get("fraud_ring_detected", False)
            cluster_size = cluster.get("size", 0)
            fraud_neighbors = cluster.get("known_fraud_neighbors", 0)
            signals = risk_info.get("synthetic_identity_signals", [])
            geo = result.get("geographic_profile", {})
            finding = (
                f"Identity graph: {cluster_size} cards in cluster, "
                f"{fraud_neighbors} with fraud verdicts"
                + (", fraud ring detected" if fraud_ring else "")
                + (f", spans {geo['merchant_count']} merchants" if geo.get("merchant_count", 0) >= 2 else "")
                + (f", {geo.get('total_unique_countries', 0)} countries involved" if geo.get("total_unique_countries", 0) >= 2 else "")
                + (f" - {signals[0]}" if signals else "")
            )
            return Evidence(
                source=tool_name,
                finding=finding,
                risk_signal="CRITICAL" if fraud_ring else label,
                details=result
            )

        return None
    
    def _llm_verdict(self, context):
        """Ask LLM to produce final verdict based on all evidence"""
        
        txn = context["transaction"]
        txn_summary = (
            f"Card: ****{txn.get('card_last4')}, "
            f"Amount: {txn.get('amount_cents', 0) / 100:.2f} {txn.get('currency')}, "
            f"Email: {txn.get('customer_email')}, "
            f"Billing: {txn.get('billing_country')}, IP: {txn.get('ip_country')}"
        )
        
        evidence_text = ""
        for e in context["evidence_gathered"]:
            result = e["result"]
            tool = e["tool"]
            # Graph tool has nested risk dict; all others have flat "risk" string
            if tool == "check_identity_graph":
                risk = result.get("risk", {}).get("label", "?")
                cluster = result.get("cluster", {})
                summary_parts = [
                    f"cluster_size: {cluster.get('size', 0)}",
                    f"fraud_neighbors: {cluster.get('known_fraud_neighbors', 0)}",
                    f"fraud_ring: {result.get('risk', {}).get('fraud_ring_detected', False)}",
                ]
            else:
                risk = result.get("risk", "?")
                summary_parts = []
                for key in ["mismatches", "transaction_count", "unique_cards", "note", "count", "failed_count"]:
                    if key in result:
                        summary_parts.append(f"{key}: {result[key]}")
            evidence_text += f"\n  [{risk}] {tool}: {', '.join(summary_parts) if summary_parts else json.dumps(result, default=str)[:200]}"
        
        triggers_text = ", ".join([f"[{t['risk']}] {t['rule']}" for t in context["pre_screen_triggers"]])
        
        prompt = f"""You are a senior fraud analyst. Based on the investigation evidence below, determine your verdict.

TRANSACTION: {txn_summary}

PRE-SCREEN ALERTS: {triggers_text}

EVIDENCE:{evidence_text}

Respond in EXACTLY this format:
RISK_LEVEL: <LOW or MEDIUM or HIGH or CRITICAL>
VERDICT: <LEGITIMATE or SUSPICIOUS or FRAUDULENT>
CONFIDENCE: <0.0 to 1.0>
SUMMARY: <2-3 sentence explanation of your conclusion, referencing specific evidence>"""

        try:
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 200}
                },
                timeout=90
            )
            
            if response.status_code != 200:
                return {"risk_level": "MEDIUM", "verdict": "SUSPICIOUS", "confidence": 0.5,
                        "summary": "LLM verdict unavailable; defaulting to suspicious based on pre-screen alerts."}
            
            result = response.json()
            answer = result.get("response", "").strip()
            print(f"  [VERDICT] LLM: {answer[:300]}")

            # Meter Ollama usage
            if self.meter:
                self.meter.record(
                    response=result,
                    provider="ollama",
                    customer_id=self._customer_id,
                    event_type="fraud_investigation",
                    session_id=self._session_id,
                    metadata={"phase": "verdict"},
                )
            
            return self._parse_verdict(answer)
            
        except Exception as e:
            print(f"  [!] Verdict error: {e}")
            return {"risk_level": "MEDIUM", "verdict": "SUSPICIOUS", "confidence": 0.5,
                    "summary": f"LLM error during verdict: {e}"}
    
    def _parse_verdict(self, answer):
        """Parse LLM verdict response"""
        verdict = {
            "risk_level": "MEDIUM",
            "verdict": "SUSPICIOUS",
            "confidence": 0.5,
            "summary": ""
        }
        
        risk_match = re.search(r'RISK_LEVEL:\s*(LOW|MEDIUM|HIGH|CRITICAL)', answer, re.IGNORECASE)
        if risk_match:
            verdict["risk_level"] = risk_match.group(1).upper()
        
        verdict_match = re.search(r'VERDICT:\s*(LEGITIMATE|SUSPICIOUS|FRAUDULENT)', answer, re.IGNORECASE)
        if verdict_match:
            verdict["verdict"] = verdict_match.group(1).upper()
        
        conf_match = re.search(r'CONFIDENCE:\s*([\d.]+)', answer)
        if conf_match:
            try:
                verdict["confidence"] = min(1.0, max(0.0, float(conf_match.group(1))))
            except:
                pass
        
        summary_match = re.search(r'SUMMARY:\s*(.+)', answer, re.DOTALL)
        if summary_match:
            verdict["summary"] = summary_match.group(1).strip()[:500]
        
        return verdict
    
    def _save_report(self, report):
        """Save investigation report to database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO investigation_reports
                (id, transaction_id, risk_level, verdict, confidence, summary, evidence, steps, tool_results, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                gen_id(),
                report.transaction_id,
                report.risk_level,
                report.verdict,
                report.confidence,
                report.summary,
                json.dumps([asdict(e) if isinstance(e, Evidence) else e for e in report.evidence], default=str),
                json.dumps([asdict(s) if isinstance(s, InvestigationStep) else s for s in report.steps], default=str),
                json.dumps(report.tool_results, default=str) if report.tool_results else None,
                report.created_at
            ))
            
            conn.commit()
            conn.close()
            print(f"  [+] Report saved to database")
        except Exception as e:
            print(f"  [!] Error saving report: {e}")
    
    def _print_report(self, report):
        """Print investigation report to console"""
        risk_icons = {"LOW": "[OK]", "MEDIUM": "[!!]", "HIGH": "[**]", "CRITICAL": "[!!]"}
        verdict_icons = {"LEGITIMATE": "[OK]", "SUSPICIOUS": "[!!]", "FRAUDULENT": "[!!]"}

        print(f"\n{'='*60}")
        print(f"INVESTIGATION REPORT")
        print(f"{'='*60}")
        print(f"Transaction: {report.transaction_id}")
        print(f"Risk Level:  {risk_icons.get(report.risk_level, '?')} {report.risk_level}")
        print(f"Verdict:     {verdict_icons.get(report.verdict, '?')} {report.verdict}")
        print(f"Confidence:  {report.confidence:.0%}")
        print(f"\nSummary: {report.summary}")

        if report.evidence:
            print(f"\nEvidence ({len(report.evidence)} items):")
            for e in report.evidence:
                if isinstance(e, Evidence):
                    signal = {"LOW": "[OK]", "MEDIUM": "[!!]", "HIGH": "[**]", "CRITICAL": "[!!]"}.get(e.risk_signal, "?")
                    print(f"  {signal} [{e.source}] {e.finding}")
        
        print(f"\nInvestigation Steps ({len(report.steps)}):")
        for s in report.steps:
            if isinstance(s, InvestigationStep):
                print(f"  Step {s.step_number} [{s.phase}]: {s.action[:80]}")
        
        print(f"{'='*60}\n")


# ── Helper ──────────────────────────────────────────────────────────────────

def gen_id():
    import uuid
    return str(uuid.uuid4())[:12]


# ── CLI ─────────────────────────────────────────────────────────────────────

def list_flagged_transactions(db_path="payment_lab.db"):
    """List transactions that would be flagged by pre-screen"""
    engine = RuleEngine(db_path)
    tools = InvestigationTools(db_path)
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT id, card_last4, amount_cents, currency, customer_email, ip_country, billing_country FROM transactions ORDER BY created_at DESC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    flagged = []
    for row in rows:
        triggers = engine.pre_screen(row["id"])
        if triggers:
            flagged.append({"transaction": row, "triggers": triggers})
    
    return flagged


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="FAA-Style Fraud Investigation Agent")
    parser.add_argument("--db", default="payment_lab.db", help="Path to SQLite database")
    parser.add_argument("--txn", help="Transaction ID to investigate")
    parser.add_argument("--list", action="store_true", help="List flagged transactions")
    parser.add_argument("--model", default="llama3.2", help="Ollama model name")
    parser.add_argument("--ollama", default="http://localhost:11434", help="Ollama URL")
    parser.add_argument("--registry", default=None, help="Path to registry.json (default: auto-detect)")
    parser.add_argument("--backend", default="auto", choices=["auto", "ollama", "claude"],
                        help="LLM backend: 'ollama' (force local), 'claude' (force API), 'auto' (Claude if key set)")
    parser.add_argument("--max-steps", type=int, default=None,
                        help="Turn ceiling, applied to both backends. "
                             "Default: Claude 6, Ollama 8.")
    parser.add_argument("--investigate-all", action="store_true", help="Investigate all flagged transactions")
    args = parser.parse_args()
    
    if args.list:
        print("\nFlagged Transactions:")
        print("-" * 70)
        flagged = list_flagged_transactions(args.db)
        for f in flagged:
            txn = f["transaction"]
            triggers = f["triggers"]
            trigger_names = [t["rule"] for t in triggers]
            print(f"  {txn['id']} | ****{txn['card_last4']} | {txn['amount_cents'] / 100:.2f} {txn['currency']} | {', '.join(trigger_names)}")
        print(f"\nTotal flagged: {len(flagged)} / total transactions")
    
    elif args.txn:
        # Initialize metering for CLI
        from services.metering import MeteringService
        meter = MeteringService(
            db_path=os.path.join(os.path.dirname(os.path.abspath(args.db)), "metering.db")
        )
        meter.init_db()

        investigator = FraudInvestigator(
            db_path=args.db,
            ollama_url=args.ollama,
            model=args.model,
            registry_path=args.registry,
            backend=args.backend,
            metering_service=meter,
            max_steps=args.max_steps,
        )
        report = investigator.investigate(args.txn)
    
    elif args.investigate_all:
        flagged = list_flagged_transactions(args.db)

        # Initialize metering for CLI
        from services.metering import MeteringService
        meter = MeteringService(
            db_path=os.path.join(os.path.dirname(os.path.abspath(args.db)), "metering.db")
        )
        meter.init_db()

        investigator = FraudInvestigator(
            db_path=args.db,
            ollama_url=args.ollama,
            model=args.model,
            registry_path=args.registry,
            backend=args.backend,
            metering_service=meter,
            max_steps=args.max_steps,
        )
        
        print(f"\nInvestigating {len(flagged)} flagged transactions...\n")
        for f in flagged:
            report = investigator.investigate(f["transaction"]["id"])
    
    else:
        parser.print_help()
