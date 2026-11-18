"""Shared configuration and time helpers. Single source of truth for env + timestamps."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int = 0) -> int:
    try:
        return int(os.getenv(name) or default)
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


API_ID = _int("API_ID")
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "kojoforextrades").lstrip("@")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
DATABASE_PATH = os.getenv("DATABASE_PATH", "./data/kojo_alerts.sqlite3")
CONFIDENCE_THRESHOLD = _float("CONFIDENCE_THRESHOLD", 0.75)
FCM_TOPIC = os.getenv("FCM_TOPIC", "kojo-alerts").strip().lstrip("/")
FCM_CHANNEL_ID = os.getenv("FCM_CHANNEL_ID", "kojo_alerts_critical")
GROQ_TIMEOUT_SECONDS = _int("GROQ_TIMEOUT_SECONDS", 15)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Admin (for /diag command — set to your own Telegram user ID)
ADMIN_CHAT_ID = _int("ADMIN_CHAT_ID", 0)

# Latency tuning
GROQ_FAST_PATH = _bool("GROQ_FAST_PATH", False)
OUTBOX_POLL_SECONDS = _int("OUTBOX_POLL_SECONDS", 30)
CHANNEL_POLL_SECONDS = _int("CHANNEL_POLL_SECONDS", 60)

# Telegram bot UX
TELEGRAM_HISTORY_LIMIT = _int("TELEGRAM_HISTORY_LIMIT", 10)


def utcnow() -> str:
    """Single canonical timestamp. Always UTC, always with +00:00 suffix."""
    return datetime.now(timezone.utc).isoformat()


def utc_after(seconds: float) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def ensure_db_dir() -> None:
    Path(DATABASE_PATH).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)