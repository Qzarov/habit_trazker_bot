"""Сценарии бота целиком: обновления подаются в диспетчер, Telegram подменён."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, GetMe, TelegramMethod
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Update, User

from habitbot.bot import (
    BTN_ADD,
    BTN_HABITS,
    BTN_TODAY,
    BTN_TZ,
    build_router,
    reminder_message,
)
from habitbot.db import Storage

MENU = [BTN_TODAY, BTN_HABITS, "🗓 Календарь", BTN_ADD, BTN_TZ, "🤝 Пригласить", "❓ Помощь"]
USER = User(id=42, is_bot=False, first_name="Тест")
CHAT = Chat(id=42, type="private")


class FakeSession(BaseSession):
    """Вместо сети: запоминает вызовы и отвечает правдоподобным сообщением."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []

    async def close(self) -> None: ...

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, GetMe):  # служебный запрос имени бота — не ответ пользователю
            return User(id=99, is_bot=True, first_name="Бот", username="test_bot")
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

    async def say(self, text: str, user: User = USER) -> TelegramMethod[Any]:
        self.n += 1
        message = Message(
            message_id=self.n,
            date=datetime.now(),
            chat=Chat(id=user.id, type="private"),
            from_user=user,
            text=text,
        )
        await self.dp.feed_update(self.bot, Update(update_id=self.n, message=message))
        return self.session.calls[-1]

    async def press(self, data: str, photo: bool = False) -> list[TelegramMethod[Any]]:
        """Нажатие инлайн-кнопки; `photo` — кнопка под сообщением с картинкой."""
        self.n += 1
        before = len(self.session.calls)
        source = Message(
            message_id=500,
            date=datetime.now(),
            chat=CHAT,
            text=None if photo else "…",
            photo=[PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)]
            if photo
            else None,
        )
        query = CallbackQuery(
            id=str(self.n), from_user=USER, chat_instance="x", data=data, message=source
        )
        await self.dp.feed_update(self.bot, Update(update_id=self.n, callback_query=query))
        return self.session.calls[before:]


def rows(call: TelegramMethod[Any]) -> list[list[str]]:
    return [[b.text for b in row] for row in call.reply_markup.inline_keyboard]


def buttons(call: TelegramMethod[Any]) -> dict[str, str]:
    markup = getattr(call, "reply_markup", None)
    return {b.text: b.callback_data for row in markup.inline_keyboard for b in row}


def find(labels: dict[str, str], part: str) -> str:
    return next(data for text, data in labels.items() if part in text)


@pytest.fixture
def bot() -> Harness:
    return Harness()


async def add(bot: Harness, name: str, kind: str, emoji: str, time: str | None = None) -> Any:
    """Проходит добавление кнопками; возвращает вызов с карточкой привычки."""
    await bot.press("n:add")
    kinds = buttons(await bot.say(name))
    palette = buttons((await bot.press(find(kinds, kind)))[0])
    times = buttons((await bot.press(palette[emoji]))[0])
    if time is None:
        return (await bot.press(find(times, "Без напоминания")))[0]
    return (await bot.press(times[time]))[0]


async def test_start_shows_inline_menu_and_drops_old_keyboard(bot: Harness) -> None:
    await bot.say("/start")
    greeting, menu = bot.session.calls[-2:]
    assert type(greeting.reply_markup).__name__ == "ReplyKeyboardRemove"
    assert list(buttons(menu)) == MENU


async def test_every_screen_leads_back_to_menu(bot: Harness) -> None:
    for action in ("today", "habits", "help"):
        screen = (await bot.press(f"n:{action}"))[0]
        back = (await bot.press(find(buttons(screen), "Меню")))[0]
        assert list(buttons(back)) == MENU
    # Произвольный текст вне диалога тоже приводит в меню.
    assert BTN_TODAY in buttons(await bot.say("что ты умеешь?"))


async def test_add_with_buttons_only_and_mark(bot: Harness) -> None:
    card = await add(bot, "Зарядка", "Полезная", "💪", "08:00")
    assert "💪 <b>Зарядка</b>" in card.text and "каждый день в 08:00" in card.text

    [habit] = bot.storage.list_habits(42)
    assert (habit.kind, habit.emoji, habit.remind_at) == ("good", "💪", "08:00")

    today = (await bot.press("n:today"))[0]
    calls = await bot.press(find(buttons(today), "Зарядка"))
    assert next(iter(buttons(calls[0]))) == "✅ 💪 Зарядка"
    assert len(bot.storage.all_marks(habit.id)) == 1
    await bot.press(find(buttons(today), "Зарядка"))
    assert bot.storage.all_marks(habit.id) == set()


async def test_own_emoji_and_typed_time(bot: Harness) -> None:
    await bot.press("n:add")
    await bot.press(find(buttons(await bot.say("Гитара")), "Полезная"))
    assert "не похоже на эмодзи" in (await bot.say("привет")).text
    await bot.say("🎻")
    card = await bot.say("19.45")
    assert "🎻 <b>Гитара</b>" in card.text and "19:45" in card.text


async def test_menu_button_interrupts_dialog(bot: Harness) -> None:
    await bot.press("n:add")
    reply = (await bot.press("n:habits"))[0]  # уход в другой раздел обрывает ввод
    assert "Привычек пока нет" in reply.text
    # Текст после этого не становится названием привычки.
    assert BTN_TODAY in buttons(await bot.say("Зарядка"))
    assert bot.storage.list_habits(42) == []
    # Из пустого списка — сразу к добавлению кнопкой.
    calls = await bot.press(find(buttons(reply), "Добавить"))
    assert "Как называется" in calls[0].text


async def test_cancel_button_returns_to_menu(bot: Harness) -> None:
    ask = (await bot.press("n:add"))[0]
    calls = await bot.press(find(buttons(ask), "Отмена"))
    assert BTN_ADD in buttons(calls[0])
    await bot.say("Зарядка")
    assert bot.storage.list_habits(42) == []


async def test_calendar_edit_any_past_day_and_delete(bot: Harness) -> None:
    card = await add(bot, "Сладкое", "Вредная", "🍬")
    assert "вредная" in card.text and "Напоминание: нет" in card.text
    [habit] = bot.storage.list_habits(42)
    today = datetime.now().date()
    assert habit.created_on == today

    photo = (await bot.press(find(buttons(card), "Календарь")))[0]
    assert type(photo).__name__ == "SendPhoto"
    assert rows(photo)[-1] == ["📣 Поделиться", "☰ Меню"]
    edit = (await bot.press(find(buttons(photo), "Изменить отметки"), photo=True))[0]
    assert type(edit).__name__ == "EditMessageMedia"
    # На плитке — числа всех дней месяца, а не только дней после заведения привычки.
    numbers = [t for row in rows(edit)[:-2] for t in row if t.strip()]
    assert numbers[0] == "1" and len(numbers) >= 28

    # Привычка заведена сегодня, отмечаем день трёхдневной давности: учёт сдвигается на него.
    day = today - timedelta(days=3)
    target = f"d:{habit.id}:{day.year}:{day.month}:{day.day}"
    calls = await bot.press(target, photo=True)
    assert bot.storage.all_marks(habit.id) == {day}
    assert bot.storage.list_habits(42)[0].created_on == day
    if day.month == today.month:
        assert f"🍬 {day.day}" in buttons(calls[0])
    await bot.press(target, photo=True)
    assert bot.storage.all_marks(habit.id) == set()

    # Будущий день и день больше года назад не отмечаются.
    for bad in (today + timedelta(days=1), today - timedelta(days=400)):
        calls = await bot.press(f"d:{habit.id}:{bad.year}:{bad.month}:{bad.day}", photo=True)
        assert calls[0].show_alert
    assert bot.storage.all_marks(habit.id) == set()

    # «Меню» под картинкой присылает меню новым сообщением.
    menu = (await bot.press("n:menu", photo=True))[0]
    assert type(menu).__name__ == "SendMessage" and list(buttons(menu)) == MENU

    confirm = buttons((await bot.press(find(buttons(card), "Удалить")))[0])
    await bot.press(find(confirm, "Да, удалить"))
    assert bot.storage.list_habits(42) == []


async def test_two_columns_and_add_next_to_menu(bot: Harness) -> None:
    for name, emoji in (("Зарядка", "💪"), ("Чтение", "📚"), ("Вода", "💧")):
        await add(bot, name, "Полезная", emoji)
    listing = (await bot.press("n:habits"))[0]
    assert rows(listing) == [["💪 Зарядка", "📚 Чтение"], ["💧 Вода"], [BTN_ADD, "☰ Меню"]]
    today = (await bot.press("n:today"))[0]
    assert rows(today) == [["▫️ 💪 Зарядка", "▫️ 📚 Чтение"], ["▫️ 💧 Вода"], ["☰ Меню"]]


async def test_invite_link_and_referral_count(bot: Harness) -> None:
    await bot.say("/start")
    invite = (await bot.press("n:invite"))[0]
    assert "https://t.me/test_bot?start=ref42" in invite.text
    assert "Пришло по ссылке: <b>0</b>" in invite.text
    share = invite.reply_markup.inline_keyboard[0][0]
    assert share.text == "📣 Поделиться"
    assert share.url.startswith("https://t.me/share/url?url=https%3A%2F%2Ft.me%2Ftest_bot")

    friend = User(id=77, is_bot=False, first_name="Друг")
    await bot.say("/start ref42", user=friend)
    assert bot.storage.invited_count(42) == 1
    notice = next(
        c for c in bot.session.calls if "По вашей ссылке" in (getattr(c, "text", "") or "")
    )
    assert notice.chat_id == 42
    # Повторный переход и приглашение самого себя не засчитываются.
    await bot.say("/start ref42", user=friend)
    await bot.say("/start ref42")
    assert bot.storage.invited_count(42) == 1
    assert "Пришло по ссылке: <b>1</b>" in (await bot.press("n:invite"))[0].text


async def test_several_habits_and_reminder_answer(bot: Harness) -> None:
    await add(bot, "Зарядка", "Полезная", "💪")
    await add(bot, "Чтение", "Полезная", "📚")
    await add(bot, "Курение", "Вредная", "🚬")
    listing = (await bot.press("n:habits"))[0]
    assert [t for t in buttons(listing) if t not in (BTN_ADD, "☰ Меню")] == [
        "💪 Зарядка",
        "📚 Чтение",
        "🚬 Курение · вредная",
    ]

    smoking = bot.storage.list_habits(42)[2]
    text, markup = reminder_message(smoking)
    assert "🚬" in text
    answers = {b.text: b.callback_data for row in markup.inline_keyboard for b in row}
    calls = await bot.press(answers["Было"])
    assert "Записал: было" in calls[0].text
    assert len(bot.storage.all_marks(smoking.id)) == 1
    await bot.press(find(answers, "Не было"))
    assert bot.storage.all_marks(smoking.id) == set()


async def test_change_emoji_reminder_and_timezone(bot: Harness) -> None:
    card = buttons(await add(bot, "Вода", "Полезная", "💧"))

    palette = buttons((await bot.press(find(card, "Эмодзи")))[0])
    assert "🎯 <b>Вода</b>" in (await bot.press(palette["🎯"]))[0].text

    times = buttons((await bot.press(find(card, "Напоминание")))[0])
    assert "каждый день в 21:00" in (await bot.press(times["21:00"]))[0].text
    await bot.press(find(card, "Напоминание"))
    assert "Не понял время" in (await bot.say("вечером")).text
    assert "каждый день в 21:30" in (await bot.say("21.30")).text

    zones = buttons((await bot.press("n:tz"))[0])
    assert "Etc/GMT-5" in (await bot.press(find(zones, "Екатеринбург")))[0].text
    assert bot.storage.get_tz(42) == "Etc/GMT-5"
    await bot.press("n:tz")
    assert "Не знаю такой пояс" in (await bot.say("Марс")).text
    assert "Europe/Berlin" in (await bot.say("Europe/Berlin")).text


async def test_old_habit_without_emoji_gets_default_icon(bot: Harness) -> None:
    bot.storage.add_habit(42, "Старая", "bad", None, datetime.now().date())
    assert "🚫 Старая · вредная" in buttons((await bot.press("n:habits"))[0])


async def test_foreign_habit_button_is_rejected(bot: Harness) -> None:
    other = bot.storage.add_habit(7, "Чужая", "good", None, datetime.now().date())
    calls = await bot.press(f"h:open:{other.id}")
    assert isinstance(calls[0], AnswerCallbackQuery) and calls[0].show_alert


async def test_overview_calendar_with_month_navigation(bot: Harness) -> None:
    # Без привычек общий календарь предлагает добавить первую.
    assert "Привычек пока нет" in (await bot.press("n:cal"))[0].text

    await add(bot, "Зарядка", "Полезная", "💪")
    await add(bot, "Сладкое", "Вредная", "🍬")
    today = datetime.now().date()
    # Привычки заведены два месяца назад — чтобы было куда листать.
    bot.storage.conn.execute(
        "UPDATE habits SET created_on = ?", ((today - timedelta(days=62)).isoformat(),)
    )

    photo = (await bot.press("n:cal"))[0]
    assert type(photo).__name__ == "SendPhoto"
    assert "Все привычки" in photo.caption
    nav, tail = rows(photo)
    assert len(nav) == 1 and nav[0].startswith("◀")  # вперёд текущего месяца нельзя
    assert tail == ["📣 Поделиться", "☰ Меню"]

    previous = (await bot.press(buttons(photo)[nav[0]], photo=True))[0]
    assert type(previous).__name__ == "EditMessageMedia"
    nav, tail = rows(previous)
    assert nav[0].startswith("◀") and nav[-1].endswith("▶")
    assert tail == ["📣 Поделиться", "☰ Меню"]
