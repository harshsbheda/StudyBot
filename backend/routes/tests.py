from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

import json

from database.db import get_db
from services.ai_guardrails import check_and_record_request, record_quota_hit
from services.ai_service import evaluate_short_answer, generate_mcq_test, generate_short_answer_test, get_last_error

tests_bp = Blueprint("tests", __name__)


def _jwt_user():
    verify_jwt_in_request()
    return int(get_jwt_identity())


def _safe_close(cur, db):
    try:
        cur.close()
        db.close()
    except Exception:
        pass


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


def _combined_subject_text(cur, user_id: int, subject_id: int):
    cur.execute(
        "SELECT id, title, extracted_text FROM study_materials WHERE user_id=%s AND subject_id=%s ORDER BY created_at DESC",
        (user_id, subject_id),
    )
    rows = cur.fetchall()
    chunks = []
    first_material_id = None
    for row in rows:
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
        ttype = data.get("type", "mcq")
        count = int(data.get("count", 10))
        diff = data.get("difficulty", "medium")
        ai_options = _request_ai_options(data)

        if not material_id and not subject_id:
            return jsonify({"error": "material_id or subject_id is required"}), 400

        db = get_db()
        cur = db.cursor(dictionary=True)

        source_title = "Selected Content"
        text = ""
        material_for_test = None

        if material_id:
            cur.execute("SELECT id, title, extracted_text FROM study_materials WHERE id=%s AND user_id=%s", (material_id, user_id))
            mat = cur.fetchone()
            if not mat:
                _safe_close(cur, db)
                return jsonify({"error": "Material not found"}), 404
            text = mat.get("extracted_text") or ""
            source_title = mat.get("title") or source_title
            material_for_test = mat.get("id")
        else:
            text, material_for_test = _combined_subject_text(cur, user_id, int(subject_id))
            cur.execute("SELECT name FROM subjects WHERE id=%s AND user_id=%s", (int(subject_id), user_id))
            s = cur.fetchone()
            source_title = s.get("name") if s else source_title

        if not material_for_test:
            _safe_close(cur, db)
            return jsonify({"error": "No materials available in selected subject"}), 400

        if not text.strip():
            _safe_close(cur, db)
            return jsonify({"error": "No text content found in selected source"}), 400

        guardrail = check_and_record_request(user_id, action="test_generate")
        if not guardrail.get("allowed"):
            _safe_close(cur, db)
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
                _safe_close(cur, db)
                return jsonify({"error": "AI quota exceeded. Please try again later.", "retry_after": retry_after}), 429

            _safe_close(cur, db)
            if ai_error:
                return jsonify({"error": "AI is temporarily unavailable. Please retry shortly.", "reason": ai_error.get("code", "provider_error")}), 503
            return jsonify({"error": "Could not generate questions. Try again or check AI settings."}), 500

        title = f"{source_title} - {ttype.replace('_', ' ').title()} Test"
        cur.execute(
            """INSERT INTO tests (user_id, material_id, title, test_type, questions, difficulty)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (user_id, material_for_test, title, ttype, json.dumps(questions, ensure_ascii=False), diff),
        )
        db.commit()
        test_id = cur.lastrowid
        _safe_close(cur, db)

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
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT * FROM tests WHERE id=%s", (test_id,))
        test = cur.fetchone()
        if not test:
            _safe_close(cur, db)
            return jsonify({"error": "Test not found"}), 404

        questions = json.loads(test["questions"])
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

        cur.execute(
            """INSERT INTO test_attempts
               (test_id, user_id, answers, score, total_questions, correct_answers, time_taken, feedback)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (test_id, user_id, json.dumps(answers, ensure_ascii=False), score, total, correct, time_taken, json.dumps(feedback, ensure_ascii=False)),
        )
        db.commit()

        cur.execute(
            """UPDATE user_progress
               SET total_tests = total_tests + 1,
                   avg_score   = (SELECT IFNULL(AVG(score),0) FROM test_attempts WHERE user_id=%s)
               WHERE user_id=%s""",
            (user_id, user_id),
        )
        db.commit()
        _safe_close(cur, db)

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
        cur = db.cursor(dictionary=True)
        cur.execute(
            """SELECT ta.id, t.title, ta.score, ta.total_questions,
                      ta.correct_answers, ta.time_taken, ta.completed_at, t.test_type
               FROM test_attempts ta
               JOIN tests t ON ta.test_id = t.id
               WHERE ta.user_id=%s
               ORDER BY ta.completed_at DESC""",
            (user_id,),
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
