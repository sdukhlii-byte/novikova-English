"""Сборка текстов из content.py. Здесь логика, не контент."""
from __future__ import annotations

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from . import content as C

STATUS = {
    "new": "🆕 Новая",
    "contacted": "✅ Связались",
    "trial": "📅 Пробный назначен",
    "paid": "💰 Оплатил",
    "lost": "❌ Не подошло",
}

DASH = "—"


def label(group: str, value: str | None) -> str:
    for text, val in C.CHOICE[group]["options"]:
        if val == value:
            return text
    return value or DASH


def teacher_who() -> str:
    t = C.TEACHER
    return t["first_name"] or t["name"] or "Преподаватель"


def _bullets(items: list[str]) -> list[str]:
    return [f"• {escape(i)}" for i in items if i]


def missing_content() -> list[str]:
    """Что ещё не заполнено в content.py (для предупреждения в логах)."""
    t = C.TEACHER
    return [k for k in ("name", "first_name", "bio", "credentials", "approach", "results", "reviews") if not t.get(k)]


# ─────────────────────────────── клиентские экраны ───────────────────────────

def welcome() -> str:
    t = C.TEACHER
    lines = [f"<b>{escape(C.BRAND['title'])}</b>"]
    if C.BRAND["tagline"]:
        lines.append(escape(C.BRAND["tagline"]))
    if t["name"]:
        meta = t["role"] + (f" · опыт {t['experience']}" if t["experience"] else "")
        lines += ["", f"<b>{escape(t['name'])}</b>", escape(meta)]
    lines += [
        "",
        "Нажмите «Подобрать программу»: 6 коротких вопросов за минуту, и вы получите:",
        "• персональную рекомендацию: с чего начать и на чём сделать упор;",
        "• подходящий формат занятий под ваш запрос;",
        f"• возможность записаться на бесплатный пробный урок ({escape(C.TRIAL['duration'])}).",
        "",
        "Уже всё решили? Запишитесь сразу, без вопросов. Бесплатно и без обязательств.",
    ]
    return "\n".join(lines)


def about_caption() -> str:
    t = C.TEACHER
    head = f"<b>{escape(t['name'])}</b>\n" if t["name"] else ""
    return head + escape(t["role"])


def about_body() -> str:
    t = C.TEACHER
    parts: list[str] = []
    if t["bio"]:
        parts.append(escape(t["bio"]))
    facts = []
    if t["experience"]:
        facts.append(f"Опыт: {escape(t['experience'])}")
    if t["languages"]:
        facts.append(f"Язык занятий: {escape(t['languages'])}")
    if facts:
        parts.append("\n".join(facts))
    if t["credentials"]:
        parts.append("<b>Образование и квалификация</b>\n" + "\n".join(_bullets(t["credentials"])))
    if t["approach"]:
        parts.append("<b>Подход к занятиям</b>\n" + "\n".join(_bullets(t["approach"])))
    if t["results"]:
        parts.append("<b>Результаты</b>\n" + "\n".join(_bullets(t["results"])))
    if not parts:
        parts.append("Подробная информация о преподавателе скоро появится.")
    return "\n\n".join(parts)


def has_reviews() -> bool:
    return bool(C.TEACHER["reviews"])


def reviews() -> str:
    out = ["<b>Отзывы</b>"]
    for r in C.TEACHER["reviews"]:
        out.append(f"«{escape(r['text'])}»\n<i>{escape(r['author'])}</i>")
    return "\n\n".join(out)


def faq_items() -> list[dict]:
    """Возражения/вопросы, у которых заполнен ответ."""
    return [o for o in C.OBJECTIONS if o["answer"].strip()]


def faq_answer(faq_id: str) -> str | None:
    for o in faq_items():
        if o["id"] == faq_id:
            return escape(o["answer"].replace("{duration}", C.TRIAL["duration"]))
    return None


def mini_intro(cold: bool = False) -> str:
    """cold=True: человек пришёл из рекламы «найди ошибку», сразу представляем автора заданий."""
    head = "🎯 <b>Мини-проверка: найди ошибку</b>\n\n"
    if cold and C.TEACHER["name"]:
        head += f"Задания от {escape(C.TEACHER['name'])}, преподавателя английского для детей 7–12 лет.\n\n"
    return head + escape(C.MINI_CHECK_INTRO)


def mini_question(i: int) -> str:
    item = C.MINI_CHECK[i]
    return (
        f"<b>Предложение {i + 1} из {len(C.MINI_CHECK)}</b> · {escape(item['tag'])}\n\n"
        f"❌ <b>{escape(item['wrong'])}</b>\n\n"
        "Где ошибка?"
    )


def mini_answer(i: int) -> str:
    item = C.MINI_CHECK[i]
    return (
        f"❌ {escape(item['wrong'])}\n"
        f"✅ <b>{escape(item['right'])}</b>\n\n"
        f"{escape(item['why'])}"
    )


def contact() -> str:
    u = C.TEACHER["contact_username"]
    if u:
        return (
            f"Напишите напрямую: {escape(u)}\n\n"
            "Или отправьте вопрос прямо здесь, он будет передан преподавателю, а ответ придёт в этот чат."
        )
    return "Напишите вопрос прямо здесь, он будет передан преподавателю, а ответ придёт в этот чат."


def result(q: dict) -> str:
    """Итог квиза: персональная рекомендация (не тест знаний)."""
    lines = [
        "<b>Ваша рекомендация</b>",
        "",
        f"Возраст: {escape(label('age', q['age']))}",
        f"Цель: {escape(label('goal', q['goal']))}",
        f"Уровень (по вашей оценке): {escape(label('level', q['level']))}",
        f"Главная задача: {escape(label('pain', q['pain']))}",
        "",
        "<b>С чего начать</b>",
        escape(C.LEVEL_PLAN[q["level"]]),
        "",
        "<b>На чём сделать упор</b>",
        escape(C.GOAL_PLAN[q["goal"]]),
        escape(C.PAIN_PLAN[q["pain"]]),
        "",
        "<b>Формат</b>",
        escape(C.FORMAT_PLAN[q["fmt"]][0].upper() + C.FORMAT_PLAN[q["fmt"]][1:]) + ".",
        *_group_line(q),
        "",
        "<i>Это предварительный план по вашим ответам. Точный уровень и программу преподаватель определит на пробном уроке.</i>",
    ]
    return "\n".join(lines)


def _group_line(q: dict) -> list[str]:
    """Для группового формата подсказываем группу, подходящую по возрасту."""
    g = C.GROUPS.get(q.get("age"))
    if g and q.get("fmt") in ("group", "unsure"):
        return [f"Подходящая группа: «{escape(g['name'])}» ({escape(g['grades'])})."]
    return []


def offer() -> str:
    tr = C.TRIAL
    lines = [
        f"🎁 <b>{escape(tr['title'])}</b> · {escape(tr['duration'])}",
        "",
        f"На пробном уроке {escape(teacher_who())}:" if C.TEACHER["first_name"] else "На пробном уроке преподаватель:",
        *_bullets(tr["includes"]),
    ]
    if tr["note"]:
        lines += ["", escape(tr["note"])]
    return "\n".join(lines)


# ─────────────────────────────── заявка ──────────────────────────────────────

def lead_summary(d: dict, quiz: dict | None, privacy_url: str) -> str:
    contact_bits = []
    if d.get("username"):
        contact_bits.append(f"@{escape(d['username'])}")
    if d.get("phone"):
        contact_bits.append(escape(d["phone"]))
    lines = [
        "<b>Проверьте заявку</b>",
        "",
        f"Имя: {escape(d['name'])}",
        f"Страна: {escape(d.get('country') or DASH)}",
        f"Контакт: {' · '.join(contact_bits) if contact_bits else DASH}",
    ]
    if quiz:
        lines += [
            "",
            f"Ребёнок: {escape(label('age', quiz['age']))} · цель: {escape(label('goal', quiz['goal']).lower())}",
            f"Удобное время: {escape(label('time', quiz['time_pref']).lower())}",
        ]
    consent = "Нажимая «Отправить», вы соглашаетесь на обработку данных для связи по вопросу занятий."
    if privacy_url:
        consent += f' <a href="{escape(privacy_url)}">Политика конфиденциальности</a>.'
    lines += ["", f"<i>{consent}</i>"]
    return "\n".join(lines)


def thanks(response_time: str) -> str:
    who = teacher_who()
    return (
        "✅ <b>Заявка отправлена</b>\n\n"
        f"{escape(who)} свяжется с вами {escape(response_time)}, чтобы согласовать время пробного урока.\n\n"
        "Если появятся вопросы, напишите их сюда, сообщение будет передано преподавателю."
    )


def _fmt_dt(ts: int, tz: ZoneInfo) -> str:
    dt = datetime.fromtimestamp(ts, tz)
    return f"{dt:%d.%m} в {dt:%H:%M}"


def tz_name(tz: ZoneInfo) -> str:
    return tz.key.split("/")[-1].replace("_", " ")


TZ_LABEL = {"Europe/Moscow": "по Москве", "Europe/Vilnius": "по Вильнюсу", "Europe/Kiev": "по Киеву"}


def tz_label(tz: ZoneInfo) -> str:
    return TZ_LABEL.get(tz.key, f"({tz_name(tz)})")


def client_tz(country: str | None) -> ZoneInfo | None:
    key = C.COUNTRY_TZ.get(country or "")
    return ZoneInfo(key) if key else None


def _when(ts: int, tz: ZoneInfo, ctz: ZoneInfo | None) -> str:
    """«25.12 в 18:00 по Москве (16:00 по вашему времени)»; если клиент в том же поясе, без скобок."""
    base = f"{_fmt_dt(ts, tz)} {tz_label(tz)}"
    if ctz is not None and ctz.key != tz.key:
        mine, local = datetime.fromtimestamp(ts, tz), datetime.fromtimestamp(ts, ctz)
        shown = f"{local:%H:%M}" if mine.date() == local.date() else f"{local:%d.%m} в {local:%H:%M}"
        base += f" ({shown} по вашему времени)"
    return base


def trial_confirmation(ts: int, note: str, tz: ZoneInfo, ctz: ZoneInfo | None = None) -> str:
    lines = ["📅 <b>Пробный урок назначен</b>", "", _when(ts, tz, ctz)]
    if note:
        lines += ["", escape(note)]
    lines += ["", "За сутки и за час до урока пришлём напоминание."]
    return "\n".join(lines)


def trial_reminder(kind: str, ts: int, note: str, tz: ZoneInfo, ctz: ZoneInfo | None = None) -> str:
    when = "завтра" if kind == "r24" else "через час"
    lines = [f"⏰ Напоминаем: пробный урок {when}, {_when(ts, tz, ctz)}."]
    if note:
        lines += ["", escape(note)]
    return "\n".join(lines)


# ─────────────────────────────── карточка преподавателю ──────────────────────

def card(lead: dict, quiz: dict | None, tz: ZoneInfo) -> str:
    lines = [f"<b>Заявка #{lead['id']}</b> · {STATUS.get(lead['status'], lead['status'])}", ""]
    lines.append(f"👤 <b>{escape(lead['name'])}</b>" + (f" · {escape(lead['country'])}" if lead.get("country") else ""))
    contact_bits = []
    if lead.get("username"):
        contact_bits.append(f"@{escape(lead['username'])}")
    if lead.get("phone"):
        contact_bits.append(escape(lead["phone"]))
    contact_bits.append(f'<a href="tg://user?id={lead["user_id"]}">профиль</a>')
    lines.append("📞 " + " · ".join(contact_bits))
    if quiz:
        lines += [
            "",
            f"👶 Возраст: {escape(label('age', quiz['age']))}",
            f"🎯 Цель: {escape(label('goal', quiz['goal']))}",
            f"📊 Уровень (со слов): {escape(label('level', quiz['level']))}",
            f"⚠️ Сложность: {escape(label('pain', quiz['pain']))}",
            f"👥 Формат: {escape(label('fmt', quiz['fmt']))}",
            f"🕒 Время: {escape(label('time', quiz['time_pref']))}",
        ]
    else:
        lines += ["", "Квиз не проходили (заявка из меню)."]
    lines += ["", f"🔗 Источник: {escape(lead.get('source') or 'direct')}"]
    if lead.get("trial_at"):
        lines.append(f"📅 Пробный: {_fmt_dt(lead['trial_at'], tz)} ({escape(tz_name(tz))})")
        if lead.get("note"):
            lines.append(f"📝 {escape(lead['note'])}")
    lines += ["", "<i>Ответьте на это сообщение, и текст уйдёт родителю в бот.</i>"]
    return "\n".join(lines)
