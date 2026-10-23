from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import content as C
from . import texts as T


def main_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="▶️ Подобрать программу (6 вопросов, 1 мин)", callback_data="quiz:start")
    b.button(text="📅 Сразу записаться на пробный", callback_data="lead:start")
    b.button(text="👩‍🏫 О преподавателе", callback_data="menu:about")
    if T.has_reviews():
        b.button(text="⭐ Отзывы", callback_data="menu:reviews")
    if T.faq_items():
        b.button(text="❓ Частые вопросы", callback_data="menu:faq")
    b.adjust(1)
    return b.as_markup()


def after_result() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📅 Записаться на бесплатный пробный урок", callback_data="lead:start")
    b.button(text="👩‍🏫 О преподавателе", callback_data="menu:about")
    if T.has_reviews():
        b.button(text="⭐ Отзывы", callback_data="menu:reviews")
    if T.faq_items():
        b.button(text="❓ Частые вопросы", callback_data="menu:faq")
    if C.MINI_CHECK:
        b.button(text="🎯 Мини-проверка: найди ошибку", callback_data="mc:intro")
    b.button(text="🔁 Пройти заново", callback_data="quiz:start")
    b.adjust(1)
    return b.as_markup()


def after_about() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="▶️ Подобрать программу (6 вопросов, 1 мин)", callback_data="quiz:start")
    b.button(text="📅 Сразу записаться на пробный", callback_data="lead:start")
    if T.has_reviews():
        b.button(text="⭐ Отзывы", callback_data="menu:reviews")
    b.adjust(1)
    return b.as_markup()


def quiz_step(step: int, options: list[str], with_back: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for i, text in enumerate(options):
        b.button(text=text, callback_data=f"q:{step}:{i}")
    short = all(len(o) <= 16 for o in options)
    b.adjust(2 if short and len(options) > 2 else 1)
    if with_back:
        b.row(InlineKeyboardButton(text="← Назад", callback_data=f"q:{step}:b"))
    return b.as_markup()


def use_name(first_name: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"Использовать «{first_name[:30]}»", callback_data="lead:usename")
    b.button(text="Отмена", callback_data="lead:cancel")
    b.adjust(1)
    return b.as_markup()


def countries() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for i, c in enumerate(C.COUNTRIES):
        b.button(text=c, callback_data=f"lead:c:{i}")
    b.adjust(2)
    return b.as_markup()


def phone_request(can_skip: bool) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]]
    if can_skip:
        rows.append([KeyboardButton(text="Продолжить без номера")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def confirm_lead() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Отправить заявку", callback_data="lead:send")
    b.button(text="✏️ Изменить данные", callback_data="lead:edit")
    b.button(text="Отмена", callback_data="lead:cancel")
    b.adjust(1)
    return b.as_markup()


def followup(
    kind: str,
    cta: str,
    faq: list[tuple[str, str]] | None = None,
    ask: bool = False,
    survey: list[tuple[str, str]] | None = None,
    action: str | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура напоминания: главная кнопка, вопросы-возражения, опрос причин и «Не напоминать»."""
    b = InlineKeyboardBuilder()
    if action == "quiz":
        b.button(text=cta, callback_data="quiz:start")
    elif action == "lead":
        b.button(text=cta, callback_data="lead:start")
    elif kind.startswith("u"):
        b.button(text=cta, callback_data="quiz:resume")
    else:
        b.button(text=cta, callback_data="lead:start")
    for faq_id, label in faq or []:
        b.button(text=f"❓ {label}", callback_data=f"faq:{faq_id}")
    if ask:
        b.button(text="💬 Задать вопрос", callback_data="menu:ask")
    for key, label in survey or []:
        b.button(text=label, callback_data=f"why:{key}")
    b.button(text="Не напоминать", callback_data="fu:mute")
    b.adjust(1)
    return b.as_markup()


def faq_menu(exclude: str | None = None) -> InlineKeyboardMarkup:
    """Список частых вопросов + кнопка записи."""
    b = InlineKeyboardBuilder()
    b.button(text="📅 Записаться на пробный урок", callback_data="lead:start")
    for item in T.faq_items():
        if item["id"] != exclude:
            b.button(text=f"❓ {item['button']}", callback_data=f"faq:{item['id']}")
    b.button(text="💬 Задать свой вопрос", callback_data="menu:ask")
    b.adjust(1)
    return b.as_markup()


def delete_confirm() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="Да, удалить мои данные", callback_data="del:yes")
    b.button(text="Отмена", callback_data="del:no")
    b.adjust(1)
    return b.as_markup()


def lead_actions(lead_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Связались", callback_data=f"st:contacted:{lead_id}")
    b.button(text="📅 Назначить пробный", callback_data=f"st:trial:{lead_id}")
    b.button(text="💰 Оплатил", callback_data=f"st:paid:{lead_id}")
    b.button(text="❌ Не подошло", callback_data=f"st:lost:{lead_id}")
    b.adjust(2)
    return b.as_markup()


def mini_start() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="Начать", callback_data="mc:q:0")
    b.adjust(1)
    return b.as_markup()


def mini_question(i: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="Показать ответ", callback_data=f"mc:a:{i}")
    b.adjust(1)
    return b.as_markup()


def mini_answer(i: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if i + 1 < len(C.MINI_CHECK):
        b.button(text="Следующее предложение", callback_data=f"mc:q:{i + 1}")
    else:
        b.button(text="▶️ Подобрать программу (1 минута)", callback_data="quiz:start")
    b.button(text="📅 Записаться на бесплатный пробный урок", callback_data="lead:start")
    b.adjust(1)
    return b.as_markup()
