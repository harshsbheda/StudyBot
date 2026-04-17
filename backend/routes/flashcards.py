from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

from datetime import datetime

from database.db import get_db, get_next_id, utcnow
from services.ai_guardrails import check_and_record_request, record_quota_hit
from services.ai_service import generate_flashcards, get_last_error

flashcards_bp = Blueprint("flashcards", __name__)


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


def _request_ai_options(data):
    provider = (data.get("ai_provider") or "").strip().lower()
    model = (data.get("ai_model") or "").strip()
    opts = {}
    if provider:
        opts["provider"] = provider
    if model:
        opts["model"] = model
    return opts


def _combined_subject_text(db, user_id: int, subject_id: int):
    mats = (
        db.collection("study_materials")
        .where("user_id", "==", user_id)
        .where("subject_id", "==", subject_id)
        .get()
    )
    mats_list = [doc.to_dict() or {} for doc in mats]
    mats_list = _sort_by_dt(mats_list, "created_at", reverse=True)
    chunks = []
    first_material_id = None
    for row in mats_list:
        if first_material_id is None:
            first_material_id = row.get("id")
        txt = (row.get("extracted_text") or "").strip()
        if txt:
            chunks.append(txt)
    return "\n\n".join(chunks)[:18000], first_material_id


@flashcards_bp.route("/subjects/<int:sid>", methods=["GET"])
def list_flashcards(sid):
    try:
        user_id = _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        db = get_db()
        subject_doc = db.collection("subjects").document(str(sid)).get()
        if not subject_doc.exists or (subject_doc.to_dict() or {}).get("user_id") != user_id:
            return jsonify({"error": "Subject not found"}), 404

        cards = (
            db.collection("flashcards")
            .where("user_id", "==", user_id)
            .where("subject_id", "==", sid)
            .get()
        )
        cards_list = [c.to_dict() or {} for c in cards]
        cards_list = _sort_by_dt(cards_list, "created_at", reverse=True)
        rows = []
        for c in cards_list:
            c["created_at"] = _to_iso(c.get("created_at"))
            c["last_reviewed"] = _to_iso(c.get("last_reviewed"))
            rows.append(c)
        return jsonify(rows), 200
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@flashcards_bp.route("/generate", methods=["POST"])
def generate():
    try:
        user_id = _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        data = request.get_json(force=True)
        material_id = data.get("material_id")
        subject_id = data.get("subject_id")
        count = int(data.get("count", 12))
        ai_options = _request_ai_options(data)

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

        if not material_id and not subject_id:
            return jsonify({"error": "material_id or subject_id is required"}), 400

        db = get_db()
        source_title = "Selected Content"
        text = ""
        material_for_cards = None
        subject_for_cards = None

        if material_id:
            mat_doc = db.collection("study_materials").document(str(material_id)).get()
            if not mat_doc.exists:
                return jsonify({"error": "Material not found"}), 404
            mat = mat_doc.to_dict() or {}
            if mat.get("user_id") != user_id:
                return jsonify({"error": "Material not found"}), 404
            text = mat.get("extracted_text") or ""
            source_title = mat.get("title") or source_title
            material_for_cards = mat.get("id")
            subject_for_cards = mat.get("subject_id")
        else:
            text, material_for_cards = _combined_subject_text(db, user_id, int(subject_id))
            subject_for_cards = int(subject_id)
            subject_doc = db.collection("subjects").document(str(int(subject_id))).get()
            if subject_doc.exists and (subject_doc.to_dict() or {}).get("user_id") == user_id:
                source_title = (subject_doc.to_dict() or {}).get("name") or source_title

        if not material_for_cards or not subject_for_cards:
            return jsonify({"error": "No materials available in selected subject"}), 400
        if not text.strip():
            return jsonify({"error": "No text content found in selected source"}), 400

        guardrail = check_and_record_request(user_id, action="flashcards_generate")
        if not guardrail.get("allowed"):
            return (
                jsonify(
                    {
                        "error": guardrail.get("message", "AI temporarily unavailable"),
                        "retry_after": guardrail.get("retry_after", 0),
                        "reason": guardrail.get("reason", "guardrail"),
                    }
                ),
                429,
            )

        cards = generate_flashcards(text, count=count, ai_options=ai_options)
        if not cards:
            ai_error = get_last_error()
            if ai_error.get("code") == "quota_exceeded":
                retry_after = ai_error.get("retry_after", 0)
                record_quota_hit(user_id, retry_after=retry_after)
                return jsonify({"error": "AI quota exceeded. Please try again later.", "retry_after": retry_after}), 429
            if ai_error:
                return jsonify({"error": "AI is temporarily unavailable. Please retry shortly.", "reason": ai_error.get("code", "provider_error")}), 503
            return jsonify({"error": "Could not generate flashcards. Try again or check AI settings."}), 500

        created = []
        for c in cards:
            cid = get_next_id("flashcards")
            payload = {
                "id": cid,
                "user_id": user_id,
                "subject_id": subject_for_cards,
                "material_id": material_for_cards,
                "question": c.get("question"),
                "answer": c.get("answer"),
                "source_title": source_title,
                "created_at": utcnow(),
                "last_reviewed": None,
                "correct_count": 0,
                "incorrect_count": 0,
            }
            db.collection("flashcards").document(str(cid)).set(payload)
            created.append(payload)

        for c in created:
            c["created_at"] = _to_iso(c.get("created_at"))
            c["last_reviewed"] = _to_iso(c.get("last_reviewed"))

        return jsonify({"count": len(created), "cards": created}), 201
    except Exception as e:
        return jsonify({"error": f"Generation error: {e}"}), 500


@flashcards_bp.route("/<int:card_id>/review", methods=["POST"])
def review(card_id):
    try:
        user_id = _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        data = request.get_json(force=True)
        correct = bool(data.get("correct", False))

        db = get_db()
        doc = db.collection("flashcards").document(str(card_id)).get()
        if not doc.exists:
            return jsonify({"error": "Flashcard not found"}), 404
        card = doc.to_dict() or {}
        if card.get("user_id") != user_id:
            return jsonify({"error": "Flashcard not found"}), 404

        updates = {"last_reviewed": utcnow()}
        if correct:
            updates["correct_count"] = (card.get("correct_count") or 0) + 1
        else:
            updates["incorrect_count"] = (card.get("incorrect_count") or 0) + 1

        doc.reference.set(updates, merge=True)
        card.update(updates)
        card["created_at"] = _to_iso(card.get("created_at"))
        card["last_reviewed"] = _to_iso(card.get("last_reviewed"))
        return jsonify({"success": True, "card": card}), 200
    except Exception as e:
        return jsonify({"error": f"Review error: {e}"}), 500
