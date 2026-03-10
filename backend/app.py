import os
import sys
from datetime import timedelta

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager

# Allow direct execution: python backend/app.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from services.ai_service import get_ai_settings

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="/")

app.config["SECRET_KEY"] = config.SECRET_KEY
app.config["JWT_SECRET_KEY"] = config.JWT_SECRET
app.config["JWT_TOKEN_LOCATION"] = ["headers"]
app.config["JWT_HEADER_NAME"] = "Authorization"
app.config["JWT_HEADER_TYPE"] = "Bearer"
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(days=config.JWT_EXPIRY_DAYS)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_MB * 1024 * 1024

CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=True)

jwt = JWTManager(app)


@jwt.expired_token_loader
def expired_token_callback(jwt_header, jwt_data):
    return jsonify({"error": "Token expired. Please log in again."}), 401


@jwt.invalid_token_loader
def invalid_token_callback(error):
    return jsonify({"error": "Invalid token. Please log in again."}), 401


@jwt.unauthorized_loader
def missing_token_callback(error):
    return jsonify({"error": "Authorization required."}), 401


os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)

from routes.admin import admin_bp
from routes.auth import auth_bp
from routes.chat import chat_bp
from routes.materials import materials_bp
from routes.progress import progress_bp
from routes.tests import tests_bp

app.register_blueprint(auth_bp, url_prefix="/api/auth")
app.register_blueprint(materials_bp, url_prefix="/api/materials")
app.register_blueprint(chat_bp, url_prefix="/api/chat")
app.register_blueprint(tests_bp, url_prefix="/api/tests")
app.register_blueprint(progress_bp, url_prefix="/api/progress")
app.register_blueprint(admin_bp, url_prefix="/api/admin")


@app.route("/api/health", methods=["GET"])
def health():
    ai_settings = get_ai_settings()
    return jsonify(
        {
            "status": "ok",
            "db_host": config.DB_HOST,
            "db_name": config.DB_NAME,
            "gemini_configured": bool(config.GEMINI_API_KEY),
            "openai_configured": bool(config.OPENAI_API_KEY),
            "ai_provider": ai_settings.get("provider"),
            "ai_model": ai_settings.get("model"),
        }
    ), 200


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve(path):
    if path.startswith("api/"):
        return jsonify({"error": "Not found"}), 404

    full_path = os.path.join(FRONTEND_DIR, path)
    if path and os.path.exists(full_path) and os.path.isfile(full_path):
        return send_from_directory(FRONTEND_DIR, path)

    return send_from_directory(FRONTEND_DIR, "index.html")


@app.errorhandler(413)
def too_large(error):
    return jsonify({"error": f"File too large. Maximum size is {config.MAX_UPLOAD_MB}MB"}), 413


@app.errorhandler(500)
def server_error(error):
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    print("=" * 60)
    print("StudyBot server starting")
    print("=" * 60)
    print(f"Frontend : {FRONTEND_DIR}")
    print(f"Uploads  : {config.UPLOAD_FOLDER}")
    print(f"DB       : {config.DB_USER}@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}")
    print(f"Gemini   : {'enabled' if config.GEMINI_API_KEY else 'disabled'}")
    print(f"Student  : http://localhost:{config.PORT}")
    print(f"Admin    : http://localhost:{config.PORT}/admin.html")
    print("=" * 60)

    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)
