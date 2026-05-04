# Payment Lab
## AI-Powered Payment System Prototype

A learning lab for exploring how AI (LLMs + agents) can fill localization and security
gaps in payment systems. Built incrementally — one piece per week.

## Architecture

```
Client (React)  →  Flask API Server  →  Stripe Test Mode
                   ├── Checkout API
                   ├── Currency Engine
                   ├── Tax Calculator
                   ├── AI Localization Agent
                   └── AI Fraud/Security Agent
```

## Roadmap

### Phase 1: Skeleton (Weeks 1-3)
- [x] Project scaffolding
- [ ] Basic Flask server with Stripe integration
- [ ] React checkout page with Stripe Elements
- [ ] End-to-end: product → checkout → payment → confirmation
- [ ] SQLite for order persistence

### Phase 2: Localization Layer (Weeks 4-8)
- [ ] Multi-currency pricing + display formatting
- [ ] Locale-aware address forms (country-specific fields)
- [ ] Payment method routing by region (iDEAL, PIX, Konbini, etc.)
- [ ] Error message localization
- [ ] AI agent: auto-detect locale gaps in checkout flow

### Phase 3: AI Agents (Weeks 6-12)
- [ ] Localization agent: audit checkout for locale issues
- [ ] Address validation agent: normalize/correct regional formats
- [ ] Fraud signal agent: locale mismatch detection
- [ ] Regulatory agent: tax/consent per jurisdiction

### Phase 4: Security (Weeks 8+)
- [ ] Locale-as-fraud-signal analysis
- [ ] AI-driven transaction risk scoring
- [ ] PCI compliance patterns
- [ ] Rate limiting + abuse detection

## Setup

```bash
# Server
cd server
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env  # Add your Stripe test keys
python app.py

# Client
cd client
npm install
npm start
```

## Stripe Test Cards
- `4242 4242 4242 4242` — Visa (success)
- `4000 0000 0000 0002` — Decline
- `4000 0025 0000 3155` — Requires 3D Secure
- Regional cards available for testing specific markets

## Key Concepts
- **Payment Gateway**: Stripe — handles card tokenization and processing
- **Payment Intent**: Stripe's object representing a payment lifecycle
- **Locale**: Language + region (e.g., fr-CA = French Canada)
- **PCI DSS**: Security standard — we never touch raw card numbers
