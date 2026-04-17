from datetime import datetime, timedelta, timezone

import config
from database.db import get_db, utcnow


def _seconds_to_next_day(now: datetime) -> int:
    next_day = datetime(now.year, now.month, now.day) + timedelta(days=1)
    return max(int((next_day - now).total_seconds()), 1)


def _counter_doc_id(user_id: int, day: datetime.date) -> str:
    return f"{user_id}_{day.isoformat()}"


def _ensure_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def check_and_record_request(user_id: int, action: str = "chat") -> dict:
    limit = max(config.AI_DAILY_REQUEST_LIMIT, 1)
    cooldown = max(config.AI_COOLDOWN_SECONDS, 0)
    now = datetime.now(timezone.utc)
    today = now.date()

    db = get_db()
    doc_id = _counter_doc_id(user_id, today)
    ref = db.collection("ai_usage_counters").document(doc_id)
    snap = ref.get()
    row = snap.to_dict() if snap.exists else {}

    request_count = int(row.get("request_count") or 0)
    last_request_at = _ensure_aware(row.get("last_request_at"))
    blocked_until = _ensure_aware(row.get("blocked_until"))

    if blocked_until and blocked_until > now:
        retry_after = max(int((blocked_until - now).total_seconds()), 1)
        return {
            "allowed": False,
            "reason": "quota_cooldown",
            "retry_after": retry_after,
            "message": f"AI is temporarily paused after quota errors. Try again in {retry_after}s.",
            "action": action,
        }

    if request_count >= limit:
        retry_after = _seconds_to_next_day(now)
        return {
            "allowed": False,
            "reason": "daily_limit",
            "retry_after": retry_after,
            "message": f"Daily AI limit reached ({limit} requests/day). Try again tomorrow.",
            "action": action,
        }

    if cooldown > 0 and last_request_at:
        elapsed = (now - last_request_at).total_seconds()
        if elapsed < cooldown:
            retry_after = max(int(cooldown - elapsed), 1)
            return {
                "allowed": False,
                "reason": "cooldown",
                "retry_after": retry_after,
                "message": f"Please wait {retry_after}s before sending another AI request.",
                "action": action,
            }

    ref.set(
        {
            "user_id": user_id,
            "usage_date": today.isoformat(),
            "request_count": request_count + 1,
            "quota_hits": int(row.get("quota_hits") or 0),
            "last_request_at": utcnow(),
            "blocked_until": row.get("blocked_until"),
        },
        merge=True,
    )

    return {"allowed": True, "reason": "ok", "retry_after": 0, "limit": limit, "action": action}


def record_quota_hit(user_id: int, retry_after: int | None = None):
    block_seconds = max(retry_after or 0, config.AI_QUOTA_BLOCK_SECONDS)
    block_seconds = min(max(block_seconds, 5), 3600)
    now = datetime.now(timezone.utc)
    today = now.date()

    db = get_db()
    doc_id = _counter_doc_id(user_id, today)
    ref = db.collection("ai_usage_counters").document(doc_id)
    snap = ref.get()
    row = snap.to_dict() if snap.exists else {}

    existing_blocked = _ensure_aware(row.get("blocked_until"))
    new_blocked = now + timedelta(seconds=block_seconds)
    if existing_blocked and existing_blocked > new_blocked:
        new_blocked = existing_blocked

    ref.set(
        {
            "user_id": user_id,
            "usage_date": today.isoformat(),
            "quota_hits": int(row.get("quota_hits") or 0) + 1,
            "last_request_at": utcnow(),
            "blocked_until": new_blocked,
        },
        merge=True,
    )
