"""Запуск: `uv run python -m habitbot`."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from . import reminders
from .bot import COMMANDS, build_router, send_reminder
from .db import Storage


def load_env(path: Path) -> None:
    """Минимальный разбор `.env`: строки KEY=VALUE, уже заданные переменные не трогаем."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    load_env(Path.cwd() / ".env")
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Нет BOT_TOKEN: получите токен у @BotFather и положите в .env")

    storage = Storage(
        # Адрес PostgreSQL важнее файла SQLite: на сервере задан он, локально — файл.
        os.environ.get("DATABASE_URL", "").strip() or os.environ.get("HABIT_DB", "habits.db"),
        default_tz=os.environ.get("DEFAULT_TZ", "Europe/Moscow"),
    )
    logging.getLogger(__name__).info("storage: %s", "postgresql" if storage.postgres else "sqlite")
    bot = Bot(token, default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True))
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(build_router(storage))
    await bot.set_my_commands(COMMANDS)

    loop = asyncio.create_task(reminders.run(storage, lambda habit: send_reminder(bot, habit)))
    try:
        await dispatcher.start_polling(bot)
    finally:
        loop.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
