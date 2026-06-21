"""
Metering Service — The Foundation
==================================
Append-only usage ledger backed by SQLite.

Design principles:
  1. APPEND-ONLY — usage_events never update or delete. If there's a billing
     adjustment, add a new record with negative tokens/cost. This is standard
     practice in financial ledgers and makes auditing trivial.
  2. PROVIDER-AGNOSTIC — receives UsageRecord objects, doesn't know or care
     which LLM was called. Provider-specific logic lives in providers/*.py.
  3. SESSION-AWARE — events within one investigation share a session_id,
     so you can query per-turn granularity OR per-investigation totals.
  4. SILENT — never raises exceptions that would break business logic.
     Metering failures are logged and swallowed. A broken meter should
     not stop a fraud investigation.

Usage:
    from services.metering import MeteringService
    from services.providers.claude import extract_claude  # auto-registers

    meter = MeteringService("metering.db")
    meter.init_db()

    # Inside your investigation loop (Option B):
    response = client.messages.create(...)
    meter.record(response, provider="claude", customer_id="merchant_123",
                 event_type="fraud_investigation", session_id=inv_id)
"""

import sqlite3
import json
import uuid
import os
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
from contextlib import contextmanager

from .providers import ProviderRegistry, UsageRecord


class MeteringService:
    """
    Core metering engine.
    Records, queries, and summarises LLM usage across providers.
    """

    def __init__(self, db_path: str = "metering.db"):
        self.db_path = db_path
        self._local = threading.local()  # thread-safe connections
        self._initialized = False

    # ------------------------------------------------------------------
    # Database setup
    # ------------------------------------------------------------------

    @contextmanager
    def _get_conn(self):
        """Thread-safe SQLite connection"""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield self._local.conn
        except Exception:
            self._local.conn.rollback()
            raise

    def init_db(self):
        """Create tables if they don't exist. Safe to call multiple times."""
        with self._get_conn() as conn:
            conn.executescript("""
                -- Raw event log: one row per LLM API call
                -- APPEND-ONLY — never update or delete rows
                CREATE TABLE IF NOT EXISTS usage_events (
                    id                    TEXT PRIMARY KEY,
                    session_id            TEXT,
                    customer_id           TEXT NOT NULL DEFAULT 'demo_merchant',
                    event_type            TEXT NOT NULL DEFAULT 'unknown',
                    provider              TEXT NOT NULL,
                    model                 TEXT NOT NULL,
                    input_tokens          INTEGER NOT NULL DEFAULT 0,
                    output_tokens         INTEGER NOT NULL DEFAULT 0,
                    total_tokens          INTEGER NOT NULL DEFAULT 0,
                    cost_usd              REAL NOT NULL DEFAULT 0.0,
                    tool_calls            INTEGER NOT NULL DEFAULT 0,
                    cache_read_tokens     INTEGER NOT NULL DEFAULT 0,
                    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
                    metadata_json         TEXT DEFAULT '{}',
                    created_at            TEXT NOT NULL
                );

                -- Indexes for common query patterns
                CREATE INDEX IF NOT EXISTS idx_events_customer
                    ON usage_events(customer_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_events_session
                    ON usage_events(session_id);
                CREATE INDEX IF NOT EXISTS idx_events_type
                    ON usage_events(event_type, created_at);

                -- Materialised rollup: one row per customer per billing period
                -- Recalculated on demand for dashboard performance
                CREATE TABLE IF NOT EXISTS usage_summaries (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_id     TEXT NOT NULL,
                    period_start    TEXT NOT NULL,
                    period_end      TEXT NOT NULL,
                    total_events    INTEGER NOT NULL DEFAULT 0,
                    total_sessions  INTEGER NOT NULL DEFAULT 0,
                    total_input_tokens   INTEGER NOT NULL DEFAULT 0,
                    total_output_tokens  INTEGER NOT NULL DEFAULT 0,
                    total_tokens         INTEGER NOT NULL DEFAULT 0,
                    total_cost_usd       REAL NOT NULL DEFAULT 0.0,
                    providers_used  TEXT DEFAULT '[]',
                    last_updated    TEXT NOT NULL,
                    UNIQUE(customer_id, period_start)
                );
            """)
            conn.commit()
        self._initialized = True
        event_count = self._count_events()
        print(f"  [METERING] Database ready: {self.db_path} ({event_count} events)")

    def _count_events(self) -> int:
        """Quick count for boot logging"""
        try:
            with self._get_conn() as conn:
                row = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()
                return row[0]
        except:
            return 0

    # ------------------------------------------------------------------
    # Recording (Option B — called explicitly from business logic)
    # ------------------------------------------------------------------

    def record(
        self,
        response: Any,
        provider: str,
        customer_id: str = "demo_merchant",
        event_type: str = "unknown",
        session_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Optional[UsageRecord]:
        """
        Extract usage from a raw API response and persist it.

        This is the main entry point for Option B metering.
        Call this right after every LLM API call:

            response = client.messages.create(...)
            meter.record(response, provider="claude",
                         customer_id="merchant_123",
                         event_type="fraud_investigation",
                         session_id=investigation_id)

        Args:
            response:    Raw API response object (Anthropic Message, dict, etc.)
            provider:    Provider name ("claude", "ollama", "openai")
            customer_id: Who gets billed
            event_type:  What kind of work ("fraud_investigation", "address_generation")
            session_id:  Groups related calls (e.g. all turns in one investigation)
            metadata:    Extra context to attach (tool names, transaction_id, etc.)

        Returns:
            UsageRecord on success, None on failure (failure is logged, not raised)
        """
        try:
            # Step 1: Extract provider-agnostic usage record
            record = ProviderRegistry.extract(provider, response)

            if not record.is_valid:
                print(f"  [METERING] Warning: Invalid record from {provider} "
                      f"(tokens={record.total_tokens}), skipping")
                return None

            # Step 2: Merge any extra metadata
            if metadata:
                record.raw_metadata.update(metadata)

            # Step 3: Persist to database
            self._write_event(record, customer_id, event_type, session_id)

            # Step 4: Log for debugging (mirrors existing [TOKENS] prints)
            print(f"  [METERING] ✓ {provider}/{record.model}: "
                  f"{record.input_tokens:,} in + {record.output_tokens:,} out "
                  f"= ${record.cost_usd:.4f} "
                  f"[{event_type}, customer={customer_id}]")

            return record

        except Exception as e:
            # CRITICAL: Never break business logic due to metering failure
            print(f"  [METERING] ✗ Recording failed: {e}")
            return None

    def _write_event(
        self,
        record: UsageRecord,
        customer_id: str,
        event_type: str,
        session_id: Optional[str],
    ):
        """Insert a single event into the ledger"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO usage_events
                    (id, session_id, customer_id, event_type, provider, model,
                     input_tokens, output_tokens, total_tokens, cost_usd,
                     tool_calls, cache_read_tokens, cache_creation_tokens,
                     metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.record_id,
                session_id,
                customer_id,
                event_type,
                record.provider,
                record.model,
                record.input_tokens,
                record.output_tokens,
                record.total_tokens,
                record.cost_usd,
                record.raw_metadata.get("tool_calls", 0),
                record.raw_metadata.get("cache_read_tokens", 0),
                record.raw_metadata.get("cache_creation_tokens", 0),
                json.dumps(record.raw_metadata),
                record.recorded_at,
            ))
            conn.commit()

    # ------------------------------------------------------------------
    # Querying — feeds the dashboard and billing system
    # ------------------------------------------------------------------

    def get_usage_by_customer(
        self,
        customer_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> List[Dict]:
        """
        Get all usage events for a customer within a date range.
        
        Args:
            customer_id: Merchant/customer identifier
            start_date:  ISO datetime string (inclusive)
            end_date:    ISO datetime string (exclusive)
            event_type:  Optional filter by event type
            
        Returns:
            List of event dicts, ordered by created_at desc
        """
        query = "SELECT * FROM usage_events WHERE customer_id = ?"
        params: list = [customer_id]

        if start_date:
            query += " AND created_at >= ?"
            params.append(start_date)
        if end_date:
            query += " AND created_at < ?"
            params.append(end_date)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)

        query += " ORDER BY created_at DESC"

        try:
            with self._get_conn() as conn:
                rows = conn.execute(query, params).fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            print(f"  [METERING] Query error: {e}")
            return []

    def get_usage_summary(
        self,
        customer_id: str,
        period_days: int = 30,
    ) -> Dict:
        """
        Get aggregated usage summary for the current billing period.
        This is what the merchant dashboard shows.
        
        Args:
            customer_id: Merchant identifier
            period_days: How many days back to aggregate (default: 30)
            
        Returns:
            Summary dict with totals, averages, and breakdowns
        """
        period_start = (
            datetime.now(timezone.utc) - timedelta(days=period_days)
        ).isoformat()

        try:
            with self._get_conn() as conn:
                # Totals
                totals = conn.execute("""
                    SELECT
                        COUNT(*)                            as total_events,
                        COUNT(DISTINCT session_id)          as total_sessions,
                        COALESCE(SUM(input_tokens), 0)      as total_input_tokens,
                        COALESCE(SUM(output_tokens), 0)     as total_output_tokens,
                        COALESCE(SUM(total_tokens), 0)      as total_tokens,
                        COALESCE(SUM(cost_usd), 0)          as total_cost_usd,
                        COALESCE(SUM(cache_read_tokens), 0)     as total_cache_read_tokens,
                        COALESCE(SUM(cache_creation_tokens), 0) as total_cache_creation_tokens,
                        MIN(created_at)                     as first_event,
                        MAX(created_at)                     as last_event
                    FROM usage_events
                    WHERE customer_id = ? AND created_at >= ?
                """, (customer_id, period_start)).fetchone()

                # Breakdown by event type
                type_breakdown = conn.execute("""
                    SELECT
                        event_type,
                        COUNT(*)                    as events,
                        COUNT(DISTINCT session_id)  as sessions,
                        SUM(total_tokens)           as tokens,
                        SUM(cost_usd)               as cost_usd
                    FROM usage_events
                    WHERE customer_id = ? AND created_at >= ?
                    GROUP BY event_type
                    ORDER BY cost_usd DESC
                """, (customer_id, period_start)).fetchall()

                # Breakdown by provider
                provider_breakdown = conn.execute("""
                    SELECT
                        provider,
                        model,
                        COUNT(*)            as events,
                        SUM(total_tokens)   as tokens,
                        SUM(cost_usd)       as cost_usd
                    FROM usage_events
                    WHERE customer_id = ? AND created_at >= ?
                    GROUP BY provider, model
                    ORDER BY cost_usd DESC
                """, (customer_id, period_start)).fetchall()

                # Per-session averages (cost per investigation)
                session_stats = conn.execute("""
                    SELECT
                        AVG(session_cost)  as avg_session_cost,
                        MIN(session_cost)  as min_session_cost,
                        MAX(session_cost)  as max_session_cost
                    FROM (
                        SELECT session_id, SUM(cost_usd) as session_cost
                        FROM usage_events
                        WHERE customer_id = ? AND created_at >= ?
                              AND session_id IS NOT NULL
                        GROUP BY session_id
                    )
                """, (customer_id, period_start)).fetchone()

                totals_dict = dict(totals)

                # Estimate cache savings: what it would have cost at full input rate
                # minus what was actually charged for cache reads (0.10× input rate).
                # We use Sonnet 4 default rate ($3/M) as a reasonable proxy here;
                # for mixed-model workloads the error is small.
                DEFAULT_INPUT_RATE = 3.00  # $ per 1M tokens
                cache_read = totals_dict.get("total_cache_read_tokens", 0) or 0
                savings = cache_read * DEFAULT_INPUT_RATE * (1.0 - 0.10) / 1_000_000
                totals_dict["estimated_cache_savings_usd"] = round(savings, 4)

                return {
                    "customer_id": customer_id,
                    "period_start": period_start,
                    "period_end": datetime.now(timezone.utc).isoformat(),
                    "period_days": period_days,
                    "totals": totals_dict,
                    "by_event_type": [dict(r) for r in type_breakdown],
                    "by_provider": [dict(r) for r in provider_breakdown],
                    "per_session": {
                        "avg_cost_usd": round(session_stats["avg_session_cost"] or 0, 4),
                        "min_cost_usd": round(session_stats["min_session_cost"] or 0, 4),
                        "max_cost_usd": round(session_stats["max_session_cost"] or 0, 4),
                    },
                }

        except Exception as e:
            print(f"  [METERING] Summary error: {e}")
            return {
                "customer_id": customer_id,
                "error": str(e),
                "totals": {},
            }

    def get_cost_breakdown(
        self,
        customer_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        group_by: str = "day",
    ) -> List[Dict]:
        """
        Cost breakdown over time — for charts and trend analysis.
        
        Args:
            group_by: "day", "hour", or "event_type"
        """
        if group_by == "day":
            date_expr = "DATE(created_at)"
        elif group_by == "hour":
            date_expr = "STRFTIME('%Y-%m-%d %H:00', created_at)"
        else:
            date_expr = "event_type"

        query = f"""
            SELECT
                {date_expr}            as period,
                COUNT(*)               as events,
                SUM(total_tokens)      as tokens,
                SUM(cost_usd)          as cost_usd
            FROM usage_events
            WHERE customer_id = ?
        """
        params: list = [customer_id]

        if start_date:
            query += " AND created_at >= ?"
            params.append(start_date)
        if end_date:
            query += " AND created_at < ?"
            params.append(end_date)

        query += f" GROUP BY {date_expr} ORDER BY period"

        try:
            with self._get_conn() as conn:
                rows = conn.execute(query, params).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            print(f"  [METERING] Breakdown error: {e}")
            return []

    def get_session_detail(self, session_id: str) -> Dict:
        """
        Get all events for a single session (investigation).
        Shows per-turn token usage and cost.
        """
        try:
            with self._get_conn() as conn:
                events = conn.execute("""
                    SELECT * FROM usage_events
                    WHERE session_id = ?
                    ORDER BY created_at ASC
                """, (session_id,)).fetchall()

                if not events:
                    return {"session_id": session_id, "events": [], "totals": {}}

                events_list = [dict(e) for e in events]

                totals = {
                    "turns": len(events_list),
                    "total_input_tokens": sum(e["input_tokens"] for e in events_list),
                    "total_output_tokens": sum(e["output_tokens"] for e in events_list),
                    "total_tokens": sum(e["total_tokens"] for e in events_list),
                    "total_cost_usd": round(sum(e["cost_usd"] for e in events_list), 6),
                    "total_tool_calls": sum(e["tool_calls"] for e in events_list),
                    "duration_seconds": None,
                }

                # Calculate duration if we have timestamps
                if len(events_list) >= 2:
                    try:
                        first = datetime.fromisoformat(events_list[0]["created_at"])
                        last = datetime.fromisoformat(events_list[-1]["created_at"])
                        totals["duration_seconds"] = (last - first).total_seconds()
                    except:
                        pass

                return {
                    "session_id": session_id,
                    "customer_id": events_list[0].get("customer_id"),
                    "event_type": events_list[0].get("event_type"),
                    "events": events_list,
                    "totals": totals,
                }

        except Exception as e:
            print(f"  [METERING] Session detail error: {e}")
            return {"session_id": session_id, "error": str(e)}

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def health(self) -> Dict:
        """System health for /api/metering/health"""
        try:
            with self._get_conn() as conn:
                stats = conn.execute("""
                    SELECT
                        COUNT(*)            as total_events,
                        COUNT(DISTINCT customer_id)  as total_customers,
                        COUNT(DISTINCT session_id)   as total_sessions,
                        COALESCE(SUM(cost_usd), 0)   as lifetime_cost_usd,
                        MAX(created_at)     as last_event_at
                    FROM usage_events
                """).fetchone()

                providers = conn.execute("""
                    SELECT DISTINCT provider FROM usage_events
                """).fetchall()

                db_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0

                return {
                    "status": "healthy",
                    "initialized": self._initialized,
                    "database": self.db_path,
                    "database_size_bytes": db_size,
                    "total_events": stats["total_events"],
                    "total_customers": stats["total_customers"],
                    "total_sessions": stats["total_sessions"],
                    "lifetime_cost_usd": round(stats["lifetime_cost_usd"], 4),
                    "last_event_at": stats["last_event_at"],
                    "registered_providers": ProviderRegistry.list_providers(),
                    "active_providers": [r["provider"] for r in providers],
                }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "initialized": self._initialized,
            }
