"""Бонус «Найди ошибку»: родитель показывает предложение ребёнку, затем смотрит ответ."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from .. import content as C
from .. import keyboards as K
from .. import texts as T
from ..db import DB

router = Router()


def _index(data: str) -> int | None:
    try:
        i = int(data.split(":")[2])
    except (IndexError, ValueError):
        return None
    return i if 0 <= i < len(C.MINI_CHECK) else None


@router.callback_query(F.data == "mc:intro")
async def cb_intro(cb: CallbackQuery):
    await cb.answer()
    if not C.MINI_CHECK:
        return
    await cb.message.answer(T.mini_intro(), reply_markup=K.mini_start())


@router.callback_query(F.data.startswith("mc:q:"))
async def cb_question(cb: CallbackQuery, db: DB):
    i = _index(cb.data)
    await cb.answer()
    if i is not None:
        if i == 0:
            await db.mark_mini(cb.from_user.id, "mini_started_at")
        await cb.message.answer(T.mini_question(i), reply_markup=K.mini_question(i))


@router.callback_query(F.data.startswith("mc:a:"))
async def cb_answer(cb: CallbackQuery, db: DB):
    i = _index(cb.data)
    await cb.answer()
    if i is not None:
        await db.mark_mini(cb.from_user.id, "mini_started_at")  # на случай, если нажали «ответ» из старого сообщения
        if i == len(C.MINI_CHECK) - 1:
            await db.mark_mini(cb.from_user.id, "mini_done_at")
        await cb.message.answer(T.mini_answer(i), reply_markup=K.mini_answer(i))
