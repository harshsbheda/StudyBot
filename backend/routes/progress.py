from flask import Blueprint, jsonify
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity

from datetime import datetime

from database.db import get_db, ensure_user_progress

progress_bp = Blueprint("progress", __name__)


def _jwt_user():
    verify_jwt_in_request()
    return int(get_jwt_identity())


def _to_iso(dt):
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt) if dt else None


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


@progress_bp.route("/", methods=["GET"])
def get_progress():
    try:
        user_id = _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        db = get_db()
        ensure_user_progress(db, user_id)
        prog_doc = db.collection("user_progress").document(str(user_id)).get()
        prog = prog_doc.to_dict() if prog_doc.exists else {}

        if prog.get("last_study_date"):
            prog["last_study_date"] = str(prog["last_study_date"])
        if prog.get("avg_score") is not None:
            prog["avg_score"] = float(prog["avg_score"] or 0)

        recent_attempts = (
            db.collection("test_attempts")
            .where("user_id", "==", user_id)
            .get()
        )
        attempts_list = [a.to_dict() for a in recent_attempts]
        attempts_list = _sort_by_dt(attempts_list, "completed_at", reverse=True)[:5]
        test_ids = {a.get("test_id") for a in attempts_list}
        tests = {}
        if test_ids:
            refs = [db.collection("tests").document(str(tid)) for tid in test_ids]
            for doc in db.get_all(refs):
                if doc.exists:
                    tests[int(doc.id)] = doc.to_dict()

        recent = []
        for a in attempts_list:
            t = tests.get(a.get("test_id"), {})
            recent.append(
                {
                    "score": float(a.get("score") or 0),
                    "completed_at": _to_iso(a.get("completed_at")),
                    "title": t.get("title"),
                    "test_type": t.get("test_type"),
                }
            )

        return jsonify({"progress": prog or {}, "recent_tests": recent}), 200
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@progress_bp.route("/scorecard", methods=["GET"])
def scorecard():
    try:
        user_id = _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        db = get_db()
        attempts = (
            db.collection("test_attempts")
            .where("user_id", "==", user_id)
            .get()
        )
        attempts_list = [a.to_dict() for a in attempts]
        attempts_list = _sort_by_dt(attempts_list, "completed_at", reverse=True)
        test_ids = {a.get("test_id") for a in attempts_list}
        tests = {}
        if test_ids:
            refs = [db.collection("tests").document(str(tid)) for tid in test_ids]
            for doc in db.get_all(refs):
                if doc.exists:
                    tests[int(doc.id)] = doc.to_dict()

        rows = []
        for a in attempts_list:
            t = tests.get(a.get("test_id"), {})
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
                }
            )

        return jsonify(rows), 200
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500
