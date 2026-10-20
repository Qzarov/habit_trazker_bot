"""Сценарии бота целиком: обновления подаются в диспетчер, Telegram подменён."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, AnswerInlineQuery, GetMe, TelegramMethod
from aiogram.types import CallbackQuery, Chat, InlineQuery, Message, PhotoSize, Update, User
from conftest import make_storage

from habitbot.bot import (
    BTN_ADD,
    BTN_HABITS,
    BTN_TODAY,
    BTN_TZ,
    build_router,
    reminder_message,
)
from habitbot.motivation import Motivator

MENU = [
    BTN_TODAY, BTN_HABITS, "🗓 Календарь", BTN_ADD,
    BTN_TZ, "🤝 Пригласить", "🔥 Мотивация", "❓ Помощь",
]  # fmt: skip
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
        if isinstance(method, (AnswerCallbackQuery, AnswerInlineQuery)):
            return True
        return Message(message_id=1000 + len(self.calls), date=datetime.now(), chat=CHAT)

    async def stream_content(self, *args, **kwargs):
        yield b""


class Harness:
    def __init__(self) -> None:
        self.storage = make_storage()
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

    async def press(
        self, data: str, photo: bool = False, markup: Any = None
    ) -> list[TelegramMethod[Any]]:
        """Нажатие инлайн-кнопки; `photo` — кнопка под сообщением с картинкой, `markup` —
        клавиатура сообщения, под которым кнопка."""
        self.n += 1
        before = len(self.session.calls)
        source = Message(
            message_id=500,
            date=datetime.now(),
            chat=CHAT,
            text=None if photo else "…",
            photo=[PhotoSize(file_id="shown", file_unique_id="u", width=1, height=1)]
            if photo
            else None,
            reply_markup=markup,
        )
        query = CallbackQuery(
            id=str(self.n), from_user=USER, chat_instance="x", data=data, message=source
        )
        await self.dp.feed_update(self.bot, Update(update_id=self.n, callback_query=query))
        return self.session.calls[before:]


async def inline(bot: Harness, text: str, user: User = USER) -> TelegramMethod[Any]:
    bot.n += 1
    query = InlineQuery(id=str(bot.n), from_user=user, query=text, offset="")
    await bot.dp.feed_update(bot.bot, Update(update_id=bot.n, inline_query=query))
    return bot.session.calls[-1]


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
    greeting, menu, question = bot.session.calls[-3:]
    assert type(greeting.reply_markup).__name__ == "ReplyKeyboardRemove"
    assert list(buttons(menu)) == MENU
    assert "Что тебя мотивирует?" in question.text


async def test_start_asks_motivation_style_once(bot: Harness) -> None:
    await bot.say("/start")
    question = bot.session.calls[-1]
    assert [len(row) for row in rows(question)] == [2, 2, 2, 1, 1, 1]
    # «Подробнее о стилях»: описание и пример у каждого стиля, выбрать можно прямо оттуда.
    info = (await bot.press(buttons(question)["ℹ️ Подробнее о стилях"]))[0]
    assert "<b>Стили мотивации</b>" in info.text
    assert info.text.count("<i>«") == 6 and "Подбирай сам" in info.text
    back = (await bot.press(buttons(info)["← Назад"]))[0]
    assert back.text == question.text and rows(back) == rows(question)
    info = (await bot.press(buttons(question)["ℹ️ Подробнее о стилях"]))[0]
    question = info  # выбираем с экрана подробностей
    done = (await bot.press(buttons(question)["🔥 Серии"]))[0]
    assert bot.storage.get_motivation(42).style == "streak"
    assert "Готово: стиль «серии»" in done.text
    assert "«☰ Меню» → «🔥 Мотивация»" in done.text and "Мягче" in done.text
    assert list(buttons(done)) == ["☰ Меню"]

    # Второй раз не спрашиваем.
    await bot.say("/start")
    assert "Что тебя мотивирует?" not in bot.session.calls[-1].text


async def test_start_question_can_be_skipped(bot: Harness) -> None:
    await bot.say("/start")
    skipped = (await bot.press(buttons(bot.session.calls[-1])["Пропустить"]))[0]
    assert "без мотивации" in skipped.text and "«🔥 Мотивация»" in skipped.text
    assert bot.storage.get_motivation(42).style is None
    # Пропуск — тоже ответ: при следующем запуске не спрашиваем.
    await bot.say("/start")
    assert "Что тебя мотивирует?" not in bot.session.calls[-1].text

    # «Подбирай сам» из вопроса — у другого пользователя, пришедшего до появления мотивации.
    bot.storage.register(77)
    assert bot.storage.ask_motivation_once(77) and not bot.storage.ask_motivation_once(77)


async def test_every_screen_leads_back_to_menu(bot: Harness) -> None:
    for action in ("today", "habits", "help", "mot"):
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
    # Второе нажатие — «не сделано», третье — снова «нет данных».
    calls = await bot.press(find(buttons(today), "Зарядка"))
    assert next(iter(buttons(calls[0]))) == "➖ 💪 Зарядка"
    assert bot.storage.all_answers(habit.id) == {datetime.now().date(): False}
    calls = await bot.press(find(buttons(today), "Зарядка"))
    assert next(iter(buttons(calls[0]))) == "▫️ 💪 Зарядка"
    assert bot.storage.all_answers(habit.id) == {}


async def test_card_has_one_answer_button_per_day(bot: Harness) -> None:
    card = await add(bot, "Сладкое", "Вредная", "🍬")
    [habit] = bot.storage.list_habits(42)
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    assert "Сегодня: <b>нет данных</b>" in card.text
    # Привычка заведена сегодня — кнопка одна.
    assert rows(card) == [
        ["▫️ Сегодня: нет данных"],
        ["📅 Календарь", "⏰ Напоминание"],
        ["🎨 Эмодзи", "🗑 Удалить"],
        ["← К списку"],
    ]

    # Нажатие меняет ответ по кругу, подпись и значок кнопки меняются вместе с ним.
    press = buttons(card)["▫️ Сегодня: нет данных"]
    card = (await bot.press(press))[0]
    assert rows(card)[0] == ["❗️ Сегодня: было"] and "Сегодня: <b>было</b>" in card.text
    assert bot.storage.all_answers(habit.id) == {today: True}
    card = (await bot.press(press))[0]
    assert rows(card)[0] == ["💪 Сегодня: не было"] and "Сегодня: <b>не было</b>" in card.text
    assert bot.storage.all_answers(habit.id) == {today: False}
    card = (await bot.press(press))[0]
    assert rows(card)[0] == ["▫️ Сегодня: нет данных"]
    assert bot.storage.all_answers(habit.id) == {}

    # Есть вчерашний день — у него своя кнопка, она сегодняшний ответ не трогает.
    bot.storage.backdate(habit.id, yesterday)
    card = (await bot.press(press))[0]
    assert rows(card)[:2] == [["❗️ Сегодня: было"], ["▫️ Вчера: нет данных"]]
    await bot.press(buttons(card)["▫️ Вчера: нет данных"])
    card = (await bot.press(buttons(card)["▫️ Вчера: нет данных"]))[0]
    assert rows(card)[:2] == [["❗️ Сегодня: было"], ["💪 Вчера: не было"]]
    assert bot.storage.all_answers(habit.id) == {today: True, yesterday: False}

    # У полезной привычки свои слова и значки.
    good = await add(bot, "Зарядка", "Полезная", "💪")
    press = buttons(good)["▫️ Сегодня: нет данных"]
    assert rows((await bot.press(press))[0])[0] == ["✅ Сегодня: сделано"]
    assert rows((await bot.press(press))[0])[0] == ["➖ Сегодня: не сделано"]


async def test_card_buttons_sent_before_update_still_work(bot: Harness) -> None:
    await add(bot, "Сладкое", "Вредная", "🍬")
    [habit] = bot.storage.list_habits(42)
    today = datetime.now().date()
    await bot.press(f"m:{habit.id}:0:c:0")  # прежняя кнопка «Сегодня: не было»
    assert bot.storage.all_answers(habit.id) == {today: False}


async def test_delete_from_card(bot: Harness) -> None:
    card = buttons(await add(bot, "Сладкое", "Вредная", "🍬"))
    await add(bot, "Зарядка", "Полезная", "💪")
    sweets = bot.storage.list_habits(42)[0]

    # «Оставить» возвращает на карточку, привычка на месте.
    confirm = (await bot.press(card["🗑 Удалить"]))[0]
    assert "Удалить «Сладкое»?" in confirm.text
    back = (await bot.press(buttons(confirm)["Оставить"]))[0]
    assert "<b>Сладкое</b>" in back.text and len(bot.storage.list_habits(42)) == 2

    # «Да, удалить» — привычка пропадает из списка и напоминаний, экран — список.
    bot.storage.set_reminder(42, sweets.id, "08:00")
    confirm = (await bot.press(card["🗑 Удалить"]))[0]
    listing = (await bot.press(buttons(confirm)["Да, удалить"]))[0]
    assert listing.text.startswith("Удалено.") and "Сладкое" not in listing.text
    assert [h.name for h in bot.storage.list_habits(42)] == ["Зарядка"]
    assert all(h.id != sweets.id for h in bot.storage.habits_with_reminders())


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
    # У вредной привычки в подписи — дни без срыва и лучшая серия.
    assert "Без срывов: <b>1 дн.</b> Лучшая серия: <b>1 дн.</b>" in photo.caption
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
    # Второе нажатие — «не было», и в подписи оно считается отдельно от «нет данных».
    calls = await bot.press(target, photo=True)
    assert bot.storage.all_answers(habit.id) == {day: False}
    if day.month == today.month:
        assert f"➖ {day.day}" in buttons(calls[0])
        assert "Не было: <b>1</b> · нет данных: <b>3</b>" in calls[0].media.caption
    await bot.press(target, photo=True)
    assert bot.storage.all_answers(habit.id) == {}

    # Будущий день и день больше года назад не отмечаются.
    for bad in (today + timedelta(days=1), today - timedelta(days=400)):
        calls = await bot.press(f"d:{habit.id}:{bad.year}:{bad.month}:{bad.day}", photo=True)
        assert calls[0].show_alert
    assert bot.storage.all_marks(habit.id) == set()

    # «Меню» под картинкой присылает меню новым сообщением.
    menu = (await bot.press("n:menu", photo=True))[0]
    assert type(menu).__name__ == "SendMessage" and list(buttons(menu)) == MENU


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
    link = "https://t.me/test_bot?start=ref42"
    # Ссылка зашита в название бота, голого адреса в тексте нет.
    assert f'<a href="{link}">Бот</a>' in invite.text
    assert link not in invite.text.replace(f'href="{link}"', "")
    assert "Пришло по ссылке: <b>0</b>" in invite.text
    assert rows(invite) == [
        ["✉️ Прислать для пересылки"],
        ["📋 Скопировать текстом", "🔗 Скопировать ссылку"],
        ["📣 Поделиться", "☰ Меню"],
    ]
    copy_text, copy_link = invite.reply_markup.inline_keyboard[1]
    assert copy_text.copy_text.text.endswith(f"Присоединяйся!\n{link}")
    assert len(copy_text.copy_text.text) <= 256  # предел Telegram для кнопки копирования
    assert copy_link.copy_text.text == link
    share = invite.reply_markup.inline_keyboard[-1][0]
    assert share.url.startswith("https://t.me/share/url?url=https%3A%2F%2Ft.me%2Ftest_bot")

    # Сообщение для пересылки: только приглашение и кнопка-ссылка.
    message = (await bot.press(buttons(invite)["✉️ Прислать для пересылки"]))[0]
    assert type(message).__name__ == "SendMessage"
    assert message.text.startswith("Веду привычки в боте <a href=") and "Пришло" not in message.text
    assert message.reply_markup.inline_keyboard[0][0].url == link

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
    today = datetime.now().date()
    text, markup = reminder_message(smoking, today)
    assert "🚬" in text
    answers = {b.text: b.callback_data for row in markup.inline_keyboard for b in row}
    calls = await bot.press(answers["Было"])
    assert "Записал: было" in calls[0].text
    assert len(bot.storage.all_marks(smoking.id)) == 1
    # «Не было» — это ответ, а не снятая отметка: он остаётся в базе.
    calls = await bot.press(find(answers, "Не было"))
    assert "Записал: не было" in calls[0].text
    assert bot.storage.all_answers(smoking.id) == {datetime.now().date(): False}

    # У полезной привычки в напоминании тоже два ответа.
    exercise = bot.storage.list_habits(42)[0]
    _, markup = reminder_message(exercise, today)
    answers = {b.text: b.callback_data for row in markup.inline_keyboard for b in row}
    assert list(answers) == ["✅ Сделано", "Не сделано"]
    calls = await bot.press(answers["Не сделано"])
    assert "Записал: не сделано" in calls[0].text
    assert bot.storage.get_answer(exercise.id, datetime.now().date()) is False
    await bot.press(answers["✅ Сделано"])
    assert bot.storage.get_answer(exercise.id, datetime.now().date()) is True


async def test_next_day_reminder_records_answer_for_yesterday(bot: Harness) -> None:
    # При добавлении: переключатель на экране выбора времени.
    await bot.press("n:add")
    kinds = buttons(await bot.say("Сладкое"))
    palette = buttons((await bot.press(find(kinds, "Вредная")))[0])
    times = (await bot.press(palette["🍬"]))[0]
    assert "Когда: сегодня" in buttons(times)
    times = (await bot.press(buttons(times)["Когда: сегодня"]))[0]
    assert "Когда: завтра" in buttons(times) and "про вчерашний день" in times.text
    card = (await bot.press(buttons(times)["09:00"]))[0]
    assert "Напоминание: каждый день в 09:00, про вчерашний день" in card.text
    [habit] = bot.storage.list_habits(42)
    assert habit.remind_next_day

    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    bot.storage.backdate(habit.id, yesterday)
    habit = bot.storage.get_habit(42, habit.id)
    text, markup = reminder_message(habit, yesterday)
    assert f"как прошёл вчерашний день ({yesterday:%d.%m})?" in text
    answers = {b.text: b.callback_data for row in markup.inline_keyboard for b in row}
    calls = await bot.press(find(answers, "Не было"))
    assert f"<b>Сладкое</b> · {yesterday:%d.%m}" in calls[0].text
    # Ответ записан за вчера, сегодняшний день остаётся без данных.
    assert bot.storage.all_answers(habit.id) == {yesterday: False}
    await bot.press(answers["Было"])
    assert bot.storage.all_answers(habit.id) == {yesterday: True}

    # Кнопка с днём, которого у привычки нет (будущий или до начала учёта), ничего не пишет.
    for wrong in (today + timedelta(days=1), yesterday - timedelta(days=1)):
        calls = await bot.press(f"r:{habit.id}:{wrong:%Y%m%d}:1")
        assert calls[0].show_alert
    assert bot.storage.all_answers(habit.id) == {yesterday: True}

    # На карточке: переключатель действует сразу и время не трогает.
    screen = (await bot.press(find(buttons(card), "Напоминание")))[0]
    assert "Сейчас: 09:00, про вчерашний день." in screen.text
    # Выбор дня и выключатель стоят в одном ряду под временем.
    assert rows(screen)[2:] == [["Когда: завтра", "✅ вкл"], ["← К привычке"]]
    screen = (await bot.press(buttons(screen)["Когда: завтра"]))[0]
    assert rows(screen)[2] == ["Когда: сегодня", "✅ вкл"]
    habit = bot.storage.get_habit(42, habit.id)
    assert (habit.remind_at, habit.remind_next_day) == ("09:00", False)
    assert "как сегодня?" in reminder_message(habit, today)[0]


async def test_reminder_is_switched_off_and_on_keeping_time(bot: Harness) -> None:
    card = await add(bot, "Зарядка", "Полезная", "💪", "08:00")
    [habit] = bot.storage.list_habits(42)
    switch = f"h:remon:{habit.id}"
    screen = (await bot.press(find(buttons(card), "Напоминание")))[0]
    # Вместо «Убрать напоминание» — выключатель в одном ряду с выбором дня.
    assert rows(screen)[2] == ["Когда: сегодня", "✅ вкл"]
    assert buttons(screen)["✅ вкл"] == switch
    assert not any("Убрать" in text for text in buttons(screen))

    screen = (await bot.press(switch))[0]
    assert rows(screen)[2] == ["Когда: сегодня", "🛑 выкл"] and "08:00, выключено" in screen.text
    habit = bot.storage.get_habit(42, habit.id)
    assert (habit.remind_at, habit.remind_enabled, habit.reminds) == ("08:00", False, False)
    assert bot.storage.habits_with_reminders() == []
    card = (await bot.press(find(buttons(screen), "К привычке")))[0]
    assert "Напоминание: выключено (время — 08:00)" in card.text

    # Включить обратно — одно нажатие, время то же.
    await bot.press(find(buttons(card), "Напоминание"))
    screen = (await bot.press(switch))[0]
    assert rows(screen)[2] == ["Когда: сегодня", "✅ вкл"]
    assert [h.remind_at for h in bot.storage.habits_with_reminders()] == ["08:00"]

    # Выбор времени у выключенного напоминания включает его.
    await bot.press(switch)
    card = (await bot.press(buttons(screen)["21:00"]))[0]
    assert "каждый день в 21:00" in card.text
    assert bot.storage.get_habit(42, habit.id).reminds

    # У привычки без времени выключателя нет: включать нечего, сначала выбирают время.
    plain = await add(bot, "Вода", "Полезная", "💧")
    screen = (await bot.press(find(buttons(plain), "Напоминание")))[0]
    assert rows(screen)[2:] == [["Когда: сегодня"], ["← К привычке"]]


async def test_emoji_palette_has_four_rows(bot: Harness) -> None:
    await bot.press("n:add")
    kinds = buttons(await bot.say("Прогулка"))
    palette = (await bot.press(find(kinds, "Полезная")))[0]
    assert [len(row) for row in rows(palette)] == [6, 6, 6, 6, 1]
    # Эмодзи из нового, четвёртого ряда выбирается как любой другой.
    times = buttons((await bot.press(buttons(palette)["🌅"]))[0])
    card = (await bot.press(find(times, "Без напоминания")))[0]
    assert "🌅 <b>Прогулка</b>" in card.text

    await bot.press("n:add")
    kinds = buttons(await bot.say("Накрутка"))
    palette = (await bot.press(find(kinds, "Вредная")))[0]
    assert [len(row) for row in rows(palette)] == [6, 6, 6, 6, 1]
    times = buttons((await bot.press(buttons(palette)["🤯"]))[0])
    card = (await bot.press(find(times, "Без напоминания")))[0]
    assert "🤯 <b>Накрутка</b>" in card.text


async def test_reminder_buttons_sent_before_update_still_work(bot: Harness) -> None:
    await add(bot, "Зарядка", "Полезная", "💪")
    [habit] = bot.storage.list_habits(42)
    calls = await bot.press(f"m:{habit.id}:0:r:1")  # кнопка прежнего вида, без дня
    assert "Отмечено" in calls[0].text
    assert bot.storage.all_answers(habit.id) == {datetime.now().date(): True}


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


async def test_share_calendar_sends_picture_with_stats_and_invitation(bot: Harness) -> None:
    card = await add(bot, "Зарядка", "Полезная", "💪")
    await add(bot, "Сладкое", "Вредная", "🍬")
    good, bad = bot.storage.list_habits(42)
    today = datetime.now().date()
    bot.storage.set_answer(good.id, today, True)
    bot.storage.set_answer(bad.id, today, True)
    link = "https://t.me/test_bot?start=ref42"

    # Календарь одной привычки.
    photo = (await bot.press(find(buttons(card), "Календарь")))[0]
    shared = (await bot.press(buttons(photo)["📣 Поделиться"], photo=True))[0]
    assert type(shared).__name__ == "SendPhoto" and shared.photo == "shown"
    assert "💪 <b>Зарядка</b>" in shared.caption
    assert "Отмечено дней за месяц: <b>1</b>" in shared.caption
    assert "Серия: <b>1 дн.</b>" in shared.caption
    assert f'<a href="{link}">Бот</a>' in shared.caption
    assert rows(shared) == [["Открыть трекер привычек"]]
    assert shared.reply_markup.inline_keyboard[0][0].url == link

    # Общий календарь: строка на каждую привычку.
    overview = (await bot.press("n:cal"))[0]
    shared = (await bot.press(buttons(overview)["📣 Поделиться"], photo=True))[0]
    assert "<b>Мои привычки</b>" in shared.caption
    assert "💪 Зарядка — дней: <b>1</b>" in shared.caption
    # У вредной — ещё и серии; сорвался сегодня, поэтому текущая 0.
    assert "🍬 Сладкое — срывов: <b>1</b>, без срыва 0 дн., лучшая серия 0 дн." in shared.caption
    assert "💪 Зарядка — дней: <b>1</b>\n" in shared.caption  # у полезной серий в строке нет
    assert f'href="{link}"' in shared.caption


async def test_share_caption_fits_telegram_limit(bot: Harness) -> None:
    today = datetime.now().date()
    for i in range(30):
        name = f"Привычка с довольно длинным названием и уточнением, номер {i:02d}"[:60]
        bot.storage.add_habit(42, name, "good", None, today, "💧")
    overview = (await bot.press("n:cal"))[0]
    shared = (await bot.press(buttons(overview)["📣 Поделиться"], photo=True))[0]
    assert "…и ещё 20" in shared.caption
    assert len(shared.caption) <= 1024


async def test_share_via_chat_picker(bot: Harness) -> None:
    card = await add(bot, "Зарядка", "Полезная", "💪")
    photo = (await bot.press(find(buttons(card), "Календарь")))[0]
    shared, hint, _ = await bot.press(buttons(photo)["📣 Поделиться"], photo=True)
    assert type(shared).__name__ == "SendPhoto"  # сообщение для пересылки — без лишних кнопок
    assert rows(shared) == [["Открыть трекер привычек"]]
    assert rows(hint) == [["📤 Выбрать чат", "☰ Меню"]]
    key = hint.reply_markup.inline_keyboard[0][0].switch_inline_query

    answer = await inline(bot, key)
    [result] = answer.results
    assert (result.photo_file_id, result.caption) == ("shown", shared.caption)
    assert result.reply_markup.inline_keyboard[0][0].url == "https://t.me/test_bot?start=ref42"
    assert answer.is_personal and answer.cache_time == 0

    # Чужой человек с тем же ключом и пустой запрос ничего не получают.
    stranger = User(id=77, is_bot=False, first_name="Чужой")
    assert (await inline(bot, key, user=stranger)).results == []
    empty = await inline(bot, "")
    assert empty.results == [] and empty.button.start_parameter == "inline"


async def test_name_username_and_join_time_are_saved(bot: Harness) -> None:
    await bot.say("/start")
    before = datetime.now(UTC).replace(microsecond=0)
    friend = User(id=77, is_bot=False, first_name="Анна", last_name="Тестова", username="anna_t")
    await bot.say("/start ref42", user=friend)

    [invited] = bot.storage.invited(42)
    assert (invited.user_id, invited.name, invited.username) == (77, "Анна Тестова", "anna_t")
    assert before <= invited.joined_at <= datetime.now(UTC)

    # Сменил имя и убрал ник — запись обновляется при следующем сообщении, время прихода прежнее.
    renamed = User(id=77, is_bot=False, first_name="Аня")
    await bot.say("привет", user=renamed)
    [again] = bot.storage.invited(42)
    assert (again.name, again.username, again.joined_at) == ("Аня", None, invited.joined_at)


async def test_profile_update_does_not_spoil_first_visit_by_invitation(bot: Harness) -> None:
    await bot.say("/start")
    # Человек сначала пишет боту по ссылке — сохранение имени не должно завести его раньше времени.
    friend = User(id=78, is_bot=False, first_name="Борис")
    await bot.say("/start ref42", user=friend)
    assert bot.storage.invited_count(42) == 1


async def test_motivation_settings(bot: Harness) -> None:
    screen = (await bot.press("n:mot"))[0]
    assert "Стиль: <b>выключена</b>" in screen.text
    assert "🔕 Выключить" not in buttons(screen)
    # Подробнее о стилях — и обратно к настройкам.
    info = (await bot.press(buttons(screen)["ℹ️ Подробнее о стилях"]))[0]
    assert "«Неделю назад было 2 из 7, сейчас 5»" in info.text
    assert list(buttons(info)) == ["← Назад"]
    screen = (await bot.press(buttons(info)["← Назад"]))[0]
    assert "Стиль: <b>выключена</b>" in screen.text

    screen = (await bot.press(buttons(screen)["🔥 Серии"]))[0]
    assert "Стиль: <b>серии</b>" in screen.text and "✔️ 🔥 Серии" in buttons(screen)
    assert bot.storage.get_motivation(42).style == "streak"

    # «Зачем мне это» — своими словами; потом его можно убрать.
    await bot.press(buttons(screen)["✍️ Зачем мне это"])
    assert "Слишком длинно" in (await bot.say("я" * 201)).text
    screen = await bot.say("  Чтобы   играть с детьми ")
    assert "«Чтобы играть с детьми»" in screen.text
    assert bot.storage.get_motivation(42).why == "Чтобы играть с детьми"
    asking = (await bot.press(buttons(screen)["✍️ Зачем мне это"]))[0]
    screen = (await bot.press(buttons(asking)["Убрать"]))[0]
    assert "Зачем тебе это: не указано" in screen.text

    screen = (await bot.press(buttons(screen)["🎲 Подбирай сам"]))[0]
    assert "Стиль: <b>подбираю сам</b>" in screen.text
    screen = (await bot.press(buttons(screen)["🔕 Выключить"]))[0]
    assert bot.storage.get_motivation(42).style is None


async def test_reminder_with_motivation_and_feedback(bot: Harness) -> None:
    await add(bot, "Зарядка", "Полезная", "💪", "08:00")
    [habit] = bot.storage.list_habits(42)
    bot.storage.set_motivation_style(42, "support")
    today = datetime.now().date()
    # Без ключа — готовые фразы; они так же попадают в журнал и получают кнопки отзыва.
    always_preferred = random.Random()
    always_preferred.random = lambda: 0.0  # без экспериментов: стиль — выбранный
    motivator = Motivator(bot.storage, api_key="", rng=always_preferred)
    message_id, words = await motivator.compose(habit, today, today)
    assert "Зарядка" in words
    assert bot.storage.get_motivation_message(42, message_id).source == "preset"

    # В напоминании — текст, мотивация и только кнопки ответа.
    text, markup = reminder_message(habit, today, (message_id, words))
    assert f"<i>{words}</i>" in text
    assert [[b.text for b in row] for row in markup.inline_keyboard] == [
        ["✅ Сделано", "Не сделано"]
    ]
    labels = {b.text: b.callback_data for row in markup.inline_keyboard for b in row}

    # Ответ: кнопки ответа уходят, мотивация остаётся, вопрос о ней приходит новым сообщением.
    calls = await bot.press(labels["✅ Сделано"], markup=markup)
    answered = calls[0]
    assert "Отмечено" in answered.text and f"<i>{words}</i>" in answered.text
    assert rows(answered) == [["☰ Меню"]]
    question = calls[-1]
    assert type(question).__name__ == "SendMessage"
    assert question.text.startswith("Как тебе такая мотивация?")
    assert question.reply_parameters.message_id == 500  # ответ на само напоминание
    assert question.reply_parameters.quote == words
    assert rows(question) == [["🪶 Мягче", "👍 Хорошо", "💥 Жёстче"]]
    # Привычку отметили после сообщения — оно засчитывается стилю.
    assert bot.storage.get_motivation_message(42, message_id).acted
    assert bot.storage.motivation_stats(42) == {"support": (1, 1, 0)}

    # «Жёстче» сдвигает тон, кнопки вопроса сменяются ответом; повторный отзыв не принимается.
    rate = buttons(question)
    calls = await bot.press(rate["💥 Жёстче"], markup=question.reply_markup)
    assert calls[0].text == "Как тебе такая мотивация? — 💥 Жёстче"
    assert calls[-1].text == "Понял, буду жёстче"
    assert bot.storage.get_motivation(42).tone == 1
    assert bot.storage.get_motivation_message(42, message_id).feedback == "harder"
    calls = await bot.press(rate["🪶 Мягче"], markup=question.reply_markup)
    assert calls[-1].text == "Отзыв уже учтён" and bot.storage.get_motivation(42).tone == 1

    # Повторный ответ на то же напоминание (кнопка из другого клиента) вопрос не присылает:
    # отзыв уже дан.
    calls = await bot.press(labels["Не сделано"], markup=markup)
    assert not any(type(c).__name__ == "SendMessage" for c in calls)


async def test_feedback_question_without_quote_if_telegram_rejects_it(bot: Harness) -> None:
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import SendMessage

    await add(bot, "Зарядка", "Полезная", "💪", "08:00")
    [habit] = bot.storage.list_habits(42)
    bot.storage.set_motivation_style(42, "support")
    today = datetime.now().date()
    motivation = await Motivator(bot.storage, api_key="").compose(habit, today, today)
    _, markup = reminder_message(habit, today, motivation)

    send = bot.session.make_request

    async def reject_quote(b, method, timeout=None):
        if isinstance(method, SendMessage) and method.reply_parameters.quote:
            raise TelegramBadRequest(method=method, message="QUOTE_TEXT_INVALID")
        return await send(b, method, timeout)

    bot.session.make_request = reject_quote
    calls = await bot.press(
        {b.text: b.callback_data for row in markup.inline_keyboard for b in row}["✅ Сделано"],
        markup=markup,
    )
    question = calls[-1]
    assert question.text.startswith("Как тебе такая мотивация?")
    assert question.reply_parameters.message_id == 500 and question.reply_parameters.quote is None


async def test_reminder_sent_before_update_keeps_feedback_buttons(bot: Harness) -> None:
    await add(bot, "Зарядка", "Полезная", "💪", "08:00")
    [habit] = bot.storage.list_habits(42)
    bot.storage.set_motivation_style(42, "support")
    today = datetime.now().date()
    message_id, words = await Motivator(bot.storage, api_key="").compose(habit, today, today)
    # Напоминание прежнего вида: кнопки ответа `r:` и кнопки отзыва под ними.
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    stamp = today.strftime("%Y%m%d")
    old = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Сделано", callback_data=f"r:{habit.id}:{stamp}:1")],
            [InlineKeyboardButton(text="💥 Жёстче", callback_data=f"f:{message_id}:1")],
        ]
    )
    calls = await bot.press(f"r:{habit.id}:{stamp}:1", markup=old)
    assert f"<i>{words}</i>" in calls[0].text
    assert rows(calls[0]) == [["💥 Жёстче"], ["☰ Меню"]]
    assert not any(type(c).__name__ == "SendMessage" for c in calls)


async def test_motivation_off_or_foreign_message(bot: Harness) -> None:
    await add(bot, "Зарядка", "Полезная", "💪", "08:00")
    [habit] = bot.storage.list_habits(42)
    today = datetime.now().date()
    assert await Motivator(bot.storage, api_key="").compose(habit, today, today) is None
    # Чужое сообщение отзывом не меняется.
    bot.storage.set_motivation_style(42, "streak")
    message_id, _ = await Motivator(bot.storage, api_key="").compose(habit, today, today)
    assert not bot.storage.set_motivation_feedback(7, message_id, "ok")
    assert bot.storage.get_motivation_message(7, message_id) is None


async def test_broadcast_motivation_question(bot: Harness) -> None:
    from aiogram.exceptions import TelegramForbiddenError
    from aiogram.methods import SendMessage

    from habitbot.ask_motivation import broadcast

    storage = bot.storage
    for user_id in (1, 2, 3, 4):
        storage.register(user_id)
    storage.set_motivation_style(2, "streak")  # уже выбрал — не спрашиваем
    storage.ask_motivation_once(3)  # уже спросили на /start
    blocked = 4

    class Recorder:
        def __init__(self) -> None:
            self.sent: list[int] = []

        async def send_message(self, chat_id, text, reply_markup=None):
            if chat_id == blocked:
                raise TelegramForbiddenError(SendMessage(chat_id=chat_id, text=text), "blocked")
            self.sent.append(chat_id)

    recorder = Recorder()
    assert await broadcast(storage, recorder, send=False) == {
        "sent": 0, "blocked": 0, "failed": 0, "total": 2,
    }  # fmt: skip
    assert recorder.sent == []
    assert await broadcast(storage, recorder, send=True) == {
        "sent": 1, "blocked": 1, "failed": 0, "total": 2,
    }  # fmt: skip
    assert recorder.sent == [1]
    # Повторный запуск никого не трогает, /start вопрос тоже не повторит.
    assert (await broadcast(storage, recorder, send=True))["total"] == 0
    assert not storage.ask_motivation_once(1)
