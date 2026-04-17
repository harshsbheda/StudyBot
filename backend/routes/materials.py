from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

import json
import os
import uuid
from datetime import datetime

import config
from database.db import get_db, get_next_id, ensure_user_progress, increment_user_progress, utcnow
from services.ai_service import get_key_topics
from services.file_processor import extract_text, get_file_type

materials_bp = Blueprint("materials", __name__)


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


def _get_subject(db, user_id: int, subject_id: int):
    doc = db.collection("subjects").document(str(subject_id)).get()
    if not doc.exists:
        return None
    data = doc.to_dict() or {}
    if data.get("user_id") != user_id:
        return None
    return data


def _resolve_subject(db, user_id: int, subject_id=None, subject_name: str = ""):
    if subject_id:
        sid = int(subject_id)
        subject = _get_subject(db, user_id, sid)
        if not subject:
            raise ValueError("Subject not found")
        return subject["id"], subject["name"]

    sname = (subject_name or "").strip() or "General"
    existing = (
        db.collection("subjects")
        .where("user_id", "==", user_id)
        .where("name", "==", sname)
        .limit(1)
        .get()
    )
    if existing:
        subject = existing[0].to_dict()
        return subject["id"], subject["name"]

    sid = get_next_id("subjects")
    db.collection("subjects").document(str(sid)).set(
        {"id": sid, "user_id": user_id, "name": sname, "created_at": utcnow()}
    )
    return sid, sname


@materials_bp.route("/subjects", methods=["GET"])
def list_subjects():
    try:
        user_id = _jwt_user()
        db = get_db()

        subjects = (
            db.collection("subjects")
            .where("user_id", "==", user_id)
            .get()
        )

        materials = (
            db.collection("study_materials").where("user_id", "==", user_id).get()
        )
        counts = {}
        for m in materials:
            data = m.to_dict() or {}
            sid = data.get("subject_id")
            if sid is not None:
                counts[sid] = counts.get(sid, 0) + 1

        subjects_list = [doc.to_dict() or {} for doc in subjects]
        subjects_list = _sort_by_dt(subjects_list, "created_at", reverse=True)
        rows = []
        for s in subjects_list:
            s["created_at"] = _to_iso(s.get("created_at"))
            s["material_count"] = counts.get(s.get("id"), 0)
            rows.append(s)

        return jsonify(rows), 200
    except Exception as e:
        return jsonify({"error": f"Error: {e}"}), 500


@materials_bp.route("/subjects", methods=["POST"])
def create_subject():
    try:
        user_id = _jwt_user()
        data = request.get_json(force=True)
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Subject name is required"}), 400

        db = get_db()
        existing = (
            db.collection("subjects")
            .where("user_id", "==", user_id)
            .where("name", "==", name)
            .limit(1)
            .get()
        )
        if existing:
            return jsonify({"error": "Subject already exists"}), 409

        sid = get_next_id("subjects")
        db.collection("subjects").document(str(sid)).set(
            {"id": sid, "user_id": user_id, "name": name, "created_at": utcnow()}
        )
        return jsonify({"id": sid, "name": name}), 201
    except Exception as e:
        return jsonify({"error": f"Error: {e}"}), 500


@materials_bp.route("/subjects/<int:sid>", methods=["PUT"])
def rename_subject(sid):
    try:
        user_id = _jwt_user()
        data = request.get_json(force=True)
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Subject name is required"}), 400

        db = get_db()
        subject = _get_subject(db, user_id, sid)
        if not subject:
            return jsonify({"error": "Subject not found"}), 404

        db.collection("subjects").document(str(sid)).set({"name": name}, merge=True)

        mats = (
            db.collection("study_materials")
            .where("user_id", "==", user_id)
            .where("subject_id", "==", sid)
            .get()
        )
        batch = db.batch()
        for doc in mats:
            batch.set(doc.reference, {"subject": name}, merge=True)
        batch.commit()

        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": f"Error: {e}"}), 500


@materials_bp.route("/subjects/<int:sid>", methods=["DELETE"])
def delete_subject(sid):
    try:
        user_id = _jwt_user()
        db = get_db()
        subject = _get_subject(db, user_id, sid)
        if not subject:
            return jsonify({"error": "Subject not found"}), 404

        mats = (
            db.collection("study_materials")
            .where("user_id", "==", user_id)
            .where("subject_id", "==", sid)
            .get()
        )
        for doc in mats:
            data = doc.to_dict() or {}
            fpath = data.get("file_path")
            try:
                if fpath and os.path.exists(fpath):
                    os.remove(fpath)
            except Exception:
                pass
            doc.reference.delete()

        db.collection("subjects").document(str(sid)).delete()

        remaining = (
            db.collection("study_materials").where("user_id", "==", user_id).get()
        )
        ensure_user_progress(db, user_id)
        db.collection("user_progress").document(str(user_id)).set(
            {"materials_uploaded": len(remaining)}, merge=True
        )

        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": f"Error: {e}"}), 500


@materials_bp.route("/subjects/<int:sid>/materials", methods=["GET"])
def materials_by_subject(sid):
    try:
        user_id = _jwt_user()
        db = get_db()
        if not _get_subject(db, user_id, sid):
            return jsonify({"error": "Subject not found"}), 404

        mats = (
            db.collection("study_materials")
            .where("user_id", "==", user_id)
            .where("subject_id", "==", sid)
            .get()
        )
        mats_list = [doc.to_dict() or {} for doc in mats]
        mats_list = _sort_by_dt(mats_list, "created_at", reverse=True)
        rows = []
        for m in mats_list:
            m["created_at"] = _to_iso(m.get("created_at"))
            rows.append(m)
        return jsonify(rows), 200
    except Exception as e:
        return jsonify({"error": f"Error: {e}"}), 500


@materials_bp.route("/subjects/<int:sid>/topics", methods=["GET"])
def subject_topics(sid):
    try:
        user_id = _jwt_user()
        db = get_db()
        if not _get_subject(db, user_id, sid):
            return jsonify({"error": "Subject not found"}), 404

        mats = (
            db.collection("study_materials")
            .where("user_id", "==", user_id)
            .where("subject_id", "==", sid)
            .get()
        )

        merged_topics = []
        combined_text = []
        for doc in mats:
            row = doc.to_dict() or {}
            kt = row.get("key_topics") or []
            if isinstance(kt, str):
                try:
                    kt = json.loads(kt)
                except Exception:
                    kt = []
            for item in kt:
                if item not in merged_topics:
                    merged_topics.append(item)
            txt = row.get("extracted_text") or ""
            if txt.strip():
                combined_text.append(txt)

        if not merged_topics and combined_text:
            merged_topics = get_key_topics("\n".join(combined_text)[:12000])

        return jsonify({"key_topics": merged_topics}), 200
    except Exception as e:
        return jsonify({"error": f"Error: {e}"}), 500


@materials_bp.route("/upload", methods=["POST"])
def upload():
    try:
        user_id = _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    file = request.files.get("file")
    title = (request.form.get("title") or "Untitled").strip()
    subject_name = (request.form.get("subject") or "").strip()
    subject_id = request.form.get("subject_id")

    if not file or file.filename == "":
        return jsonify({"error": "No file provided"}), 400
    if not title:
        return jsonify({"error": "Title is required"}), 400

    try:
        db = get_db()
        sid, sname = _resolve_subject(db, user_id, subject_id, subject_name)

        ftype = get_file_type(file.filename)
        fname = f"{uuid.uuid4()}_{file.filename}"
        save_dir = os.path.join(config.UPLOAD_FOLDER, str(user_id), str(sid))
        os.makedirs(save_dir, exist_ok=True)
        path = os.path.join(save_dir, fname)
        file.save(path)

        try:
            text = extract_text(path, ftype)
        except Exception:
            text = ""

        try:
            topics = get_key_topics(text) if text and len(text) > 50 else []
        except Exception:
            topics = []

        mid = get_next_id("study_materials")
        db.collection("study_materials").document(str(mid)).set(
            {
                "id": mid,
                "user_id": user_id,
                "subject_id": sid,
                "title": title,
                "subject": sname,
                "filename": file.filename,
                "file_path": path,
                "file_type": ftype,
                "file_size": os.path.getsize(path),
                "extracted_text": text,
                "key_topics": topics,
                "created_at": utcnow(),
            }
        )

        ensure_user_progress(db, user_id)
        increment_user_progress(db, user_id, "materials_uploaded", 1)

        return (
            jsonify(
                {
                    "id": mid,
                    "subject_id": sid,
                    "title": title,
                    "subject": sname,
                    "file_type": ftype,
                    "key_topics": topics,
                    "message": "Material uploaded and processed successfully",
                }
            ),
            201,
        )
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@materials_bp.route("/upload-multiple", methods=["POST"])
def upload_multiple():
    try:
        user_id = _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    subject_id = request.form.get("subject_id")
    subject_name = (request.form.get("subject") or "").strip()
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files provided"}), 400

    try:
        db = get_db()
        sid, sname = _resolve_subject(db, user_id, subject_id, subject_name)

        created = []
        for file in files:
            if not file or not file.filename:
                continue

            title = file.filename
            ftype = get_file_type(file.filename)
            fname = f"{uuid.uuid4()}_{file.filename}"
            save_dir = os.path.join(config.UPLOAD_FOLDER, str(user_id), str(sid))
            os.makedirs(save_dir, exist_ok=True)
            path = os.path.join(save_dir, fname)
            file.save(path)

            try:
                text = extract_text(path, ftype)
            except Exception:
                text = ""

            try:
                topics = get_key_topics(text) if text and len(text) > 50 else []
            except Exception:
                topics = []

            mid = get_next_id("study_materials")
            db.collection("study_materials").document(str(mid)).set(
                {
                    "id": mid,
                    "user_id": user_id,
                    "subject_id": sid,
                    "title": title,
                    "subject": sname,
                    "filename": file.filename,
                    "file_path": path,
                    "file_type": ftype,
                    "file_size": os.path.getsize(path),
                    "extracted_text": text,
                    "key_topics": topics,
                    "created_at": utcnow(),
                }
            )
            created.append({"id": mid, "title": title})

        ensure_user_progress(db, user_id)
        increment_user_progress(db, user_id, "materials_uploaded", len(created))

        return (
            jsonify({"success": True, "subject_id": sid, "created": created, "count": len(created)}),
            201,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@materials_bp.route("/list", methods=["GET"])
def list_materials():
    try:
        user_id = _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        db = get_db()
        mats = (
            db.collection("study_materials")
            .where("user_id", "==", user_id)
            .get()
        )
        mats_list = [doc.to_dict() or {} for doc in mats]
        mats_list = _sort_by_dt(mats_list, "created_at", reverse=True)
        rows = []
        for m in mats_list:
            m["created_at"] = _to_iso(m.get("created_at"))
            rows.append(m)
        return jsonify(rows), 200
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@materials_bp.route("/<int:mid>", methods=["GET"])
def get_material(mid):
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
        mat["created_at"] = _to_iso(mat.get("created_at"))
        return jsonify(mat), 200
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@materials_bp.route("/<int:mid>/topics", methods=["GET"])
def get_topics(mid):
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
        return jsonify({"key_topics": mat.get("key_topics") or []}), 200
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@materials_bp.route("/<int:mid>", methods=["DELETE"])
def delete_material(mid):
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

        try:
            if mat.get("file_path") and os.path.exists(mat["file_path"]):
                os.remove(mat["file_path"])
        except Exception:
            pass

        doc.reference.delete()
        remaining = (
            db.collection("study_materials").where("user_id", "==", user_id).get()
        )
        ensure_user_progress(db, user_id)
        db.collection("user_progress").document(str(user_id)).set(
            {"materials_uploaded": len(remaining)}, merge=True
        )

        return jsonify({"success": True, "message": "Material deleted"}), 200
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@materials_bp.route("/<int:mid>", methods=["PUT"])
def update_material(mid):
    try:
        user_id = _jwt_user()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        data = request.get_json(force=True)
        new_title = (data.get("title") or "").strip()
        new_subject_id = data.get("subject_id")
        new_subject_name = (data.get("subject") or "").strip()

        db = get_db()
        doc = db.collection("study_materials").document(str(mid)).get()
        if not doc.exists:
            return jsonify({"error": "Material not found"}), 404
        mat = doc.to_dict() or {}
        if mat.get("user_id") != user_id:
            return jsonify({"error": "Material not found"}), 404

        update_title = new_title if new_title else mat.get("title")
        update_sid = mat.get("subject_id")
        update_sname = mat.get("subject") or "General"

        if new_subject_id or new_subject_name:
            update_sid, update_sname = _resolve_subject(db, user_id, new_subject_id, new_subject_name)

        db.collection("study_materials").document(str(mid)).set(
            {"title": update_title, "subject_id": update_sid, "subject": update_sname}, merge=True
        )
        return jsonify({"success": True}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500
