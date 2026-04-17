from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token
import bcrypt
import secrets
import re
from datetime import timedelta
import json
from urllib import request as urlrequest, parse as urlparse, error as urlerror

import config
from database.db import get_db, get_next_id, ensure_user_progress, utcnow
from services.email_service import send_email

auth_bp = Blueprint("auth", __name__)


def _is_valid_email(email: str) -> bool:
    return bool(re.match(r"[^@]+@[^@]+\.[^@]+", email))


def _user_by_email(db, email: str):
    docs = db.collection("users").where("email", "==", email).limit(1).get()
    return docs[0].to_dict() if docs else None


def _user_by_google_id(db, google_id: str):
    docs = db.collection("users").where("google_id", "==", google_id).limit(1).get()
    return docs[0].to_dict() if docs else None


def _http_json(url: str, headers: dict | None = None):
    req = urlrequest.Request(url, headers=headers or {})
    with urlrequest.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _google_profile_from_access_token(access_token: str):
    tokeninfo_url = f"https://oauth2.googleapis.com/tokeninfo?access_token={urlparse.quote(access_token)}"
    try:
        tokeninfo = _http_json(tokeninfo_url)
    except urlerror.HTTPError as e:
        details = e.read().decode("utf-8", errors="ignore")
        raise ValueError(details or "Google access token is invalid") from e
    except Exception as e:
        raise ValueError(f"Could not validate Google access token: {e}") from e

    aud = tokeninfo.get("aud") or tokeninfo.get("azp") or tokeninfo.get("issued_to")
    if config.GOOGLE_CLIENT_ID and aud and aud != config.GOOGLE_CLIENT_ID:
        raise ValueError("Google token audience mismatch")

    try:
        userinfo = _http_json(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except urlerror.HTTPError as e:
        details = e.read().decode("utf-8", errors="ignore")
        raise ValueError(details or "Could not fetch Google profile") from e
    except Exception as e:
        raise ValueError(f"Could not fetch Google profile: {e}") from e

    return {
        "sub": userinfo.get("sub") or tokeninfo.get("sub") or tokeninfo.get("user_id"),
        "email": (userinfo.get("email") or "").strip().lower(),
        "name": (userinfo.get("name") or userinfo.get("given_name") or "Student").strip(),
        "picture": (userinfo.get("picture") or "").strip(),
        "email_verified": bool(userinfo.get("email_verified", True)),
    }


@auth_bp.route("/register", methods=["POST"])
def register():
    try:
        data = request.get_json(force=True)
        name = (data.get("name") or "").strip()
        email = (data.get("email") or "").strip().lower()
        password = (data.get("password") or "")

        if not name:
            return jsonify({"error": "Name is required"}), 400
        if not email or not _is_valid_email(email):
            return jsonify({"error": "Valid email is required"}), 400
        if len(password) < 6:
            return jsonify({"error": "Password must be at least 6 characters"}), 400

        db = get_db()
        if _user_by_email(db, email):
            return jsonify({"error": "Email already registered. Please login."}), 409

        user_id = get_next_id("users")
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        db.collection("users").document(str(user_id)).set(
            {
                "id": user_id,
                "name": name,
                "email": email,
                "password_hash": hashed,
                "role": "student",
                "is_active": True,
                "email_verified": False,
                "created_at": utcnow(),
                "last_login": None,
                "google_id": None,
                "avatar_url": None,
            }
        )
        ensure_user_progress(db, user_id)

        otp = f"{secrets.randbelow(1000000):06d}"
        otp_hash = bcrypt.hashpw(otp.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        otp_id = get_next_id("signup_otps")
        db.collection("signup_otps").document(str(otp_id)).set(
            {
                "id": otp_id,
                "user_id": user_id,
                "otp_hash": otp_hash,
                "used": False,
                "created_at": utcnow(),
                "expires_at": utcnow() + timedelta(minutes=30),
            }
        )
        send_email(
            email,
            "StudyBot Email Verification OTP",
            f"Your StudyBot verification OTP is: {otp}\n\nThis OTP expires in 30 minutes.",
        )

        return (
            jsonify(
                {
                    "otp_required": True,
                    "email": email,
                    "message": "Verification OTP sent to email.",
                }
            ),
            201,
        )

    except Exception as e:
        print(f"[Auth] Register error: {e}")
        return jsonify({"error": "Server error during registration"}), 500


@auth_bp.route("/login", methods=["POST"])
def login():
    try:
        data = request.get_json(force=True)
        email = (data.get("email") or "").strip().lower()
        password = (data.get("password") or "")

        if not email or not password:
            return jsonify({"error": "Email and password are required"}), 400

        db = get_db()
        user = _user_by_email(db, email)
        if not user or not user.get("is_active", True):
            return jsonify({"error": "Invalid email or password"}), 401

        if not user.get("password_hash"):
            return jsonify({"error": "This account uses Google login. Please use Google Sign-In."}), 401

        if not bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
            return jsonify({"error": "Invalid email or password"}), 401

        if user.get("email_verified") is False:
            otp = f"{secrets.randbelow(1000000):06d}"
            otp_hash = bcrypt.hashpw(otp.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            otp_id = get_next_id("signup_otps")
            db.collection("signup_otps").document(str(otp_id)).set(
                {
                    "id": otp_id,
                    "user_id": user.get("id"),
                    "otp_hash": otp_hash,
                    "used": False,
                    "created_at": utcnow(),
                    "expires_at": utcnow() + timedelta(minutes=30),
                }
            )
            send_email(
                user.get("email"),
                "StudyBot Email Verification OTP",
                f"Your StudyBot verification OTP is: {otp}\n\nThis OTP expires in 30 minutes.",
            )
            return jsonify({"verify_required": True, "email": user.get("email")}), 200

        db.collection("users").document(str(user["id"])).set({"last_login": utcnow()}, merge=True)

        token = create_access_token(
            identity=str(user["id"]),
            additional_claims={"role": user["role"], "name": user["name"]},
        )

        return (
            jsonify(
                {
                    "token": token,
                    "user": {
                        "id": user["id"],
                        "name": user["name"],
                        "email": user["email"],
                        "role": user["role"],
                        "avatar_url": user.get("avatar_url"),
                        "bio": user.get("bio", ""),
                        "phone": user.get("phone", ""),
                    },
                }
            ),
            200,
        )

    except Exception as e:
        print(f"[Auth] Login error: {e}")
        return jsonify({"error": "Server error during login"}), 500


@auth_bp.route("/google", methods=["POST"])
def google_login():
    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as google_requests

        data = request.get_json(force=True)
        credential = data.get("credential") or data.get("id_token")
        access_token = data.get("access_token")

        if not credential and not access_token:
            return jsonify({"error": "No Google credential provided"}), 400

        if access_token:
            try:
                info = _google_profile_from_access_token(access_token)
            except Exception as e:
                return jsonify({"error": f"Google verification failed: {str(e)}"}), 401
        else:
            try:
                info = id_token.verify_oauth2_token(
                    credential, google_requests.Request(), config.GOOGLE_CLIENT_ID
                )
            except Exception as e:
                return jsonify({"error": f"Google verification failed: {str(e)}"}), 401

        google_id = info.get("sub")
        email = info.get("email", "").lower()
        name = info.get("name", "Student")
        avatar = info.get("picture", "")

        db = get_db()
        user = _user_by_google_id(db, google_id) if google_id else None
        if not user and email:
            user = _user_by_email(db, email)

        if not user:
            user_id = get_next_id("users")
            db.collection("users").document(str(user_id)).set(
                {
                    "id": user_id,
                    "name": name,
                    "email": email,
                    "password_hash": None,
                    "google_id": google_id,
                    "avatar_url": avatar,
                    "bio": "",
                    "phone": "",
                    "role": "student",
                    "is_active": True,
                    "email_verified": True,
                    "created_at": utcnow(),
                    "last_login": utcnow(),
                }
            )
            ensure_user_progress(db, user_id)
            role = "student"
        else:
            user_id = user["id"]
            role = user.get("role", "student")
            name = user.get("name") or name
            email = user.get("email") or email
            updates = {"last_login": utcnow()}
            if google_id and not user.get("google_id"):
                updates["google_id"] = google_id
            if avatar:
                updates["avatar_url"] = avatar
            db.collection("users").document(str(user_id)).set(updates, merge=True)

        token = create_access_token(
            identity=str(user_id), additional_claims={"role": role, "name": name}
        )

        return (
            jsonify(
                {
                    "token": token,
                    "user": {
                        "id": user_id,
                        "name": name,
                        "email": email,
                        "role": role,
                        "avatar_url": avatar,
                        "bio": user.get("bio", "") if user else "",
                        "phone": user.get("phone", "") if user else "",
                    },
                }
            ),
            200,
        )

    except Exception as e:
        print(f"[Auth] Google login error: {e}")
        return jsonify({"error": "Google login failed"}), 500


@auth_bp.route("/verify", methods=["GET"])
def verify():
    from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity, get_jwt

    try:
        verify_jwt_in_request()
        uid = get_jwt_identity()
        claims = get_jwt()
        db = get_db()
        user_doc = db.collection("users").document(str(uid)).get()
        user = user_doc.to_dict() if user_doc.exists else {}
        return (
            jsonify(
                {
                    "valid": True,
                    "user_id": uid,
                    "role": claims.get("role"),
                    "user": {
                        "id": user.get("id"),
                        "name": user.get("name"),
                        "email": user.get("email"),
                        "role": user.get("role"),
                        "avatar_url": user.get("avatar_url"),
                        "bio": user.get("bio", ""),
                        "phone": user.get("phone", ""),
                    },
                }
            ),
            200,
        )
    except Exception as e:
        return jsonify({"valid": False, "error": str(e)}), 401


@auth_bp.route("/profile", methods=["GET"])
def get_profile():
    from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity

    try:
        verify_jwt_in_request()
        uid = int(get_jwt_identity())
        db = get_db()
        doc = db.collection("users").document(str(uid)).get()
        if not doc.exists:
            return jsonify({"error": "User not found"}), 404
        u = doc.to_dict() or {}
        return (
            jsonify(
                {
                    "id": u.get("id"),
                    "name": u.get("name"),
                    "email": u.get("email"),
                    "role": u.get("role"),
                    "avatar_url": u.get("avatar_url"),
                    "bio": u.get("bio", ""),
                    "phone": u.get("phone", ""),
                }
            ),
            200,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 401


@auth_bp.route("/profile", methods=["PUT"])
def update_profile():
    from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity

    try:
        verify_jwt_in_request()
        uid = int(get_jwt_identity())
        data = request.get_json(force=True)
        name = (data.get("name") or "").strip()
        email = (data.get("email") or "").strip().lower()
        avatar_url = (data.get("avatar_url") or "").strip()
        bio = (data.get("bio") or "").strip()
        phone = (data.get("phone") or "").strip()

        if not name:
            return jsonify({"error": "Name is required"}), 400
        if not email or not _is_valid_email(email):
            return jsonify({"error": "Valid email is required"}), 400

        db = get_db()
        existing = _user_by_email(db, email)
        if existing and existing.get("id") != uid:
            return jsonify({"error": "Email already in use"}), 409

        updates = {
            "name": name,
            "email": email,
            "avatar_url": avatar_url or None,
            "bio": bio,
            "phone": phone,
        }
        db.collection("users").document(str(uid)).set(updates, merge=True)
        return jsonify({"success": True, "user": updates}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@auth_bp.route("/google-config", methods=["GET"])
def google_config():
    return jsonify({"configured": bool(config.GOOGLE_CLIENT_ID), "client_id": config.GOOGLE_CLIENT_ID}), 200


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


@auth_bp.route("/forgot-password", methods=["POST"])
def forgot_password():
    try:
        data = request.get_json(force=True)
        email = (data.get("email") or "").strip().lower()
        if not email or not _is_valid_email(email):
            return jsonify({"error": "Valid email is required"}), 400

        db = get_db()
        user = _user_by_email(db, email)
        # Always return success to avoid user enumeration.
        if not user:
            return jsonify({"success": True, "message": "If the email exists, a reset code was created."}), 200

        otp = f"{secrets.randbelow(1000000):06d}"
        reset_id = get_next_id("password_resets")
        expires_at = utcnow() + timedelta(minutes=30)
        otp_hash = bcrypt.hashpw(otp.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        db.collection("password_resets").document(str(reset_id)).set(
            {
                "id": reset_id,
                "user_id": user.get("id"),
                "otp_hash": otp_hash,
                "used": False,
                "created_at": utcnow(),
                "expires_at": expires_at,
            }
        )

        send_email(
            email,
            "StudyBot Password Reset OTP",
            f"Your StudyBot password reset OTP is: {otp}\n\nThis OTP expires in 30 minutes.",
        )

        return jsonify({"success": True, "message": "If the email exists, an OTP was sent.", "expires_in_minutes": 30}), 200
    except Exception as e:
        return jsonify({"error": f"Reset error: {e}"}), 500


@auth_bp.route("/reset-password", methods=["POST"])
def reset_password():
    try:
        data = request.get_json(force=True)
        email = (data.get("email") or "").strip().lower()
        otp = (data.get("token") or "").strip()
        new_password = (data.get("new_password") or "").strip()

        if not email or not _is_valid_email(email):
            return jsonify({"error": "Valid email is required"}), 400
        if not otp:
            return jsonify({"error": "OTP is required"}), 400
        if len(new_password) < 6:
            return jsonify({"error": "Password must be at least 6 characters"}), 400

        db = get_db()
        user = _user_by_email(db, email)
        if not user:
            return jsonify({"error": "Invalid reset token"}), 400

        resets = db.collection("password_resets").where("user_id", "==", user.get("id")).get()
        if not resets:
            return jsonify({"error": "Invalid OTP"}), 400

        reset_doc = None
        reset = None
        for doc in resets:
            row = doc.to_dict() or {}
            if row.get("used"):
                continue
            expires_at = row.get("expires_at")
            if expires_at and expires_at < utcnow():
                continue
            if row.get("otp_hash") and bcrypt.checkpw(otp.encode("utf-8"), row["otp_hash"].encode("utf-8")):
                reset_doc = doc
                reset = row
                break

        if not reset_doc:
            return jsonify({"error": "Invalid or expired OTP"}), 400

        password_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        db.collection("users").document(str(user.get("id"))).set({"password_hash": password_hash}, merge=True)
        reset_doc.reference.set({"used": True, "used_at": utcnow()}, merge=True)
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": f"Reset error: {e}"}), 500


@auth_bp.route("/verify-email", methods=["POST"])
def verify_email():
    try:
        data = request.get_json(force=True)
        email = (data.get("email") or "").strip().lower()
        otp = (data.get("otp") or "").strip()
        if not email or not _is_valid_email(email):
            return jsonify({"error": "Valid email is required"}), 400
        if not otp:
            return jsonify({"error": "OTP is required"}), 400

        db = get_db()
        user = _user_by_email(db, email)
        if not user:
            return jsonify({"error": "Invalid OTP"}), 400

        otps = db.collection("signup_otps").where("user_id", "==", user.get("id")).get()
        if not otps:
            return jsonify({"error": "Invalid OTP"}), 400

        otp_doc = None
        for doc in otps:
            row = doc.to_dict() or {}
            if row.get("used"):
                continue
            expires_at = row.get("expires_at")
            if expires_at and expires_at < utcnow():
                continue
            if row.get("otp_hash") and bcrypt.checkpw(otp.encode("utf-8"), row["otp_hash"].encode("utf-8")):
                otp_doc = doc
                break

        if not otp_doc:
            return jsonify({"error": "Invalid or expired OTP"}), 400

        otp_doc.reference.set({"used": True, "used_at": utcnow()}, merge=True)
        db.collection("users").document(str(user.get("id"))).set({"email_verified": True}, merge=True)
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": f"Verify error: {e}"}), 500


@auth_bp.route("/resend-verification", methods=["POST"])
def resend_verification():
    try:
        data = request.get_json(force=True)
        email = (data.get("email") or "").strip().lower()
        if not email or not _is_valid_email(email):
            return jsonify({"error": "Valid email is required"}), 400

        db = get_db()
        user = _user_by_email(db, email)
        if not user:
            return jsonify({"success": True, "message": "If the email exists, an OTP was sent."}), 200
        if user.get("email_verified") is True:
            return jsonify({"success": True, "message": "Email already verified."}), 200

        otp = f"{secrets.randbelow(1000000):06d}"
        otp_hash = bcrypt.hashpw(otp.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        otp_id = get_next_id("signup_otps")
        db.collection("signup_otps").document(str(otp_id)).set(
            {
                "id": otp_id,
                "user_id": user.get("id"),
                "otp_hash": otp_hash,
                "used": False,
                "created_at": utcnow(),
                "expires_at": utcnow() + timedelta(minutes=30),
            }
        )
        send_email(
            email,
            "StudyBot Email Verification OTP",
            f"Your StudyBot verification OTP is: {otp}\n\nThis OTP expires in 30 minutes.",
        )
        return jsonify({"success": True, "message": "Verification OTP sent."}), 200
    except Exception as e:
        return jsonify({"error": f"Resend error: {e}"}), 500
