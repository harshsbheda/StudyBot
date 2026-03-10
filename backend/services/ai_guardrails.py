from datetime import datetime, timedelta

import config
from database.db import get_db

_schema_ready = False


def _safe_close(cur, db):
    try:
        cur.close()
        db.close()
    except Exception:
        pass


def _ensure_schema():
    global _schema_ready
    if _schema_ready:
        return

    db = get_db()
    cur = db.cursor()
    try:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS ai_usage_counters (
                user_id INT NOT NULL,
                usage_date DATE NOT NULL,
                request_count INT NOT NULL DEFAULT 0,
                quota_hits INT NOT NULL DEFAULT 0,
                last_request_at DATETIME DEFAULT NULL,
                blocked_until DATETIME DEFAULT NULL,
                PRIMARY KEY (user_id, usage_date),
                INDEX idx_ai_usage_date (usage_date),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )"""
        )
        db.commit()
        _schema_ready = True
    finally:
        _safe_close(cur, db)


def _seconds_to_next_day(now: datetime) -> int:
    next_day = datetime(now.year, now.month, now.day) + timedelta(days=1)
    return max(int((next_day - now).total_seconds()), 1)


def check_and_record_request(user_id: int, action: str = "chat") -> dict:
    _ensure_schema()

    limit = max(config.AI_DAILY_REQUEST_LIMIT, 1)
    cooldown = max(config.AI_COOLDOWN_SECONDS, 0)
    now = datetime.now()
    today = now.date()

    db = get_db()
    cur = db.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT request_count, last_request_at, blocked_until FROM ai_usage_counters WHERE user_id=%s AND usage_date=%s",
            (user_id, today),
        )
        row = cur.fetchone() or {}

        request_count = int(row.get("request_count") or 0)
        last_request_at = row.get("last_request_at")
        blocked_until = row.get("blocked_until")

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

        cur.execute(
            """INSERT INTO ai_usage_counters (user_id, usage_date, request_count, quota_hits, last_request_at, blocked_until)
               VALUES (%s, %s, 1, 0, NOW(), NULL)
               ON DUPLICATE KEY UPDATE request_count=request_count+1, last_request_at=NOW()""",
            (user_id, today),
        )
        db.commit()

        return {
            "allowed": True,
            "reason": "ok",
            "retry_after": 0,
            "limit": limit,
            "action": action,
        }
    finally:
        _safe_close(cur, db)


def record_quota_hit(user_id: int, retry_after: int | None = None):
    _ensure_schema()

    block_seconds = max(retry_after or 0, config.AI_QUOTA_BLOCK_SECONDS)
    block_seconds = min(max(block_seconds, 5), 3600)
    today = datetime.now().date()

    db = get_db()
    cur = db.cursor()
    try:
        cur.execute(
            """INSERT INTO ai_usage_counters (user_id, usage_date, request_count, quota_hits, last_request_at, blocked_until)
               VALUES (%s, %s, 0, 1, NOW(), DATE_ADD(NOW(), INTERVAL %s SECOND))
               ON DUPLICATE KEY UPDATE
                   quota_hits = quota_hits + 1,
                   blocked_until = GREATEST(IFNULL(blocked_until, NOW()), DATE_ADD(NOW(), INTERVAL %s SECOND))""",
            (user_id, today, block_seconds, block_seconds),
        )
        db.commit()
    finally:
        _safe_close(cur, db)

