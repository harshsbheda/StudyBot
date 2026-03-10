from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

import json
import os
import uuid

import config
from database.db import get_db
from services.ai_service import get_key_topics
from services.file_processor import extract_text, get_file_type

materials_bp = Blueprint("materials", __name__)

_schema_ready = False


def _safe_close(cur, db):
    try:
        cur.close()
        db.close()
    except Exception:
        pass


def _jwt_user():
    verify_jwt_in_request()
    return int(get_jwt_identity())


def _ensure_subject_schema():
    global _schema_ready
    if _schema_ready:
        return

    db = get_db()
    cur = db.cursor()
    try:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS subjects (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                name VARCHAR(150) NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uniq_user_subject (user_id, name),
                INDEX idx_subject_user (user_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )"""
        )

        cur.execute("SHOW COLUMNS FROM study_materials LIKE 'subject_id'")
        if not cur.fetchone():
            cur.execute("ALTER TABLE study_materials ADD COLUMN subject_id INT NULL")
            cur.execute("ALTER TABLE study_materials ADD INDEX idx_subject_id (subject_id)")

        db.commit()
        _schema_ready = True
    finally:
        _safe_close(cur, db)


def _get_subject(user_id: int, subject_id: int):
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM subjects WHERE id=%s AND user_id=%s", (subject_id, user_id))
    subject = cur.fetchone()
    _safe_close(cur, db)
    return subject


def _resolve_subject(cur, user_id: int, subject_id=None, subject_name: str = ""):
    sid = None
    sname = (subject_name or "").strip()

    if subject_id:
        sid = int(subject_id)
        cur.execute("SELECT id, name FROM subjects WHERE id=%s AND user_id=%s", (sid, user_id))
        row = cur.fetchone()
        if not row:
            raise ValueError("Subject not found")
        return row["id"], row["name"]

    if not sname:
        sname = "General"

    cur.execute("SELECT id FROM subjects WHERE user_id=%s AND name=%s", (user_id, sname))
    row = cur.fetchone()
    if row:
        sid = row["id"]
    else:
        cur.execute("INSERT INTO subjects (user_id, name) VALUES (%s, %s)", (user_id, sname))
        sid = cur.lastrowid
    return sid, sname


@materials_bp.route("/subjects", methods=["GET"])
def list_subjects():
    try:
        user_id = _jwt_user()
        _ensure_subject_schema()

        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute(
            """SELECT s.id, s.name, s.created_at, COUNT(sm.id) AS material_count
               FROM subjects s
               LEFT JOIN study_materials sm ON sm.subject_id = s.id
               WHERE s.user_id = %s
               GROUP BY s.id, s.name, s.created_at
               ORDER BY s.created_at DESC""",
            (user_id,),
        )
        rows = cur.fetchall()
        _safe_close(cur, db)

        for row in rows:
            if row.get("created_at"):
                row["created_at"] = str(row["created_at"])

        return jsonify(rows), 200
    except Exception as e:
        return jsonify({"error": f"Error: {e}"}), 500


@materials_bp.route("/subjects", methods=["POST"])
def create_subject():
    try:
        user_id = _jwt_user()
        _ensure_subject_schema()

        data = request.get_json(force=True)
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Subject name is required"}), 400

        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT id FROM subjects WHERE user_id=%s AND name=%s", (user_id, name))
        existing = cur.fetchone()
        if existing:
            _safe_close(cur, db)
            return jsonify({"error": "Subject already exists"}), 409

        cur.execute("INSERT INTO subjects (user_id, name) VALUES (%s, %s)", (user_id, name))
        db.commit()
        sid = cur.lastrowid
        _safe_close(cur, db)

        return jsonify({"id": sid, "name": name}), 201
    except Exception as e:
        return jsonify({"error": f"Error: {e}"}), 500


@materials_bp.route("/subjects/<int:sid>", methods=["PUT"])
def rename_subject(sid):
    try:
        user_id = _jwt_user()
        _ensure_subject_schema()

        data = request.get_json(force=True)
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Subject name is required"}), 400

        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT id FROM subjects WHERE id=%s AND user_id=%s", (sid, user_id))
        if not cur.fetchone():
            _safe_close(cur, db)
            return jsonify({"error": "Subject not found"}), 404

        cur.execute("UPDATE subjects SET name=%s WHERE id=%s AND user_id=%s", (name, sid, user_id))
        cur.execute("UPDATE study_materials SET subject=%s WHERE subject_id=%s AND user_id=%s", (name, sid, user_id))
        db.commit()
        _safe_close(cur, db)

        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": f"Error: {e}"}), 500


@materials_bp.route("/subjects/<int:sid>", methods=["DELETE"])
def delete_subject(sid):
    try:
        user_id = _jwt_user()
        _ensure_subject_schema()

        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT id FROM subjects WHERE id=%s AND user_id=%s", (sid, user_id))
        if not cur.fetchone():
            _safe_close(cur, db)
            return jsonify({"error": "Subject not found"}), 404

        cur.execute("SELECT id, file_path FROM study_materials WHERE subject_id=%s AND user_id=%s", (sid, user_id))
        mats = cur.fetchall()
        for m in mats:
            try:
                if m.get("file_path") and os.path.exists(m["file_path"]):
                    os.remove(m["file_path"])
            except Exception:
                pass

        cur.execute("DELETE FROM study_materials WHERE subject_id=%s AND user_id=%s", (sid, user_id))
        cur.execute("DELETE FROM subjects WHERE id=%s AND user_id=%s", (sid, user_id))
        db.commit()

        cur.execute("UPDATE user_progress SET materials_uploaded=(SELECT COUNT(*) FROM study_materials WHERE user_id=%s) WHERE user_id=%s", (user_id, user_id))
        db.commit()
        _safe_close(cur, db)

        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": f"Error: {e}"}), 500


@materials_bp.route("/subjects/<int:sid>/materials", methods=["GET"])
def materials_by_subject(sid):
    try:
        user_id = _jwt_user()
        _ensure_subject_schema()

        if not _get_subject(user_id, sid):
            return jsonify({"error": "Subject not found"}), 404

        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute(
            """SELECT id, subject_id, title, subject, file_type, file_size, key_topics, created_at
               FROM study_materials
               WHERE user_id=%s AND subject_id=%s
               ORDER BY created_at DESC""",
            (user_id, sid),
        )
        rows = cur.fetchall()
        _safe_close(cur, db)

        for row in rows:
            if row.get("created_at"):
                row["created_at"] = str(row["created_at"])

        return jsonify(rows), 200
    except Exception as e:
        return jsonify({"error": f"Error: {e}"}), 500


@materials_bp.route("/subjects/<int:sid>/topics", methods=["GET"])
def subject_topics(sid):
    try:
        user_id = _jwt_user()
        _ensure_subject_schema()

        if not _get_subject(user_id, sid):
            return jsonify({"error": "Subject not found"}), 404

        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute(
            "SELECT key_topics, extracted_text FROM study_materials WHERE user_id=%s AND subject_id=%s",
            (user_id, sid),
        )
        rows = cur.fetchall()
        _safe_close(cur, db)

        merged_topics = []
        combined_text = []
        for row in rows:
            kt = row.get("key_topics")
            if kt:
                try:
                    for item in json.loads(kt):
                        if item not in merged_topics:
                            merged_topics.append(item)
                except Exception:
                    pass
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
        _ensure_subject_schema()
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
        cur = db.cursor(dictionary=True)

        sid = None
        if subject_id:
            sid = int(subject_id)
            cur.execute("SELECT id, name FROM subjects WHERE id=%s AND user_id=%s", (sid, user_id))
            row = cur.fetchone()
            if not row:
                _safe_close(cur, db)
                return jsonify({"error": "Subject not found"}), 404
            subject_name = row["name"]
        else:
            if not subject_name:
                subject_name = "General"
            cur.execute("SELECT id FROM subjects WHERE user_id=%s AND name=%s", (user_id, subject_name))
            row = cur.fetchone()
            if row:
                sid = row["id"]
            else:
                cur.execute("INSERT INTO subjects (user_id, name) VALUES (%s, %s)", (user_id, subject_name))
                db.commit()
                sid = cur.lastrowid

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

        cur.execute(
            """INSERT INTO study_materials
               (user_id, subject_id, title, subject, filename, file_path, file_type, file_size, extracted_text, key_topics)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, sid, title, subject_name, file.filename, path, ftype, os.path.getsize(path), text, json.dumps(topics, ensure_ascii=False)),
        )
        db.commit()
        mid = cur.lastrowid

        cur.execute("UPDATE user_progress SET materials_uploaded = materials_uploaded + 1 WHERE user_id=%s", (user_id,))
        db.commit()
        _safe_close(cur, db)

        return jsonify({
            "id": mid,
            "subject_id": sid,
            "title": title,
            "subject": subject_name,
            "file_type": ftype,
            "key_topics": topics,
            "message": "Material uploaded and processed successfully",
        }), 201
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@materials_bp.route("/upload-multiple", methods=["POST"])
def upload_multiple():
    try:
        user_id = _jwt_user()
        _ensure_subject_schema()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    subject_id = request.form.get("subject_id")
    subject_name = (request.form.get("subject") or "").strip()
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files provided"}), 400

    try:
        db = get_db()
        cur = db.cursor(dictionary=True)

        sid, sname = _resolve_subject(cur, user_id, subject_id, subject_name)
        db.commit()

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

            cur.execute(
                """INSERT INTO study_materials
                   (user_id, subject_id, title, subject, filename, file_path, file_type, file_size, extracted_text, key_topics)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    user_id,
                    sid,
                    title,
                    sname,
                    file.filename,
                    path,
                    ftype,
                    os.path.getsize(path),
                    text,
                    json.dumps(topics, ensure_ascii=False),
                ),
            )
            created.append({"id": cur.lastrowid, "title": title})

        db.commit()
        cur.execute(
            "UPDATE user_progress SET materials_uploaded = materials_uploaded + %s WHERE user_id=%s",
            (len(created), user_id),
        )
        db.commit()
        _safe_close(cur, db)

        return jsonify({"success": True, "subject_id": sid, "created": created, "count": len(created)}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@materials_bp.route("/list", methods=["GET"])
def list_materials():
    try:
        user_id = _jwt_user()
        _ensure_subject_schema()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute(
            """SELECT id, subject_id, title, subject, file_type, file_size, key_topics, created_at
               FROM study_materials
               WHERE user_id = %s
               ORDER BY created_at DESC""",
            (user_id,),
        )
        rows = cur.fetchall()
        _safe_close(cur, db)

        for row in rows:
            if row.get("created_at"):
                row["created_at"] = str(row["created_at"])

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
        cur = db.cursor(dictionary=True)
        cur.execute(
            """SELECT id, subject_id, title, subject, file_type, file_size, key_topics, created_at
               FROM study_materials
               WHERE id = %s AND user_id = %s""",
            (mid, user_id),
        )
        mat = cur.fetchone()
        _safe_close(cur, db)

        if not mat:
            return jsonify({"error": "Material not found"}), 404

        if mat.get("created_at"):
            mat["created_at"] = str(mat["created_at"])

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
        cur = db.cursor(dictionary=True)
        cur.execute(
            "SELECT key_topics FROM study_materials WHERE id = %s AND user_id = %s",
            (mid, user_id),
        )
        mat = cur.fetchone()
        _safe_close(cur, db)

        if not mat:
            return jsonify({"error": "Material not found"}), 404

        return jsonify({"key_topics": mat.get("key_topics")}), 200
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
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT file_path FROM study_materials WHERE id=%s AND user_id=%s", (mid, user_id))
        mat = cur.fetchone()
        if not mat:
            _safe_close(cur, db)
            return jsonify({"error": "Material not found"}), 404

        try:
            if mat.get("file_path") and os.path.exists(mat["file_path"]):
                os.remove(mat["file_path"])
        except Exception:
            pass

        cur.execute("DELETE FROM study_materials WHERE id=%s", (mid,))
        db.commit()
        cur.execute("UPDATE user_progress SET materials_uploaded = GREATEST(materials_uploaded - 1, 0) WHERE user_id=%s", (user_id,))
        db.commit()
        _safe_close(cur, db)

        return jsonify({"success": True, "message": "Material deleted"}), 200
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500


@materials_bp.route("/<int:mid>", methods=["PUT"])
def update_material(mid):
    try:
        user_id = _jwt_user()
        _ensure_subject_schema()
    except Exception as e:
        return jsonify({"error": "Unauthorized: " + str(e)}), 401

    try:
        data = request.get_json(force=True)
        new_title = (data.get("title") or "").strip()
        new_subject_id = data.get("subject_id")
        new_subject_name = (data.get("subject") or "").strip()

        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT id, title, subject_id, subject FROM study_materials WHERE id=%s AND user_id=%s", (mid, user_id))
        mat = cur.fetchone()
        if not mat:
            _safe_close(cur, db)
            return jsonify({"error": "Material not found"}), 404

        update_title = new_title if new_title else mat["title"]
        update_sid = mat.get("subject_id")
        update_sname = mat.get("subject") or "General"

        if new_subject_id or new_subject_name:
            update_sid, update_sname = _resolve_subject(cur, user_id, new_subject_id, new_subject_name)

        cur.execute(
            "UPDATE study_materials SET title=%s, subject_id=%s, subject=%s WHERE id=%s AND user_id=%s",
            (update_title, update_sid, update_sname, mid, user_id),
        )
        db.commit()
        _safe_close(cur, db)
        return jsonify({"success": True}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500
