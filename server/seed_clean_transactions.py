"""
seed_clean_transactions.py
Adds legitimate-looking transactions to payment_lab.db that do NOT share
any infrastructure with the Shibuya fraud ring.

This gives the identity graph visual contrast:
  - Dense red-ringed cluster = fraud ring (Shibuya address, shared IPs)
  - Scattered clean nodes = normal customers (unique addresses, unique emails)

Run from the server/ directory:
  python seed_clean_transactions.py
"""

import sqlite3
import random
import string
from datetime import datetime, timedelta

DB_PATH = "payment_lab.db"

# --- Clean customer profiles (no overlap with fraud ring) ---
CLEAN_CUSTOMERS = [
    {
        "card": "5500",
        "email": "sarah.johnson@outlook.com",
        "address": "742 Evergreen Terrace, Springfield IL 62704",
        "ip": "US",
        "device": "fp_clean_sarah_01",
        "name": "Sarah Johnson",
    },
    {
        "card": "4200",
        "email": "m.chen@protonmail.com",
        "address": "88 Queens Road Central, Hong Kong",
        "ip": "HK",
        "device": "fp_clean_chen_02",
        "name": "Michael Chen",
    },
    {
        "card": "3700",
        "email": "lucas.mueller@gmail.com",
        "address": "Friedrichstrasse 43, 10117 Berlin",
        "ip": "DE",
        "device": "fp_clean_lucas_03",
        "name": "Lucas Mueller",
    },
    {
        "card": "6011",
        "email": "priya.sharma@yahoo.com",
        "address": "14 MG Road, Bangalore 560001",
        "ip": "IN",
        "device": "fp_clean_priya_04",
        "name": "Priya Sharma",
    },
    {
        "card": "4900",
        "email": "emma.dubois@gmail.com",
        "address": "27 Rue de Rivoli, 75001 Paris",
        "ip": "FR",
        "device": "fp_clean_emma_05",
        "name": "Emma Dubois",
    },
    {
        "card": "5200",
        "email": "james.wilson@icloud.com",
        "address": "15 Baker Street, London W1U 3BW",
        "ip": "GB",
        "device": "fp_clean_james_06",
        "name": "James Wilson",
    },
    {
        "card": "4100",
        "email": "yuki.tanaka@ezweb.ne.jp",
        "address": "4-5-6 Roppongi, Minato-ku, Tokyo 106-0032",
        "ip": "JP",
        "device": "fp_clean_yuki_07",
        "name": "Yuki Tanaka",
    },
    {
        "card": "3500",
        "email": "carlos.silva@gmail.com",
        "address": "Av Paulista 1000, Sao Paulo 01310-100",
        "ip": "BR",
        "device": "fp_clean_carlos_08",
        "name": "Carlos Silva",
    },
]


def rand_hex(n=8):
    return ''.join(random.choices(string.hexdigits.lower(), k=n))


def seed_clean_transactions():
    conn = sqlite3.connect(DB_PATH)

    # Check current count
    existing = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    print(f"Existing transactions: {existing}")

    rows = []
    now = datetime.utcnow()

    for customer in CLEAN_CUSTOMERS:
        # Each clean customer gets 1-3 transactions over the past month
        num_txns = random.randint(1, 3)
        for i in range(num_txns):
            txn_id = f"clean-{rand_hex(8)}-{rand_hex(4)}"
            days_ago = random.randint(0, 30)
            created = (now - timedelta(days=days_ago, hours=random.randint(0, 23))).isoformat() + "Z"
            amount = random.choice([1999, 2499, 3499, 4999, 7999, 9999, 14999])

            rows.append((
                txn_id,
                customer["card"],
                customer["email"],
                customer["name"],
                customer["address"],
                customer["address"],  # billing = shipping for clean customers
                amount,
                "usd",
                "success",
                customer["ip"],
                customer["ip"],  # browser locale matches
                customer["device"],
                "en-US",
                None,   # fraud_verdict = NULL (clean)
                created,
            ))

    # Determine column list from existing table
    cursor = conn.execute("PRAGMA table_info(transactions)")
    columns = [row[1] for row in cursor.fetchall()]
    print(f"Table columns: {columns}")

    # Insert using explicit column mapping
    # We need to match the actual table schema
    # Common PaymentLab schema:
    #   id, card_last4, customer_email, customer_name, shipping_address,
    #   billing_address, amount_cents, currency, status, ip_country,
    #   billing_country, device_fingerprint, browser_locale, fraud_verdict, created_at

    insert_cols = [
        "id", "card_last4", "customer_email", "customer_name",
        "shipping_address", "billing_address", "amount_cents", "currency",
        "status", "ip_country", "billing_country", "device_fingerprint",
        "browser_locale", "fraud_verdict", "created_at"
    ]

    # Filter to only columns that exist in the table
    valid_cols = [c for c in insert_cols if c in columns]
    valid_indices = [insert_cols.index(c) for c in valid_cols]

    placeholders = ",".join(["?"] * len(valid_cols))
    col_str = ",".join(valid_cols)

    inserted = 0
    for row in rows:
        filtered_row = tuple(row[i] for i in valid_indices)
        try:
            conn.execute(f"INSERT INTO transactions ({col_str}) VALUES ({placeholders})", filtered_row)
            inserted += 1
        except sqlite3.IntegrityError:
            pass  # skip duplicates

    conn.commit()
    new_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    print(f"Inserted {inserted} clean transactions")
    print(f"Total transactions now: {new_count}")

    # Verify the contrast
    fraud_count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE fraud_verdict = 'fraudulent'"
    ).fetchone()[0]
    clean_count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE fraud_verdict IS NULL"
    ).fetchone()[0]
    print(f"\nFraud-labeled: {fraud_count}")
    print(f"Clean (no verdict): {clean_count}")
    print(f"\nThe identity graph should now show:")
    print(f"  - Dense cluster: Shibuya fraud ring ({fraud_count} fraud txns)")
    print(f"  - Scattered nodes: {len(CLEAN_CUSTOMERS)} clean customers")

    conn.close()


if __name__ == "__main__":
    seed_clean_transactions()
