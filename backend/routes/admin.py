from flask import Blueprint, jsonify, request, send_file
from flask_jwt_extended import get_jwt, get_jwt_identity, verify_jwt_in_request

import bcrypt
import json
import os
import re

import config
from database.db import get_db

admin_bp = Blueprint("admin", __name__)


def _safe_close(cur, db):
    try:
        cur.close()
        db.close()
    except Exception:
        pass


def _require_admin():
    verify_jwt_in_request()
    claims = get_jwt()
    if claims.get("role") != "admin":
        raise PermissionError("Admin access required")
    return int(get_jwt_identity())


def _is_valid_email(email: str) -> bool:
    return bool(re.match(r"[^@]+@[^@]+\.[^@]+", email or ""))


def _parse_topics(raw):
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return []


def _norm_origin(url: str) -> str:
    value = (url or "").strip().rstrip("/")
    if not value:
        return ""
    if not value.startswith("http://") and not value.startswith("https://"):
        return ""
    return value


@admin_bp.route("/stats", methods=["GET"])
def stats():
    try:
        _require_admin()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        db = get_db()
        cur = db.cursor(dictionary=True)

        cur.execute("SELECT COUNT(*) AS total FROM users WHERE role='student'")
        users = cur.fetchone()["total"]

        cur.execute("SELECT COUNT(*) AS total FROM study_materials")
        materials = cur.fetchone()["total"]

        cur.execute("SELECT COUNT(*) AS total FROM test_attempts")
        tests = cur.fetchone()["total"]

        cur.execute("SELECT IFNULL(AVG(score),0) AS avg FROM test_attempts")
        avg_score = round(float(cur.fetchone()["avg"]), 2)

        _safe_close(cur, db)

        return jsonify(
            {
                "total_users": users,
                "total_materials": materials,
                "total_tests_taken": tests,
                "platform_avg_score": avg_score,
            }
        ), 200

    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@admin_bp.route("/users", methods=["GET"])
def list_users():
    try:
        _require_admin()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute(
            """SELECT u.id, u.name, u.email, u.role, u.is_active,
                      u.created_at, u.last_login,
                      IFNULL(up.total_tests, 0)        AS total_tests,
                      IFNULL(up.avg_score, 0)          AS avg_score,
                      IFNULL(up.materials_uploaded, 0) AS materials_uploaded
               FROM users u
               LEFT JOIN user_progress up ON u.id = up.user_id
               ORDER BY u.created_at DESC"""
        )
        rows = cur.fetchall()
        _safe_close(cur, db)

        for r in rows:
            for f in ["created_at", "last_login"]:
                if r.get(f):
                    r[f] = str(r[f])
            if r.get("avg_score") is not None:
                r["avg_score"] = float(r["avg_score"])

        return jsonify(rows), 200

    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@admin_bp.route("/users", methods=["POST"])
def create_user():
    try:
        _require_admin()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        data = request.get_json(force=True)
        name = (data.get("name") or "").strip()
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        role = (data.get("role") or "student").strip().lower()
        is_active = 1 if bool(data.get("is_active", True)) else 0

        if not name:
            return jsonify({"error": "Name is required"}), 400
        if not _is_valid_email(email):
            return jsonify({"error": "Valid email is required"}), 400
        if len(password) < 6:
            return jsonify({"error": "Password must be at least 6 characters"}), 400
        if role not in ("student", "admin"):
            return jsonify({"error": "Role must be student or admin"}), 400

        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            _safe_close(cur, db)
            return jsonify({"error": "Email already exists"}), 409

        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        cur.execute(
            """INSERT INTO users (name, email, password_hash, role, is_active)
               VALUES (%s, %s, %s, %s, %s)""",
            (name, email, password_hash, role, is_active),
        )
        db.commit()
        user_id = cur.lastrowid

        cur.execute("INSERT IGNORE INTO user_progress (user_id) VALUES (%s)", (user_id,))
        db.commit()
        _safe_close(cur, db)

        return (
            jsonify(
                {
                    "success": True,
                    "user": {
                        "id": user_id,
                        "name": name,
                        "email": email,
                        "role": role,
                        "is_active": is_active,
                    },
                }
            ),
            201,
        )

    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@admin_bp.route("/users/<int:uid>", methods=["PUT"])
def update_user(uid):
    try:
        _require_admin()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        data = request.get_json(force=True)
        db = get_db()
        cur = db.cursor()

        if "is_active" in data:
            cur.execute("UPDATE users SET is_active=%s WHERE id=%s", (int(data["is_active"]), uid))
        if "role" in data and data["role"] in ("student", "admin"):
            cur.execute("UPDATE users SET role=%s WHERE id=%s", (data["role"], uid))

        db.commit()
        _safe_close(cur, db)
        return jsonify({"success": True}), 200

    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@admin_bp.route("/users/<int:uid>", methods=["DELETE"])
def delete_user(uid):
    try:
        _require_admin()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        db = get_db()
        cur = db.cursor(dictionary=True)

        cur.execute("SELECT role FROM users WHERE id=%s", (uid,))
        user = cur.fetchone()
        if not user:
            _safe_close(cur, db)
            return jsonify({"error": "User not found"}), 404
        if user["role"] == "admin":
            _safe_close(cur, db)
            return jsonify({"error": "Cannot delete admin accounts"}), 403

        cur.execute("DELETE FROM users WHERE id=%s", (uid,))
        db.commit()
        _safe_close(cur, db)
        return jsonify({"success": True}), 200

    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@admin_bp.route("/materials", methods=["GET"])
def all_materials():
    try:
        _require_admin()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute(
            """SELECT sm.id, sm.title, sm.subject, sm.file_type,
                      sm.file_size, sm.created_at, sm.file_path, sm.filename,
                      u.name AS user_name, u.email AS user_email
               FROM study_materials sm
               JOIN users u ON sm.user_id = u.id
               ORDER BY sm.created_at DESC"""
        )
        rows = cur.fetchall()
        _safe_close(cur, db)

        for r in rows:
            if r.get("created_at"):
                r["created_at"] = str(r["created_at"])

        return jsonify(rows), 200

    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@admin_bp.route("/materials/<int:mid>/content", methods=["GET"])
def material_content(mid):
    try:
        _require_admin()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute(
            """SELECT sm.id, sm.title, sm.subject, sm.filename, sm.file_type, sm.file_size,
                      sm.extracted_text, sm.key_topics, sm.created_at,
                      u.name AS user_name, u.email AS user_email
               FROM study_materials sm
               JOIN users u ON sm.user_id = u.id
               WHERE sm.id=%s""",
            (mid,),
        )
        row = cur.fetchone()
        _safe_close(cur, db)

        if not row:
            return jsonify({"error": "Material not found"}), 404

        if row.get("created_at"):
            row["created_at"] = str(row["created_at"])
        row["key_topics"] = _parse_topics(row.get("key_topics"))
        row["extracted_text"] = row.get("extracted_text") or ""

        return jsonify(row), 200

    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@admin_bp.route("/materials/<int:mid>/download", methods=["GET"])
def download_material(mid):
    try:
        _require_admin()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT filename, file_path FROM study_materials WHERE id=%s", (mid,))
        mat = cur.fetchone()
        _safe_close(cur, db)

        if not mat:
            return jsonify({"error": "Material not found"}), 404

        file_path = mat.get("file_path")
        if not file_path or not os.path.isfile(file_path):
            return jsonify({"error": "File missing on server"}), 404

        return send_file(file_path, as_attachment=True, download_name=mat.get("filename") or os.path.basename(file_path))

    except Exception as e:
        return jsonify({"error": f"Download error: {e}"}), 500


@admin_bp.route("/materials/<int:mid>", methods=["DELETE"])
def delete_material(mid):
    try:
        _require_admin()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT file_path FROM study_materials WHERE id=%s", (mid,))
        mat = cur.fetchone()

        if mat:
            try:
                if os.path.exists(mat["file_path"]):
                    os.remove(mat["file_path"])
            except Exception:
                pass
            cur.execute("DELETE FROM study_materials WHERE id=%s", (mid,))
            db.commit()

        _safe_close(cur, db)
        return jsonify({"success": True}), 200

    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@admin_bp.route("/tests", methods=["GET"])
def all_tests():
    try:
        _require_admin()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute(
            """SELECT ta.id, t.title, t.test_type,
                      ta.score, ta.correct_answers, ta.total_questions,
                      ta.time_taken, ta.completed_at,
                      u.name AS student_name, u.email AS student_email
               FROM test_attempts ta
               JOIN tests t ON ta.test_id = t.id
               JOIN users u ON ta.user_id = u.id
               ORDER BY ta.completed_at DESC LIMIT 500"""
        )
        rows = cur.fetchall()
        _safe_close(cur, db)

        for r in rows:
            if r.get("completed_at"):
                r["completed_at"] = str(r["completed_at"])
            if r.get("score") is not None:
                r["score"] = float(r["score"])

        return jsonify(rows), 200

    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500

@admin_bp.route("/users/<int:uid>/reset-password", methods=["POST"])
def reset_user_password(uid):
    try:
        _require_admin()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        data = request.get_json(force=True)
        new_password = (data.get("new_password") or "").strip()
        if len(new_password) < 6:
            return jsonify({"error": "Password must be at least 6 characters"}), 400

        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT id FROM users WHERE id=%s", (uid,))
        if not cur.fetchone():
            _safe_close(cur, db)
            return jsonify({"error": "User not found"}), 404

        password_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        cur.execute("UPDATE users SET password_hash=%s WHERE id=%s", (password_hash, uid))
        db.commit()
        _safe_close(cur, db)

        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@admin_bp.route("/users/<int:uid>/profile", methods=["GET"])
def user_profile(uid):
    try:
        _require_admin()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        db = get_db()
        cur = db.cursor(dictionary=True)

        cur.execute(
            """SELECT u.id, u.name, u.email, u.role, u.is_active, u.created_at, u.last_login,
                      IFNULL(up.total_tests,0) AS total_tests,
                      IFNULL(up.avg_score,0) AS avg_score,
                      IFNULL(up.materials_uploaded,0) AS materials_uploaded,
                      IFNULL(up.chat_sessions,0) AS chat_sessions
               FROM users u
               LEFT JOIN user_progress up ON up.user_id = u.id
               WHERE u.id=%s""",
            (uid,),
        )
        profile = cur.fetchone()
        if not profile:
            _safe_close(cur, db)
            return jsonify({"error": "User not found"}), 404

        for dtf in ["created_at", "last_login"]:
            if profile.get(dtf):
                profile[dtf] = str(profile[dtf])
        profile["avg_score"] = float(profile.get("avg_score") or 0)

        cur.execute(
            """SELECT id, title, subject, created_at
               FROM study_materials
               WHERE user_id=%s
               ORDER BY created_at DESC
               LIMIT 8""",
            (uid,),
        )
        recent_materials = cur.fetchall()
        for r in recent_materials:
            if r.get("created_at"):
                r["created_at"] = str(r["created_at"])

        cur.execute(
            """SELECT ta.id, t.title, ta.score, ta.completed_at
               FROM test_attempts ta
               JOIN tests t ON t.id = ta.test_id
               WHERE ta.user_id=%s
               ORDER BY ta.completed_at DESC
               LIMIT 8""",
            (uid,),
        )
        recent_tests = cur.fetchall()
        for r in recent_tests:
            if r.get("completed_at"):
                r["completed_at"] = str(r["completed_at"])
            r["score"] = float(r.get("score") or 0)

        cur.execute(
            """SELECT DATE(created_at) AS day, COUNT(*) AS uploaded
               FROM study_materials
               WHERE user_id=%s
               GROUP BY DATE(created_at)
               ORDER BY day DESC
               LIMIT 14""",
            (uid,),
        )
        uploads_timeline = cur.fetchall()
        for r in uploads_timeline:
            if r.get("day"):
                r["day"] = str(r["day"])

        cur.execute(
            """SELECT DATE(completed_at) AS day, COUNT(*) AS attempts, IFNULL(AVG(score),0) AS avg_score
               FROM test_attempts
               WHERE user_id=%s
               GROUP BY DATE(completed_at)
               ORDER BY day DESC
               LIMIT 14""",
            (uid,),
        )
        tests_timeline = cur.fetchall()
        for r in tests_timeline:
            if r.get("day"):
                r["day"] = str(r["day"])
            r["avg_score"] = float(r.get("avg_score") or 0)

        _safe_close(cur, db)
        return jsonify(
            {
                "profile": profile,
                "recent_materials": recent_materials,
                "recent_tests": recent_tests,
                "uploads_timeline": uploads_timeline,
                "tests_timeline": tests_timeline,
            }
        ), 200
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@admin_bp.route("/subjects/analytics", methods=["GET"])
def subject_analytics():
    try:
        _require_admin()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute(
            """SELECT u.id AS user_id,
                      u.name AS user_name,
                      IFNULL(NULLIF(sm.subject,''), 'General') AS subject_name,
                      COUNT(DISTINCT sm.id) AS materials,
                      COUNT(DISTINCT ta.id) AS attempts,
                      IFNULL(AVG(ta.score),0) AS avg_score
               FROM study_materials sm
               JOIN users u ON u.id = sm.user_id
               LEFT JOIN tests t ON t.material_id = sm.id
               LEFT JOIN test_attempts ta ON ta.test_id = t.id
               GROUP BY u.id, u.name, IFNULL(NULLIF(sm.subject,''), 'General')
               ORDER BY materials DESC, avg_score DESC
               LIMIT 500"""
        )
        rows = cur.fetchall()
        _safe_close(cur, db)

        for r in rows:
            r["avg_score"] = round(float(r.get("avg_score") or 0), 2)

        return jsonify(rows), 200
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@admin_bp.route("/oauth/google-check", methods=["GET"])
def google_oauth_check():
    try:
        _require_admin()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception:
        return jsonify({"error": "Unauthorized"}), 401

    detected_origin = _norm_origin(request.host_url)
    frontend_origin = detected_origin

    configured_client_id = bool(config.GOOGLE_CLIENT_ID)
    configured_client_secret = bool(config.GOOGLE_CLIENT_SECRET)

    allowed_origins = [_norm_origin(x) for x in config.GOOGLE_ALLOWED_ORIGINS if _norm_origin(x)]
    allowed_redirects = [x.strip() for x in config.GOOGLE_ALLOWED_REDIRECTS if x.strip()]

    suggested_origins = []
    for candidate in [frontend_origin, "http://localhost:5000", "http://127.0.0.1:5000"]:
        if candidate and candidate not in suggested_origins:
            suggested_origins.append(candidate)

    suggested_redirects = []
    for origin in suggested_origins:
        for uri in [f"{origin}/auth/google/callback", f"{origin}/oauth2/callback"]:
            if uri not in suggested_redirects:
                suggested_redirects.append(uri)

    origin_ok = frontend_origin in allowed_origins
    redirect_ok = any(uri in allowed_redirects for uri in suggested_redirects)
    healthy = configured_client_id and configured_client_secret and origin_ok

    checks = [
        {
            "id": "client_id",
            "ok": configured_client_id,
            "message": "GOOGLE_CLIENT_ID is configured." if configured_client_id else "Missing GOOGLE_CLIENT_ID in backend/.env",
        },
        {
            "id": "client_secret",
            "ok": configured_client_secret,
            "message": "GOOGLE_CLIENT_SECRET is configured." if configured_client_secret else "Missing GOOGLE_CLIENT_SECRET in backend/.env",
        },
        {
            "id": "origin",
            "ok": origin_ok,
            "message": (
                f"Origin {frontend_origin} is registered."
                if origin_ok
                else f"Add {frontend_origin} to GOOGLE_ALLOWED_ORIGINS and Google Cloud Authorized JavaScript origins."
            ),
        },
        {
            "id": "redirect",
            "ok": redirect_ok,
            "message": (
                "At least one suggested redirect URI is registered."
                if redirect_ok
                else "Optional but recommended: add a suggested redirect URI for future OAuth code-flow use."
            ),
        },
    ]

    return (
        jsonify(
            {
                "healthy": healthy,
                "detected_origin": frontend_origin,
                "google_client_id_configured": configured_client_id,
                "google_client_secret_configured": configured_client_secret,
                "allowed_origins": allowed_origins,
                "allowed_redirects": allowed_redirects,
                "suggested_origins": suggested_origins,
                "suggested_redirects": suggested_redirects,
                "checks": checks,
                "google_console_credentials_url": "https://console.cloud.google.com/apis/credentials",
                "note": "For Google One Tap / GIS sign-in, Authorized JavaScript origins is required.",
            }
        ),
        200,
    )
