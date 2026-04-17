from flask import Blueprint, jsonify, request, send_file, Response
from flask_jwt_extended import get_jwt, get_jwt_identity, verify_jwt_in_request

import bcrypt
import csv
import json
import io
import os
import re
from datetime import datetime, timedelta, date

import config
from database.db import get_db, get_next_id, ensure_user_progress, set_user_progress, utcnow

admin_bp = Blueprint("admin", __name__)


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


def _to_iso(dt):
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt) if dt else None


def _as_dt(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None
    return None


def _date_key(value):
    dt = _as_dt(value)
    if not dt:
        return None
    return dt.date().isoformat()


def _days_ago(n: int) -> datetime:
    return utcnow() - timedelta(days=n)


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except Exception:
        return None


def _in_date_range(dt_value, start: date | None, end: date | None) -> bool:
    if not start and not end:
        return True
    dt = _as_dt(dt_value)
    if not dt:
        return False
    d = dt.date()
    if start and d < start:
        return False
    if end and d > end:
        return False
    return True


def _sort_by_dt(items, field: str, reverse: bool = True):
    def _key(item):
        value = item.get(field)
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except Exception:
                return datetime.min
        return datetime.min

    return sorted(items, key=_key, reverse=reverse)


def _norm_origin(url: str) -> str:
    value = (url or "").strip().rstrip("/")
    if not value:
        return ""
    if not value.startswith("http://") and not value.startswith("https://"):
        return ""
    return value


def _user_by_email(db, email: str):
    docs = db.collection("users").where("email", "==", email).limit(1).get()
    return docs[0].to_dict() if docs else None


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
        users = [u.to_dict() for u in db.collection("users").get()]
        materials = list(db.collection("study_materials").get())
        attempts = [a.to_dict() for a in db.collection("test_attempts").get()]

        total_users = len([u for u in users if u.get("role") == "student"])
        total_materials = len(materials)
        total_tests = len(attempts)
        avg_score = 0
        if attempts:
            avg_score = round(sum(float(a.get("score") or 0) for a in attempts) / len(attempts), 2)

        return jsonify(
            {
                "total_users": total_users,
                "total_materials": total_materials,
                "total_tests_taken": total_tests,
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
        users_docs = db.collection("users").get()
        users = [u.to_dict() for u in users_docs]
        users = _sort_by_dt(users, "created_at", reverse=True)

        progress_docs = db.collection("user_progress").get()
        progress = {int(p.id): p.to_dict() for p in progress_docs if p.id.isdigit()}

        rows = []
        for u in users:
            up = progress.get(u.get("id"), {})
            row = {
                "id": u.get("id"),
                "name": u.get("name"),
                "email": u.get("email"),
                "role": u.get("role"),
                "is_active": u.get("is_active", True),
                "created_at": _to_iso(u.get("created_at")),
                "last_login": _to_iso(u.get("last_login")),
                "total_tests": int(up.get("total_tests") or 0),
                "avg_score": float(up.get("avg_score") or 0),
                "materials_uploaded": int(up.get("materials_uploaded") or 0),
            }
            rows.append(row)

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
        is_active = bool(data.get("is_active", True))

        if not name:
            return jsonify({"error": "Name is required"}), 400
        if not _is_valid_email(email):
            return jsonify({"error": "Valid email is required"}), 400
        if len(password) < 6:
            return jsonify({"error": "Password must be at least 6 characters"}), 400
        if role not in ("student", "admin"):
            return jsonify({"error": "Role must be student or admin"}), 400

        db = get_db()
        if _user_by_email(db, email):
            return jsonify({"error": "Email already exists"}), 409

        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        user_id = get_next_id("users")
        db.collection("users").document(str(user_id)).set(
            {
                "id": user_id,
                "name": name,
                "email": email,
                "password_hash": password_hash,
                "role": role,
                "is_active": is_active,
                "email_verified": True,
                "created_at": utcnow(),
                "last_login": None,
                "google_id": None,
                "avatar_url": None,
            }
        )
        ensure_user_progress(db, user_id)

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
        updates = {}
        if "is_active" in data:
            updates["is_active"] = bool(data["is_active"])
        if "role" in data and data["role"] in ("student", "admin"):
            updates["role"] = data["role"]
        if updates:
            db.collection("users").document(str(uid)).set(updates, merge=True)
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


def _delete_user_related(db, user_id: int):
    # Delete materials and files
    mats = db.collection("study_materials").where("user_id", "==", user_id).get()
    for doc in mats:
        data = doc.to_dict() or {}
        fpath = data.get("file_path")
        try:
            if fpath and os.path.exists(fpath):
                os.remove(fpath)
        except Exception:
            pass
        doc.reference.delete()

    # Delete subjects
    subjects = db.collection("subjects").where("user_id", "==", user_id).get()
    for doc in subjects:
        doc.reference.delete()

    # Delete tests and attempts
    tests = db.collection("tests").where("user_id", "==", user_id).get()
    for doc in tests:
        doc.reference.delete()
    attempts = db.collection("test_attempts").where("user_id", "==", user_id).get()
    for doc in attempts:
        doc.reference.delete()

    # Delete chat sessions and messages
    sessions = db.collection("chat_sessions").where("user_id", "==", user_id).get()
    for s in sessions:
        sid = (s.to_dict() or {}).get("id")
        msgs = db.collection("chat_messages").where("session_id", "==", sid).get()
        for m in msgs:
            m.reference.delete()
        s.reference.delete()

    # Delete AI usage counters
    counters = db.collection("ai_usage_counters").where("user_id", "==", user_id).get()
    for doc in counters:
        doc.reference.delete()

    # Delete progress
    db.collection("user_progress").document(str(user_id)).delete()


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
        user_doc = db.collection("users").document(str(uid)).get()
        if not user_doc.exists:
            return jsonify({"error": "User not found"}), 404
        user = user_doc.to_dict() or {}
        if user.get("role") == "admin":
            return jsonify({"error": "Cannot delete admin accounts"}), 403

        _delete_user_related(db, uid)
        user_doc.reference.delete()
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
        mats_docs = db.collection("study_materials").get()
        mats = [d.to_dict() for d in mats_docs]
        mats = _sort_by_dt(mats, "created_at", reverse=True)
        user_ids = {m.get("user_id") for m in mats}
        users = {}
        if user_ids:
            refs = [db.collection("users").document(str(uid)) for uid in user_ids]
            for doc in db.get_all(refs):
                if doc.exists:
                    users[int(doc.id)] = doc.to_dict()

        rows = []
        for m in mats:
            u = users.get(m.get("user_id"), {})
            rows.append(
                {
                    "id": m.get("id"),
                    "title": m.get("title"),
                    "subject": m.get("subject"),
                    "file_type": m.get("file_type"),
                    "file_size": m.get("file_size"),
                    "created_at": _to_iso(m.get("created_at")),
                    "file_path": m.get("file_path"),
                    "filename": m.get("filename"),
                    "user_name": u.get("name"),
                    "user_email": u.get("email"),
                }
            )

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
        doc = db.collection("study_materials").document(str(mid)).get()
        if not doc.exists:
            return jsonify({"error": "Material not found"}), 404
        m = doc.to_dict() or {}
        user = db.collection("users").document(str(m.get("user_id"))).get()
        u = user.to_dict() if user.exists else {}

        row = {
            "id": m.get("id"),
            "title": m.get("title"),
            "subject": m.get("subject"),
            "filename": m.get("filename"),
            "file_type": m.get("file_type"),
            "file_size": m.get("file_size"),
            "extracted_text": m.get("extracted_text") or "",
            "key_topics": _parse_topics(m.get("key_topics")),
            "created_at": _to_iso(m.get("created_at")),
            "user_name": u.get("name"),
            "user_email": u.get("email"),
        }
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
        doc = db.collection("study_materials").document(str(mid)).get()
        if not doc.exists:
            return jsonify({"error": "Material not found"}), 404
        mat = doc.to_dict() or {}

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
        doc = db.collection("study_materials").document(str(mid)).get()
        if doc.exists:
            mat = doc.to_dict() or {}
            try:
                if mat.get("file_path") and os.path.exists(mat["file_path"]):
                    os.remove(mat["file_path"])
            except Exception:
                pass
            doc.reference.delete()
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
        attempts_docs = db.collection("test_attempts").get()
        attempts = [a.to_dict() for a in attempts_docs]
        attempts = _sort_by_dt(attempts, "completed_at", reverse=True)[:500]
        test_ids = {a.get("test_id") for a in attempts}
        user_ids = {a.get("user_id") for a in attempts}

        tests = {}
        if test_ids:
            refs = [db.collection("tests").document(str(tid)) for tid in test_ids]
            for doc in db.get_all(refs):
                if doc.exists:
                    tests[int(doc.id)] = doc.to_dict()

        users = {}
        if user_ids:
            refs = [db.collection("users").document(str(uid)) for uid in user_ids]
            for doc in db.get_all(refs):
                if doc.exists:
                    users[int(doc.id)] = doc.to_dict()

        rows = []
        for a in attempts:
            t = tests.get(a.get("test_id"), {})
            u = users.get(a.get("user_id"), {})
            rows.append(
                {
                    "id": a.get("id"),
                    "title": t.get("title"),
                    "test_type": t.get("test_type"),
                    "score": float(a.get("score") or 0),
                    "correct_answers": a.get("correct_answers"),
                    "total_questions": a.get("total_questions"),
                    "time_taken": a.get("time_taken"),
                    "completed_at": _to_iso(a.get("completed_at")),
                    "student_name": u.get("name"),
                    "student_email": u.get("email"),
                }
            )

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
        user_doc = db.collection("users").document(str(uid)).get()
        if not user_doc.exists:
            return jsonify({"error": "User not found"}), 404

        password_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        db.collection("users").document(str(uid)).set({"password_hash": password_hash}, merge=True)
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
        user_doc = db.collection("users").document(str(uid)).get()
        if not user_doc.exists:
            return jsonify({"error": "User not found"}), 404
        u = user_doc.to_dict() or {}

        ensure_user_progress(db, uid)
        prog_doc = db.collection("user_progress").document(str(uid)).get()
        prog = prog_doc.to_dict() if prog_doc.exists else {}

        profile = {
            "id": u.get("id"),
            "name": u.get("name"),
            "email": u.get("email"),
            "role": u.get("role"),
            "is_active": u.get("is_active", True),
            "created_at": _to_iso(u.get("created_at")),
            "last_login": _to_iso(u.get("last_login")),
            "total_tests": int(prog.get("total_tests") or 0),
            "avg_score": float(prog.get("avg_score") or 0),
            "materials_uploaded": int(prog.get("materials_uploaded") or 0),
            "chat_sessions": int(prog.get("chat_sessions") or 0),
        }

        recent_mats = db.collection("study_materials").where("user_id", "==", uid).get()
        recent_mats_list = [m.to_dict() or {} for m in recent_mats]
        recent_mats_list = _sort_by_dt(recent_mats_list, "created_at", reverse=True)[:8]
        recent_materials = []
        for row in recent_mats_list:
            recent_materials.append(
                {"id": row.get("id"), "title": row.get("title"), "subject": row.get("subject"), "created_at": _to_iso(row.get("created_at"))}
            )

        recent_attempts = db.collection("test_attempts").where("user_id", "==", uid).get()
        recent_attempts_list = [a.to_dict() for a in recent_attempts]
        recent_attempts_list = _sort_by_dt(recent_attempts_list, "completed_at", reverse=True)[:8]
        test_ids = {a.get("test_id") for a in recent_attempts_list}
        tests = {}
        if test_ids:
            refs = [db.collection("tests").document(str(tid)) for tid in test_ids]
            for doc in db.get_all(refs):
                if doc.exists:
                    tests[int(doc.id)] = doc.to_dict()

        recent_tests = []
        for a in recent_attempts_list:
            t = tests.get(a.get("test_id"), {})
            recent_tests.append(
                {"id": a.get("id"), "title": t.get("title"), "score": float(a.get("score") or 0), "completed_at": _to_iso(a.get("completed_at"))}
            )

        all_mats = db.collection("study_materials").where("user_id", "==", uid).get()
        uploads_timeline = {}
        for m in all_mats:
            row = m.to_dict() or {}
            day = _to_iso(row.get("created_at"))
            if day:
                day = day.split("T")[0]
                uploads_timeline[day] = uploads_timeline.get(day, 0) + 1
        uploads_rows = [{"day": k, "uploaded": v} for k, v in sorted(uploads_timeline.items(), reverse=True)[:14]]

        all_attempts = db.collection("test_attempts").where("user_id", "==", uid).get()
        tests_timeline = {}
        tests_scores = {}
        for a in all_attempts:
            a = a.to_dict() or {}
            day = _to_iso(a.get("completed_at"))
            if day:
                day = day.split("T")[0]
                tests_timeline[day] = tests_timeline.get(day, 0) + 1
                tests_scores.setdefault(day, []).append(float(a.get("score") or 0))
        tests_rows = []
        for day, count in sorted(tests_timeline.items(), reverse=True)[:14]:
            scores = tests_scores.get(day, [])
            avg = round(sum(scores) / len(scores), 2) if scores else 0
            tests_rows.append({"day": day, "attempts": count, "avg_score": avg})

        return jsonify(
            {
                "profile": profile,
                "recent_materials": recent_materials,
                "recent_tests": recent_tests,
                "uploads_timeline": uploads_rows,
                "tests_timeline": tests_rows,
            }
        ), 200
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


def _subject_analytics_rows(db):
    mats = [m.to_dict() for m in db.collection("study_materials").get()]
    users = {int(u.id): u.to_dict() for u in db.collection("users").get() if u.id.isdigit()}
    tests = {int(t.id): t.to_dict() for t in db.collection("tests").get() if t.id.isdigit()}
    attempts = [a.to_dict() for a in db.collection("test_attempts").get()]

    subject_map = {}
    for m in mats:
        subject_name = (m.get("subject") or "General").strip() or "General"
        key = (m.get("user_id"), subject_name)
        entry = subject_map.setdefault(key, {"materials": 0, "attempts": 0, "scores": []})
        entry["materials"] += 1

    material_by_id = {m.get("id"): m for m in mats}
    for a in attempts:
        test = tests.get(a.get("test_id"), {})
        material = material_by_id.get(test.get("material_id"))
        if not material:
            continue
        subject_name = (material.get("subject") or "General").strip() or "General"
        key = (material.get("user_id"), subject_name)
        entry = subject_map.setdefault(key, {"materials": 0, "attempts": 0, "scores": []})
        entry["attempts"] += 1
        entry["scores"].append(float(a.get("score") or 0))

    rows = []
    for (user_id, subject_name), entry in subject_map.items():
        scores = entry.get("scores") or []
        avg_score = round(sum(scores) / len(scores), 2) if scores else 0
        u = users.get(user_id, {})
        rows.append(
            {
                "user_id": user_id,
                "user_name": u.get("name"),
                "subject_name": subject_name,
                "materials": entry.get("materials", 0),
                "attempts": entry.get("attempts", 0),
                "avg_score": avg_score,
            }
        )

    rows.sort(key=lambda r: (r["materials"], r["avg_score"]), reverse=True)
    return rows[:500]


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
        return jsonify(_subject_analytics_rows(db)), 200
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


@admin_bp.route("/reports/overview", methods=["GET"])
def reports_overview():
    try:
        _require_admin()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        db = get_db()
        start = _parse_date(request.args.get("start_date"))
        end = _parse_date(request.args.get("end_date"))

        users = [u.to_dict() for u in db.collection("users").get()]
        materials = [m.to_dict() for m in db.collection("study_materials").get()]
        attempts = [a.to_dict() for a in db.collection("test_attempts").get()]
        sessions = [s.to_dict() for s in db.collection("chat_sessions").get()]

        students = [u for u in users if u.get("role") == "student"]
        total_users = len(students)

        last_activity = {}
        for u in students:
            last_activity[u.get("id")] = _as_dt(u.get("last_login"))

        for m in materials:
            if not _in_date_range(m.get("created_at"), start, end):
                continue
            uid = m.get("user_id")
            dt = _as_dt(m.get("created_at"))
            if dt and (last_activity.get(uid) is None or dt > last_activity.get(uid)):
                last_activity[uid] = dt

        for a in attempts:
            if not _in_date_range(a.get("completed_at"), start, end):
                continue
            uid = a.get("user_id")
            dt = _as_dt(a.get("completed_at"))
            if dt and (last_activity.get(uid) is None or dt > last_activity.get(uid)):
                last_activity[uid] = dt

        for s in sessions:
            if not _in_date_range(s.get("created_at"), start, end):
                continue
            uid = s.get("user_id")
            dt = _as_dt(s.get("created_at"))
            if dt and (last_activity.get(uid) is None or dt > last_activity.get(uid)):
                last_activity[uid] = dt

        active_7 = len([uid for uid, dt in last_activity.items() if dt and dt >= _days_ago(7)])
        active_30 = len([uid for uid, dt in last_activity.items() if dt and dt >= _days_ago(30)])

        materials_in_range = [m for m in materials if _in_date_range(m.get("created_at"), start, end)]
        attempts_in_range = [a for a in attempts if _in_date_range(a.get("completed_at"), start, end)]
        sessions_in_range = [s for s in sessions if _in_date_range(s.get("created_at"), start, end)]

        avg_materials_per_user = round((len(materials_in_range) / total_users), 2) if total_users else 0
        avg_tests_per_user = round((len(attempts_in_range) / total_users), 2) if total_users else 0

        uploads_timeline = {}
        for m in materials_in_range:
            day = _date_key(m.get("created_at"))
            if day:
                uploads_timeline[day] = uploads_timeline.get(day, 0) + 1
        uploads_rows = [{"day": k, "count": v} for k, v in sorted(uploads_timeline.items(), reverse=True)[:14]]

        tests_timeline = {}
        tests_scores = {}
        for a in attempts_in_range:
            day = _date_key(a.get("completed_at"))
            if day:
                tests_timeline[day] = tests_timeline.get(day, 0) + 1
                tests_scores.setdefault(day, []).append(float(a.get("score") or 0))
        tests_rows = []
        for day, count in sorted(tests_timeline.items(), reverse=True)[:14]:
            scores = tests_scores.get(day, [])
            avg = round(sum(scores) / len(scores), 2) if scores else 0
            tests_rows.append({"day": day, "count": count, "avg_score": avg})

        new_users_timeline = {}
        for u in students:
            if not _in_date_range(u.get("created_at"), start, end):
                continue
            day = _date_key(u.get("created_at"))
            if day:
                new_users_timeline[day] = new_users_timeline.get(day, 0) + 1
        new_users_rows = [{"day": k, "count": v} for k, v in sorted(new_users_timeline.items(), reverse=True)[:14]]

        subject_counts = {}
        for m in materials_in_range:
            subject = (m.get("subject") or "General").strip() or "General"
            entry = subject_counts.setdefault(subject, {"materials": 0, "users": set()})
            entry["materials"] += 1
            if m.get("user_id") is not None:
                entry["users"].add(m.get("user_id"))
        top_subjects = [
            {"subject": s, "materials": v["materials"], "users": len(v["users"])}
            for s, v in subject_counts.items()
        ]
        top_subjects.sort(key=lambda r: (r["materials"], r["users"]), reverse=True)

        return jsonify(
            {
                "totals": {
                "total_users": total_users,
                    "total_materials": len(materials_in_range),
                    "total_tests_taken": len(attempts_in_range),
                    "total_chat_sessions": len(sessions_in_range),
                },
                "activity": {
                    "active_7d": active_7,
                    "active_30d": active_30,
                    "avg_materials_per_user": avg_materials_per_user,
                    "avg_tests_per_user": avg_tests_per_user,
                },
                "top_subjects": top_subjects[:10],
                "uploads_timeline": uploads_rows,
                "tests_timeline": tests_rows,
                "new_users_timeline": new_users_rows,
            }
        ), 200
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


def _user_insights_data(db, start: date | None = None, end: date | None = None):
    users_docs = db.collection("users").get()
    users = [u.to_dict() for u in users_docs if u.to_dict().get("role") == "student"]

    progress_docs = db.collection("user_progress").get()
    progress = {int(p.id): p.to_dict() for p in progress_docs if p.id.isdigit()}

    materials = [m.to_dict() for m in db.collection("study_materials").get()]
    attempts = [a.to_dict() for a in db.collection("test_attempts").get()]
    sessions = [s.to_dict() for s in db.collection("chat_sessions").get()]

    materials = [m for m in materials if _in_date_range(m.get("created_at"), start, end)]
    attempts = [a for a in attempts if _in_date_range(a.get("completed_at"), start, end)]
    sessions = [s for s in sessions if _in_date_range(s.get("created_at"), start, end)]

    last_activity = {}
    for u in users:
        last_activity[u.get("id")] = _as_dt(u.get("last_login"))
    for m in materials:
        uid = m.get("user_id")
        dt = _as_dt(m.get("created_at"))
        if dt and (last_activity.get(uid) is None or dt > last_activity.get(uid)):
            last_activity[uid] = dt
    for a in attempts:
        uid = a.get("user_id")
        dt = _as_dt(a.get("completed_at"))
        if dt and (last_activity.get(uid) is None or dt > last_activity.get(uid)):
            last_activity[uid] = dt
    for s in sessions:
        uid = s.get("user_id")
        dt = _as_dt(s.get("created_at"))
        if dt and (last_activity.get(uid) is None or dt > last_activity.get(uid)):
            last_activity[uid] = dt

    materials_by_user = {}
    for m in materials:
        uid = m.get("user_id")
        materials_by_user[uid] = materials_by_user.get(uid, 0) + 1

    tests_by_user = {}
    scores_by_user = {}
    for a in attempts:
        uid = a.get("user_id")
        tests_by_user[uid] = tests_by_user.get(uid, 0) + 1
        scores_by_user.setdefault(uid, []).append(float(a.get("score") or 0))

    sessions_by_user = {}
    for s in sessions:
        uid = s.get("user_id")
        sessions_by_user[uid] = sessions_by_user.get(uid, 0) + 1

    def _user_row(u):
        uid = u.get("id")
        up = progress.get(uid, {})
        scores = scores_by_user.get(uid, [])
        avg_score = round(sum(scores) / len(scores), 2) if scores else float(up.get("avg_score") or 0)
        return {
            "id": uid,
            "name": u.get("name"),
            "email": u.get("email"),
            "materials": materials_by_user.get(uid, 0),
            "tests": tests_by_user.get(uid, 0),
            "avg_score": avg_score,
            "chat_sessions": sessions_by_user.get(uid, 0),
            "last_activity": _to_iso(last_activity.get(uid)),
        }

    rows = [_user_row(u) for u in users]
    top_materials = sorted(rows, key=lambda r: r["materials"], reverse=True)[:10]
    top_tests = sorted(rows, key=lambda r: r["tests"], reverse=True)[:10]
    top_scores = sorted(rows, key=lambda r: r["avg_score"], reverse=True)[:10]

    inactive_7 = [r for r in rows if not r["last_activity"] or _as_dt(r["last_activity"]) < _days_ago(7)]
    inactive_30 = [r for r in rows if not r["last_activity"] or _as_dt(r["last_activity"]) < _days_ago(30)]

    return {
        "top_materials": top_materials,
        "top_tests": top_tests,
        "top_scores": top_scores,
        "inactive_7d": inactive_7[:10],
        "inactive_30d": inactive_30[:10],
    }


@admin_bp.route("/users/insights", methods=["GET"])
def user_insights():
    try:
        _require_admin()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        db = get_db()
        start = _parse_date(request.args.get("start_date"))
        end = _parse_date(request.args.get("end_date"))
        return jsonify(_user_insights_data(db, start, end)), 200
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@admin_bp.route("/export/<string:kind>", methods=["GET"])
def export(kind):
    try:
        _require_admin()
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        db = get_db()
        start = _parse_date(request.args.get("start_date"))
        end = _parse_date(request.args.get("end_date"))
        selected_ids = [s for s in (request.args.get("user_ids") or "").split(",") if s.strip().isdigit()]
        selected_ids = {int(s) for s in selected_ids} if selected_ids else set()

        output = io.StringIO()
        writer = csv.writer(output)

        if kind == "users":
            writer.writerow(["id", "name", "email", "role", "is_active", "created_at", "last_login"])
            for u in db.collection("users").get():
                row = u.to_dict() or {}
                if selected_ids and int(row.get("id") or 0) not in selected_ids:
                    continue
                if not _in_date_range(row.get("created_at"), start, end):
                    continue
                writer.writerow([
                    row.get("id"),
                    row.get("name"),
                    row.get("email"),
                    row.get("role"),
                    row.get("is_active", True),
                    _to_iso(row.get("created_at")),
                    _to_iso(row.get("last_login")),
                ])
        elif kind == "materials":
            writer.writerow(["id", "title", "subject", "user_id", "file_type", "file_size", "created_at"])
            for m in db.collection("study_materials").get():
                row = m.to_dict() or {}
                if not _in_date_range(row.get("created_at"), start, end):
                    continue
                writer.writerow([
                    row.get("id"),
                    row.get("title"),
                    row.get("subject"),
                    row.get("user_id"),
                    row.get("file_type"),
                    row.get("file_size"),
                    _to_iso(row.get("created_at")),
                ])
        elif kind == "tests":
            writer.writerow(["attempt_id", "test_id", "user_id", "score", "correct", "total", "time_taken", "completed_at"])
            for a in db.collection("test_attempts").get():
                row = a.to_dict() or {}
                if not _in_date_range(row.get("completed_at"), start, end):
                    continue
                writer.writerow([
                    row.get("id"),
                    row.get("test_id"),
                    row.get("user_id"),
                    row.get("score"),
                    row.get("correct_answers"),
                    row.get("total_questions"),
                    row.get("time_taken"),
                    _to_iso(row.get("completed_at")),
                ])
        elif kind == "subjects":
            rows = _subject_analytics_rows(db)
            writer.writerow(["user_id", "user_name", "subject", "materials", "attempts", "avg_score"])
            for r in rows:
                writer.writerow([r.get("user_id"), r.get("user_name"), r.get("subject_name"), r.get("materials"), r.get("attempts"), r.get("avg_score")])
        elif kind == "insights":
            insights = _user_insights_data(db, start, end)
            writer.writerow(["segment", "user_id", "name", "email", "materials", "tests", "avg_score", "chat_sessions", "last_activity"])
            for seg in ["top_materials", "top_tests", "top_scores", "inactive_7d", "inactive_30d"]:
                for r in insights.get(seg, []):
                    writer.writerow([
                        seg,
                        r.get("id"),
                        r.get("name"),
                        r.get("email"),
                        r.get("materials"),
                        r.get("tests"),
                        r.get("avg_score"),
                        r.get("chat_sessions"),
                        r.get("last_activity"),
                    ])
        else:
            return jsonify({"error": "Invalid export kind"}), 400

        csv_data = output.getvalue()
        output.close()
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=studybot_{kind}.csv"},
        )
    except Exception as e:
        return jsonify({"error": f"Export error: {e}"}), 500
