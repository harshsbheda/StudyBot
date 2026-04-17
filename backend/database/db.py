from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore

import config

_db = None


def _init_firestore() -> None:
    global _db
    if _db is not None:
        return

    if not firebase_admin._apps:
        cred_json = (config.FIREBASE_SERVICE_ACCOUNT_JSON_RAW or "").strip()
        cred_path = (config.FIREBASE_SERVICE_ACCOUNT_JSON or "").strip()
        options = {}
        if config.FIREBASE_PROJECT_ID:
            options["projectId"] = config.FIREBASE_PROJECT_ID

        if cred_json:
            try:
                cred_payload = json.loads(cred_json)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "FIREBASE_SERVICE_ACCOUNT_JSON_RAW must contain valid JSON on a single line."
                ) from exc

            if not isinstance(cred_payload, dict):
                raise RuntimeError("FIREBASE_SERVICE_ACCOUNT_JSON_RAW must decode to a JSON object.")

            cred = credentials.Certificate(cred_payload)
            firebase_admin.initialize_app(cred, options or None)
        elif cred_path:
            cred_file = Path(cred_path).expanduser()
            if not cred_file.exists():
                raise RuntimeError(f"Firebase service account file not found: {cred_file}")
            cred = credentials.Certificate(str(cred_file))
            firebase_admin.initialize_app(cred, options or None)
        else:
            # Uses GOOGLE_APPLICATION_CREDENTIALS or default credentials.
            firebase_admin.initialize_app()

    _db = firestore.client()


def get_db():
    _init_firestore()
    return _db


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_next_id(counter_name: str) -> int:
    db = get_db()
    counter_ref = db.collection("counters").document(counter_name)

    @firestore.transactional
    def _increment(transaction):
        snap = counter_ref.get(transaction=transaction)
        current = 0
        if snap.exists:
            current = int(snap.to_dict().get("value", 0) or 0)
        new_value = current + 1
        transaction.set(counter_ref, {"value": new_value}, merge=True)
        return new_value

    return _increment(db.transaction())


def doc_to_dict(doc) -> dict[str, Any]:
    if not doc:
        return {}
    data = doc.to_dict() or {}
    return data


def ensure_user_progress(db, user_id: int) -> None:
    ref = db.collection("user_progress").document(str(user_id))
    snap = ref.get()
    if snap.exists:
        return
    ref.set(
        {
            "user_id": user_id,
            "total_tests": 0,
            "avg_score": 0,
            "materials_uploaded": 0,
            "chat_sessions": 0,
            "study_streak": 0,
            "last_study_date": None,
        }
    )


def increment_user_progress(db, user_id: int, field: str, amount: int = 1) -> None:
    ref = db.collection("user_progress").document(str(user_id))
    ref.set({"user_id": user_id, field: firestore.Increment(amount)}, merge=True)


def set_user_progress(db, user_id: int, data: dict) -> None:
    ref = db.collection("user_progress").document(str(user_id))
    payload = {"user_id": user_id}
    payload.update(data)
    ref.set(payload, merge=True)
