"""Разовая рассылка вопроса о стиле мотивации тем, кого о нём ещё не спрашивали.

    uv run python -m habitbot.ask_motivation            # показать, кому уйдёт, без отправки
    uv run python -m habitbot.ask_motivation --send     # отправить

Сообщение то же, что на /start, с теми же кнопками. После отправки вопрос считается заданным:
повторный запуск и /start его не повторят. Заблокировавшим бота — пропуск, вопрос всё равно
отмечается заданным, чтобы не стучаться к ним снова.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from .__main__ import load_env
from .bot import motivation_question
from .db import Storage

#: Пауза между сообщениями: Telegram пропускает около 30 сообщений в секунду.
PAUSE = 0.05

log = logging.getLogger(__name__)


async def broadcast(storage: Storage, bot: Bot, send: bool) -> dict[str, int]:
    """Рассылает вопрос; возвращает счётчики: отправлено, заблокировали, ошибки, всего."""
    users = storage.unasked_about_motivation()
    counts = {"sent": 0, "blocked": 0, "failed": 0, "total": len(users)}
    if not send:
        return counts
    text, kb = motivation_question()
    for user_id in users:
        for _ in range(3):
            try:
                await bot.send_message(user_id, text, reply_markup=kb)
                counts["sent"] += 1
                storage.ask_motivation_once(user_id)
            except TelegramRetryAfter as error:
                await asyncio.sleep(error.retry_after)
                continue
            except TelegramForbiddenError:
                counts["blocked"] += 1
                storage.ask_motivation_once(user_id)
            except Exception:
                log.exception("user %s not asked", user_id)
                counts["failed"] += 1
            break
        await asyncio.sleep(PAUSE)
    return counts


async def main(send: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env(Path.cwd() / ".env")
    storage = Storage(
        os.environ.get("DATABASE_URL", "").strip() or os.environ.get("HABIT_DB", "habits.db")
    )
    proxy = os.environ.get("PROXY_URL", "").strip() or None
    bot = Bot(
        os.environ["BOT_TOKEN"].strip(),
        session=AiohttpSession(proxy=proxy) if proxy else None,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    try:
        counts = await broadcast(storage, bot, send)
    finally:
        await bot.session.close()
    if send:
        print(
            f"Отправлено: {counts['sent']} из {counts['total']}, "
            f"заблокировали бота: {counts['blocked']}, ошибки: {counts['failed']}"
        )
    else:
        print(f"Получат вопрос: {counts['total']}. Отправить: --send")


if __name__ == "__main__":
    asyncio.run(main("--send" in sys.argv[1:]))
