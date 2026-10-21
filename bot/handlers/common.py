from __future__ import annotations

import re
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, FSInputFile, Message

from .. import content as C
from .. import keyboards as K
from .. import texts as T
from ..config import Settings
from ..db import DB
from ..sheets import Sheets
from ..ui import show

router = Router()

BASE_DIR = Path(__file__).resolve().parent.parent.parent
SRC_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_photo_file_id: dict[str, str] = {}


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, db: DB):
    uid = message.from_user.id
    if command.args and SRC_RE.match(command.args):
        await db.set_source(uid, command.args)
    await db.clear_session(uid)
    # реклама «найди ошибку» (метка содержит game): продолжаем ту же игру, а не показываем общее приветствие
    if command.args and "game" in command.args.lower() and C.MINI_CHECK:
        await message.answer(T.mini_intro(cold=True), reply_markup=K.mini_start())
        return
    await message.answer(T.welcome(), reply_markup=K.main_menu())


@router.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(
        f"Ваш user id: <code>{message.from_user.id}</code>\nId этого чата: <code>{message.chat.id}</code>"
    )


async def send_about(msg: Message) -> None:
    photo = BASE_DIR / C.TEACHER["photo"] if C.TEACHER["photo"] else None
    if photo and photo.exists():
        src = _photo_file_id.get("teacher") or FSInputFile(photo)
        sent = await msg.answer_photo(src, caption=T.about_caption())
        if getattr(sent, "photo", None):
            _photo_file_id["teacher"] = sent.photo[-1].file_id
        await msg.answer(T.about_body(), reply_markup=K.after_about())
    else:
        await msg.answer(f"{T.about_caption()}\n\n{T.about_body()}", reply_markup=K.after_about())


@router.message(Command("about"))
async def cmd_about(message: Message):
    await send_about(message)


@router.callback_query(F.data == "menu:about")
async def cb_about(cb: CallbackQuery):
    await cb.answer()
    await send_about(cb.message)


@router.message(Command("reviews"))
async def cmd_reviews(message: Message):
    if T.has_reviews():
        await message.answer(T.reviews(), reply_markup=K.after_about())
    else:
        await message.answer("Отзывы скоро появятся.", reply_markup=K.main_menu())


@router.callback_query(F.data == "menu:reviews")
async def cb_reviews(cb: CallbackQuery):
    await cb.answer()
    await cb.message.answer(T.reviews(), reply_markup=K.after_about())


@router.message(Command("contact"))
async def cmd_contact(message: Message):
    await message.answer(T.contact())


@router.message(Command("privacy"))
async def cmd_privacy(message: Message, cfg: Settings):
    text = (
        "<b>Конфиденциальность</b>\n"
        "Бот хранит ваши ответы в квизе и контактные данные только для связи по вопросу занятий. "
        "Данные не передаются третьим лицам, кроме преподавателя. "
        "Удалить все свои данные можно командой /delete."
    )
    if cfg.privacy_url:
        text += f'\n\n<a href="{cfg.privacy_url}">Политика конфиденциальности</a>'
    await message.answer(text)


@router.message(Command("delete"))
async def cmd_delete(message: Message):
    await message.answer(
        "Удалить все ваши данные из бота (ответы, заявку, переписку)? Действие необратимо.",
        reply_markup=K.delete_confirm(),
    )


@router.callback_query(F.data == "del:yes")
async def cb_delete_yes(cb: CallbackQuery, db: DB, sheets: Sheets):
    lead_ids = await db.delete_user_data(cb.from_user.id)
    for lid in lead_ids:
        await sheets.delete_lead(lid)
    await cb.answer("Удалено")
    await show(cb, "Ваши данные удалены. Если захотите вернуться, отправьте /start.")


@router.callback_query(F.data == "del:no")
async def cb_delete_no(cb: CallbackQuery):
    await cb.answer()
    await show(cb, "Хорошо, ничего не удаляем.")


@router.message(Command("faq"))
async def cmd_faq(message: Message):
    if T.faq_items():
        await message.answer("<b>Частые вопросы</b>", reply_markup=K.faq_menu())
    else:
        await message.answer(T.contact())


@router.callback_query(F.data == "menu:faq")
async def cb_faq_menu(cb: CallbackQuery):
    await cb.answer()
    await cb.message.answer("<b>Частые вопросы</b>", reply_markup=K.faq_menu())


@router.callback_query(F.data.startswith("faq:"))
async def cb_faq(cb: CallbackQuery):
    answer = T.faq_answer(cb.data.split(":", 1)[1])
    if not answer:
        await cb.answer("Ответ скоро появится")
        return
    await cb.answer()
    await cb.message.answer(answer, reply_markup=K.faq_menu(exclude=cb.data.split(":", 1)[1]))


@router.callback_query(F.data == "menu:ask")
async def cb_ask(cb: CallbackQuery):
    await cb.answer()
    await cb.message.answer(T.contact())


@router.callback_query(F.data.startswith("why:"))
async def cb_why(cb: CallbackQuery, db: DB):
    """Опрос «что остановило» из последнего напоминания: сохраняем причину и отвечаем по сути."""
    key = cb.data.split(":", 1)[1]
    row = next((s for s in C.SURVEY if s[0] == key), None)
    if not row:
        await cb.answer()
        return
    await db.update_user(cb.from_user.id, dropoff_reason=key)
    await cb.answer("Спасибо!")
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    _, _, faq_id = row
    if faq_id is None:  # «пока не актуально»: уважаем и ставим на паузу
        await db.update_user(cb.from_user.id, muted=1)
        await cb.message.answer(C.SURVEY_LATER_REPLY)
        return
    answer = T.faq_answer(faq_id)
    if answer:
        await cb.message.answer(answer, reply_markup=K.faq_menu(exclude=faq_id))


@router.callback_query(F.data == "fu:mute")
async def cb_mute(cb: CallbackQuery, db: DB):
    await db.update_user(cb.from_user.id, muted=1)
    await cb.answer("Больше не будем напоминать")
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
