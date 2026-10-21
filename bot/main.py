from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from . import followups
from . import texts as T
from .config import load_settings
from .db import DB
from .handlers import common, lead, mini, quiz, staff
from .middleware import TouchUser
from .sheets import Sheets

log = logging.getLogger("bot")


def build_dispatcher(db: DB, cfg, sheets: Sheets) -> Dispatcher:
    dp = Dispatcher(db=db, cfg=cfg, sheets=sheets)
    dp.update.outer_middleware(TouchUser())
    # порядок важен: staff раньше lead, чтобы ответы преподавателя не уходили в обработку «клиентских» текстов
    dp.include_routers(staff.router, common.router, quiz.router, mini.router, lead.router)
    return dp


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_settings()

    missing = T.missing_content()
    if missing:
        log.warning("В bot/content.py не заполнено: %s (бот работает, эти блоки просто скрыты)", ", ".join(missing))
    if not cfg.teacher_chat_id and not cfg.admin_ids:
        log.warning("TEACHER_CHAT_ID и ADMIN_IDS не заданы: заявки будет некуда отправлять!")

    db = DB(cfg.db_path)
    await db.connect()
    sheets = Sheets(cfg.sheet_id, cfg.google_creds)
    bot = Bot(
        cfg.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    dp = build_dispatcher(db, cfg, sheets)

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Начать"),
            BotCommand(command="about", description="О преподавателе"),
            BotCommand(command="faq", description="Частые вопросы"),
            BotCommand(command="contact", description="Задать вопрос"),
            BotCommand(command="privacy", description="Конфиденциальность"),
            BotCommand(command="delete", description="Удалить мои данные"),
        ]
    )

    task = asyncio.create_task(followups.run(bot, db, cfg)) if cfg.followups_enabled else None
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if task:
            task.cancel()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
