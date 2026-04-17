from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

from datetime import datetime

from database.db import get_db, get_next_id, ensure_user_progress, set_user_progress, utcnow
from services.ai_guardrails import check_and_record_request, record_quota_hit
from services.ai_service import evaluate_short_answer, generate_mcq_test, generate_short_answer_test, get_last_error

tests_bp = Blueprint("tests", __name__)


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


def _get_grade(score: float) -> str:
    if score >= 90:
        return "A+"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    if score >= 50:
        return "D"
    return "F"


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


@tests_bp.route("/generate", methods=["POST"])
def generate():
    try:
        user_id = _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        data = request.get_json(force=True)
        material_id = data.get("material_id")
        subject_id = data.get("subject_id")
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
        ttype = data.get("type", "mcq")
        count = int(data.get("count", 10))
        diff = data.get("difficulty", "medium")
        ai_options = _request_ai_options(data)

        if not material_id and not subject_id:
            return jsonify({"error": "material_id or subject_id is required"}), 400

        db = get_db()

        source_title = "Selected Content"
        text = ""
        material_for_test = None

        if material_id:
            mat_doc = db.collection("study_materials").document(str(material_id)).get()
            if not mat_doc.exists:
                return jsonify({"error": "Material not found"}), 404
            mat = mat_doc.to_dict() or {}
            if mat.get("user_id") != user_id:
                return jsonify({"error": "Material not found"}), 404
            text = mat.get("extracted_text") or ""
            source_title = mat.get("title") or source_title
            material_for_test = mat.get("id")
        else:
            text, material_for_test = _combined_subject_text(db, user_id, int(subject_id))
            subj = db.collection("subjects").document(str(int(subject_id))).get()
            if subj.exists and (subj.to_dict() or {}).get("user_id") == user_id:
                source_title = (subj.to_dict() or {}).get("name") or source_title

        if not material_for_test:
            return jsonify({"error": "No materials available in selected subject"}), 400
        if not text.strip():
            return jsonify({"error": "No text content found in selected source"}), 400

        guardrail = check_and_record_request(user_id, action="test_generate")
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

        if ttype == "mcq":
            questions = generate_mcq_test(text, count, diff, ai_options=ai_options)
        elif ttype == "short_answer":
            questions = generate_short_answer_test(text, count, ai_options=ai_options)
        else:
            half = max(count // 2, 1)
            questions = generate_mcq_test(text, half, diff, ai_options=ai_options) + generate_short_answer_test(
                text, half, ai_options=ai_options
            )

        if not questions:
            ai_error = get_last_error()
            if ai_error.get("code") == "quota_exceeded":
                retry_after = ai_error.get("retry_after", 0)
                record_quota_hit(user_id, retry_after=retry_after)
                return jsonify({"error": "AI quota exceeded. Please try again later.", "retry_after": retry_after}), 429
            if ai_error:
                return jsonify({"error": "AI is temporarily unavailable. Please retry shortly.", "reason": ai_error.get("code", "provider_error")}), 503
            return jsonify({"error": "Could not generate questions. Try again or check AI settings."}), 500

        title = f"{source_title} - {ttype.replace('_', ' ').title()} Test"
        test_id = get_next_id("tests")
        db.collection("tests").document(str(test_id)).set(
            {
                "id": test_id,
                "user_id": user_id,
                "material_id": material_for_test,
                "title": title,
                "test_type": ttype,
                "questions": questions,
                "difficulty": diff,
                "created_at": utcnow(),
            }
        )

        return jsonify({"test_id": test_id, "title": title, "questions": questions, "count": len(questions)}), 201
    except Exception as e:
        return jsonify({"error": f"Generation error: {e}"}), 500


@tests_bp.route("/<int:test_id>/submit", methods=["POST"])
def submit(test_id):
    try:
        user_id = _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        data = request.get_json(force=True)
        answers = data.get("answers", {})
        time_taken = int(data.get("time_taken", 0))

        db = get_db()
        test_doc = db.collection("tests").document(str(test_id)).get()
        if not test_doc.exists:
            return jsonify({"error": "Test not found"}), 404
        test = test_doc.to_dict() or {}

        questions = test.get("questions") or []
        feedback = []
        correct = 0
        total = len(questions)

        for i, q in enumerate(questions):
            student_ans = str(answers.get(str(i), "")).strip()
            if "options" in q:
                is_correct = student_ans.upper() == str(q.get("correct", "")).upper()
                if is_correct:
                    correct += 1
                feedback.append(
                    {
                        "type": "mcq",
                        "question": q["question"],
                        "your_answer": student_ans,
                        "correct_answer": q.get("correct", ""),
                        "correct": is_correct,
                        "explanation": q.get("explanation", ""),
                    }
                )
            else:
                ev = evaluate_short_answer(q["question"], q.get("model_answer", ""), student_ans)
                if ev.get("score", 0) >= 6:
                    correct += 1
                feedback.append(
                    {
                        "type": "short",
                        "question": q["question"],
                        "your_answer": student_ans,
                        "model_answer": q.get("model_answer", ""),
                        "score": ev.get("score", 0),
                        "feedback": ev.get("feedback", ""),
                        "missed": ev.get("missed_points", []),
                    }
                )

        score = round((correct / total * 100), 2) if total > 0 else 0
        grade = _get_grade(score)

        attempt_id = get_next_id("test_attempts")
        db.collection("test_attempts").document(str(attempt_id)).set(
            {
                "id": attempt_id,
                "test_id": test_id,
                "user_id": user_id,
                "title": test.get("title"),
                "test_type": test.get("test_type"),
                "answers": answers,
                "score": score,
                "total_questions": total,
                "correct_answers": correct,
                "time_taken": time_taken,
                "feedback": feedback,
                "completed_at": utcnow(),
            }
        )

        attempts = (
            db.collection("test_attempts").where("user_id", "==", user_id).get()
        )
        avg_score = 0
        if attempts:
            avg_score = round(sum(float(a.to_dict().get("score") or 0) for a in attempts) / len(attempts), 2)

        ensure_user_progress(db, user_id)
        set_user_progress(db, user_id, {"total_tests": len(attempts), "avg_score": avg_score})

        return jsonify({"score": score, "grade": grade, "correct": correct, "total": total, "feedback": feedback}), 200
    except Exception as e:
        return jsonify({"error": f"Submit error: {e}"}), 500


@tests_bp.route("/history", methods=["GET"])
def history():
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
                    "title": t.get("title") or a.get("title") or f"Test Attempt #{a.get('id')}",
                    "score": float(a.get("score") or 0),
                    "total_questions": a.get("total_questions"),
                    "correct_answers": a.get("correct_answers"),
                    "time_taken": a.get("time_taken"),
                    "completed_at": _to_iso(a.get("completed_at")),
                    "test_type": t.get("test_type") or a.get("test_type") or "test",
                }
            )

        return jsonify(rows), 200
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@tests_bp.route("/attempts/<int:attempt_id>", methods=["GET"])
def attempt_detail(attempt_id):
    try:
        user_id = _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        db = get_db()
        attempt_doc = db.collection("test_attempts").document(str(attempt_id)).get()
        if not attempt_doc.exists:
            return jsonify({"error": "Test attempt not found"}), 404

        attempt = attempt_doc.to_dict() or {}
        if attempt.get("user_id") != user_id:
            return jsonify({"error": "Test attempt not found"}), 404

        test = {}
        test_id = attempt.get("test_id")
        if test_id is not None:
            test_doc = db.collection("tests").document(str(test_id)).get()
            if test_doc.exists:
                test = test_doc.to_dict() or {}

        score = float(attempt.get("score") or 0)
        detail = {
            "id": attempt.get("id"),
            "test_id": test_id,
            "title": test.get("title") or attempt.get("title") or "Test Review",
            "test_type": test.get("test_type") or attempt.get("test_type") or "test",
            "score": score,
            "grade": _get_grade(score),
            "total_questions": attempt.get("total_questions") or 0,
            "correct_answers": attempt.get("correct_answers") or 0,
            "time_taken": attempt.get("time_taken") or 0,
            "completed_at": _to_iso(attempt.get("completed_at")),
            "feedback": attempt.get("feedback") or [],
        }
        return jsonify(detail), 200
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500
