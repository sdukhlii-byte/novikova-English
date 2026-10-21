from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

try:  # python-dotenv необязателен: на хостинге переменные задаются в панели
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

log = logging.getLogger(__name__)


def _int_set(raw: str) -> frozenset[int]:
    out = set()
    for part in re.split(r"[,\s;]+", (raw or "").strip()):
        if part and part.lstrip("-").isdigit():
            out.add(int(part))
    return frozenset(out)


def _google_creds() -> dict | None:
    raw = os.getenv("GOOGLE_CREDS_JSON", "").strip()
    b64 = os.getenv("GOOGLE_CREDS_B64", "").strip()
    try:
        if raw:
            return json.loads(raw)
        if b64:
            return json.loads(base64.b64decode(b64).decode("utf-8"))
    except Exception:
        log.exception("Не удалось разобрать Google credentials — Sheets отключены")
    return None


@dataclass(frozen=True)
class Settings:
    bot_token: str
    teacher_chat_id: int | None
    admin_ids: frozenset[int]
    db_path: str
    privacy_url: str
    teacher_tz: ZoneInfo
    followup_tz: ZoneInfo
    sheet_id: str
    google_creds: dict | None
    followups_enabled: bool

    @property
    def staff_ids(self) -> frozenset[int]:
        """Кому разрешено нажимать кнопки статусов и отвечать клиентам через бота."""
        ids = set(self.admin_ids)
        if self.teacher_chat_id and self.teacher_chat_id > 0:  # личка преподавателя
            ids.add(self.teacher_chat_id)
        return frozenset(ids)


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN не задан. Скопируйте .env.example в .env и заполните.")

    chat = os.getenv("TEACHER_CHAT_ID", "").strip()
    return Settings(
        bot_token=token,
        teacher_chat_id=int(chat) if chat.lstrip("-").isdigit() else None,
        admin_ids=_int_set(os.getenv("ADMIN_IDS", "")),
        db_path=os.getenv("DB_PATH", "data/bot.db"),
        privacy_url=os.getenv("PRIVACY_URL", "").strip(),
        teacher_tz=ZoneInfo(os.getenv("TEACHER_TZ", "Europe/Moscow")),
        followup_tz=ZoneInfo(os.getenv("FOLLOWUP_TZ", "Europe/Berlin")),
        sheet_id=os.getenv("GOOGLE_SHEET_ID", "").strip(),
        google_creds=_google_creds(),
        followups_enabled=os.getenv("FOLLOWUPS_ENABLED", "1") != "0",
    )
