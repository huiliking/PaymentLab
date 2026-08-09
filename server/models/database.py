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
        conn = get_db()  # reopen — Sprint 5 migration below needs to see the seeded rows
    else:
        print("[DB] Initialized payment_lab.db")

    _migrate_business_layer(conn)
    conn.close()


def _migrate_business_layer(conn):
    """
    Sprint 5: merchants / tiers / tier_tools + transactions.merchant_ref.

    Idempotent — safe to call on every app startup, same as the ALTER
    migrations above. Reconciliation order: expand -> backfill -> verify
    -> constrain. transactions.merchant_id (the original free-text column)
    is left untouched as an audit trail; merchant_ref is the new,
    FK-backed column derived from it.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tiers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS merchants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            registration_id TEXT UNIQUE NOT NULL,
            tier_id INTEGER NOT NULL REFERENCES tiers(id)
        );

        CREATE TABLE IF NOT EXISTS tier_tools (
            tier_id INTEGER NOT NULL REFERENCES tiers(id),
            tool_id INTEGER NOT NULL,
            PRIMARY KEY (tier_id, tool_id)
        );
    """)
    conn.commit()

    # Default tier — every backfilled merchant lands here.
    row = conn.execute("SELECT id FROM tiers WHERE name = 'default'").fetchone()
    if row is None:
        conn.execute("INSERT INTO tiers (name) VALUES ('default')")
        conn.commit()
        row = conn.execute("SELECT id FROM tiers WHERE name = 'default'").fetchone()
    default_tier_id = row[0]

    # Seed the default tier's grants with the tool set that was active at
    # migration time — a one-time snapshot (explicit tier_tools rows are
    # the source of truth), not a live "all active tools" fallback. This
    # preserves pre-Sprint-5 behavior (every investigation used all active
    # tools) for merchants left on 'default'.
    already_seeded = conn.execute(
        "SELECT COUNT(*) FROM tier_tools WHERE tier_id = ?", (default_tier_id,)
    ).fetchone()[0]
    if already_seeded == 0:
        registry_path = os.path.join(
            os.path.dirname(__file__), "..", "ai_agents", "tools", "registry.json"
        )
        if not os.path.exists(registry_path):
            registry_path = os.path.join(os.path.dirname(__file__), "..", "ai_agents", "registry.json")
        try:
            with open(registry_path, "r", encoding="utf-8") as f:
                registry_data = json.load(f)
            for tool in registry_data.get("tools", []):
                if tool.get("status") == "active" and "tool_id" in tool:
                    conn.execute(
                        "INSERT OR IGNORE INTO tier_tools (tier_id, tool_id) VALUES (?, ?)",
                        (default_tier_id, tool["tool_id"]),
                    )
            conn.commit()
        except FileNotFoundError:
            print("[DB] WARNING: registry.json not found — default tier seeded with no tool grants")

    # Backfill: one merchants row per distinct merchant_id string seen in
    # transactions, all assigned to 'default'. Inspection during design
    # found only 3 distinct values, no NULLs — no sentinel merchant needed.
    distinct_merchants = conn.execute(
        "SELECT DISTINCT merchant_id FROM transactions WHERE merchant_id IS NOT NULL"
    ).fetchall()
    for (merchant_id_str,) in distinct_merchants:
        existing = conn.execute(
            "SELECT id FROM merchants WHERE registration_id = ?", (merchant_id_str,)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO merchants (registration_id, tier_id) VALUES (?, ?)",
                (merchant_id_str, default_tier_id),
            )
    conn.commit()

    # Expand: add merchant_ref if missing (plain nullable column first —
    # the FK constraint is added later via table rebuild, see below).
    cols = [c[1] for c in conn.execute("PRAGMA table_info(transactions)").fetchall()]
    if "merchant_ref" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN merchant_ref INTEGER")
        conn.commit()

    # Backfill merchant_ref for any row not yet populated.
    conn.execute("""
        UPDATE transactions
        SET merchant_ref = (SELECT id FROM merchants WHERE merchants.registration_id = transactions.merchant_id)
        WHERE merchant_ref IS NULL AND merchant_id IS NOT NULL
    """)
    conn.commit()

    # Verify (pure reads, both must pass before constraining).
    orphan_count = conn.execute("""
        SELECT COUNT(*) FROM transactions t
        LEFT JOIN merchants m ON t.merchant_ref = m.id
        WHERE t.merchant_id IS NOT NULL AND m.id IS NULL
    """).fetchone()[0]
    if orphan_count:
        raise RuntimeError(f"[DB] Sprint 5 migration: {orphan_count} transaction(s) failed to resolve a merchant")

    dup_count = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT registration_id FROM merchants GROUP BY registration_id HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    if dup_count:
        raise RuntimeError(f"[DB] Sprint 5 migration: {dup_count} duplicate merchant registration_id(s)")

    # Constrain: SQLite can't ALTER a populated table to add a FOREIGN KEY
    # constraint — only a full rebuild does. Guarded so it runs once.
    fk_already_present = any(
        fk[3] == "merchant_ref" for fk in conn.execute("PRAGMA foreign_key_list(transactions)").fetchall()
    )
    if not fk_already_present:
        _rebuild_transactions_with_merchant_fk(conn)


def _rebuild_transactions_with_merchant_fk(conn):
    """
    12-step rebuild: create a constrained copy of transactions, copy rows,
    drop the original, rename the copy into place. This is the only way
    SQLite can add a FOREIGN KEY to an already-populated table — ALTER
    TABLE ADD COLUMN can't attach an enforced constraint after the fact.
    Called once; guarded by the caller's PRAGMA foreign_key_list check.
    """
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript("""
        CREATE TABLE transactions_new (
            id TEXT PRIMARY KEY,
            merchant_id TEXT,
            merchant_ref INTEGER REFERENCES merchants(id),
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

        INSERT INTO transactions_new (
            id, merchant_id, merchant_ref, stripe_payment_intent, card_last4, card_brand,
            card_country, amount_cents, currency, status, customer_email, customer_name,
            billing_country, billing_postal, shipping_address, browser_locale, ip_country,
            device_fingerprint, created_at, metadata
        )
        SELECT
            id, merchant_id, merchant_ref, stripe_payment_intent, card_last4, card_brand,
            card_country, amount_cents, currency, status, customer_email, customer_name,
            billing_country, billing_postal, shipping_address, browser_locale, ip_country,
            device_fingerprint, created_at, metadata
        FROM transactions;

        DROP TABLE transactions;
        ALTER TABLE transactions_new RENAME TO transactions;

        CREATE INDEX IF NOT EXISTS idx_txn_card ON transactions(card_last4);
        CREATE INDEX IF NOT EXISTS idx_txn_email ON transactions(customer_email);
        CREATE INDEX IF NOT EXISTS idx_txn_status ON transactions(status);
        CREATE INDEX IF NOT EXISTS idx_txn_merchant ON transactions(merchant_id);
        CREATE INDEX IF NOT EXISTS idx_txn_merchant_ref ON transactions(merchant_ref);
    """)
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    print("[DB] Rebuilt transactions table with merchant_ref FK constraint")


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
