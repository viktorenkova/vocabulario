import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    token: str
    user_id: int
    timezone: ZoneInfo
    reminder: time
    limit: int
    retention: float
    voice: str
    dictionary: Path
    database: Path
    evening_reminder: time = time(20, 0)

    @classmethod
    def load(cls):
        load_dotenv()
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        uid = os.getenv("ALLOWED_USER_ID", "").strip()
        if not token:
            raise ValueError("Заполните TELEGRAM_BOT_TOKEN в .env (см. README.md).")
        tz = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
        hour, minute = map(int, os.getenv("REMINDER_TIME", "09:00").split(":"))
        limit = int(os.getenv("DAILY_LIMIT", "15"))
        retention = float(os.getenv("DESIRED_RETENTION", "0.90"))
        if not 1 <= limit <= 100 or not 0.7 <= retention <= 0.99:
            raise ValueError("DAILY_LIMIT: 1..100; DESIRED_RETENTION: 0.70..0.99")
        return cls(
            token,
            int(uid or 0),
            tz,
            time(hour, minute, tzinfo=tz),
            limit,
            retention,
            os.getenv("TTS_VOICE", "es-ES-ElviraNeural"),
            Path(os.getenv("DICTIONARY_PATH", "data/dictionary.jsonl")),
            Path(os.getenv("DATABASE_PATH", "runtime/progress.sqlite3")),
            time.fromisoformat(os.getenv("REMINDER_EVENING_TIME", "20:00")),
        )
