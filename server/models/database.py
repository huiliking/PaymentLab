"""
Database models - SQLite for order persistence and audit logging.
Deliberately simple — we'll add complexity as localization needs emerge.
"""

import sqlite3
import os
import json
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "payment_lab.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stripe_payment_intent_id TEXT UNIQUE,
            status TEXT DEFAULT 'pending',
            amount INTEGER,              -- in smallest currency unit (cents, yen, etc.)
            currency TEXT DEFAULT 'usd',
            customer_email TEXT,
            customer_locale TEXT,         -- detected or declared locale (e.g., 'fr-CA')
            billing_country TEXT,
            shipping_country TEXT,
            items_json TEXT,              -- JSON array of cart items
            metadata_json TEXT,           -- extensible metadata (IP, user-agent, etc.)
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER,
            event_type TEXT,             -- 'payment_created', 'locale_mismatch', 'fraud_flag', etc.
            details_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (order_id) REFERENCES orders(id)
        );

        -- Fraud investigation tables
        CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            merchant_id TEXT,
            stripe_payment_intent TEXT,
            card_last4 TEXT,
            card_brand TEXT,
            card_country TEXT,
            amount_cents INTEGER,
            currency TEXT,
            status TEXT,
            customer_email TEXT,
            customer_name TEXT,
            billing_country TEXT,
            billing_postal TEXT,
            shipping_address TEXT,
            browser_locale TEXT,
            ip_country TEXT,
            device_fingerprint TEXT,
            created_at TEXT,
            metadata TEXT
        );

        CREATE TABLE IF NOT EXISTS investigation_reports (
            id TEXT PRIMARY KEY,
            transaction_id TEXT,
            risk_level TEXT,
            verdict TEXT,
            confidence REAL,
            summary TEXT,
            evidence TEXT,
            steps TEXT,
            tool_results TEXT,
            created_at TEXT,
            FOREIGN KEY (transaction_id) REFERENCES transactions(id)
        );

        CREATE INDEX IF NOT EXISTS idx_txn_card ON transactions(card_last4);
        CREATE INDEX IF NOT EXISTS idx_txn_email ON transactions(customer_email);
        CREATE INDEX IF NOT EXISTS idx_txn_status ON transactions(status);
        CREATE INDEX IF NOT EXISTS idx_txn_merchant ON transactions(merchant_id);
    """)
    # Migrate: add columns if missing (existing DBs won't have them)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(investigation_reports)").fetchall()]
    if "tool_results" not in cols:
        conn.execute("ALTER TABLE investigation_reports ADD COLUMN tool_results TEXT")
        conn.commit()
    if "confidence" not in cols:
        conn.execute("ALTER TABLE investigation_reports ADD COLUMN confidence REAL")
        conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    if count == 0:
        conn.close()
        from seed_transactions import seed_database
        seed_database(DB_PATH)
        print("[DB] Auto-seeded transaction data")
    else:
        conn.close()
        print("[DB] Initialized payment_lab.db")


def create_order(payment_intent_id, amount, currency, customer_email,
                 customer_locale=None, billing_country=None, items=None, metadata=None):
    conn = get_db()
    cursor = conn.execute(
        """INSERT INTO orders 
           (stripe_payment_intent_id, amount, currency, customer_email,
            customer_locale, billing_country, items_json, metadata_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (payment_intent_id, amount, currency, customer_email,
         customer_locale, billing_country,
         json.dumps(items or []),
         json.dumps(metadata or {}))
    )
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return order_id


def log_audit_event(order_id, event_type, details=None):
    conn = get_db()
    conn.execute(
        "INSERT INTO audit_log (order_id, event_type, details_json) VALUES (?, ?, ?)",
        (order_id, event_type, json.dumps(details or {}))
    )
    conn.commit()
    conn.close()
