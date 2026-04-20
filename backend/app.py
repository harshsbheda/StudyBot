import os
import sys
from datetime import timedelta

import bcrypt
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager

# Allow direct execution: python backend/app.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from services.ai_service import get_ai_settings
from database.db import get_db, get_next_id, ensure_user_progress, utcnow

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

# Build CORS origins list - always include production domains
cors_origins = [
    # Development
    "http://localhost:3000",
    "http://localhost:5000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5000",
    "http://127.0.0.1:5173",
    # Production (always included)
    "https://study-bot-new.vercel.app",
    "https://studybot-production-f344.up.railway.app",
    "https://*.vercel.app",
]

# Add environment variable origins if specified
if config.CORS_ALLOWED_ORIGINS:
    cors_origins.extend(config.CORS_ALLOWED_ORIGINS)

# Add Google allowed origins if configured
if config.GOOGLE_ALLOWED_ORIGINS:
    cors_origins.extend(config.GOOGLE_ALLOWED_ORIGINS)

# Remove duplicates while preserving order
cors_origins = list(dict.fromkeys(cors_origins))

# Configure CORS
CORS(app, 
     resources={r"/api/*": {
         "origins": cors_origins,
         "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
         "allow_headers": ["Content-Type", "Authorization"],
         "expose_headers": ["Content-Type", "Authorization"],
         "supports_credentials": True,
         "max_age": 3600
     }})

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


def _validate_runtime_config() -> None:
    if config.DEBUG:
        return

    insecure_secret_values = {
        "",
        "change-this-secret-key",
        "change-this-jwt-secret",
        "change-me-to-a-long-random-secret",
        "change-me-to-a-different-long-random-secret",
        "studybot-xyz-2024-abc",
        "studybot-super-secret-jwt-key-2024-xyz987",
    }

    missing = []
    if config.SECRET_KEY.strip() in insecure_secret_values:
        missing.append("SECRET_KEY")
    if config.JWT_SECRET.strip() in insecure_secret_values:
        missing.append("JWT_SECRET")

    if missing:
        raise RuntimeError(
            "Set secure values for {} before running with DEBUG=false.".format(", ".join(missing))
        )


def _ensure_bootstrap_admin() -> None:
    email = config.BOOTSTRAP_ADMIN_EMAIL
    password = config.BOOTSTRAP_ADMIN_PASSWORD

    if not email and not password:
        return

    if not email or not password:
        raise RuntimeError(
            "Set both BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD, or leave both empty."
        )

    if len(password) < 8:
        raise RuntimeError("BOOTSTRAP_ADMIN_PASSWORD must be at least 8 characters.")

    db = get_db()
    users = db.collection("users").where("email", "==", email).limit(1).get()
    if users:
        return

    admin_id = get_next_id("users")
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    db.collection("users").document(str(admin_id)).set(
        {
            "id": admin_id,
            "name": config.BOOTSTRAP_ADMIN_NAME,
            "email": email,
            "password_hash": password_hash,
            "role": "admin",
            "is_active": True,
            "email_verified": True,
            "created_at": utcnow(),
            "last_login": None,
            "google_id": None,
            "avatar_url": None,
        }
    )
    ensure_user_progress(db, admin_id)


_validate_runtime_config()
_ensure_bootstrap_admin()

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
            "firebase_project_id": config.FIREBASE_PROJECT_ID,
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
    print(f"Firestore: {config.FIREBASE_PROJECT_ID or 'default credentials'}")
    print(f"Gemini   : {'enabled' if config.GEMINI_API_KEY else 'disabled'}")
    print(f"Student  : http://localhost:{config.PORT}")
    print(f"Admin    : http://localhost:{config.PORT}/admin.html")
    print("=" * 60)

    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)
