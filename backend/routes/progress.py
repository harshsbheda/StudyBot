from flask import Blueprint, jsonify
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
from database.db import get_db

progress_bp = Blueprint("progress", __name__)


def _jwt_user():
    verify_jwt_in_request()
    return int(get_jwt_identity())


def _safe_close(cur, db):
    try:
        cur.close()
        db.close()
    except Exception:
        pass


@progress_bp.route("/", methods=["GET"])
def get_progress():
    try:
        user_id = _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        db  = get_db()
        cur = db.cursor(dictionary=True)

        cur.execute(
            "SELECT * FROM user_progress WHERE user_id=%s",
            (user_id,)
        )
        prog = cur.fetchone()

        if prog:
            if prog.get("last_study_date"):
                prog["last_study_date"] = str(prog["last_study_date"])
            if prog.get("avg_score"):
                prog["avg_score"] = float(prog["avg_score"])

        # Recent 5 tests
        cur.execute(
            """SELECT ta.score, ta.completed_at, t.title, t.test_type
               FROM test_attempts ta
               JOIN tests t ON ta.test_id = t.id
               WHERE ta.user_id=%s
               ORDER BY ta.completed_at DESC LIMIT 5""",
            (user_id,)
        )
        recent = cur.fetchall()
        _safe_close(cur, db)

        for r in recent:
            if r.get("completed_at"):
                r["completed_at"] = str(r["completed_at"])
            if r.get("score"):
                r["score"] = float(r["score"])

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
        db  = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute(
            """SELECT ta.id, t.title, t.test_type, ta.score,
                      ta.correct_answers, ta.total_questions,
                      ta.time_taken, ta.completed_at
               FROM test_attempts ta
               JOIN tests t ON ta.test_id = t.id
               WHERE ta.user_id=%s
               ORDER BY ta.completed_at DESC""",
            (user_id,)
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
