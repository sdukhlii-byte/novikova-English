"""Автодогрев: цепочка напоминаний тем, кто не дошёл до заявки, и напоминания о пробном уроке.

Каждый пользователь закреплён за треком a или b (по чётности user_id), тексты и кнопки лежат в content.py.
Конверсию каждого сообщения и трека показывает /stats.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from . import content as C
from . import keyboards as K
from . import texts as T
from .config import Settings
from .db import DB, now

log = logging.getLogger(__name__)

QUIET_FROM, QUIET_TO = 9, 21  # слать только с 09:00 до 21:00 по FOLLOWUP_TZ
TICK_SECONDS = 60
QUIZ_TOTAL = len(C.STEPS)


def track(user_id: int) -> str:
    """Стабильное закрепление пользователя за треком: чётные id → a, нечётные → b."""
    return "a" if user_id % 2 == 0 else "b"


def left_text(n: int) -> str:
    n = max(1, n)
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} вопрос"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} вопроса"
    return f"{n} вопросов"


def _fill(text: str, **kw: str) -> str:
    for k, v in kw.items():
        text = text.replace("{" + k + "}", v)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def faq_buttons(step: dict, pain: str | None) -> list[tuple[str, str]]:
    """До двух кнопок-возражений: сначала релевантное сложности ребёнка. Пустые ответы не показываем."""
    answered = {o["id"]: o["button"] for o in C.OBJECTIONS if o["answer"].strip()}
    ids = list(step.get("faq", []))
    if pain in C.PAIN_FAQ:
        ids.insert(0, C.PAIN_FAQ[pain])
    out: list[tuple[str, str]] = []
    for i in ids:
        if i in answered and i not in [x for x, _ in out]:
            out.append((i, answered[i]))
    return out[:2]


def survey_buttons() -> list[tuple[str, str]]:
    answered = {o["id"] for o in C.OBJECTIONS if o["answer"].strip()}
    return [(key, label) for key, label, faq_id in C.SURVEY if faq_id is None or faq_id in answered]


def render(step: dict, variant: str, u: dict, left: int) -> tuple[str, dict]:
    """Возвращает (текст, вариант-описание) для пользователя u."""
    v = step["variants"][variant]
    text = v["text"]
    reviews = C.TEACHER["reviews"]
    if v.get("needs_reviews") and not reviews:
        text = v.get("fallback", text)
    proof = ""
    if reviews:
        r = reviews[u["user_id"] % len(reviews)]
        proof = f"«{escape(r['text'])}»\n<i>{escape(r['author'])}</i>"
    pain, goal = u.get("pain"), u.get("goal")
    text = _fill(
        text,
        first_name=escape((u.get("first_name") or "Здравствуйте").strip()),
        duration=escape(C.TRIAL["duration"]),
        pain_hook=escape(C.PAIN_HOOK.get(pain, "")),
        pain_tip=escape(C.PAIN_TIP.get(pain, "")),
        goal_line=escape(C.GOAL_PLAN.get(goal, "")),
        proof=proof,
        left_text=left_text(left),
    )
    return text, v


async def _send(bot: Bot, db: DB, uid: int, text: str, kb=None) -> bool:
    try:
        await bot.send_message(uid, text, reply_markup=kb)
        return True
    except TelegramForbiddenError:
        await db.update_user(uid, blocked=1)
    except Exception:
        log.exception("Не удалось отправить напоминание пользователю %s", uid)
    return False


def _due(plan: dict, ts: int, t: int) -> list[str]:
    offsets = sorted((v["after_h"] * 3600, k) for k, v in plan.items())
    return [k for off, k in offsets if t >= ts + off]


async def _deliver(bot: Bot, db: DB, plan: dict, step_id: str, u: dict) -> tuple[bool, str]:
    step = plan[step_id]
    variant = track(u["user_id"])
    if variant not in step["variants"]:
        variant = sorted(step["variants"])[0]
    left = QUIZ_TOTAL
    if step_id.startswith("u"):
        sess = await db.get_session(u["user_id"])
        if sess and sess["mode"] == "quiz":
            left = QUIZ_TOTAL - sess["step"]
    text, v = render(step, variant, u, left)
    kb = K.followup(
        step_id,
        cta=v["cta"],
        faq=[] if v.get("survey") else faq_buttons(step, u.get("pain")),  # под опросом лишних кнопок не нужно
        ask=bool(v.get("ask")),
        survey=survey_buttons() if v.get("survey") else [],
        action=step.get("action"),
    )
    return await _send(bot, db, u["user_id"], text, kb), variant


async def _sequence(bot: Bot, db: DB, plan: dict, rows: list[dict], t: int) -> None:
    """Для каждого пользователя шлём только самое свежее из наступивших сообщений
    (если бот долго не работал, не вываливаем сразу всю цепочку)."""
    for u in rows:
        due = _due(plan, u["ts"], t)
        if not due:
            continue
        sent = await db.sent_kinds(u["user_id"])
        latest = due[-1]
        if latest in sent:
            await db.mark_followups(u["user_id"], due)
            continue
        ok, variant = await _deliver(bot, db, plan, latest, u)
        await db.mark_followups(u["user_id"], due)
        if ok:
            await db.mark_delivered(u["user_id"], latest, variant)
        await asyncio.sleep(0.1)


async def _trial_reminders(bot: Bot, db: DB, cfg: Settings, t: int) -> None:
    for tr in await db.upcoming_trials(t):
        sent = await db.sent_kinds(tr["user_id"])
        for kind, lead_before in (("r1", 3600), ("r24", 86400)):  # ближайшее напоминание первым
            key = f"{kind}:{tr['id']}"
            if key in sent or t < tr["trial_at"] - lead_before:
                continue
            text = T.trial_reminder(kind, tr["trial_at"], tr["note"] or "", cfg.teacher_tz, T.client_tz(tr.get("country")))
            await _send(bot, db, tr["user_id"], text)
            await db.mark_followups(tr["user_id"], [f"r24:{tr['id']}", key])
            await asyncio.sleep(0.1)
            break


async def tick(bot: Bot, db: DB, cfg: Settings) -> None:
    t = now()
    await _trial_reminders(bot, db, cfg, t)
    hour = datetime.now(cfg.followup_tz).hour
    if not (QUIET_FROM <= hour < QUIET_TO):
        return
    horizon = max(v["after_h"] for v in C.FOLLOWUPS.values()) * 3600 + 86400
    await _sequence(bot, db, C.FOLLOWUPS, await db.quiz_done_candidates(t - horizon, t), t)
    await _sequence(bot, db, C.UNFINISHED, await db.unfinished_candidates(t - 3 * 86400, t), t)
    await _sequence(bot, db, C.MINI_FOLLOWUPS, await db.mini_candidates(t - 4 * 86400, t), t)
    await _sequence(bot, db, C.IDLE_FOLLOWUPS, await db.idle_candidates(t - 4 * 86400, t), t)


async def run(bot: Bot, db: DB, cfg: Settings) -> None:
    log.info("Автодогрев запущен")
    while True:
        try:
            await tick(bot, db, cfg)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка в цикле автодогрева")
        await asyncio.sleep(TICK_SECONDS)
