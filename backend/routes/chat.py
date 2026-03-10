from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

import config
from database.db import get_db
from services.ai_service import (
    answer_from_material,
    generate_important_questions,
    get_last_error,
    get_ai_settings,
    set_ai_settings,
)
from services.ai_guardrails import check_and_record_request, record_quota_hit

chat_bp = Blueprint("chat", __name__)


def _jwt_user():
    verify_jwt_in_request()
    return int(get_jwt_identity())


def _safe_close(cur, db):
    try:
        cur.close()
        db.close()
    except Exception:
        pass


def _request_ai_options(data: dict) -> dict:
    provider = (data.get("ai_provider") or "").strip().lower()
    model = (data.get("ai_model") or "").strip()
    opts = {}
    if provider:
        opts["provider"] = provider
    if model:
        opts["model"] = model
    return opts


def _fallback_message_for_guardrail(reason: str, message: str) -> str:
    if config.AI_FALLBACK_MODE == "links":
        links = (
            "\n\nFallback resources:\n"
            "- Google: https://google.com\n"
            "- Google Scholar: https://scholar.google.com\n"
            "- Khan Academy: https://khanacademy.org"
        )
    else:
        links = ""

    if reason == "daily_limit":
        return (
            f"{message}\n\n"
            "Fallback mode is active. You can still study uploaded materials, topics, and previous chat history."
            f"{links}"
        )
    if reason == "cooldown":
        return message + links
    if reason == "quota_cooldown":
        return f"{message}\n\nThe AI provider quota is temporarily blocked. Please retry after the cooldown.{links}"
    return message + links


def _combined_subject_text(cur, user_id: int, subject_id: int) -> str:
    cur.execute(
        "SELECT extracted_text FROM study_materials WHERE user_id=%s AND subject_id=%s ORDER BY created_at DESC",
        (user_id, subject_id),
    )
    rows = cur.fetchall()
    chunks = []
    for row in rows:
        txt = (row.get("extracted_text") or "").strip()
        if txt:
            chunks.append(txt)
    return "\n\n".join(chunks)[:18000]


@chat_bp.route("/message", methods=["POST"])
def message():
    try:
        user_id = _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        data = request.get_json(force=True)
        text = (data.get("message") or "").strip()
        material_id = data.get("material_id")
        subject_id = data.get("subject_id")
        session_id = data.get("session_id")
        ai_options = _request_ai_options(data)

        if not text:
            return jsonify({"error": "Empty message"}), 400

        db = get_db()
        cur = db.cursor(dictionary=True)

        if not session_id:
            session_name = text[:60] + "..." if len(text) > 60 else text
            cur.execute(
                "INSERT INTO chat_sessions (user_id, material_id, session_name) VALUES (%s,%s,%s)",
                (user_id, material_id, session_name),
            )
            db.commit()
            session_id = cur.lastrowid
            cur.execute("UPDATE user_progress SET chat_sessions = chat_sessions + 1 WHERE user_id=%s", (user_id,))
            db.commit()

        cur.execute(
            "SELECT role, content FROM chat_messages WHERE session_id=%s ORDER BY created_at ASC LIMIT 10",
            (session_id,),
        )
        history = cur.fetchall()

        material_text = ""
        if material_id:
            cur.execute("SELECT extracted_text FROM study_materials WHERE id=%s AND user_id=%s", (material_id, user_id))
            mat = cur.fetchone()
            if mat:
                material_text = mat.get("extracted_text") or ""
        elif subject_id:
            material_text = _combined_subject_text(cur, user_id, int(subject_id))

        cur.execute(
            "INSERT INTO chat_messages (session_id, role, content, source) VALUES (%s, 'user', %s, 'user')",
            (session_id, text),
        )
        db.commit()

        guardrail = check_and_record_request(user_id, action="chat_message")
        if not guardrail.get("allowed"):
            result = {
                "answer": _fallback_message_for_guardrail(guardrail.get("reason", ""), guardrail.get("message", "AI is unavailable.")),
                "source": "guardrail",
            }
        elif material_text.strip():
            result = answer_from_material(text, material_text, history, ai_options=ai_options)
        else:
            result = {
                "answer": "Please select a subject (or material) first. I answer based only on your uploaded content.",
                "source": "ai",
            }

        if result.get("source") == "ai_unavailable":
            ai_error = get_last_error()
            if ai_error.get("code") == "quota_exceeded":
                retry_after = ai_error.get("retry_after", 0)
                record_quota_hit(user_id, retry_after=retry_after)
                msg = "AI provider quota exceeded. Please retry shortly."
                if retry_after:
                    msg = f"{msg} Retry after about {retry_after}s."
                result = {"answer": msg, "source": "quota_exceeded"}

        cur.execute(
            "INSERT INTO chat_messages (session_id, role, content, source) VALUES (%s, 'assistant', %s, %s)",
            (session_id, result["answer"], result["source"]),
        )
        cur.execute("UPDATE chat_sessions SET updated_at=NOW() WHERE id=%s", (session_id,))
        db.commit()
        _safe_close(cur, db)

        return jsonify({"answer": result["answer"], "source": result["source"], "session_id": session_id}), 200
    except Exception as e:
        return jsonify({"error": f"Chat error: {e}"}), 500


@chat_bp.route("/sessions", methods=["GET"])
def sessions():
    try:
        user_id = _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute(
            """SELECT cs.id, cs.session_name, cs.updated_at, sm.title AS material_title
               FROM chat_sessions cs
               LEFT JOIN study_materials sm ON cs.material_id = sm.id
               WHERE cs.user_id=%s
               ORDER BY cs.updated_at DESC LIMIT 20""",
            (user_id,),
        )
        rows = cur.fetchall()
        _safe_close(cur, db)

        for r in rows:
            if r.get("updated_at"):
                r["updated_at"] = str(r["updated_at"])

        return jsonify(rows), 200
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@chat_bp.route("/sessions/<int:sid>", methods=["GET"])
def session_messages(sid):
    try:
        user_id = _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT id FROM chat_sessions WHERE id=%s AND user_id=%s", (sid, user_id))
        if not cur.fetchone():
            _safe_close(cur, db)
            return jsonify({"error": "Session not found"}), 404

        cur.execute("SELECT role, content, source, created_at FROM chat_messages WHERE session_id=%s ORDER BY created_at ASC", (sid,))
        rows = cur.fetchall()
        _safe_close(cur, db)

        for r in rows:
            if r.get("created_at"):
                r["created_at"] = str(r["created_at"])

        return jsonify(rows), 200
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@chat_bp.route("/important-questions/<int:mid>", methods=["GET"])
def important_questions(mid):
    try:
        user_id = _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT extracted_text FROM study_materials WHERE id=%s AND user_id=%s", (mid, user_id))
        mat = cur.fetchone()
        _safe_close(cur, db)

        if not mat:
            return jsonify({"error": "Material not found"}), 404

        text = mat.get("extracted_text") or ""
        if not text.strip():
            return jsonify({"questions": [], "message": "No text found in material"}), 200

        ai_options = _request_ai_options(
            {
                "ai_provider": request.args.get("ai_provider", ""),
                "ai_model": request.args.get("ai_model", ""),
            }
        )
        guardrail = check_and_record_request(user_id, action="important_questions_material")
        if not guardrail.get("allowed"):
            return jsonify({"error": guardrail.get("message", "AI temporarily unavailable"), "retry_after": guardrail.get("retry_after", 0)}), 429

        questions = generate_important_questions(text, ai_options=ai_options)
        if not questions and get_last_error().get("code") == "quota_exceeded":
            retry_after = get_last_error().get("retry_after", 0)
            record_quota_hit(user_id, retry_after=retry_after)
            return jsonify({"error": "AI quota exceeded. Try again later.", "retry_after": retry_after}), 429
        return jsonify({"questions": questions}), 200
    except Exception as e:
        return jsonify({"error": f"Error: {e}"}), 500


@chat_bp.route("/important-questions-by-subject/<int:sid>", methods=["GET"])
def important_questions_by_subject(sid):
    try:
        user_id = _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        db = get_db()
        cur = db.cursor(dictionary=True)
        text = _combined_subject_text(cur, user_id, sid)
        _safe_close(cur, db)

        if not text.strip():
            return jsonify({"questions": [], "message": "No text found in subject"}), 200

        ai_options = _request_ai_options(
            {
                "ai_provider": request.args.get("ai_provider", ""),
                "ai_model": request.args.get("ai_model", ""),
            }
        )
        guardrail = check_and_record_request(user_id, action="important_questions_subject")
        if not guardrail.get("allowed"):
            return jsonify({"error": guardrail.get("message", "AI temporarily unavailable"), "retry_after": guardrail.get("retry_after", 0)}), 429

        questions = generate_important_questions(text, ai_options=ai_options)
        if not questions and get_last_error().get("code") == "quota_exceeded":
            retry_after = get_last_error().get("retry_after", 0)
            record_quota_hit(user_id, retry_after=retry_after)
            return jsonify({"error": "AI quota exceeded. Try again later.", "retry_after": retry_after}), 429
        return jsonify({"questions": questions}), 200
    except Exception as e:
        return jsonify({"error": f"Error: {e}"}), 500


@chat_bp.route("/ai-settings", methods=["GET"])
def ai_settings():
    try:
        _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401
    return jsonify(get_ai_settings()), 200


@chat_bp.route("/ai-settings", methods=["PUT"])
def update_ai_settings():
    try:
        _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        data = request.get_json(force=True)
        settings = set_ai_settings(provider=data.get("provider"), model=data.get("model"))
        return jsonify(settings), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Update error: {e}"}), 500
