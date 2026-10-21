from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message


async def show(target: Message | CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    """Для колбэков редактирует текущее сообщение (экран «обновляется на месте»),
    при невозможности отправляет новое."""
    if isinstance(target, CallbackQuery):
        msg = target.message
        try:
            await msg.edit_text(text, reply_markup=kb)
            return
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                return
        await msg.answer(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)
