from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

from datetime import datetime

import config
from database.db import get_db, get_next_id, ensure_user_progress, increment_user_progress, utcnow
from services.ai_service import (
    answer_from_material,
    generate_important_questions,
    get_last_error,
    get_ai_settings,
    set_ai_settings,
    summarize_session,
)
from services.ai_guardrails import check_and_record_request, record_quota_hit
import traceback

chat_bp = Blueprint("chat", __name__)


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


def _combined_subject_text(db, user_id: int, subject_id: int) -> str:
    mats = (
        db.collection("study_materials")
        .where("user_id", "==", user_id)
        .where("subject_id", "==", subject_id)
        .get()
    )
    mats_list = [doc.to_dict() or {} for doc in mats]
    mats_list = _sort_by_dt(mats_list, "created_at", reverse=True)
    chunks = []
    for row in mats_list:
        txt = (row.get("extracted_text") or "")
        txt = txt.strip()
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
        if material_id is not None:
            try:
                material_id = int(material_id)
            except Exception:
                material_id = None
        if subject_id is not None:
            try:
                subject_id = int(subject_id)
            except Exception:
                subject_id = None
        if session_id is not None:
            try:
                session_id = int(session_id)
            except Exception:
                session_id = None
        ai_options = _request_ai_options(data)

        if not text:
            return jsonify({"error": "Empty message"}), 400

        db = get_db()

        session_summary = ""
        message_count = 0
        if not session_id:
            session_name = text[:60] + "..." if len(text) > 60 else text
            session_id = get_next_id("chat_sessions")
            db.collection("chat_sessions").document(str(session_id)).set(
                {
                    "id": session_id,
                    "user_id": user_id,
                    "material_id": material_id,
                    "session_name": session_name,
                    "created_at": utcnow(),
                    "updated_at": utcnow(),
                    "summary": "",
                    "message_count": 0,
                }
            )
            ensure_user_progress(db, user_id)
            increment_user_progress(db, user_id, "chat_sessions", 1)
        else:
            sess_doc = db.collection("chat_sessions").document(str(session_id)).get()
            if sess_doc.exists:
                sess = sess_doc.to_dict() or {}
                if sess.get("user_id") == user_id:
                    session_summary = sess.get("summary") or ""
                    message_count = int(sess.get("message_count") or 0)

        history_docs = (
            db.collection("chat_messages")
            .where("session_id", "==", session_id)
            .get()
        )
        history_list = [d.to_dict() for d in history_docs]
        history = _sort_by_dt(history_list, "created_at", reverse=False)[:10]

        material_text = ""
        if material_id:
            mat_doc = db.collection("study_materials").document(str(material_id)).get()
            if mat_doc.exists:
                mat = mat_doc.to_dict() or {}
                if mat.get("user_id") == user_id:
                    material_text = mat.get("extracted_text") or ""
        elif subject_id:
            material_text = _combined_subject_text(db, user_id, int(subject_id))

        user_msg_id = get_next_id("chat_messages")
        db.collection("chat_messages").document(str(user_msg_id)).set(
            {
                "id": user_msg_id,
                "session_id": session_id,
                "role": "user",
                "content": text,
                "source": "user",
                "created_at": utcnow(),
            }
        )

        guardrail = check_and_record_request(user_id, action="chat_message")
        if not guardrail.get("allowed"):
            result = {
                "answer": _fallback_message_for_guardrail(
                    guardrail.get("reason", ""), guardrail.get("message", "AI is unavailable.")
                ),
                "source": "guardrail",
                "confidence": "low",
                "citations": [],
                "model_info": None,
            }
        elif material_text.strip():
            result = answer_from_material(
                text,
                material_text,
                history,
                ai_options=ai_options,
                session_summary=session_summary,
            )
        else:
            result = {
                "answer": "Please select a subject (or material) first. I answer based only on your uploaded content.",
                "source": "ai",
                "confidence": "low",
                "citations": [],
                "model_info": None,
            }

        if result.get("source") == "ai_unavailable":
            ai_error = get_last_error()
            if ai_error.get("code") == "quota_exceeded":
                retry_after = ai_error.get("retry_after", 0)
                record_quota_hit(user_id, retry_after=retry_after)
                msg = "AI provider quota exceeded. Please retry shortly."
                if retry_after:
                    msg = f"{msg} Retry after about {retry_after}s."
                result = {"answer": msg, "source": "quota_exceeded", "confidence": "low", "citations": [], "model_info": None}

        ai_msg_id = get_next_id("chat_messages")
        db.collection("chat_messages").document(str(ai_msg_id)).set(
            {
                "id": ai_msg_id,
                "session_id": session_id,
                "role": "assistant",
                "content": result["answer"],
                "source": result["source"],
                "confidence": result.get("confidence"),
                "citations": result.get("citations") or [],
                "model_info": result.get("model_info"),
                "created_at": utcnow(),
            }
        )
        message_count = message_count + 2
        should_summarize = message_count % 6 == 0
        if should_summarize:
            recent_for_summary = (history_list[-6:] if history_list else []) + [
                {"role": "user", "content": text},
                {"role": "assistant", "content": result["answer"]},
            ]
            session_summary = summarize_session(session_summary, recent_for_summary, ai_options=ai_options)

        db.collection("chat_sessions").document(str(session_id)).set(
            {"updated_at": utcnow(), "summary": session_summary, "message_count": message_count},
            merge=True,
        )

        return jsonify(
            {
                "answer": result["answer"],
                "source": result["source"],
                "confidence": result.get("confidence"),
                "citations": result.get("citations") or [],
                "model_info": result.get("model_info"),
                "session_id": session_id,
            }
        ), 200
    except Exception as e:
        traceback.print_exc()
        if config.DEBUG:
            return jsonify({"error": f"Chat error: {e}"}), 500
        return jsonify({"error": "Chat error"}), 500


@chat_bp.route("/sessions", methods=["GET"])
def sessions():
    try:
        user_id = _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        db = get_db()
        sessions_docs = (
            db.collection("chat_sessions")
            .where("user_id", "==", user_id)
            .get()
        )
        sessions_list = [d.to_dict() for d in sessions_docs]
        sessions_list = _sort_by_dt(sessions_list, "updated_at", reverse=True)[:20]

        material_ids = {s.get("material_id") for s in sessions_list if s.get("material_id")}
        materials = {}
        if material_ids:
            refs = [db.collection("study_materials").document(str(mid)) for mid in material_ids]
            for doc in db.get_all(refs):
                if doc.exists:
                    materials[int(doc.id)] = doc.to_dict()

        rows = []
        for s in sessions_list:
            row = {
                "id": s.get("id"),
                "session_name": s.get("session_name"),
                "updated_at": _to_iso(s.get("updated_at")),
                "material_title": None,
            }
            mid = s.get("material_id")
            if mid and mid in materials:
                row["material_title"] = materials[mid].get("title")
            rows.append(row)

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
        sess_doc = db.collection("chat_sessions").document(str(sid)).get()
        if not sess_doc.exists:
            return jsonify({"error": "Session not found"}), 404
        sess = sess_doc.to_dict() or {}
        if sess.get("user_id") != user_id:
            return jsonify({"error": "Session not found"}), 404

        msgs = (
            db.collection("chat_messages")
            .where("session_id", "==", sid)
            .get()
        )
        msgs_list = [d.to_dict() or {} for d in msgs]
        msgs_list = _sort_by_dt(msgs_list, "created_at", reverse=False)
        rows = []
        for r in msgs_list:
            r["created_at"] = _to_iso(r.get("created_at"))
            rows.append(r)

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
        doc = db.collection("study_materials").document(str(mid)).get()
        if not doc.exists:
            return jsonify({"error": "Material not found"}), 404
        mat = doc.to_dict() or {}
        if mat.get("user_id") != user_id:
            return jsonify({"error": "Material not found"}), 404

        text = mat.get("extracted_text") or ""
        if not text.strip():
            return jsonify({"questions": [], "message": "No text found in material"}), 200

        ai_options = _request_ai_options(
            {"ai_provider": request.args.get("ai_provider", ""), "ai_model": request.args.get("ai_model", "")}
        )
        guardrail = check_and_record_request(user_id, action="important_questions_material")
        if not guardrail.get("allowed"):
            return (
                jsonify(
                    {
                        "error": guardrail.get("message", "AI temporarily unavailable"),
                        "retry_after": guardrail.get("retry_after", 0),
                    }
                ),
                429,
            )

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
        text = _combined_subject_text(db, user_id, sid)
        if not text.strip():
            return jsonify({"questions": [], "message": "No text found in subject"}), 200

        ai_options = _request_ai_options(
            {"ai_provider": request.args.get("ai_provider", ""), "ai_model": request.args.get("ai_model", "")}
        )
        guardrail = check_and_record_request(user_id, action="important_questions_subject")
        if not guardrail.get("allowed"):
            return (
                jsonify(
                    {
                        "error": guardrail.get("message", "AI temporarily unavailable"),
                        "retry_after": guardrail.get("retry_after", 0),
                    }
                ),
                429,
            )

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
