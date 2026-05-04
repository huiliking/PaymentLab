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
    """)
    conn.commit()
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
