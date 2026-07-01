"""
seed_transactions.py
====================
Populates payment_lab.db with synthetic transaction history
including realistic fraud patterns for the investigation agent.

Fraud patterns seeded:
1. VELOCITY BURST    — 8 transactions in 2 hours from same card
2. GEO MISMATCH      — browser locale ja-JP but billing country US
3. CARD TESTING       — multiple small ($1-5) charges across merchants  
4. ADDRESS REUSE      — same shipping address, 4 different cards
5. CURRENCY HOPPING   — same card used in USD, EUR, JPY within 1 hour

Run: python seed_transactions.py [--db path/to/payment_lab.db]
"""

import sqlite3
import random
import uuid
import json
from datetime import datetime, timedelta
import argparse

# ── Schema ──────────────────────────────────────────────────────────────────

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    merchant_id TEXT,
    stripe_payment_intent TEXT,
    card_last4 TEXT,
    card_brand TEXT,
    card_country TEXT,
    amount_cents INTEGER,
    currency TEXT,
    status TEXT,  -- succeeded, failed, refunded
    customer_email TEXT,
    customer_name TEXT,
    billing_country TEXT,
    billing_postal TEXT,
    shipping_address TEXT,
    browser_locale TEXT,
    ip_country TEXT,
    device_fingerprint TEXT,
    created_at TEXT,
    metadata TEXT  -- JSON blob for extra signals
);

CREATE TABLE IF NOT EXISTS investigation_reports (
    id TEXT PRIMARY KEY,
    transaction_id TEXT,
    risk_level TEXT,  -- LOW, MEDIUM, HIGH, CRITICAL
    verdict TEXT,     -- LEGITIMATE, SUSPICIOUS, FRAUDULENT
    summary TEXT,
    evidence TEXT,    -- JSON array of evidence items
    steps TEXT,       -- JSON array of investigation steps
    created_at TEXT,
    FOREIGN KEY (transaction_id) REFERENCES transactions(id)
);

CREATE INDEX IF NOT EXISTS idx_txn_card ON transactions(card_last4);
CREATE INDEX IF NOT EXISTS idx_txn_email ON transactions(customer_email);
CREATE INDEX IF NOT EXISTS idx_txn_shipping ON transactions(shipping_address);
CREATE INDEX IF NOT EXISTS idx_txn_created ON transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_txn_ip ON transactions(ip_country);
CREATE INDEX IF NOT EXISTS idx_txn_merchant ON transactions(merchant_id);
"""

# ── Realistic data pools ────────────────────────────────────────────────────

NAMES = [
    ("Alice Chen", "alice.chen@gmail.com"),
    ("Bob Smith", "bob.smith@outlook.com"),
    ("Carlos Mendez", "carlos.m@yahoo.com"),
    ("Diana Petrova", "d.petrova@mail.ru"),
    ("Emi Tanaka", "emi.tanaka@docomo.ne.jp"),
    ("François Dupont", "f.dupont@orange.fr"),
    ("Greta Müller", "greta.m@web.de"),
    ("Hiro Nakamura", "hiro.n@softbank.jp"),
    ("Ingrid Larsson", "ingrid.l@telia.se"),
    ("José García", "jose.g@telefonica.es"),
    ("Kenji Yamada", "kenji.y@icloud.com"),
    ("Laura Rossi", "laura.r@libero.it"),
    ("Mohammed Al-Rashid", "m.alrashid@gmail.com"),
    ("Nina Kowalski", "nina.k@wp.pl"),
    ("Oscar Fernandez", "oscar.f@gmail.com"),
]

ADDRESSES = [
    "123 Main St, San Francisco, CA 94105",
    "456 Oak Ave, New York, NY 10001",
    "789 Elm Dr, Austin, TX 78701",
    "321 Pine Rd, Seattle, WA 98101",
    "654 Maple Ln, Chicago, IL 60601",
    "1-2-3 Shibuya, Tokyo 150-0002",
    "45 Rue de Rivoli, Paris 75001",
    "10 Kurfürstendamm, Berlin 10719",
    "88 Queen St, Toronto ON M5H 2N2",
    "Unit 5, 200 George St, Sydney NSW 2000",
]

PRODUCTS = [
    ("Pro Plan Monthly", 2999),
    ("Pro Plan Annual", 29999),
    ("Starter Plan", 999),
    ("Enterprise Seat", 4999),
    ("API Credits Pack", 1999),
    ("Widget Bundle", 4500),
    ("Premium Support", 9999),
    ("Data Export Add-on", 799),
]

CARD_BRANDS = ["visa", "mastercard", "amex"]
CURRENCIES = ["usd", "eur", "gbp", "cad", "jpy"]
LOCALES = ["en-US", "en-CA", "fr-FR", "de-DE", "ja-JP", "es-ES", "it-IT", "ko-KR", "zh-CN", "pt-BR"]
COUNTRIES = ["US", "CA", "FR", "DE", "JP", "ES", "IT", "KR", "CN", "BR", "GB", "AU"]


def gen_id():
    return str(uuid.uuid4())[:12]

def gen_card():
    return str(random.randint(1000, 9999))

def gen_device():
    return f"fp_{uuid.uuid4().hex[:16]}"

def random_time(base, hours_range=720):
    """Random time within hours_range hours before base"""
    delta = timedelta(hours=random.randint(1, hours_range))
    return base - delta


# ── Legitimate transaction generator ────────────────────────────────────────

def generate_legitimate_transactions(n=80):
    """Generate n normal-looking transactions spread over 30 days"""
    txns = []
    now = datetime.now()

    for _ in range(n):
        name, email = random.choice(NAMES)
        product, amount = random.choice(PRODUCTS)
        country = random.choice(["US", "CA", "GB", "DE", "FR", "JP"])
        
        # Consistent locale/country pairing
        locale_map = {"US": "en-US", "CA": "en-CA", "GB": "en-GB", "DE": "de-DE", "FR": "fr-FR", "JP": "ja-JP"}
        locale = locale_map.get(country, "en-US")
        
        currency_map = {"US": "usd", "CA": "cad", "GB": "gbp", "DE": "eur", "FR": "eur", "JP": "jpy"}
        currency = currency_map.get(country, "usd")
        
        if currency == "jpy":
            amount = amount  # already in smallest unit (yen)
        
        txn = {
            "id": gen_id(),
            "merchant_id": random.choice(["merchant_alpha", "merchant_beta", "merchant_gamma"]),
            "stripe_payment_intent": f"pi_{uuid.uuid4().hex[:24]}",
            "card_last4": gen_card(),
            "card_brand": random.choice(CARD_BRANDS),
            "card_country": country,
            "amount_cents": amount,
            "currency": currency,
            "status": "succeeded",
            "customer_email": email,
            "customer_name": name,
            "billing_country": country,
            "billing_postal": f"{random.randint(10000, 99999)}",
            "shipping_address": random.choice(ADDRESSES[:5]) if country == "US" else random.choice(ADDRESSES[5:]),
            "browser_locale": locale,
            "ip_country": country,
            "device_fingerprint": gen_device(),
            "created_at": random_time(now, 720).isoformat() + "Z",
            "metadata": json.dumps({"product": product, "fraud_label": "legitimate"})
        }
        txns.append(txn)
    
    return txns


# ── Fraud pattern generators ────────────────────────────────────────────────

def generate_velocity_burst():
    """Pattern 1: 8 transactions in 2 hours from same card"""
    txns = []
    now = datetime.now()
    base_time = now - timedelta(hours=random.randint(2, 48))
    card = gen_card()
    device = gen_device()
    
    for i in range(8):
        product, amount = random.choice(PRODUCTS)
        txn = {
            "id": gen_id(),
            "merchant_id": "merchant_alpha",
            "stripe_payment_intent": f"pi_{uuid.uuid4().hex[:24]}",
            "card_last4": card,
            "card_brand": "visa",
            "card_country": "US",
            "amount_cents": amount,
            "currency": "usd",
            "status": "succeeded" if i < 6 else random.choice(["succeeded", "failed"]),
            "customer_email": "quick.buyer@tempmail.org",
            "customer_name": "Quick Buyer",
            "billing_country": "US",
            "billing_postal": "90210",
            "shipping_address": "123 Main St, San Francisco, CA 94105",
            "browser_locale": "en-US",
            "ip_country": "US",
            "device_fingerprint": device,
            "created_at": (base_time + timedelta(minutes=random.randint(0, 120))).isoformat() + "Z",
            "metadata": json.dumps({"product": product, "fraud_label": "velocity_burst", "pattern_id": "VB-001"})
        }
        txns.append(txn)
    
    return txns


def generate_geo_mismatch():
    """Pattern 2: Browser locale ja-JP but billing/card country US"""
    txns = []
    now = datetime.now()
    card = gen_card()
    
    for i in range(3):
        product, amount = random.choice(PRODUCTS)
        txn = {
            "id": gen_id(),
            "merchant_id": "merchant_alpha",
            "stripe_payment_intent": f"pi_{uuid.uuid4().hex[:24]}",
            "card_last4": card,
            "card_brand": "mastercard",
            "card_country": "US",
            "amount_cents": amount,
            "currency": "usd",
            "status": "succeeded",
            "customer_email": "tanaka.test@gmail.com",
            "customer_name": "John Tanaka",
            "billing_country": "US",
            "billing_postal": "10001",
            "shipping_address": "1-2-3 Shibuya, Tokyo 150-0002",
            "browser_locale": "ja-JP",
            "ip_country": "JP",
            "device_fingerprint": gen_device(),
            "created_at": random_time(now, 72).isoformat() + "Z",
            "metadata": json.dumps({"product": product, "fraud_label": "geo_mismatch", "pattern_id": "GM-001"})
        }
        txns.append(txn)
    
    return txns


def generate_card_testing():
    """Pattern 3: Multiple small charges ($1-5) testing stolen card"""
    txns = []
    now = datetime.now()
    base_time = now - timedelta(hours=random.randint(1, 24))
    card = gen_card()
    
    for i in range(5):
        txn = {
            "id": gen_id(),
            "merchant_id": "merchant_alpha",
            "stripe_payment_intent": f"pi_{uuid.uuid4().hex[:24]}",
            "card_last4": card,
            "card_brand": "visa",
            "card_country": "CA",
            "amount_cents": random.randint(100, 500),  # $1-5
            "currency": "usd",
            "status": "succeeded" if i < 3 else "failed",
            "customer_email": f"user{random.randint(100,999)}@tempmail.org",
            "customer_name": f"Test User {i+1}",
            "billing_country": "CA",
            "billing_postal": "M5H 2N2",
            "shipping_address": "N/A",
            "browser_locale": "en-US",
            "ip_country": random.choice(["NG", "RO", "PH"]),  # suspicious IP origins
            "device_fingerprint": gen_device(),  # different device each time
            "created_at": (base_time + timedelta(minutes=i * 3)).isoformat() + "Z",
            "metadata": json.dumps({"product": "Starter Plan", "fraud_label": "card_testing", "pattern_id": "CT-001"})
        }
        txns.append(txn)
    
    return txns


def generate_address_reuse():
    """Pattern 4: Same shipping address used with 4 different cards"""
    txns = []
    now = datetime.now()
    shared_address = "Unit 7B, 999 Suspicious Lane, Miami FL 33101"
    
    for i in range(4):
        product, amount = random.choice(PRODUCTS)
        txn = {
            "id": gen_id(),
            "merchant_id": "merchant_alpha",
            "stripe_payment_intent": f"pi_{uuid.uuid4().hex[:24]}",
            "card_last4": gen_card(),  # different card each time
            "card_brand": random.choice(CARD_BRANDS),
            "card_country": "US",
            "amount_cents": amount,
            "currency": "usd",
            "status": "succeeded",
            "customer_email": f"buyer{i+1}@freemail.com",
            "customer_name": f"Buyer {chr(65+i)}",
            "billing_country": "US",
            "billing_postal": "33101",
            "shipping_address": shared_address,
            "browser_locale": "en-US",
            "ip_country": "US",
            "device_fingerprint": gen_device(),
            "created_at": random_time(now, 168).isoformat() + "Z",
            "metadata": json.dumps({"product": product, "fraud_label": "address_reuse", "pattern_id": "AR-001"})
        }
        txns.append(txn)
    
    return txns


def generate_currency_hopping():
    """Pattern 5: Same card used in USD, EUR, JPY within 1 hour"""
    txns = []
    now = datetime.now()
    base_time = now - timedelta(hours=random.randint(3, 36))
    card = gen_card()
    
    for i, (currency, country, locale) in enumerate([
        ("usd", "US", "en-US"),
        ("eur", "DE", "de-DE"),
        ("jpy", "JP", "ja-JP"),
    ]):
        product, amount = random.choice(PRODUCTS)
        txn = {
            "id": gen_id(),
            "merchant_id": "merchant_alpha",
            "stripe_payment_intent": f"pi_{uuid.uuid4().hex[:24]}",
            "card_last4": card,
            "card_brand": "visa",
            "card_country": "US",  # card is US-issued but used globally
            "amount_cents": amount,
            "currency": currency,
            "status": "succeeded",
            "customer_email": "globe.trotter@gmail.com",
            "customer_name": "Globe Trotter",
            "billing_country": country,
            "billing_postal": "10001",
            "shipping_address": random.choice(ADDRESSES),
            "browser_locale": locale,
            "ip_country": country,
            "device_fingerprint": gen_device(),
            "created_at": (base_time + timedelta(minutes=i * 18)).isoformat() + "Z",
            "metadata": json.dumps({"product": product, "fraud_label": "currency_hopping", "pattern_id": "CH-001"})
        }
        txns.append(txn)
    
    return txns


# ── Cross-merchant fraud patterns ──────────────────────────────────────────

def generate_cross_merchant_drop_address():
    """Pattern 6: Same drop address used across multiple merchants, cards from different countries"""
    txns = []
    now = datetime.now()
    drop_address = "Unit 12, 555 Drop Lane, Houston TX 77001"
    merchants = ["merchant_alpha", "merchant_beta", "merchant_gamma"]
    card_origins = [
        ("PH", "en-US"),
        ("NG", "en-US"),
        ("RO", "en-US"),
        ("US", "en-US"),
        ("PH", "en-US"),
        ("NG", "en-US"),
    ]

    for i, (card_country, locale) in enumerate(card_origins):
        product, amount = random.choice(PRODUCTS)
        txn = {
            "id": gen_id(),
            "merchant_id": merchants[i % len(merchants)],
            "stripe_payment_intent": f"pi_{uuid.uuid4().hex[:24]}",
            "card_last4": gen_card(),
            "card_brand": random.choice(CARD_BRANDS),
            "card_country": card_country,
            "amount_cents": amount,
            "currency": "usd",
            "status": "succeeded",
            "customer_email": f"drop.recv{i+1}@freemail.com",
            "customer_name": f"Receiver {chr(65+i)}",
            "billing_country": "US",
            "billing_postal": "77001",
            "shipping_address": drop_address,
            "browser_locale": locale,
            "ip_country": card_country,
            "device_fingerprint": gen_device(),
            "created_at": random_time(now, 168).isoformat() + "Z",
            "metadata": json.dumps({"product": product, "fraud_label": "cross_merchant_drop", "pattern_id": "XD-001"})
        }
        txns.append(txn)

    return txns


def generate_cross_merchant_card_testing():
    """Pattern 7: Same card tested across 3 merchants with escalating amounts"""
    txns = []
    now = datetime.now()
    base_time = now - timedelta(hours=random.randint(2, 24))
    card = gen_card()
    amounts_by_merchant = [
        ("merchant_alpha", [100, 200, 500]),
        ("merchant_beta", [1500, 2500]),
        ("merchant_gamma", [9900]),
    ]

    seq = 0
    for merchant, amounts in amounts_by_merchant:
        for amount in amounts:
            txn = {
                "id": gen_id(),
                "merchant_id": merchant,
                "stripe_payment_intent": f"pi_{uuid.uuid4().hex[:24]}",
                "card_last4": card,
                "card_brand": "visa",
                "card_country": "US",
                "amount_cents": amount,
                "currency": "usd",
                "status": "succeeded" if seq < 4 else "failed",
                "customer_email": f"tester.{merchant}@tempmail.org",
                "customer_name": f"Card Tester {seq+1}",
                "billing_country": "US",
                "billing_postal": "10001",
                "shipping_address": "N/A",
                "browser_locale": "en-US",
                "ip_country": random.choice(["RO", "PH"]),
                "device_fingerprint": gen_device(),
                "created_at": (base_time + timedelta(minutes=seq * 8)).isoformat() + "Z",
                "metadata": json.dumps({"product": "Starter Plan", "fraud_label": "cross_merchant_card_test", "pattern_id": "XT-001"})
            }
            txns.append(txn)
            seq += 1

    return txns


def generate_cross_border_reshipping_ring():
    """Pattern 8: International fraud ring -- cards from PH/NG/RO, billing US, shipping to drop points across merchants"""
    txns = []
    now = datetime.now()
    shared_device = gen_device()
    drop_points = [
        "Apt 4C, 321 Reship Ave, Newark NJ 07102",
        "Unit 9, 888 Forward Blvd, Los Angeles CA 90015",
    ]
    ring_configs = [
        {"merchant": "merchant_alpha", "card_country": "PH", "ip": "PH", "drop": 0},
        {"merchant": "merchant_alpha", "card_country": "NG", "ip": "NG", "drop": 0},
        {"merchant": "merchant_beta",  "card_country": "RO", "ip": "RO", "drop": 1},
        {"merchant": "merchant_beta",  "card_country": "PH", "ip": "PH", "drop": 1},
        {"merchant": "merchant_gamma", "card_country": "NG", "ip": "NG", "drop": 0},
        {"merchant": "merchant_gamma", "card_country": "RO", "ip": "RO", "drop": 1},
        {"merchant": "merchant_alpha", "card_country": "PH", "ip": "PH", "drop": 0},
        {"merchant": "merchant_beta",  "card_country": "NG", "ip": "NG", "drop": 1},
    ]

    for i, cfg in enumerate(ring_configs):
        product, amount = random.choice(PRODUCTS)
        txn = {
            "id": gen_id(),
            "merchant_id": cfg["merchant"],
            "stripe_payment_intent": f"pi_{uuid.uuid4().hex[:24]}",
            "card_last4": gen_card(),
            "card_brand": random.choice(CARD_BRANDS),
            "card_country": cfg["card_country"],
            "amount_cents": amount,
            "currency": "usd",
            "status": "succeeded",
            "customer_email": f"ring.member{i+1}@freemail.com",
            "customer_name": f"Ring Member {i+1}",
            "billing_country": "US",
            "billing_postal": f"{random.randint(10000, 99999)}",
            "shipping_address": drop_points[cfg["drop"]],
            "browser_locale": "en-US",
            "ip_country": cfg["ip"],
            "device_fingerprint": shared_device if i % 3 == 0 else gen_device(),
            "created_at": random_time(now, 72).isoformat() + "Z",
            "metadata": json.dumps({"product": product, "fraud_label": "cross_border_reshipping", "pattern_id": "XR-001"})
        }
        txns.append(txn)

    return txns


# ── Database writer ─────────────────────────────────────────────────────────

def seed_database(db_path="payment_lab.db"):
    """Create tables and insert all transactions"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Create tables
    cursor.executescript(CREATE_TABLES)
    
    # Clear existing synthetic data (keep real Stripe transactions if any)
    cursor.execute("DELETE FROM transactions WHERE metadata LIKE '%fraud_label%'")
    
    # Generate all transactions
    all_txns = []
    
    print("Generating legitimate transactions...")
    all_txns.extend(generate_legitimate_transactions(80))
    
    print("Generating fraud pattern: Velocity Burst...")
    all_txns.extend(generate_velocity_burst())
    
    print("Generating fraud pattern: Geo Mismatch...")
    all_txns.extend(generate_geo_mismatch())
    
    print("Generating fraud pattern: Card Testing...")
    all_txns.extend(generate_card_testing())
    
    print("Generating fraud pattern: Address Reuse...")
    all_txns.extend(generate_address_reuse())
    
    print("Generating fraud pattern: Currency Hopping...")
    all_txns.extend(generate_currency_hopping())

    print("Generating fraud pattern: Cross-Merchant Drop Address...")
    all_txns.extend(generate_cross_merchant_drop_address())

    print("Generating fraud pattern: Cross-Merchant Card Testing...")
    all_txns.extend(generate_cross_merchant_card_testing())

    print("Generating fraud pattern: Cross-Border Reshipping Ring...")
    all_txns.extend(generate_cross_border_reshipping_ring())

    # Insert
    for txn in all_txns:
        cursor.execute("""
            INSERT OR REPLACE INTO transactions
            (id, merchant_id, stripe_payment_intent, card_last4, card_brand, card_country,
             amount_cents, currency, status, customer_email, customer_name,
             billing_country, billing_postal, shipping_address, browser_locale,
             ip_country, device_fingerprint, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            txn["id"], txn["merchant_id"], txn["stripe_payment_intent"], txn["card_last4"],
            txn["card_brand"], txn["card_country"], txn["amount_cents"],
            txn["currency"], txn["status"], txn["customer_email"],
            txn["customer_name"], txn["billing_country"], txn["billing_postal"],
            txn["shipping_address"], txn["browser_locale"], txn["ip_country"],
            txn["device_fingerprint"], txn["created_at"], txn["metadata"]
        ))
    
    conn.commit()
    
    # Summary
    cursor.execute("SELECT COUNT(*) FROM transactions")
    total = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM transactions WHERE metadata LIKE '%legitimate%'")
    legit = cursor.fetchone()[0]
    
    fraud = total - legit
    
    print(f"\n{'='*50}")
    print(f"Database seeded: {db_path}")
    print(f"  Total transactions: {total}")
    print(f"  Legitimate: {legit}")
    print(f"  Fraud patterns: {fraud}")
    print(f"    - Velocity Burst: 8 txns")
    print(f"    - Geo Mismatch: 3 txns")
    print(f"    - Card Testing: 5 txns")
    print(f"    - Address Reuse: 4 txns")
    print(f"    - Currency Hopping: 3 txns")
    print(f"    - Cross-Merchant Drop Address: 6 txns")
    print(f"    - Cross-Merchant Card Testing: 6 txns")
    print(f"    - Cross-Border Reshipping Ring: 8 txns")
    print(f"{'='*50}")
    
    conn.close()
    return all_txns


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed transaction database for fraud investigation")
    parser.add_argument("--db", default="payment_lab.db", help="Path to SQLite database")
    args = parser.parse_args()
    
    seed_database(args.db)
