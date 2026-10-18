"""Напоминания: раз в полминуты смотрим, кому пора написать."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .db import Habit, Storage

log = logging.getLogger(__name__)

#: Если бот в нужную минуту не работал, напоминание ещё уместно в течение этого времени.
GRACE = timedelta(minutes=30)

_TIME = re.compile(r"^\s*(\d{1,2})[:.\s-]?(\d{2})\s*$")


def parse_time(text: str) -> str | None:
    """«9:30», «21.00», «0745» → «09:30», «21:00», «07:45»; иначе `None`."""
    match = _TIME.match(text)
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        return None
    return f"{hours:02d}:{minutes:02d}"


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def is_due(habit: Habit, now: datetime) -> bool:
    """Пора ли напомнить: время наступило (не позже `GRACE` назад) и сегодня ещё не писали."""
    if not habit.reminds or habit.last_reminded == now.date():
        return False
    hours, minutes = map(int, habit.remind_at.split(":"))
    at = now.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    return at <= now < at + GRACE


async def run(
    storage: Storage,
    send: Callable[[Habit, date], Awaitable[None]],
    *,
    interval: float = 30.0,
) -> None:
    """Бесконечный цикл напоминаний. `send` — отправка сообщения по привычке про данный день."""
    while True:
        try:
            await tick(storage, send)
        except Exception:  # цикл не должен умирать из-за одной ошибки
            log.exception("reminder tick failed")
        await asyncio.sleep(interval)


async def tick(
    storage: Storage,
    send: Callable[[Habit, date], Awaitable[None]],
    now_utc: datetime | None = None,
) -> int:
    """Один проход; возвращает число отправленных напоминаний."""
    now_utc = now_utc or datetime.now(tz=ZoneInfo("UTC"))
    sent = 0
    for habit in storage.habits_with_reminders():
        now = now_utc.astimezone(zone(storage.get_tz(habit.user_id)))
        if not is_due(habit, now):
            continue
        # Отмечаем до отправки: упавшая отправка не должна слать одно и то же каждые полминуты.
        storage.mark_reminded(habit.id, now.date())
        # Напоминание про вчерашний день, если оно «на следующий день», иначе — про сегодняшний.
        day = now.date() - timedelta(days=1) if habit.remind_next_day else now.date()
        # До заведения привычки спрашивать не о чем.
        if day < habit.created_on:
            continue
        # Человек уже ответил сам — любым ответом, у полезной и у вредной: не дёргаем.
        if storage.get_answer(habit.id, day) is not None:
            continue
        try:
            await send(habit, day)
            sent += 1
        except Exception:
            log.exception("reminder for habit %s not sent", habit.id)
    return sent
