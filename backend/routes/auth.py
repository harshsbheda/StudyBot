from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token
import bcrypt
from database.db import get_db
import config
import re

auth_bp = Blueprint("auth", __name__)


def _is_valid_email(email: str) -> bool:
    return bool(re.match(r"[^@]+@[^@]+\.[^@]+", email))


def _safe_close(cur, db):
    try:
        cur.close()
        db.close()
    except Exception:
        pass


# ─────────────────────────────────────────────────
# REGISTER
# ─────────────────────────────────────────────────
@auth_bp.route("/register", methods=["POST"])
def register():
    try:
        data     = request.get_json(force=True)
        name     = (data.get("name") or "").strip()
        email    = (data.get("email") or "").strip().lower()
        password = (data.get("password") or "")

        # Validate
        if not name:
            return jsonify({"error": "Name is required"}), 400
        if not email or not _is_valid_email(email):
            return jsonify({"error": "Valid email is required"}), 400
        if len(password) < 6:
            return jsonify({"error": "Password must be at least 6 characters"}), 400

        db  = get_db()
        cur = db.cursor(dictionary=True)

        # Check duplicate
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            _safe_close(cur, db)
            return jsonify({"error": "Email already registered. Please login."}), 409

        # Hash password
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        # Insert user
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, 'student')",
            (name, email, hashed)
        )
        db.commit()
        user_id = cur.lastrowid

        # Init progress row
        cur.execute(
            "INSERT IGNORE INTO user_progress (user_id) VALUES (%s)",
            (user_id,)
        )
        db.commit()
        _safe_close(cur, db)

        # Create JWT token
        token = create_access_token(
            identity=str(user_id),
            additional_claims={"role": "student", "name": name}
        )

        return jsonify({
            "token": token,
            "user": {"id": user_id, "name": name, "email": email, "role": "student"}
        }), 201

    except Exception as e:
        print(f"[Auth] Register error: {e}")
        return jsonify({"error": "Server error during registration"}), 500


# ─────────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────────
@auth_bp.route("/login", methods=["POST"])
def login():
    try:
        data     = request.get_json(force=True)
        email    = (data.get("email") or "").strip().lower()
        password = (data.get("password") or "")

        if not email or not password:
            return jsonify({"error": "Email and password are required"}), 400

        db  = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute(
            "SELECT * FROM users WHERE email = %s AND is_active = 1",
            (email,)
        )
        user = cur.fetchone()

        if not user:
            _safe_close(cur, db)
            return jsonify({"error": "Invalid email or password"}), 401

        if not user.get("password_hash"):
            _safe_close(cur, db)
            return jsonify({"error": "This account uses Google login. Please use Google Sign-In."}), 401

        if not bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
            _safe_close(cur, db)
            return jsonify({"error": "Invalid email or password"}), 401

        # Update last login
        cur.execute("UPDATE users SET last_login = NOW() WHERE id = %s", (user["id"],))
        db.commit()
        _safe_close(cur, db)

        token = create_access_token(
            identity=str(user["id"]),
            additional_claims={"role": user["role"], "name": user["name"]}
        )

        return jsonify({
            "token": token,
            "user": {
                "id":    user["id"],
                "name":  user["name"],
                "email": user["email"],
                "role":  user["role"]
            }
        }), 200

    except Exception as e:
        print(f"[Auth] Login error: {e}")
        return jsonify({"error": "Server error during login"}), 500


# ─────────────────────────────────────────────────
# GOOGLE LOGIN
# ─────────────────────────────────────────────────
@auth_bp.route("/google", methods=["POST"])
def google_login():
    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as google_requests

        data       = request.get_json(force=True)
        credential = data.get("credential") or data.get("id_token")

        if not credential:
            return jsonify({"error": "No Google credential provided"}), 400

        # Verify Google token
        try:
            info = id_token.verify_oauth2_token(
                credential,
                google_requests.Request(),
                config.GOOGLE_CLIENT_ID
            )
        except Exception as e:
            return jsonify({"error": f"Google verification failed: {str(e)}"}), 401

        google_id = info.get("sub")
        email     = info.get("email", "").lower()
        name      = info.get("name", "Student")
        avatar    = info.get("picture", "")

        db  = get_db()
        cur = db.cursor(dictionary=True)

        # Check if user exists
        cur.execute(
            "SELECT * FROM users WHERE google_id = %s OR email = %s",
            (google_id, email)
        )
        user = cur.fetchone()

        if not user:
            # Create new user
            cur.execute(
                "INSERT INTO users (name, email, google_id, avatar_url, role) VALUES (%s,%s,%s,%s,'student')",
                (name, email, google_id, avatar)
            )
            db.commit()
            user_id = cur.lastrowid
            role    = "student"
            cur.execute("INSERT IGNORE INTO user_progress (user_id) VALUES (%s)", (user_id,))
            db.commit()
        else:
            user_id = user["id"]
            role    = user["role"]
            # Update google_id if missing
            if not user.get("google_id"):
                cur.execute("UPDATE users SET google_id=%s WHERE id=%s", (google_id, user_id))
            cur.execute("UPDATE users SET last_login=NOW() WHERE id=%s", (user_id,))
            db.commit()

        _safe_close(cur, db)

        token = create_access_token(
            identity=str(user_id),
            additional_claims={"role": role, "name": name}
        )

        return jsonify({
            "token": token,
            "user":  {"id": user_id, "name": name, "email": email, "role": role, "avatar": avatar}
        }), 200

    except Exception as e:
        print(f"[Auth] Google login error: {e}")
        return jsonify({"error": "Google login failed"}), 500


# ─────────────────────────────────────────────────
# VERIFY TOKEN (health check)
# ─────────────────────────────────────────────────
@auth_bp.route("/verify", methods=["GET"])
def verify():
    from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity, get_jwt
    try:
        verify_jwt_in_request()
        uid    = get_jwt_identity()
        claims = get_jwt()
        return jsonify({"valid": True, "user_id": uid, "role": claims.get("role")}), 200
    except Exception as e:
        return jsonify({"valid": False, "error": str(e)}), 401

@auth_bp.route("/google-config", methods=["GET"])
def google_config():
    return jsonify({
        "configured": bool(config.GOOGLE_CLIENT_ID),
        "client_id": config.GOOGLE_CLIENT_ID,
    }), 200


@auth_bp.route("/google-config-check", methods=["GET"])
def google_config_check():
    detected_origin = (request.host_url or "").strip().rstrip("/")
    allowed_origins = [x for x in config.GOOGLE_ALLOWED_ORIGINS if x]
    allowed_redirects = [x for x in config.GOOGLE_ALLOWED_REDIRECTS if x]

    suggested_origins = []
    for origin in [detected_origin, "http://localhost:5000", "http://127.0.0.1:5000"]:
        if origin and origin not in suggested_origins:
            suggested_origins.append(origin)

    suggested_redirects = []
    for origin in suggested_origins:
        for uri in [f"{origin}/auth/google/callback", f"{origin}/oauth2/callback"]:
            if uri not in suggested_redirects:
                suggested_redirects.append(uri)

    return (
        jsonify(
            {
                "configured": bool(config.GOOGLE_CLIENT_ID),
                "client_id_present": bool(config.GOOGLE_CLIENT_ID),
                "client_secret_present": bool(config.GOOGLE_CLIENT_SECRET),
                "detected_origin": detected_origin,
                "origin_registered": detected_origin in allowed_origins,
                "suggested_origins": suggested_origins,
                "suggested_redirects": suggested_redirects,
                "allowed_origins": allowed_origins,
                "allowed_redirects": allowed_redirects,
            }
        ),
        200,
    )
