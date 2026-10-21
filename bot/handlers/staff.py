"""Команды и кнопки для преподавателя/админа: статусы заявок, статистика, экспорт, ответы клиентам."""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, ForceReply, Message, ReactionTypeEmoji

from .. import texts as T
from ..cards import refresh_card
from ..config import Settings
from ..db import DB, now
from ..sheets import Sheets

log = logging.getLogger(__name__)
router = Router()

TRIAL_RE = re.compile(
    r"^\s*(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\s+(\d{1,2})[:.](\d{2})\s*(.*)$", re.S
)


def parse_trial(text: str, tz: ZoneInfo, now_dt: datetime) -> tuple[int, str] | None:
    """'25.10 18:00 ссылка' → (unix_ts, 'ссылка'). Год необязателен."""
    m = TRIAL_RE.match(text)
    if not m:
        return None
    day, mon, year, hh, mm, note = m.groups()
    try:
        y = int(year) if year else now_dt.year
        if y < 100:
            y += 2000
        dt = datetime(y, int(mon), int(day), int(hh), int(mm), tzinfo=tz)
        if not year and dt < now_dt - timedelta(days=1):
            dt = dt.replace(year=y + 1)  # дата в начале следующего года
    except ValueError:
        return None
    return int(dt.timestamp()), note.strip()


def _is_staff(user_id: int, cfg: Settings) -> bool:
    return user_id in cfg.staff_ids


# ───────────────────────────── кнопки статуса ────────────────────────────────

@router.callback_query(F.data.startswith("st:"))
async def cb_status(cb: CallbackQuery, db: DB, bot: Bot, cfg: Settings, sheets: Sheets):
    if not _is_staff(cb.from_user.id, cfg):
        await cb.answer("Нет доступа", show_alert=True)
        return
    _, action, lead_id_s = cb.data.split(":")
    lead_id = int(lead_id_s)
    lead = await db.get_lead(lead_id)
    if not lead:
        await cb.answer("Заявка не найдена", show_alert=True)
        return

    if action == "trial":
        await db.set_session(cb.from_user.id, "await_trial", 0, {"lead_id": lead_id})
        await cb.answer()
        await cb.message.answer(
            "Отправьте дату и время пробного урока <b>ответом на это сообщение</b>, например:\n"
            "<code>25.10 18:00</code>\n"
            "Можно добавить ссылку или заметку для клиента:\n"
            "<code>25.10 18:00 Zoom: https://...</code>",
            reply_markup=ForceReply(selective=True),
        )
        return

    status = {"contacted": "contacted", "paid": "paid", "lost": "lost"}.get(action)
    if not status:
        await cb.answer()
        return
    await db.update_lead(lead_id, status=status)
    await cb.answer("Готово")
    await refresh_card(bot, db, cfg, lead_id)
    await sheets.update_lead(await db.get_lead(lead_id))


async def awaiting_trial(message: Message, db: DB) -> bool:
    sess = await db.get_session(message.from_user.id)
    return bool(sess and sess["mode"] == "await_trial")


@router.message(F.text, awaiting_trial)
async def on_trial_input(message: Message, db: DB, bot: Bot, cfg: Settings, sheets: Sheets):
    uid = message.from_user.id
    sess = await db.get_session(uid)
    lead_id = sess["data"]["lead_id"]
    parsed = parse_trial(message.text, cfg.teacher_tz, datetime.now(cfg.teacher_tz))
    if not parsed:
        await message.reply("Не получилось распознать. Формат: <code>25.10 18:00</code> (день.месяц часы:минуты).")
        return
    ts, note = parsed
    lead = await db.get_lead(lead_id)
    await db.update_lead(lead_id, status="trial", trial_at=ts, note=note or None)
    await db.clear_session(uid)

    # не шлём «завтра»/«через час», если урок ближе, чем через сутки/час
    left = ts - now()
    skip = ([f"r24:{lead_id}"] if left < 86400 else []) + ([f"r1:{lead_id}"] if left < 3600 else [])
    if skip:
        await db.mark_followups(lead["user_id"], skip)

    try:
        await bot.send_message(lead["user_id"], T.trial_confirmation(ts, note, cfg.teacher_tz, T.client_tz(lead.get('country'))))
        sent = True
    except TelegramForbiddenError:
        await db.update_user(lead["user_id"], blocked=1)
        sent = False
    except Exception:
        log.exception("Не удалось отправить подтверждение пробного урока")
        sent = False

    await message.reply(
        "✅ Пробный урок записан, клиенту отправлено подтверждение."
        if sent
        else "Пробный урок записан, но клиенту отправить не удалось (возможно, он заблокировал бота). Свяжитесь вручную."
    )
    await refresh_card(bot, db, cfg, lead_id)
    await sheets.update_lead(await db.get_lead(lead_id))


# ───────────────────── ответ клиенту через бота (reply на карточку) ──────────

async def relay_reply(message: Message, db: DB, cfg: Settings):
    if not message.reply_to_message or not _is_staff(message.from_user.id, cfg):
        return False
    uid = await db.relay_get(message.chat.id, message.reply_to_message.message_id)
    return {"relay_user": uid} if uid else False


@router.message(relay_reply)
async def on_staff_reply(message: Message, bot: Bot, db: DB, relay_user: int):
    try:
        if message.text:
            await bot.send_message(relay_user, f"💬 <b>{escape(T.teacher_who())}:</b>\n{escape(message.text)}")
        else:
            await message.copy_to(relay_user)
        try:
            await message.react([ReactionTypeEmoji(emoji="👌")])  # отметка «доставлено»
        except Exception:
            pass
    except TelegramForbiddenError:
        await db.update_user(relay_user, blocked=1)
        await message.reply("Клиент заблокировал бота, сообщение не доставлено.")
    except Exception:
        log.exception("Ошибка пересылки ответа клиенту")
        await message.reply("Не удалось доставить сообщение клиенту.")


# ─────────────────────────────── команды админа ──────────────────────────────

def _pct(a: int, b: int) -> str:
    return f"{a / b * 100:.0f}%" if b else "—"


def _cost(spend: float | None, n: int) -> str:
    return f"€{spend / n:.2f}" if spend is not None and n else "—"


@router.message(Command("stats"))
async def cmd_stats(message: Message, command: CommandObject, db: DB, cfg: Settings):
    if not _is_staff(message.from_user.id, cfg):
        return
    args = (command.args or "").split()
    days = int(args[0]) if args and args[0].isdigit() else 7
    spend = None
    if len(args) > 1:
        try:
            spend = float(args[1].replace(",", "."))
        except ValueError:
            pass

    rows = await db.funnel(now() - days * 86400)
    if not rows:
        await message.answer(f"За {days} дн. данных пока нет.")
        return

    tot = {k: sum(r[k] for r in rows) for k in ("starts", "mini_started", "mini_done", "quiz_started", "quiz_done", "leads", "trial", "paid")}
    out = [f"<b>Воронка за {days} дн.</b>" + (f" · расход €{spend:g}" if spend is not None else "")]
    for r in rows:
        out += [
            "",
            f"<b>{escape(r['source'])}</b>",
            f"Старт {r['starts']} → квиз {r['quiz_started']} ({_pct(r['quiz_started'], r['starts'])})"
            f" → готово {r['quiz_done']} ({_pct(r['quiz_done'], r['quiz_started'])})"
            f" → заявки {r['leads']} ({_pct(r['leads'], r['quiz_done'])})",
            f"Пробный {r['trial']} · оплата {r['paid']}",
        ]
        if r["mini_started"]:
            out.append(
                f"Мини-проверка: начали {r['mini_started']} ({_pct(r['mini_started'], r['starts'])} от старта)"
                f" → прошли все {r['mini_done']} ({_pct(r['mini_done'], r['mini_started'])})"
            )
    out += [
        "",
        "<b>Итого</b>",
        f"Старт {tot['starts']} · квиз {tot['quiz_done']} · заявки {tot['leads']} · пробный {tot['trial']} · оплата {tot['paid']}",
    ]
    if tot["mini_started"]:
        out.append(f"Мини-проверка всего: начали {tot['mini_started']} · прошли все {tot['mini_done']}")
    if spend is not None:
        out += [
            f"Цена старта: {_cost(spend, tot['starts'])}",
            f"Цена завершённого квиза: {_cost(spend, tot['quiz_done'])}",
            f"<b>CPL (заявка): {_cost(spend, tot['leads'])}</b>",
            f"Цена пробного: {_cost(spend, tot['trial'])}",
            f"Цена оплаты: {_cost(spend, tot['paid'])}",
        ]
    else:
        out += ["", "<i>Добавьте расход для расчёта цены лида: /stats 7 150</i>"]
    out += await _followup_block(db, days)
    await message.answer("\n".join(out))


TRACK_NAME = {"a": "A (информационный)", "b": "B (вовлекающий)"}
REASON_NAME = {"unsure": "не уверены, подойдёт ли", "time": "неудобное время", "price": "цена", "later": "пока не актуально"}


async def _followup_block(db: DB, days: int) -> list[str]:
    """Какие сообщения догрева и какой трек приводят заявки."""
    fs = await db.followup_stats(now() - days * 86400)
    if not fs["delivered"] and not fs["organic_leads"] and not fs["leads_by_kind"]:
        return []
    out = ["", "<b>Догрев: что приводит заявки</b>"]
    for r in fs["delivered"]:
        key = f"{r['kind']}:{r['variant']}"
        leads = fs["leads_by_kind"].get(key, 0)
        out.append(f"{r['kind']} · трек {r['variant'].upper()}: отправлено {r['n']} → заявок {leads} ({_pct(leads, r['n'])})")
    if fs["tracks"]:
        out.append("")
        for tr in fs["tracks"]:
            leads = sum(v for k, v in fs["leads_by_kind"].items() if k.endswith(f":{tr['variant']}"))
            out.append(f"Трек {TRACK_NAME.get(tr['variant'], tr['variant'])}: получили {tr['users']} → заявок {leads} ({_pct(leads, tr['users'])})")
    out.append(f"Заявки без догрева (сами или после диалога): {fs['organic_leads']}")
    if fs["reasons"]:
        out += ["", "<b>Почему не записались</b> (опрос)"]
        out += [f"{REASON_NAME.get(r['reason'], r['reason'])}: {r['n']}" for r in fs["reasons"]]
    out.append("<i>Заявка засчитывается последнему напоминанию, полученному за 36 часов до неё. Выводы делайте от ~30 получателей на трек.</i>")
    return out


@router.message(Command("leads"))
async def cmd_leads(message: Message, db: DB, cfg: Settings):
    if not _is_staff(message.from_user.id, cfg):
        return
    leads = await db.list_leads(10)
    if not leads:
        await message.answer("Заявок пока нет.")
        return
    lines = ["<b>Последние заявки</b>"]
    for l in leads:
        lines.append(
            f"#{l['id']} · {T.STATUS.get(l['status'], l['status'])} · {escape(l['name'])}"
            f" · {escape(l.get('country') or '—')} · {escape(l.get('source') or 'direct')}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("export"))
async def cmd_export(message: Message, db: DB, cfg: Settings):
    if not _is_staff(message.from_user.id, cfg):
        return
    rows = await db.export_rows()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        ["id", "created_at", "status", "name", "country", "phone", "username", "age", "goal",
         "level_by_parent", "pain", "format", "time_pref", "source", "trial_at", "note"]
    )
    for r in rows:
        w.writerow(
            [
                r["id"], datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m-%d %H:%M"), r["status"],
                r["name"], r["country"], r["phone"], r["username"], r["age"], r["goal"], r["level"],
                r["pain"], r["fmt"], r["time_pref"], r["source"],
                datetime.fromtimestamp(r["trial_at"]).strftime("%Y-%m-%d %H:%M") if r["trial_at"] else "",
                r["note"] or "",
            ]
        )
    data = ("﻿" + buf.getvalue()).encode("utf-8")  # BOM, чтобы Excel открыл кириллицу
    await message.answer_document(BufferedInputFile(data, filename="leads.csv"), caption=f"Заявок: {len(rows)}")
