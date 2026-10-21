from __future__ import annotations

import re
from html import escape

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from .. import content as C
from .. import keyboards as K
from .. import texts as T
from ..cards import send_card, staff_target
from ..config import Settings
from ..db import DB, now
from ..sheets import Sheets
from ..ui import show

router = Router()

PHONE_RE = re.compile(r"^\+?[\d\s\-()]{7,24}$")
SKIP_TEXT = "Продолжить без номера"


# ───────────────────────────── вход в форму ──────────────────────────────────

@router.callback_query(F.data == "lead:start")
async def cb_lead_start(cb: CallbackQuery, db: DB):
    await cb.answer()
    uid = cb.from_user.id
    existing = await db.latest_lead(uid)
    if existing and existing["status"] != "lost":
        await cb.message.answer(
            f"Ваша заявка уже у преподавателя (№{existing['id']}). "
            "Если нужно что-то уточнить или добавить, просто напишите сюда."
        )
        return
    quiz = await db.latest_quiz(uid)
    await db.set_session(
        uid, "lead", 0,
        {"step": "name", "quiz_id": quiz["id"] if quiz else None, "username": cb.from_user.username},
    )
    await ask_name(cb.message, cb.from_user.first_name or "")


async def ask_name(msg: Message, first_name: str) -> None:
    text = "<b>Запись на пробный урок</b>\n\nКак к вам обращаться?"
    kb = K.use_name(first_name) if first_name else None
    await msg.answer(text, reply_markup=kb)


async def ask_country(msg: Message) -> None:
    await msg.answer("В какой стране вы живёте?", reply_markup=K.countries())


async def ask_phone(msg: Message, has_username: bool) -> None:
    if has_username:
        text = (
            "Преподаватель напишет вам в Telegram. Если удобнее WhatsApp или звонок, "
            "поделитесь номером телефона, это необязательно."
        )
    else:
        text = (
            "Чтобы преподаватель мог с вами связаться, поделитесь номером телефона: "
            "кнопка ниже или просто напишите его сообщением."
        )
    await msg.answer(text, reply_markup=K.phone_request(can_skip=has_username))


async def ask_confirm(msg: Message, db: DB, cfg: Settings, uid: int, data: dict) -> None:
    quiz = await db.get_quiz(data.get("quiz_id"))
    data["step"] = "confirm"
    await db.set_session(uid, "lead", 0, data)
    # убираем клавиатуру с кнопкой номера (отправляем и сразу удаляем служебное сообщение)
    try:
        tmp = await msg.answer("Спасибо!", reply_markup=ReplyKeyboardRemove())
        await tmp.delete()
    except Exception:
        pass
    await msg.answer(T.lead_summary(data, quiz, cfg.privacy_url), reply_markup=K.confirm_lead())


# ───────────────────────────── шаги формы ────────────────────────────────────

@router.callback_query(F.data == "lead:usename")
async def cb_use_name(cb: CallbackQuery, db: DB):
    sess = await db.get_session(cb.from_user.id)
    if not sess or sess["mode"] != "lead" or sess["data"].get("step") != "name":
        await cb.answer("Эта кнопка устарела")
        return
    await cb.answer()
    data = sess["data"]
    data["name"] = (cb.from_user.first_name or "Родитель").strip()[:60]
    data["step"] = "country"
    await db.set_session(cb.from_user.id, "lead", 0, data)
    await ask_country(cb.message)


@router.callback_query(F.data.startswith("lead:c:"))
async def cb_country(cb: CallbackQuery, db: DB):
    sess = await db.get_session(cb.from_user.id)
    if not sess or sess["mode"] != "lead" or sess["data"].get("step") != "country":
        await cb.answer("Эта кнопка устарела")
        return
    await cb.answer()
    data = sess["data"]
    data["country"] = C.COUNTRIES[int(cb.data.split(":")[2])]
    data["step"] = "phone"
    await db.set_session(cb.from_user.id, "lead", 0, data)
    await ask_phone(cb.message, has_username=bool(data.get("username")))


@router.callback_query(F.data == "lead:edit")
async def cb_edit(cb: CallbackQuery, db: DB):
    sess = await db.get_session(cb.from_user.id)
    if not sess or sess["mode"] != "lead":
        await cb.answer("Эта кнопка устарела")
        return
    await cb.answer()
    data = {"step": "name", "quiz_id": sess["data"].get("quiz_id"), "username": cb.from_user.username}
    await db.set_session(cb.from_user.id, "lead", 0, data)
    await ask_name(cb.message, cb.from_user.first_name or "")


@router.callback_query(F.data == "lead:cancel")
async def cb_cancel(cb: CallbackQuery, db: DB):
    await cb.answer()
    await db.clear_session(cb.from_user.id)
    await show(cb, "Хорошо, заявку не отправляем. Вернуться в начало: /start")


@router.callback_query(F.data == "lead:send")
async def cb_send(cb: CallbackQuery, db: DB, bot: Bot, cfg: Settings, sheets: Sheets):
    uid = cb.from_user.id
    sess = await db.get_session(uid)
    if not sess or sess["mode"] != "lead" or sess["data"].get("step") != "confirm":
        await cb.answer("Эта кнопка устарела")
        return
    await cb.answer()
    d = sess["data"]
    user = await db.get_user(uid)
    lead_id = await db.create_lead(
        user_id=uid,
        name=d["name"],
        country=d.get("country"),
        phone=d.get("phone"),
        username=d.get("username"),
        quiz_id=d.get("quiz_id"),
        source=(user or {}).get("source") or "direct",
        followup_kind=await db.last_followup(uid),  # какое напоминание привело к заявке (за 36 ч)
    )
    await db.update_user(uid, lead_at=now())
    await db.clear_session(uid)
    await show(cb, T.thanks(C.TEACHER["response_time"]))

    await send_card(bot, db, cfg, lead_id)
    lead = await db.get_lead(lead_id)
    await sheets.append_lead(lead, await db.get_quiz(lead["quiz_id"]))


# ───────────────────────── ввод текста и контакты ────────────────────────────

@router.message(F.contact & (F.chat.type == "private"))
async def on_contact(message: Message, db: DB, cfg: Settings):
    uid = message.from_user.id
    sess = await db.get_session(uid)
    if not sess or sess["mode"] != "lead" or sess["data"].get("step") != "phone":
        return
    phone = message.contact.phone_number
    data = sess["data"]
    data["phone"] = phone if phone.startswith("+") else f"+{phone}"
    await ask_confirm(message, db, cfg, uid, data)


async def _lead_text(message: Message, db: DB, cfg: Settings, sess: dict) -> None:
    uid = message.from_user.id
    data = sess["data"]
    text = (message.text or "").strip()
    step = data.get("step")

    if step == "name":
        if not 2 <= len(text) <= 60:
            await message.answer("Напишите, пожалуйста, имя (от 2 до 60 символов).")
            return
        data["name"] = text
        data["step"] = "country"
        await db.set_session(uid, "lead", 0, data)
        await ask_country(message)
    elif step == "phone":
        if text == SKIP_TEXT and data.get("username"):
            await ask_confirm(message, db, cfg, uid, data)
        elif PHONE_RE.match(text) and sum(ch.isdigit() for ch in text) >= 7:
            data["phone"] = text
            await ask_confirm(message, db, cfg, uid, data)
        else:
            await message.answer("Не получилось распознать номер. Пример: +49 151 234 56 78. Или нажмите кнопку ниже.")
    else:
        await message.answer("Выберите вариант кнопкой выше или нажмите /start, чтобы начать заново.")


# ─────────────── свободные сообщения клиента → преподавателю ─────────────────

async def _relay_to_staff(message: Message, db: DB, bot: Bot, cfg: Settings) -> None:
    target = staff_target(cfg)
    if target is None:
        await message.answer(T.contact())
        return
    uid = message.from_user.id
    lead = await db.latest_lead(uid)
    name = lead["name"] if lead else message.from_user.full_name
    header = f"💬 <b>{escape(name)}</b>" + (f" · заявка #{lead['id']}" if lead else " · без заявки")
    if message.from_user.username:
        header += f" · @{escape(message.from_user.username)}"

    sent_ids: list[int] = []
    if message.text:
        sent = await bot.send_message(target, f"{header}\n\n{escape(message.text)}")
        sent_ids.append(sent.message_id)
    else:
        head = await bot.send_message(target, header)
        copy = await message.copy_to(target)
        sent_ids += [head.message_id, copy.message_id]
    for mid in sent_ids:
        await db.relay_put(target, mid, uid)
    # клиент общается с преподавателем: на 3 дня ставим автодогрев на паузу
    await db.update_user(uid, pause_until=now() + 3 * 86400)
    await message.answer("✓ Передано преподавателю. Ответ придёт в этот чат.")


@router.message(F.text & ~F.text.startswith("/") & (F.chat.type == "private"))
async def on_text(message: Message, db: DB, bot: Bot, cfg: Settings):
    sess = await db.get_session(message.from_user.id)
    if sess and sess["mode"] == "lead":
        await _lead_text(message, db, cfg, sess)
    elif sess and sess["mode"] == "quiz":
        await message.answer("Выберите вариант кнопкой под вопросом 👆")
    else:
        await _relay_to_staff(message, db, bot, cfg)


@router.message(
    (F.chat.type == "private")
    & F.content_type.in_({"photo", "voice", "video", "video_note", "document", "audio"})
)
async def on_media(message: Message, db: DB, bot: Bot, cfg: Settings):
    await _relay_to_staff(message, db, bot, cfg)
