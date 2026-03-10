import os
from pathlib import Path


def _load_env_file() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_env_file()


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_csv(name: str) -> list[str]:
    value = os.getenv(name, "")
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# AI
AI_PROVIDER = os.getenv("AI_PROVIDER", "auto").strip().lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")
AI_DAILY_REQUEST_LIMIT = _get_int("AI_DAILY_REQUEST_LIMIT", 40)
AI_COOLDOWN_SECONDS = _get_int("AI_COOLDOWN_SECONDS", 2)
AI_QUOTA_BLOCK_SECONDS = _get_int("AI_QUOTA_BLOCK_SECONDS", 60)
AI_FALLBACK_MODE = os.getenv("AI_FALLBACK_MODE", "links").strip().lower()

# Database (XAMPP defaults)
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = _get_int("DB_PORT", 3306)
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "studybot_db")
DB_POOL_SIZE = _get_int("DB_POOL_SIZE", 10)
DB_CONNECT_TIMEOUT = _get_int("DB_CONNECT_TIMEOUT", 10)

# Security
SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-key")
JWT_SECRET = os.getenv("JWT_SECRET", "change-this-jwt-secret")
JWT_EXPIRY_DAYS = _get_int("JWT_EXPIRY_DAYS", 30)

# Google OAuth (optional)
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_ALLOWED_ORIGINS = _get_csv("GOOGLE_ALLOWED_ORIGINS")
GOOGLE_ALLOWED_REDIRECTS = _get_csv("GOOGLE_ALLOWED_REDIRECTS")

# Uploads
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", os.path.join(BASE_DIR, "uploads"))
MAX_UPLOAD_MB = _get_int("MAX_UPLOAD_MB", 50)

# Server
HOST = os.getenv("HOST", "0.0.0.0")
PORT = _get_int("PORT", 5000)
DEBUG = _get_bool("DEBUG", False)
