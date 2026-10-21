"""Карточка заявки в чате преподавателя: отправка и обновление статуса."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from . import keyboards as K
from . import texts as T
from .config import Settings
from .db import DB

log = logging.getLogger(__name__)


def staff_target(cfg: Settings) -> int | None:
    """Куда слать карточки: чат преподавателя, а если не задан — первому админу."""
    if cfg.teacher_chat_id:
        return cfg.teacher_chat_id
    return min(cfg.admin_ids) if cfg.admin_ids else None


async def send_card(bot: Bot, db: DB, cfg: Settings, lead_id: int) -> None:
    target = staff_target(cfg)
    if target is None:
        log.warning("TEACHER_CHAT_ID и ADMIN_IDS не заданы: карточка заявки #%s некуда отправить", lead_id)
        return
    lead = await db.get_lead(lead_id)
    quiz = await db.get_quiz(lead["quiz_id"])
    try:
        msg = await bot.send_message(target, T.card(lead, quiz, cfg.teacher_tz), reply_markup=K.lead_actions(lead_id))
    except Exception:
        log.exception("Не удалось отправить карточку заявки #%s", lead_id)
        return
    await db.update_lead(lead_id, card_chat_id=target, card_message_id=msg.message_id)
    await db.relay_put(target, msg.message_id, lead["user_id"])


async def refresh_card(bot: Bot, db: DB, cfg: Settings, lead_id: int) -> None:
    lead = await db.get_lead(lead_id)
    if not lead or not lead.get("card_chat_id"):
        return
    quiz = await db.get_quiz(lead["quiz_id"])
    try:
        await bot.edit_message_text(
            T.card(lead, quiz, cfg.teacher_tz),
            chat_id=lead["card_chat_id"],
            message_id=lead["card_message_id"],
            reply_markup=K.lead_actions(lead_id),
        )
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            log.warning("Не удалось обновить карточку #%s: %s", lead_id, e)
