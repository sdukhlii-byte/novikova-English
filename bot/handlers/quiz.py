from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery

from .. import content as C
from .. import keyboards as K
from .. import texts as T
from ..db import DB, now
from ..ui import show

router = Router()

TOTAL = len(C.STEPS)


def _bar(done: int) -> str:
    return "▰" * done + "▱" * (TOTAL - done)


def render_step(step: int) -> tuple[str, object]:
    sid = C.STEPS[step]
    q = C.CHOICE[sid]
    lines = [f"<b>Шаг {step + 1} из {TOTAL}</b>   {_bar(step)}", "", f"<b>{escape(q['q'])}</b>"]
    if q.get("hint"):
        lines.append(f"<i>{escape(q['hint'])}</i>")
    kb = K.quiz_step(step, [text for text, _ in q["options"]], with_back=step > 0)
    return "\n".join(lines), kb


async def begin(cb: CallbackQuery, db: DB, resume: bool = False) -> None:
    uid = cb.from_user.id
    sess = await db.get_session(uid)
    if resume and sess and sess["mode"] == "quiz":
        step = sess["step"]  # продолжаем с того же места, время старта не трогаем
    else:
        step = 0
        await db.set_session(uid, "quiz", 0, {})
        await db.update_user(uid, quiz_started_at=now())
    text, kb = render_step(step)
    await show(cb, text, kb)


@router.callback_query(F.data == "quiz:start")
async def cb_start(cb: CallbackQuery, db: DB):
    await cb.answer()
    await begin(cb, db)


@router.callback_query(F.data == "quiz:resume")
async def cb_resume(cb: CallbackQuery, db: DB):
    await cb.answer()
    await begin(cb, db, resume=True)


@router.callback_query(F.data.startswith("q:"))
async def cb_answer(cb: CallbackQuery, db: DB):
    uid = cb.from_user.id
    _, step_s, opt = cb.data.split(":")
    step = int(step_s)
    sess = await db.get_session(uid)

    if not sess or sess["mode"] != "quiz" or sess["step"] != step:
        await cb.answer("Эта кнопка устарела")
        if sess and sess["mode"] == "quiz":
            text, kb = render_step(sess["step"])
            await show(cb, text, kb)
        return

    data = sess["data"]
    await cb.answer()

    if opt == "b":
        step = max(step - 1, 0)
        data.pop(C.STEPS[step], None)
        await db.set_session(uid, "quiz", step, data)
        text, kb = render_step(step)
        await show(cb, text, kb)
        return

    sid = C.STEPS[step]
    data[sid] = C.CHOICE[sid]["options"][int(opt)][1]

    if step + 1 < TOTAL:
        await db.set_session(uid, "quiz", step + 1, data)
        text, kb = render_step(step + 1)
        await show(cb, text, kb)
        return

    # квиз завершён
    quiz_id = await db.save_quiz(uid, data)
    await db.update_user(uid, quiz_done_at=now())
    await db.clear_session(uid)
    quiz = await db.get_quiz(quiz_id)
    await show(cb, T.result(quiz))
    await cb.message.answer(T.offer(), reply_markup=K.after_result())
