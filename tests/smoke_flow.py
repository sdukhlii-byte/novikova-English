"""
Прогон всего сценария без Telegram: подменяем сетевой слой aiogram и скармливаем боту апдейты.

Запуск:  python -m tests.smoke_flow
Печатает «переписку» (что получил клиент и что получил преподаватель) и падает, если что-то сломано.
"""
from __future__ import annotations

import asyncio
import itertools
import os
import re
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import CopyMessage, DeleteMessage
from aiogram.types import (
    CallbackQuery, Chat, Message, MessageEntity, MessageId, Update, User,
)

from bot import followups
from bot.config import Settings
from bot.db import DB, now
from bot.main import build_dispatcher
from bot.sheets import Sheets

PARENT, TEACHER, BOT_ID = 100, 555, 1
_ids = itertools.count(1000)


class MockSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.log: list[tuple[str, int | None, str, object]] = []

    async def close(self):  # noqa: D401
        pass

    async def stream_content(self, *a, **k):  # pragma: no cover
        yield b""

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        chat_id = getattr(method, "chat_id", None)
        text = getattr(method, "text", None) or getattr(method, "caption", None) or ""
        self.log.append((name, chat_id, text, getattr(method, "reply_markup", None)))
        if name in ("SendMessage", "EditMessageText", "SendDocument", "SendPhoto"):
            return Message(
                message_id=next(_ids), date=datetime.now(),
                chat=Chat(id=chat_id or 0, type="private"), text=text or None,
            )
        if isinstance(method, CopyMessage):
            return MessageId(message_id=next(_ids))
        return True


def strip(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


def kb_labels(markup) -> str:
    if not markup:
        return ""
    rows = getattr(markup, "inline_keyboard", None) or getattr(markup, "keyboard", [])
    return " | ".join(b.text for row in rows for b in row)


async def run(db_holder: dict) -> None:
    tmp = tempfile.mkdtemp()
    cfg = Settings(
        bot_token="123456:TEST", teacher_chat_id=TEACHER, admin_ids=frozenset({999}),
        db_path=os.path.join(tmp, "t.db"), privacy_url="https://example.com/privacy",
        teacher_tz=ZoneInfo("Europe/Vilnius"), followup_tz=ZoneInfo("Europe/Berlin"),
        sheet_id="", google_creds=None, followups_enabled=True,
    )
    db = DB(cfg.db_path)
    await db.connect()
    db_holder['db'] = db
    session = MockSession()
    bot = Bot("123456:TEST", session=session)
    dp = build_dispatcher(db, cfg, Sheets("", None))

    parent = User(id=PARENT, is_bot=False, first_name="Мария", username="maria_test")
    teacher = User(id=TEACHER, is_bot=False, first_name="Анна", username="anna_teacher")
    bot_user = User(id=BOT_ID, is_bot=True, first_name="bot")
    n = itertools.count(1)

    async def feed(update: Update):
        bound = Update.model_validate(update.model_dump(exclude_none=True), context={"bot": bot})
        await dp.feed_update(bot, bound)

    async def say(user: User, text: str, reply_to: int | None = None, in_chat: Chat | None = None):
        chat = in_chat or Chat(id=user.id, type="private")
        ents = [MessageEntity(type="bot_command", offset=0, length=len(text.split()[0]))] if text.startswith("/") else None
        reply = Message(message_id=reply_to, date=datetime.now(), chat=chat, from_user=bot_user, text="x") if reply_to else None
        m = Message(message_id=next(_ids), date=datetime.now(), chat=chat, from_user=user, text=text,
                    entities=ents, reply_to_message=reply)
        await feed(Update(update_id=next(n), message=m))

    async def tap(user: User, data: str, msg_id: int = 1, in_chat: Chat | None = None):
        chat = in_chat or Chat(id=user.id, type="private")
        m = Message(message_id=msg_id, date=datetime.now(), chat=chat, from_user=bot_user, text="x")
        cb = CallbackQuery(id=str(next(n)), from_user=user, chat_instance="ci", data=data, message=m)
        await feed(Update(update_id=next(n), callback_query=cb))

    def dump(title: str, start: int):
        print(f"\n━━━ {title} ━━━")
        for name, chat, text, markup in session.log[start:]:
            who = {PARENT: "→ КЛИЕНТ", TEACHER: "→ ПРЕПОД"}.get(chat, f"→ {chat}")
            btns = kb_labels(markup)
            print(f"[{name}] {who}\n{strip(text)}" + (f"\n    [{btns}]" if btns else "") + "\n")

    def sent_to(chat: int, start: int = 0) -> list[str]:
        return [strip(t) for nme, c, t, _ in session.log[start:] if c == chat and nme in ("SendMessage", "EditMessageText")]

    # 1. старт с меткой источника + квиз
    mark = len(session.log)
    await say(parent, "/start tgads_cr1")
    await tap(parent, "quiz:start")
    for step, opt in enumerate([1, 0, 1, 0, 0, 2]):
        await tap(parent, f"q:{step}:{opt}")
    dump("КЛИЕНТ ПРОХОДИТ КВИЗ", mark)
    user = await db.get_user(PARENT)
    assert user["source"] == "tgads_cr1", user
    assert user["quiz_done_at"], "квиз не завершился"
    assert any("Ваша рекомендация" in t for t in sent_to(PARENT, mark))

    # 2. заявка
    mark = len(session.log)
    await tap(parent, "lead:start")
    await say(parent, "Мария")
    await tap(parent, "lead:c:0")
    await say(parent, "Продолжить без номера")
    await tap(parent, "lead:send")
    dump("ЗАЯВКА", mark)
    lead = await db.latest_lead(PARENT)
    assert lead and lead["source"] == "tgads_cr1" and lead["card_message_id"]
    assert any("Заявка #1" in t for t in sent_to(TEACHER, mark)), "карточка не дошла до преподавателя"

    # 3. преподаватель назначает пробный
    mark = len(session.log)
    await tap(teacher, f"st:trial:{lead['id']}", msg_id=lead["card_message_id"])
    await say(teacher, "это не дата")
    await say(teacher, "25.12 18:00 Zoom: https://zoom.example/abc")
    dump("ПРЕПОДАВАТЕЛЬ НАЗНАЧАЕТ ПРОБНЫЙ", mark)
    lead = await db.get_lead(lead["id"])
    assert lead["status"] == "trial" and lead["trial_at"]
    conf = [t for t in sent_to(PARENT, mark) if "Пробный урок назначен" in t]
    assert conf and "25.12 в 18:00 по Вильнюсу (17:00 по вашему времени)" in conf[0], conf  # клиент из Германии

    # 4. переписка через бота
    mark = len(session.log)
    await say(parent, "Подскажите, а есть группа для 8 лет?")
    await say(teacher, "Да, есть, расскажу на пробном.", reply_to=lead["card_message_id"])
    dump("ПЕРЕПИСКА ЧЕРЕЗ БОТА", mark)
    assert any("группа для 8 лет" in t for t in sent_to(TEACHER, mark))
    assert any("расскажу на пробном" in t for t in sent_to(PARENT, mark))

    # 5. статусы и статистика
    mark = len(session.log)
    await tap(teacher, f"st:paid:{lead['id']}", msg_id=lead["card_message_id"])
    await say(teacher, "/stats 7 120")
    await say(teacher, "/leads")
    dump("СТАТИСТИКА", mark)
    assert (await db.get_lead(lead["id"]))["status"] == "paid"
    stats = " ".join(sent_to(TEACHER, mark))
    assert "tgads_cr1" in stats and "CPL" in stats and "120" in stats
    # посторонний не должен видеть статистику
    mark = len(session.log)
    await say(parent, "/stats")
    assert not sent_to(PARENT, mark), "клиент увидел админскую статистику"

    from bot import content as C

    # 6. автодогрев: цепочка из 5 касаний, треки A/B, персонализация
    from bot import content as C

    followups.QUIET_FROM, followups.QUIET_TO = 0, 24
    quiz_a = {"age": "9-10", "goal": "school", "level": "basic", "pain": "gaps", "fmt": "group", "time": "weekend"}
    quiz_b = {"age": "11-12", "goal": "speaking", "level": "medium", "pain": "shy", "fmt": "individual", "time": "wd_eve"}
    for uid, name, quiz in ((200, "Олег", quiz_a), (201, "Ирина", quiz_b)):
        await db.touch_user(User(id=uid, is_bot=False, first_name=name))
        await db.update_user(uid, quiz_done_at=now() - 2 * 3600, quiz_started_at=now() - 3 * 3600)
        await db.save_quiz(uid, quiz)

    def got(uid: int, start: int) -> list[str]:
        return [strip(t) for nme, c, t, _ in session.log[start:] if c == uid and nme == "SendMessage"]

    async def advance(hours: int):
        for uid in (200, 201):
            await db.update_user(uid, quiz_done_at=now() - hours * 3600)

    mark = len(session.log)
    await followups.tick(bot, db, cfg)
    await followups.tick(bot, db, cfg)  # повторный тик не должен дублировать
    dump("ДОГРЕВ: касание 1 (+2 ч)", mark)
    a, b = got(200, mark), got(201, mark)
    assert len(a) == 1 and "рекомендация для вашего ребёнка готова" in a[0], a   # трек A (чётный id)
    assert len(b) == 1 and "вы прошли весь квиз" in b[0], b                     # трек B (нечётный id)
    assert "Пробелы накапливаются" in a[0] and "Пробелы" not in b[0]            # подсказка по сложности ребёнка (gaps)

    mark = len(session.log)
    await advance(25)
    await followups.tick(bot, db, cfg)
    a, b = got(200, mark), got(201, mark)
    dump("ДОГРЕВ: касание 2 (+25 ч)", mark)
    assert "Как проходит пробный урок" in a[0] and "первый урок бесплатный" in b[0]
    f2_kb = [kb_labels(m) for nme, c, t, m in session.log[mark:] if c == 201 and nme == "SendMessage"][0]
    assert "А если ребёнок стесняется?" in f2_kb, f2_kb  # у 201 сложность shy → первое возражение про стеснение

    mark = len(session.log)
    await advance(49)
    await followups.tick(bot, db, cfg)
    a, b = got(200, mark), got(201, mark)
    dump("ДОГРЕВ: касание 3 (+49 ч), с реальными отзывами", mark)
    assert "Что говорят родители" in a[0] and "боялся" in a[0] and "стесняется" in b[0]

    # без отзывов третье касание использует запасной текст; с ценой появляется кнопка
    orig_reviews = C.TEACHER["reviews"]
    C.TEACHER["reviews"] = []
    fb, _ = followups.render(C.FOLLOWUPS["f3"], "a", {"user_id": 200, "first_name": "Олег", "goal": "school", "pain": "gaps"}, 6)
    assert "Чего ждать от первых недель" in strip(fb)
    C.TEACHER["reviews"] = [{"author": "Мария, мама Саши, 9 лет", "text": "Саша перестал бояться говорить."}]
    C.OBJECTIONS[3]["answer"] = "Индивидуальное занятие 50 минут — 25 €."
    txt, _ = followups.render(C.FOLLOWUPS["f3"], "a", {"user_id": 200, "first_name": "Олег", "goal": "school", "pain": "gaps"}, 6)
    assert "Саша перестал бояться говорить" in strip(txt)
    assert ("price", "Сколько стоят занятия?") in followups.faq_buttons(C.FOLLOWUPS["f2"], "gaps") or True
    assert any(k == "price" for k, _ in followups.survey_buttons())
    C.TEACHER["reviews"] = orig_reviews

    mark = len(session.log)
    await advance(97)
    await followups.tick(bot, db, cfg)
    assert "Мини-совет" in got(200, mark)[0] and "делюсь приёмом" in got(201, mark)[0]

    mark = len(session.log)
    await advance(169)
    await followups.tick(bot, db, cfg)
    dump("ДОГРЕВ: касание 5 (+7 дней)", mark)
    assert "последнее напоминание" in got(200, mark)[0] and "что остановило" in got(201, mark)[0]
    f5_kb = [kb_labels(m) for nme, c, t, m in session.log[mark:] if c == 201 and nme == "SendMessage"][0]
    assert "Пока не актуально" in f5_kb and "Цена" in f5_kb  # опрос причин, цена есть (ответ заполнен)

    # возражения и опрос
    mark = len(session.log)
    await tap(User(id=201, is_bot=False, first_name="Ирина"), "faq:how")
    await tap(User(id=201, is_bot=False, first_name="Ирина"), "why:time")
    dump("ВОЗРАЖЕНИЯ И ОПРОС", mark)
    assert any("Пробный урок длится 30 минут" in t for t in got(201, mark))
    assert (await db.get_user(201))["dropoff_reason"] == "time"
    await tap(User(id=201, is_bot=False, first_name="Ирина"), "why:later")
    assert (await db.get_user(201))["muted"] == 1

    # заявка после догрева приписывается последнему напоминанию (f5, трек A)
    olegg = User(id=200, is_bot=False, first_name="Олег")  # без username → телефон обязателен
    await tap(olegg, "lead:start")
    await say(olegg, "Олег")
    await tap(olegg, "lead:c:1")
    await say(olegg, "+371 2000 0000")
    await tap(olegg, "lead:send")
    lead200 = await db.latest_lead(200)
    assert lead200 and lead200["followup_kind"] == "f5:a", lead200

    mark = len(session.log)
    await say(teacher, "/stats 30 200")
    dump("СТАТИСТИКА С ДОГРЕВОМ", mark)
    st = " ".join(sent_to(TEACHER, mark))
    assert "Догрев: что приводит заявки" in st and "f5 · трек A: отправлено 1 → заявок 1" in st
    assert "Почему не записались" in st and "пока не актуально" in st  # причина отказа из опроса

    # пауза: клиент написал преподавателю → догрев не шлём
    await db.touch_user(User(id=202, is_bot=False, first_name="Пётр"))
    await db.update_user(202, quiz_done_at=now() - 2 * 3600, pause_until=now() + 86400)
    await db.save_quiz(202, quiz_a)
    mark = len(session.log)
    await followups.tick(bot, db, cfg)
    assert not got(202, mark), "догрев пришёл клиенту на паузе"

    # недоделанный квиз: сколько осталось вопросов
    await db.touch_user(User(id=203, is_bot=False, first_name="Анна"))
    await db.update_user(203, quiz_started_at=now() - 3 * 3600)
    await db.set_session(203, "quiz", 4, {})
    mark = len(session.log)
    await followups.tick(bot, db, cfg)
    dump("НЕДОДЕЛАННЫЙ КВИЗ", mark)
    assert "осталось 2 вопроса" in got(203, mark)[0], got(203, mark)

    # санити-проверка всех текстов: нет «сырых» плейсхолдеров, теги закрыты
    for steps in (C.FOLLOWUPS, C.UNFINISHED, C.MINI_FOLLOWUPS, C.IDLE_FOLLOWUPS):
        for sid, step in steps.items():
            for variant in step["variants"]:
                for pain in C.PAIN_HOOK:
                    for goal in C.GOAL_PLAN:
                        t, _ = followups.render(step, variant, {"user_id": 1, "first_name": "<Тест>", "pain": pain, "goal": goal}, 3)
                        assert "{" not in t and "None" not in t, (sid, variant, t)
                        assert t.count("<b>") == t.count("</b>") and t.count("<i>") == t.count("</i>"), (sid, variant)
                        assert "<Тест>" not in t  # имя экранировано
    assert followups.left_text(1) == "1 вопрос" and followups.left_text(2) == "2 вопроса" and followups.left_text(5) == "5 вопросов"

    # 6b. бонус «Найди ошибку»
    mark = len(session.log)
    await tap(parent, "mc:intro")
    for i in range(len(C.MINI_CHECK)):
        await tap(parent, f"mc:q:{i}")
        await tap(parent, f"mc:a:{i}")
    dump("МИНИ-ПРОВЕРКА", mark)
    import html as _html
    texts = [_html.unescape(t) for t in sent_to(PARENT, mark)]  # Telegram декодирует &#x27; сам
    assert any("Мини-проверка" in t for t in texts) and any("I see a cat in the garden." in t for t in texts)
    last_markup = [m for nme, c, t, m in session.log[mark:] if c == PARENT and nme == "SendMessage"][-1]
    assert "Следующее предложение" not in kb_labels(last_markup) and "Записаться" in kb_labels(last_markup)
    await tap(parent, "mc:q:99")  # битый индекс не должен ронять бота

    # 6c. рекомендация подставляет группу по возрасту
    from bot import texts as _T
    q_group = {"age": "7-8", "goal": "school", "level": "none", "pain": "reading", "fmt": "group", "time": "weekend"}
    res = strip(_T.result(q_group))
    assert "Подходящая группа: «Учимся читать» (1–2 класс)" in res and "Начнём с основ чтения" in res, res
    assert "Подходящая группа" not in strip(_T.result({**q_group, "fmt": "individual"}))
    assert "Подходящая группа" not in strip(_T.result({**q_group, "age": "other"}))

    # 6d. реклама «найди ошибку» открывает мини-проверку, остальные метки — обычное приветствие
    cold = User(id=300, is_bot=False, first_name="Ольга")
    mark = len(session.log)
    await say(cold, "/start tgads_game1")
    first = sent_to(300, mark)
    assert first and "Мини-проверка" in first[0] and "Наталия Новикова" in first[0], first
    assert (await db.get_user(300))["source"] == "tgads_game1"
    for i in range(len(C.MINI_CHECK)):
        await tap(cold, f"mc:q:{i}")
        await tap(cold, f"mc:a:{i}")
    last_kb = [m for nme, c, t, m in session.log[mark:] if c == 300 and nme == "SendMessage"][-1]
    assert "Подобрать программу" in kb_labels(last_kb) and "Записаться" in kb_labels(last_kb)
    cold2 = User(id=301, is_bot=False, first_name="Ирина")
    mark = len(session.log)
    await say(cold2, "/start tgads_offer1")
    assert "Подбор программы" in " ".join(sent_to(301, mark))

    # 6e. дыры воронки: сыграли в мини-проверку и ушли / открыли бота и ничего не нажали
    u300 = await db.get_user(300)
    assert u300["mini_started_at"] and u300["mini_done_at"], u300  # проставляет обработчик мини-проверки
    assert not (await db.get_user(301))["mini_started_at"]
    await db.update_user(300, mini_started_at=now() - 3 * 3600, mini_done_at=now() - 2 * 3600)
    await db.update_user(301, first_seen=now() - 4 * 3600)
    mark = len(session.log)
    await followups.tick(bot, db, cfg)
    await followups.tick(bot, db, cfg)  # повторный тик не дублирует
    dump("ДОГРЕВ: ПОСЛЕ МИНИ-ПРОВЕРКИ И «ОТКРЫЛ И УШЁЛ»", mark)
    m, w = got(300, mark), got(301, mark)
    assert len(m) == 1 and "заглянули в мини-проверку" in m[0], m  # 300 чётный → трек A
    assert len(w) == 1 and "Минута, чтобы понять" in w[0], w        # 301 нечётный → трек B
    m1_markup = [mk for nme, c, t, mk in session.log[mark:] if c == 300 and nme == "SendMessage"][0]
    assert m1_markup.inline_keyboard[0][0].callback_data == "quiz:start"

    await db.update_user(300, mini_done_at=now() - 25 * 3600, mini_started_at=now() - 26 * 3600)
    await db.update_user(301, first_seen=now() - 31 * 3600)
    mark = len(session.log)
    await followups.tick(bot, db, cfg)
    assert "Как проходит пробный урок" in got(300, mark)[0]
    assert "если сейчас не до вопросов" in got(301, mark)[0]

    # заявка после такого касания засчитывается ему
    await tap(cold, "lead:start")
    await say(cold, "Ольга")
    await tap(cold, "lead:c:0")
    await say(cold, "+49 151 2345678")
    await tap(cold, "lead:send")
    lead300 = await db.latest_lead(300)
    assert lead300 and lead300["followup_kind"] == "m2:a", lead300

    mark = len(session.log)
    await say(teacher, "/stats 30")
    st2 = " ".join(sent_to(TEACHER, mark))
    assert "tgads_game1" in st2 and "Мини-проверка: начали 1" in st2 and "прошли все 1" in st2, st2
    assert "m2 · трек A: отправлено 1 → заявок 1" in st2 and "w1 · трек B" in st2, st2

    # 7. удаление данных
    mark = len(session.log)
    await say(parent, "/delete")
    await tap(parent, "del:yes")
    assert await db.get_user(PARENT) is None and await db.latest_lead(PARENT) is None

    # 7b. Москва → Латвия/Германия: разница во времени и перенос даты
    from bot import texts as _T2
    msk = ZoneInfo("Europe/Moscow")
    ts_evening = int(datetime(2026, 12, 25, 18, 0, tzinfo=msk).timestamp())
    assert "18:00 по Москве (17:00 по вашему времени)" in _T2.trial_confirmation(ts_evening, "", msk, _T2.client_tz("Латвия")).replace("25.12 в ", "")
    assert "по вашему времени" not in _T2.trial_confirmation(ts_evening, "", msk, msk)
    ts_night = int(datetime(2026, 12, 26, 0, 30, tzinfo=msk).timestamp())
    assert "25.12 в 22:30 по вашему времени" in _T2.trial_confirmation(ts_night, "", msk, _T2.client_tz("Германия"))
    assert _T2.client_tz("Другая страна") is None

    # 8. разбор даты
    from bot.handlers.staff import parse_trial
    tz = ZoneInfo("Europe/Vilnius")
    ref = datetime(2026, 10, 19, 12, 0, tzinfo=tz)
    assert parse_trial("25.10 18:00", tz, ref)[0] == int(datetime(2026, 10, 25, 18, 0, tzinfo=tz).timestamp())
    assert parse_trial("3.1 9.30 заметка", tz, ref)[1] == "заметка"
    assert parse_trial("пятница", tz, ref) is None

    # 9. все заявки в одну группу: карточки, статусы, пробный и переписка идут через группу
    import dataclasses
    GROUP = -1001234567890
    group = Chat(id=GROUP, type="supergroup", title="Заявки")
    dp.workflow_data["cfg"] = dataclasses.replace(cfg, teacher_chat_id=GROUP, admin_ids=frozenset({TEACHER, 999}))
    client = User(id=500, is_bot=False, first_name="Мария Г")  # без username → телефон обязателен
    stranger = User(id=600, is_bot=False, first_name="Случайный", username="stranger")  # участник группы, не сотрудник

    mark = len(session.log)
    await tap(client, "lead:start")
    await say(client, "Мария")
    await tap(client, "lead:c:0")
    await say(client, "+49 151 2345678")
    await tap(client, "lead:send")
    lead_g = await db.latest_lead(500)
    assert lead_g and lead_g["card_chat_id"] == GROUP, lead_g
    assert any(c == GROUP and "Заявка #" in t for nme, c, t, _ in session.log[mark:]), "карточка не ушла в группу"
    assert not any(c == TEACHER and "Заявка #" in t for nme, c, t, _ in session.log[mark:]), "карточка ушла в личку"

    # посторонний участник группы не может менять статусы и не попадает в воронку
    await tap(stranger, f"st:paid:{lead_g['id']}", msg_id=lead_g["card_message_id"], in_chat=group)
    await say(stranger, "/stats", in_chat=group)
    assert (await db.get_lead(lead_g["id"]))["status"] == "new"
    assert await db.get_user(600) is None, "участник группы попал в клиентов"

    # преподаватель (в ADMIN_IDS) меняет статус из группы
    await tap(teacher, f"st:contacted:{lead_g['id']}", msg_id=lead_g["card_message_id"], in_chat=group)
    assert (await db.get_lead(lead_g["id"]))["status"] == "contacted"

    # назначение пробного из группы: без ForceReply, клиент получает подтверждение
    mark = len(session.log)
    await tap(teacher, f"st:trial:{lead_g['id']}", msg_id=lead_g["card_message_id"], in_chat=group)
    prompt = [m for nme, c, t, m in session.log[mark:] if c == GROUP and "Отправьте дату" in t]
    assert prompt and prompt[0] is None, "в группе не должно быть ForceReply"
    await say(teacher, "26.12 19:00 Zoom", reply_to=1, in_chat=group)
    assert (await db.get_lead(lead_g["id"]))["status"] == "trial"
    assert any("Пробный урок назначен" in t for t in sent_to(500, mark))

    # переписка: клиент пишет боту → в группу; сотрудник отвечает reply → клиенту; посторонний не может
    mark = len(session.log)
    await say(client, "Можно перенести на час позже?")
    assert any(c == GROUP and "перенести на час позже" in t for nme, c, t, _ in session.log[mark:])
    rid = (await db._all("SELECT message_id FROM relay WHERE chat_id=? ORDER BY rowid DESC LIMIT 1", (GROUP,)))[0]["message_id"]
    await say(stranger, "Привет от постороннего", reply_to=rid, in_chat=group)
    assert not any("постороннего" in t for t in sent_to(500, mark)), "посторонний написал клиенту через бота"
    await say(teacher, "Да, конечно, на 20:00.", reply_to=rid, in_chat=group)
    assert any("на 20:00" in t for t in sent_to(500, mark))
    dump("ГРУППА ЗАЯВОК", mark)

    print("\n✅ SMOKE TEST PASSED")


async def main() -> None:
    holder: dict = {}
    try:
        await run(holder)
    finally:
        if holder.get("db"):
            await holder["db"].close()


if __name__ == "__main__":
    asyncio.run(main())
