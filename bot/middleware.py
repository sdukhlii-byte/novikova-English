from __future__ import annotations

from aiogram import BaseMiddleware


class TouchUser(BaseMiddleware):
    """Фиксирует пользователя и время последней активности при любом апдейте."""

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        db = data.get("db")
        cfg = data.get("cfg")
        # преподаватель/админ при тестировании бота не должны попадать в статистику воронки
        is_staff = bool(user and cfg and user.id in cfg.staff_ids)
        if user and db and not user.is_bot and not is_staff:
            await db.touch_user(user)
        return await handler(event, data)
