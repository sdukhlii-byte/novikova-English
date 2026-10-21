"""Дублирование заявок в Google Sheets. Все ошибки логируются и не ломают бота."""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone

from . import texts as T

log = logging.getLogger(__name__)

HEADERS = [
    "lead_id", "created_at", "status", "name", "username", "phone", "country",
    "child_age", "goal", "level_by_parent", "pain", "format", "time_pref",
    "source", "trial_at", "note",
]
WORKSHEET = "Leads"


def _iso(ts: int | None) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else ""


class Sheets:
    def __init__(self, sheet_id: str, creds: dict | None):
        self.enabled = bool(sheet_id and creds)
        self._sheet_id = sheet_id
        self._creds = creds
        self._ws = None
        self._lock = threading.Lock()
        if not self.enabled:
            log.info("Google Sheets не настроены — заявки хранятся только в базе и в Telegram.")

    # ── синхронная часть (выполняется в потоке) ──
    def _worksheet(self):
        if self._ws is not None:
            return self._ws
        import gspread

        gc = gspread.service_account_from_dict(self._creds)
        sh = gc.open_by_key(self._sheet_id)
        try:
            ws = sh.worksheet(WORKSHEET)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=WORKSHEET, rows=1000, cols=len(HEADERS))
        if ws.row_values(1) != HEADERS:
            ws.update([HEADERS], "A1")
        self._ws = ws
        return ws

    def _row_for(self, lead: dict, quiz: dict | None) -> list:
        q = quiz or {}
        return [
            lead["id"], _iso(lead.get("created_at")), lead.get("status", ""), lead.get("name", ""),
            lead.get("username") or "", lead.get("phone") or "", lead.get("country") or "",
            T.label("age", q.get("age")) if q else "", T.label("goal", q.get("goal")) if q else "",
            T.label("level", q.get("level")) if q else "", T.label("pain", q.get("pain")) if q else "",
            T.label("fmt", q.get("fmt")) if q else "", T.label("time", q.get("time_pref")) if q else "",
            lead.get("source") or "direct", _iso(lead.get("trial_at")), lead.get("note") or "",
        ]

    def _append(self, lead: dict, quiz: dict | None) -> None:
        with self._lock:
            self._worksheet().append_row(self._row_for(lead, quiz), value_input_option="USER_ENTERED")

    def _update(self, lead: dict) -> None:
        with self._lock:
            ws = self._worksheet()
            cell = ws.find(str(lead["id"]), in_column=1)
            if not cell:
                return
            for col_name, value in (
                ("status", lead.get("status", "")),
                ("trial_at", _iso(lead.get("trial_at"))),
                ("note", lead.get("note") or ""),
            ):
                ws.update_cell(cell.row, HEADERS.index(col_name) + 1, value)

    def _delete(self, lead_id: int) -> None:
        with self._lock:
            ws = self._worksheet()
            cell = ws.find(str(lead_id), in_column=1)
            if cell:
                ws.delete_rows(cell.row)

    # ── асинхронный интерфейс ──
    async def _run(self, fn, *args) -> None:
        if not self.enabled:
            return
        try:
            await asyncio.to_thread(fn, *args)
        except Exception:
            log.exception("Ошибка записи в Google Sheets")

    async def append_lead(self, lead: dict, quiz: dict | None) -> None:
        await self._run(self._append, lead, quiz)

    async def update_lead(self, lead: dict) -> None:
        await self._run(self._update, lead)

    async def delete_lead(self, lead_id: int) -> None:
        await self._run(self._delete, lead_id)
