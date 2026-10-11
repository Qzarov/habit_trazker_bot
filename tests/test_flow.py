"""Сценарии бота целиком: обновления подаются в диспетчер, Telegram подменён."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, TelegramMethod
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from habitbot.bot import build_router, reminder_message
from habitbot.db import Storage

USER = User(id=42, is_bot=False, first_name="Тест")
CHAT = Chat(id=42, type="private")


class FakeSession(BaseSession):
    """Вместо сети: запоминает вызовы и отвечает правдоподобным сообщением."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []

    async def close(self) -> None: ...

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, AnswerCallbackQuery):
            return True
        return Message(message_id=1000 + len(self.calls), date=datetime.now(), chat=CHAT)

    async def stream_content(self, *args, **kwargs):
        yield b""


class Harness:
    def __init__(self) -> None:
        self.storage = Storage(":memory:")
        self.session = FakeSession()
        self.bot = Bot(
            "42:TEST", session=self.session, default=DefaultBotProperties(parse_mode="HTML")
        )
        self.dp = Dispatcher(storage=MemoryStorage())
        self.dp.include_router(build_router(self.storage))
        self.n = 0

    async def say(self, text: str) -> TelegramMethod[Any]:
        self.n += 1
        message = Message(
            message_id=self.n, date=datetime.now(), chat=CHAT, from_user=USER, text=text
        )
        await self.dp.feed_update(self.bot, Update(update_id=self.n, message=message))
        return self.session.calls[-1]

    async def press(self, data: str) -> list[TelegramMethod[Any]]:
        self.n += 1
        before = len(self.session.calls)
        query = CallbackQuery(
            id=str(self.n),
            from_user=USER,
            chat_instance="x",
            data=data,
            message=Message(message_id=500, date=datetime.now(), chat=CHAT, text="…"),
        )
        await self.dp.feed_update(self.bot, Update(update_id=self.n, callback_query=query))
        return self.session.calls[before:]


def buttons(call: TelegramMethod[Any]) -> dict[str, str]:
    markup = getattr(call, "reply_markup", None)
    return {b.text: b.callback_data for row in markup.inline_keyboard for b in row}


def find(labels: dict[str, str], part: str) -> str:
    return next(data for text, data in labels.items() if part in text)


@pytest.fixture
def bot() -> Harness:
    return Harness()


async def test_add_good_habit_with_reminder_and_mark_it(bot: Harness) -> None:
    assert "Как называется" in (await bot.say("/add")).text
    kinds = buttons(await bot.say("Зарядка"))
    calls = await bot.press(find(kinds, "Полезная"))
    assert "Во сколько напоминать" in calls[0].text
    card = await bot.say("8:00")
    assert "Зарядка" in card.text and "каждый день в 08:00" in card.text

    [habit] = bot.storage.list_habits(42)
    assert (habit.kind, habit.remind_at) == ("good", "08:00")

    today = await bot.say("/today")
    calls = await bot.press(find(buttons(today), "Зарядка"))
    assert "сделано" in next(iter(buttons(calls[0])))
    assert len(bot.storage.all_marks(habit.id)) == 1
    # Повторное нажатие снимает отметку.
    await bot.press(find(buttons(today), "Зарядка"))
    assert bot.storage.all_marks(habit.id) == set()


async def test_bad_habit_without_reminder_calendar_and_delete(bot: Harness) -> None:
    await bot.say("/add")
    kinds = buttons(await bot.say("Сладкое"))
    await bot.press(find(kinds, "Вредная"))
    calls = await bot.press("h:norem:0")
    card = buttons(calls[0])
    assert "вредная" in calls[0].text and "Напоминание: нет" in calls[0].text

    [habit] = bot.storage.list_habits(42)
    await bot.press(find(card, "Сегодня"))
    assert len(bot.storage.all_marks(habit.id)) == 1

    photo = (await bot.press(find(card, "Календарь")))[0]
    assert type(photo).__name__ == "SendPhoto"
    assert "Срывов за месяц: <b>1</b>" in photo.caption

    confirm = buttons((await bot.press(find(card, "Удалить")))[0])
    await bot.press(find(confirm, "Да, удалить"))
    assert bot.storage.list_habits(42) == []


async def test_several_habits_and_reminder_answer(bot: Harness) -> None:
    for name, kind in (("Зарядка", "Полезная"), ("Чтение", "Полезная"), ("Курение", "Вредная")):
        await bot.say("/add")
        await bot.press(find(buttons(await bot.say(name)), kind))
        await bot.press("h:norem:0")
    listing = await bot.say("/habits")
    assert len(buttons(listing)) == 3

    smoking = bot.storage.list_habits(42)[2]
    _, markup = reminder_message(smoking)
    answers = {b.text: b.callback_data for row in markup.inline_keyboard for b in row}
    calls = await bot.press(find(answers, "Было"))
    assert "Записал: было" in calls[0].text
    assert len(bot.storage.all_marks(smoking.id)) == 1
    await bot.press(find(answers, "Не было"))
    assert bot.storage.all_marks(smoking.id) == set()


async def test_change_reminder_and_timezone(bot: Harness) -> None:
    await bot.say("/add")
    await bot.press(find(buttons(await bot.say("Вода")), "Полезная"))
    card = buttons((await bot.press("h:norem:0"))[0])
    await bot.press(find(card, "Напоминание"))
    assert "Не понял время" in (await bot.say("вечером")).text
    assert "каждый день в 21:30" in (await bot.say("21.30")).text

    await bot.say("/tz")
    assert "Не знаю такой пояс" in (await bot.say("Марс")).text
    assert "Etc/GMT-5" in (await bot.say("+5")).text
    assert bot.storage.get_tz(42) == "Etc/GMT-5"


async def test_foreign_habit_button_is_rejected(bot: Harness) -> None:
    other = bot.storage.add_habit(7, "Чужая", "good", None, datetime.now().date())
    calls = await bot.press(f"h:open:{other.id}")
    assert isinstance(calls[0], AnswerCallbackQuery) and calls[0].show_alert
