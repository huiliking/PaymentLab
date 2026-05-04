"""
Payment Lab - Flask API Server
Handles checkout, payment processing via Stripe, and order management.
"""

import os
from flask import Flask
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

def create_app():
    app = Flask(__name__)
    CORS(app, origins=["http://localhost:3000", "http://localhost:5173"])

    app.config["STRIPE_SECRET_KEY"] = os.getenv("STRIPE_SECRET_KEY")
    app.config["STRIPE_PUBLISHABLE_KEY"] = os.getenv("STRIPE_PUBLISHABLE_KEY")

    # Register blueprints
    from routes.checkout import checkout_bp
    from routes.products import products_bp
    from routes.config import config_bp

    app.register_blueprint(checkout_bp, url_prefix="/api")
    app.register_blueprint(products_bp, url_prefix="/api")
    app.register_blueprint(config_bp, url_prefix="/api")

    # Initialize database
    from models.database import init_db
    init_db()

    return app


if __name__ == "__main__":
    app = create_app()
    print("\n" + "=" * 50)
    print("  Payment Lab Server")
    print("  http://localhost:5000")
    print("=" * 50 + "\n")
    app.run(debug=True, port=5000)
