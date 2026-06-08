"""
Payment Lab - Flask API Server
Handles checkout, payment processing via Stripe, and order management.
"""
import os
from flask import Flask, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from services.metering import MeteringService
from routes.metering import metering_bp, init_metering_routes
load_dotenv()

def create_app():
    app = Flask(__name__,
                static_folder='static',
                static_url_path='')
    CORS(app, origins=["http://localhost:3000", "http://localhost:5173"])
    app.config["STRIPE_SECRET_KEY"] = os.getenv("STRIPE_SECRET_KEY")
    app.config["STRIPE_PUBLISHABLE_KEY"] = os.getenv("STRIPE_PUBLISHABLE_KEY")

    # Register blueprints
    from routes.checkout import checkout_bp
    from routes.products import products_bp
    from routes.config import config_bp
    from routes.fraud import fraud_bp
    app.register_blueprint(checkout_bp, url_prefix="/api")
    app.register_blueprint(products_bp, url_prefix="/api")
    app.register_blueprint(config_bp, url_prefix="/api")
    app.register_blueprint(fraud_bp, url_prefix="/api")

    
    # ============================================================
    # METERING
    # ============================================================
    metering_service = MeteringService(
        db_path=os.path.join(os.path.dirname(__file__), "metering.db")
    )
    metering_service.init_db()
    init_metering_routes(metering_service)
    app.register_blueprint(metering_bp)

    # Make metering_service available to other modules
    app.config['METERING_SERVICE'] = metering_service




    # Initialize database
    from models.database import init_db
    init_db()

    # Catch-all: serve React frontend (must be AFTER all /api blueprints)
    @app.route('/')
    @app.route('/<path:path>')
    def serve_react(path=''):
        static_file = os.path.join(app.static_folder, path)
        if path and os.path.isfile(static_file):
            return send_from_directory(app.static_folder, path)
        return send_from_directory(app.static_folder, 'index.html')

    return app

if __name__ == "__main__":
    app = create_app()
    print("\n" + "=" * 50)
    print("  Payment Lab Server")
    print("  http://localhost:5000")
    print("=" * 50 + "\n")
    app.run(debug=True, port=5000)