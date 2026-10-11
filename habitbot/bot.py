"""Команды и кнопки бота."""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .calendar_image import (
    MONTHS,
    OverviewHabit,
    can_draw,
    render_month,
    render_overview,
    shift_month,
)
from .db import BAD, GOOD, Habit, Storage, streak
from .reminders import parse_time, zone

MAX_NAME = 60
MAX_HABITS = 30

# Подписи кнопок главного меню (инлайн-кнопки под сообщением).
BTN_TODAY = "📋 Сегодня"
BTN_HABITS = "📚 Привычки"
BTN_ADD = "➕ Добавить"
BTN_CAL = "🗓 Календарь"
BTN_TZ = "🌍 Часовой пояс"
BTN_HELP = "❓ Помощь"
BTN_MENU = "☰ Меню"

# Команды остаются запасным входом; в списке команд Telegram — только самое нужное.
COMMANDS = [
    BotCommand(command="start", description="Показать меню"),
    BotCommand(command="help", description="Как пользоваться"),
]

HELP = (
    "Я помогаю следить за привычками. Всё управление — кнопками под сообщениями.\n\n"
    "• <b>Полезные</b> — отмечайте дни, когда сделали. Зелёный день в календаре — сделано.\n"
    "• <b>Вредные</b> — отмечайте дни, когда сорвались. Красный день — было, зелёный — не было.\n"
    "• У каждой привычки свой эмодзи — им помечены отмеченные дни на картинке.\n\n"
    f"{BTN_TODAY} — отметить за сегодня\n"
    f"{BTN_HABITS} — список, календарь, напоминания, эмодзи, удаление\n"
    f"{BTN_CAL} — общая картинка месяца со всеми привычками\n"
    f"{BTN_ADD} — новая привычка\n"
    f"{BTN_TZ} — по нему приходят напоминания и считается «сегодня»"
)

GOOD_EMOJI = ["💪", "🏃", "📚", "💧", "🧘", "🥗", "😴", "🦷", "🎯", "✍️", "🎸", "🧹"]
BAD_EMOJI = ["🚬", "🍺", "🍬", "🍔", "📱", "☕", "🎮", "💸", "🤬", "🍕", "🍷", "🛋️"]
QUICK_TIMES = ["07:00", "08:00", "09:00", "12:00", "18:00", "20:00", "21:00", "22:00"]
# Смещения от UTC для кнопок выбора пояса.
QUICK_ZONES = [
    ("Калининград", 2), ("Москва", 3), ("Самара", 4), ("Екатеринбург", 5),
    ("Омск", 6), ("Красноярск", 7), ("Иркутск", 8), ("Якутск", 9),
    ("Владивосток", 10), ("Магадан", 11), ("Камчатка", 12),
]  # fmt: skip


def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=BTN_TODAY, callback_data=NavCb(action="today"))
    kb.button(text=BTN_HABITS, callback_data=NavCb(action="habits"))
    kb.button(text=BTN_CAL, callback_data=NavCb(action="cal"))
    kb.button(text=BTN_ADD, callback_data=NavCb(action="add"))
    kb.button(text=BTN_TZ, callback_data=NavCb(action="tz"))
    kb.button(text=BTN_HELP, callback_data=NavCb(action="help"))
    kb.adjust(2, 2, 2)
    return kb.as_markup()


MENU_TEXT = "<b>Трекер привычек</b>\nЧто делаем?"


def clean_emoji(text: str) -> str | None:
    """Эмодзи от пользователя: короткая строка без букв и цифр, которую можно нарисовать."""
    value = text.strip()
    if not value or len(value) > 12 or any(ch.isalnum() or ch.isspace() for ch in value):
        return None
    if ord(max(value)) < 0x2190:  # обычная пунктуация — не эмодзи
        return None
    # Если на машине вовсе нет шрифта эмодзи, проверить отрисовку нечем — принимаем как есть.
    if can_draw("✅") and not can_draw(value):
        return None
    return value


class Add(StatesGroup):
    name = State()
    kind = State()
    emoji = State()
    time = State()


class EmojiEdit(StatesGroup):
    value = State()


class Remind(StatesGroup):
    time = State()


class Tz(StatesGroup):
    value = State()


class HabitCb(CallbackData, prefix="h"):
    action: str  # open | cal | rem | norem | del | delok | list | emoji
    id: int = 0


class MarkCb(CallbackData, prefix="m"):
    id: int
    back: int  # 0 — сегодня, 1 — вчера
    src: str  # t — список «сегодня», c — карточка, r — напоминание
    on: int = -1  # -1 — переключить, 0/1 — выставить


class CalCb(CallbackData, prefix="c"):
    id: int
    year: int
    month: int
    edit: int = 0  # 1 — под картинкой сетка дней для правки отметок


class DayCb(CallbackData, prefix="d"):
    id: int
    year: int
    month: int
    day: int  # 0 — пустая клетка сетки, нажатие ничего не делает


class KindCb(CallbackData, prefix="k"):
    kind: str


class NavCb(CallbackData, prefix="n"):
    action: str  # menu | today | habits | cal | add | tz | help | cancel


class OverviewCb(CallbackData, prefix="o"):
    year: int
    month: int


class TimeCb(CallbackData, prefix="t"):
    value: str  # ЧЧММ — двоеточие занято разделителем


class TzCb(CallbackData, prefix="z"):
    offset: int


class EmojiCb(CallbackData, prefix="e"):
    kind: str
    idx: int


def build_router(storage: Storage) -> Router:
    router = Router()

    def today_of(user_id: int) -> date:
        return datetime.now(zone(storage.get_tz(user_id))).date()

    # --- тексты и клавиатуры ---

    def mark_label(habit: Habit, marked: bool) -> str:
        title = f"{habit.icon} {habit.name}"
        if habit.is_bad:
            return f"❗️ {title} — было" if marked else f"▫️ {title} — не было"
        return f"✅ {title} — сделано" if marked else f"▫️ {title}"

    def empty_view() -> tuple[str, InlineKeyboardMarkup]:
        kb = InlineKeyboardBuilder()
        kb.button(text=BTN_ADD, callback_data=NavCb(action="add"))
        menu_button(kb)
        return "Привычек пока нет. Добавьте первую.", kb.as_markup()

    def menu_button(kb: InlineKeyboardBuilder) -> None:
        kb.button(text=BTN_MENU, callback_data=NavCb(action="menu"))

    def only_menu() -> InlineKeyboardMarkup:
        kb = InlineKeyboardBuilder()
        menu_button(kb)
        return kb.as_markup()

    def cancel_button(kb: InlineKeyboardBuilder) -> None:
        kb.button(text="Отмена", callback_data=NavCb(action="cancel"))

    def time_keyboard(*extra: tuple[str, CallbackData]) -> InlineKeyboardMarkup:
        kb = InlineKeyboardBuilder()
        for value in QUICK_TIMES:
            kb.button(text=value, callback_data=TimeCb(value=value.replace(":", "")))
        for text, data in extra:
            kb.button(text=text, callback_data=data)
        kb.adjust(4, 4, *([1] * len(extra)))
        return kb.as_markup()

    def emoji_keyboard(kind: str, back: CallbackData) -> InlineKeyboardMarkup:
        kb = InlineKeyboardBuilder()
        palette = BAD_EMOJI if kind == BAD else GOOD_EMOJI
        for idx, emoji in enumerate(palette):
            kb.button(text=emoji, callback_data=EmojiCb(kind=kind, idx=idx))
        kb.button(text="Отмена", callback_data=back)
        kb.adjust(6, 6, 1)
        return kb.as_markup()

    def palette_emoji(data: EmojiCb) -> str:
        palette = BAD_EMOJI if data.kind == BAD else GOOD_EMOJI
        return palette[data.idx % len(palette)]

    def today_view(user_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
        habits = storage.list_habits(user_id)
        if not habits:
            return empty_view()
        day = today_of(user_id)
        kb = InlineKeyboardBuilder()
        for habit in habits:
            kb.button(
                text=mark_label(habit, storage.is_marked(habit.id, day)),
                callback_data=MarkCb(id=habit.id, back=0, src="t"),
            )
        menu_button(kb)
        kb.adjust(1)
        text = (
            f"<b>Сегодня, {day.day} {MONTHS[day.month].lower()}</b>\n"
            "Нажмите на привычку, чтобы отметить или снять отметку.\n"
            "У вредных отметка значит «было»."
        )
        return text, kb.as_markup()

    def list_view(user_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
        habits = storage.list_habits(user_id)
        if not habits:
            return empty_view()
        kb = InlineKeyboardBuilder()
        for habit in habits:
            kb.button(
                text=f"{habit.icon} {habit.name}" + (" · вредная" if habit.is_bad else ""),
                callback_data=HabitCb(action="open", id=habit.id),
            )
        kb.button(text=BTN_ADD, callback_data=NavCb(action="add"))
        menu_button(kb)
        kb.adjust(1)
        return "<b>Мои привычки</b>\nНажмите на привычку, чтобы открыть её.", kb.as_markup()

    def streak_line(habit: Habit, day: date) -> str:
        days = streak(habit, storage.all_marks(habit.id), day)
        if habit.is_bad:
            return f"Без срывов: <b>{days} дн.</b>"
        return f"Серия: <b>{days} дн.</b> подряд"

    def card_view(habit: Habit) -> tuple[str, InlineKeyboardMarkup]:
        day = today_of(habit.user_id)
        yesterday = day - timedelta(days=1)
        kind = "вредная" if habit.is_bad else "полезная"
        remind = f"каждый день в {habit.remind_at}" if habit.remind_at else "нет"
        text = (
            f"{habit.icon} <b>{escape(habit.name)}</b>\n"
            f"Тип: {kind}\n"
            f"Напоминание: {remind}\n"
            f"{streak_line(habit, day)}"
        )
        word = "было" if habit.is_bad else "сделано"

        def state(d: date) -> str:
            return f"✔️ {word}" if storage.is_marked(habit.id, d) else f"▫️ не {word}"

        kb = InlineKeyboardBuilder()
        kb.button(text=f"Сегодня: {state(day)}", callback_data=MarkCb(id=habit.id, back=0, src="c"))
        if yesterday >= habit.created_on:
            kb.button(
                text=f"Вчера: {state(yesterday)}",
                callback_data=MarkCb(id=habit.id, back=1, src="c"),
            )
        kb.button(text="📅 Календарь", callback_data=HabitCb(action="cal", id=habit.id))
        kb.button(text="⏰ Напоминание", callback_data=HabitCb(action="rem", id=habit.id))
        kb.button(text="🎨 Эмодзи", callback_data=HabitCb(action="emoji", id=habit.id))
        kb.button(text="🗑 Удалить", callback_data=HabitCb(action="del", id=habit.id))
        kb.button(text="← К списку", callback_data=HabitCb(action="list"))
        kb.adjust(1, 1, 2, 2, 1) if yesterday >= habit.created_on else kb.adjust(1, 2, 2, 1)
        return text, kb.as_markup()

    def calendar_view(
        habit: Habit, year: int, month: int, edit: bool = False
    ) -> tuple[bytes, str, InlineKeyboardMarkup]:
        day = today_of(habit.user_id)
        marks = storage.all_marks(habit.id)
        image = render_month(
            name=habit.name,
            is_bad=habit.is_bad,
            emoji=habit.icon,
            year=year,
            month=month,
            marks=marks,
            created_on=habit.created_on,
            today=day,
        )
        in_month = sum(1 for d in marks if (d.year, d.month) == (year, month))
        word = "Срывов" if habit.is_bad else "Отмечено дней"
        caption = (
            f"{habit.icon} <b>{escape(habit.name)}</b> · {MONTHS[month]} {year}\n"
            f"{word} за месяц: <b>{in_month}</b>. {streak_line(habit, day)}"
        )
        kb = InlineKeyboardBuilder()
        rows: list[int] = []
        if edit:
            what = "было" if habit.is_bad else "сделано"
            caption += f"\n\nНажмите на день, чтобы поставить или снять отметку «{what}»."
            for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
                for d in week:
                    # Править можно дни этого месяца от заведения привычки до сегодня.
                    open_day = d.month == month and habit.created_on <= d <= day
                    label = (
                        (f"{habit.icon}{d.day}" if d in marks else str(d.day)) if open_day else " "
                    )
                    kb.button(
                        text=label,
                        callback_data=DayCb(
                            id=habit.id, year=year, month=month, day=d.day if open_day else 0
                        ),
                    )
                rows.append(7)
        prev_y, prev_m = shift_month(year, month, -1)
        next_y, next_m = shift_month(year, month, 1)
        nav = 0
        # Раньше месяца, когда привычку завели, и позже текущего листать незачем.
        if (prev_y, prev_m) >= (habit.created_on.year, habit.created_on.month):
            kb.button(
                text="◀", callback_data=CalCb(id=habit.id, year=prev_y, month=prev_m, edit=edit)
            )
            nav += 1
        kb.button(
            text="Готово" if edit else "✏️ Изменить отметки",
            callback_data=CalCb(id=habit.id, year=year, month=month, edit=0 if edit else 1),
        )
        nav += 1
        if (next_y, next_m) <= (day.year, day.month):
            kb.button(
                text="▶", callback_data=CalCb(id=habit.id, year=next_y, month=next_m, edit=edit)
            )
            nav += 1
        kb.adjust(*rows, nav)
        return image, caption, kb.as_markup()

    def overview_view(
        user_id: int, year: int, month: int
    ) -> tuple[bytes, str, InlineKeyboardMarkup]:
        """Общий календарь: все привычки пользователя на одной картинке месяца."""
        day = today_of(user_id)
        habits = storage.list_habits(user_id)
        image = render_overview(
            year=year,
            month=month,
            habits=[
                OverviewHabit(
                    name=h.name,
                    emoji=h.icon,
                    is_bad=h.is_bad,
                    marks=frozenset(storage.all_marks(h.id)),
                )
                for h in habits
            ],
            today=day,
        )
        caption = (
            f"<b>Все привычки</b> · {MONTHS[month]} {year}\n"
            "Зелёный значок — полезная сделана, красный — вредная была."
        )
        first = min((h.created_on for h in habits), default=day)
        prev_y, prev_m = shift_month(year, month, -1)
        next_y, next_m = shift_month(year, month, 1)
        kb = InlineKeyboardBuilder()
        # Листаем от месяца самой ранней привычки до текущего.
        if (prev_y, prev_m) >= (first.year, first.month):
            kb.button(
                text=f"◀ {MONTHS[prev_m]}", callback_data=OverviewCb(year=prev_y, month=prev_m)
            )
        if (next_y, next_m) <= (day.year, day.month):
            kb.button(
                text=f"{MONTHS[next_m]} ▶", callback_data=OverviewCb(year=next_y, month=next_m)
            )
        return image, caption, kb.as_markup()

    @router.callback_query(NavCb.filter(F.action == "cal"))
    async def open_overview(query: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        if not storage.list_habits(query.from_user.id):
            text, kb = empty_view()
            await query.message.edit_text(text, reply_markup=kb)
            await query.answer()
            return
        day = today_of(query.from_user.id)
        image, caption, kb = overview_view(query.from_user.id, day.year, day.month)
        await query.message.answer_photo(
            BufferedInputFile(image, filename="overview.png"), caption=caption, reply_markup=kb
        )
        await query.answer()

    @router.callback_query(OverviewCb.filter())
    async def turn_overview(query: CallbackQuery, callback_data: OverviewCb) -> None:
        image, caption, kb = overview_view(
            query.from_user.id, callback_data.year, callback_data.month
        )
        await query.message.edit_media(
            InputMediaPhoto(
                media=BufferedInputFile(image, filename="overview.png"),
                caption=caption,
                parse_mode="HTML",
            ),
            reply_markup=kb,
        )
        await query.answer()

    # --- меню и общие команды ---

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        storage.ensure_user(message.from_user.id)
        # Первым сообщением убираем клавиатуру прежних версий, если она осталась на экране.
        await message.answer("Привет! " + HELP, reply_markup=ReplyKeyboardRemove())
        await message.answer(MENU_TEXT, reply_markup=main_menu())

    @router.message(Command("help"))
    async def help_(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(HELP, reply_markup=only_menu())

    @router.callback_query(
        NavCb.filter(F.action.in_({"menu", "cancel", "today", "habits", "help"}))
    )
    async def navigate(query: CallbackQuery, callback_data: NavCb, state: FSMContext) -> None:
        """Переходы по меню: тот же экран перерисовывается в том же сообщении."""
        await state.clear()
        user_id = query.from_user.id
        if callback_data.action == "today":
            text, kb = today_view(user_id)
        elif callback_data.action == "habits":
            text, kb = list_view(user_id)
        elif callback_data.action == "help":
            text, kb = HELP, only_menu()
        else:
            text, kb = MENU_TEXT, main_menu()
        await query.message.edit_text(text, reply_markup=kb)
        await query.answer("Отменено" if callback_data.action == "cancel" else None)

    # --- добавление: название → тип → эмодзи → напоминание ---

    async def begin_add(user_id: int, state: FSMContext) -> tuple[str, InlineKeyboardMarkup]:
        await state.clear()
        if len(storage.list_habits(user_id)) >= MAX_HABITS:
            return (
                f"Уже {MAX_HABITS} привычек — удалите лишние в разделе «{BTN_HABITS}».",
                only_menu(),
            )
        await state.set_state(Add.name)
        kb = InlineKeyboardBuilder()
        cancel_button(kb)
        return "Как называется привычка? Например: «Зарядка» или «Сладкое».", kb.as_markup()

    @router.callback_query(NavCb.filter(F.action == "add"))
    async def add(query: CallbackQuery, state: FSMContext) -> None:
        text, kb = await begin_add(query.from_user.id, state)
        await query.message.edit_text(text, reply_markup=kb)
        await query.answer()

    @router.message(Add.name, F.text)
    async def add_name(message: Message, state: FSMContext) -> None:
        name = " ".join(message.text.split())
        if not name or name.startswith("/"):
            await message.answer("Пришлите название текстом.")
            return
        if len(name) > MAX_NAME:
            await message.answer(f"Слишком длинно — до {MAX_NAME} символов.")
            return
        await state.update_data(name=name)
        await state.set_state(Add.kind)
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Полезная", callback_data=KindCb(kind=GOOD))
        kb.button(text="🚫 Вредная", callback_data=KindCb(kind=BAD))
        cancel_button(kb)
        kb.adjust(2, 1)
        await message.answer(
            "Это полезная привычка или вредная?\n"
            "Полезную отмечают, когда сделали. Вредную — когда сорвались.",
            reply_markup=kb.as_markup(),
        )

    @router.callback_query(Add.kind, KindCb.filter())
    async def add_kind(query: CallbackQuery, callback_data: KindCb, state: FSMContext) -> None:
        await state.update_data(kind=callback_data.kind)
        await state.set_state(Add.emoji)
        await query.message.edit_text(
            "Выберите эмодзи — им привычка обозначается в списках и на календаре.\n"
            "Можно прислать свой.",
            reply_markup=emoji_keyboard(callback_data.kind, NavCb(action="cancel")),
        )
        await query.answer()

    def time_question(kind: str) -> tuple[str, InlineKeyboardMarkup]:
        hint = (
            "Во сколько спрашивать, как прошёл день?" if kind == BAD else "Во сколько напоминать?"
        )
        kb = time_keyboard(
            ("Без напоминания", HabitCb(action="norem")),
            ("Отмена", NavCb(action="cancel")),
        )
        return f"{hint} Выберите время или пришлите своё, например 21:30.", kb

    @router.callback_query(Add.emoji, EmojiCb.filter())
    async def add_emoji(query: CallbackQuery, callback_data: EmojiCb, state: FSMContext) -> None:
        await state.update_data(emoji=palette_emoji(callback_data))
        await state.set_state(Add.time)
        text, kb = time_question((await state.get_data())["kind"])
        await query.message.edit_text(text, reply_markup=kb)
        await query.answer()

    @router.message(Add.emoji, F.text)
    async def add_own_emoji(message: Message, state: FSMContext) -> None:
        emoji = clean_emoji(message.text)
        if emoji is None:
            await message.answer(
                "Это не похоже на эмодзи, который я смогу нарисовать. Пришлите другой или выберите из кнопок выше."
            )
            return
        await state.update_data(emoji=emoji)
        await state.set_state(Add.time)
        text, kb = time_question((await state.get_data())["kind"])
        await message.answer(text, reply_markup=kb)

    async def finish_add(user_id: int, state: FSMContext, remind_at: str | None) -> Habit:
        data = await state.get_data()
        await state.clear()
        return storage.add_habit(
            user_id, data["name"], data["kind"], remind_at, today_of(user_id), data.get("emoji")
        )

    def added_text(habit: Habit) -> str:
        text, _ = card_view(habit)
        if habit.remind_at:
            tz = storage.get_tz(habit.user_id)
            return f"Добавлено. Часовой пояс напоминаний: {tz}.\n\n{text}"
        return f"Добавлено.\n\n{text}"

    @router.callback_query(Add.time, HabitCb.filter(F.action == "norem"))
    async def add_no_time(query: CallbackQuery, state: FSMContext) -> None:
        habit = await finish_add(query.from_user.id, state, None)
        await query.message.edit_text(added_text(habit), reply_markup=card_view(habit)[1])
        await query.answer()

    @router.callback_query(Add.time, TimeCb.filter())
    async def add_quick_time(
        query: CallbackQuery, callback_data: TimeCb, state: FSMContext
    ) -> None:
        habit = await finish_add(query.from_user.id, state, parse_time(callback_data.value))
        await query.message.edit_text(added_text(habit), reply_markup=card_view(habit)[1])
        await query.answer()

    @router.message(Add.time, F.text)
    async def add_time(message: Message, state: FSMContext) -> None:
        remind_at = parse_time(message.text)
        if remind_at is None:
            await message.answer("Не понял время. Пришлите в виде ЧЧ:ММ, например 21:30.")
            return
        habit = await finish_add(message.from_user.id, state, remind_at)
        await message.answer(added_text(habit), reply_markup=card_view(habit)[1])

    # --- карточка, календарь, удаление ---

    async def habit_or_alert(query: CallbackQuery, habit_id: int) -> Habit | None:
        habit = storage.get_habit(query.from_user.id, habit_id)
        if habit is None:
            await query.answer("Этой привычки уже нет.", show_alert=True)
        return habit

    @router.callback_query(HabitCb.filter(F.action == "list"))
    async def open_list(query: CallbackQuery) -> None:
        text, kb = list_view(query.from_user.id)
        await query.message.edit_text(text, reply_markup=kb)
        await query.answer()

    @router.callback_query(HabitCb.filter(F.action == "open"))
    async def open_card(query: CallbackQuery, callback_data: HabitCb, state: FSMContext) -> None:
        await state.clear()  # «Отмена» из выбора времени или эмодзи ведёт сюда же
        habit = await habit_or_alert(query, callback_data.id)
        if habit is None:
            return
        text, kb = card_view(habit)
        await query.message.edit_text(text, reply_markup=kb)
        await query.answer()

    @router.callback_query(HabitCb.filter(F.action == "cal"))
    async def open_calendar(query: CallbackQuery, callback_data: HabitCb) -> None:
        habit = await habit_or_alert(query, callback_data.id)
        if habit is None:
            return
        day = today_of(habit.user_id)
        image, caption, kb = calendar_view(habit, day.year, day.month)
        await query.message.answer_photo(
            BufferedInputFile(image, filename="calendar.png"), caption=caption, reply_markup=kb
        )
        await query.answer()

    @router.callback_query(CalCb.filter())
    async def turn_calendar(query: CallbackQuery, callback_data: CalCb) -> None:
        habit = await habit_or_alert(query, callback_data.id)
        if habit is None:
            return
        await show_calendar(
            query, habit, callback_data.year, callback_data.month, bool(callback_data.edit)
        )
        await query.answer()

    async def show_calendar(
        query: CallbackQuery, habit: Habit, year: int, month: int, edit: bool
    ) -> None:
        image, caption, kb = calendar_view(habit, year, month, edit)
        await query.message.edit_media(
            InputMediaPhoto(
                media=BufferedInputFile(image, filename="calendar.png"),
                caption=caption,
                parse_mode="HTML",
            ),
            reply_markup=kb,
        )

    @router.callback_query(DayCb.filter())
    async def toggle_day(query: CallbackQuery, callback_data: DayCb) -> None:
        if callback_data.day == 0:
            await query.answer()
            return
        habit = await habit_or_alert(query, callback_data.id)
        if habit is None:
            return
        day = date(callback_data.year, callback_data.month, callback_data.day)
        # Кнопка могла устареть: проверяем границы заново.
        if not habit.created_on <= day <= today_of(habit.user_id):
            await query.answer("Этот день отметить нельзя.", show_alert=True)
            return
        on = storage.toggle_mark(habit.id, day)
        await show_calendar(query, habit, callback_data.year, callback_data.month, True)
        word = ("было" if habit.is_bad else "сделано") if on else "отметка снята"
        await query.answer(f"{day.day} {MONTHS[day.month].lower()}: {word}")

    # --- эмодзи ---

    @router.callback_query(HabitCb.filter(F.action == "emoji"))
    async def ask_emoji(query: CallbackQuery, callback_data: HabitCb, state: FSMContext) -> None:
        habit = await habit_or_alert(query, callback_data.id)
        if habit is None:
            return
        await state.set_state(EmojiEdit.value)
        await state.update_data(habit_id=habit.id)
        await query.message.edit_text(
            f"Сейчас у «{escape(habit.name)}» эмодзи {habit.icon}. Выберите новый или пришлите свой.",
            reply_markup=emoji_keyboard(habit.kind, HabitCb(action="open", id=habit.id)),
        )
        await query.answer()

    @router.callback_query(EmojiEdit.value, EmojiCb.filter())
    async def pick_emoji(query: CallbackQuery, callback_data: EmojiCb, state: FSMContext) -> None:
        data = await state.get_data()
        await state.clear()
        storage.set_emoji(query.from_user.id, data["habit_id"], palette_emoji(callback_data))
        habit = await habit_or_alert(query, data["habit_id"])
        if habit is None:
            return
        text, kb = card_view(habit)
        await query.message.edit_text(text, reply_markup=kb)
        await query.answer("Эмодзи изменён")

    @router.message(EmojiEdit.value, F.text)
    async def send_emoji(message: Message, state: FSMContext) -> None:
        emoji = clean_emoji(message.text)
        if emoji is None:
            await message.answer(
                "Это не похоже на эмодзи, который я смогу нарисовать. Пришлите другой или выберите из кнопок выше."
            )
            return
        data = await state.get_data()
        await state.clear()
        storage.set_emoji(message.from_user.id, data["habit_id"], emoji)
        habit = storage.get_habit(message.from_user.id, data["habit_id"])
        if habit is None:
            await message.answer("Этой привычки уже нет.")
            return
        text, kb = card_view(habit)
        await message.answer(text, reply_markup=kb)

    @router.callback_query(HabitCb.filter(F.action == "del"))
    async def ask_delete(query: CallbackQuery, callback_data: HabitCb) -> None:
        habit = await habit_or_alert(query, callback_data.id)
        if habit is None:
            return
        kb = InlineKeyboardBuilder()
        kb.button(text="Да, удалить", callback_data=HabitCb(action="delok", id=habit.id))
        kb.button(text="Оставить", callback_data=HabitCb(action="open", id=habit.id))
        await query.message.edit_text(
            f"Удалить «{escape(habit.name)}» вместе с отметками?", reply_markup=kb.as_markup()
        )
        await query.answer()

    @router.callback_query(HabitCb.filter(F.action == "delok"))
    async def delete(query: CallbackQuery, callback_data: HabitCb) -> None:
        storage.delete_habit(query.from_user.id, callback_data.id)
        text, kb = list_view(query.from_user.id)
        await query.message.edit_text("Удалено.\n\n" + text, reply_markup=kb)
        await query.answer()

    # --- отметки ---

    @router.callback_query(MarkCb.filter())
    async def mark(query: CallbackQuery, callback_data: MarkCb) -> None:
        habit = await habit_or_alert(query, callback_data.id)
        if habit is None:
            return
        day = today_of(habit.user_id) - timedelta(days=callback_data.back)
        if day < habit.created_on:
            await query.answer("Привычки в этот день ещё не было.", show_alert=True)
            return
        if callback_data.on == -1:
            on = storage.toggle_mark(habit.id, day)
        else:
            on = bool(callback_data.on)
            storage.set_mark(habit.id, day, on)

        if habit.is_bad:
            note = "Записал: было. Завтра новый день." if on else "Записал: не было 💪"
        else:
            note = "Отмечено ✅" if on else "Отметка снята"

        if callback_data.src == "t":
            _, kb = today_view(query.from_user.id)
            await query.message.edit_reply_markup(reply_markup=kb)
        elif callback_data.src == "c":
            text, kb = card_view(habit)
            await query.message.edit_text(text, reply_markup=kb)
        else:  # ответ на напоминание — кнопки больше не нужны
            await query.message.edit_text(
                f"<b>{escape(habit.name)}</b>\n{note}\n{streak_line(habit, today_of(habit.user_id))}",
                reply_markup=only_menu(),
            )
        await query.answer(note)

    # --- напоминание ---

    @router.callback_query(HabitCb.filter(F.action == "rem"))
    async def ask_reminder(query: CallbackQuery, callback_data: HabitCb, state: FSMContext) -> None:
        habit = await habit_or_alert(query, callback_data.id)
        if habit is None:
            return
        await state.set_state(Remind.time)
        await state.update_data(habit_id=habit.id)
        extra: list[tuple[str, CallbackData]] = []
        if habit.remind_at:
            extra.append(("Убрать напоминание", HabitCb(action="norem", id=habit.id)))
        extra.append(("Отмена", HabitCb(action="open", id=habit.id)))
        current = f"Сейчас: {habit.remind_at}. " if habit.remind_at else ""
        await query.message.edit_text(
            f"{current}Во сколько напоминать про «{escape(habit.name)}»? Выберите время или "
            f"пришлите своё, например 21:30.\nЧасовой пояс: {storage.get_tz(habit.user_id)}.",
            reply_markup=time_keyboard(*extra),
        )
        await query.answer()

    @router.callback_query(Remind.time, TimeCb.filter())
    async def quick_reminder(
        query: CallbackQuery, callback_data: TimeCb, state: FSMContext
    ) -> None:
        data = await state.get_data()
        await state.clear()
        storage.set_reminder(query.from_user.id, data["habit_id"], parse_time(callback_data.value))
        habit = await habit_or_alert(query, data["habit_id"])
        if habit is None:
            return
        text, kb = card_view(habit)
        await query.message.edit_text(text, reply_markup=kb)
        await query.answer(f"Напоминание в {habit.remind_at}")

    @router.callback_query(Remind.time, HabitCb.filter(F.action == "norem"))
    async def drop_reminder(
        query: CallbackQuery, callback_data: HabitCb, state: FSMContext
    ) -> None:
        await state.clear()
        storage.set_reminder(query.from_user.id, callback_data.id, None)
        habit = await habit_or_alert(query, callback_data.id)
        if habit is None:
            return
        text, kb = card_view(habit)
        await query.message.edit_text(text, reply_markup=kb)
        await query.answer("Напоминание убрано")

    @router.message(Remind.time, F.text)
    async def set_reminder(message: Message, state: FSMContext) -> None:
        remind_at = parse_time(message.text)
        if remind_at is None:
            await message.answer("Не понял время. Пришлите в виде ЧЧ:ММ, например 21:30.")
            return
        data = await state.get_data()
        await state.clear()
        storage.set_reminder(message.from_user.id, data["habit_id"], remind_at)
        habit = storage.get_habit(message.from_user.id, data["habit_id"])
        if habit is None:
            await message.answer("Этой привычки уже нет.")
            return
        text, kb = card_view(habit)
        await message.answer(text, reply_markup=kb)

    # --- часовой пояс ---

    @router.callback_query(NavCb.filter(F.action == "tz"))
    async def tz(query: CallbackQuery, state: FSMContext) -> None:
        current = storage.get_tz(query.from_user.id)
        now = datetime.now(zone(current)).strftime("%H:%M")
        await state.set_state(Tz.value)
        kb = InlineKeyboardBuilder()
        for city, offset in QUICK_ZONES:
            kb.button(text=f"{city} +{offset}", callback_data=TzCb(offset=offset))
        cancel_button(kb)
        kb.adjust(2)
        await query.message.edit_text(
            f"Сейчас пояс <b>{current}</b>, у вас {now}.\n"
            "Выберите город или пришлите название пояса (Europe/Berlin) либо смещение "
            "от UTC (+1, -5).",
            reply_markup=kb.as_markup(),
        )
        await query.answer()

    def tz_done(user_id: int, name: str) -> str:
        storage.set_tz(user_id, name)
        now = datetime.now(zone(name)).strftime("%H:%M")
        return f"Готово: {name}, у вас сейчас {now}."

    @router.callback_query(TzCb.filter())
    async def pick_tz(query: CallbackQuery, callback_data: TzCb, state: FSMContext) -> None:
        await state.clear()
        name = parse_tz(f"+{callback_data.offset}")
        if name is None:
            await query.answer("Не знаю такой пояс.", show_alert=True)
            return
        await query.message.edit_text(tz_done(query.from_user.id, name), reply_markup=only_menu())
        await query.answer()

    @router.message(Tz.value, F.text)
    async def set_tz(message: Message, state: FSMContext) -> None:
        name = parse_tz(message.text)
        if name is None:
            await message.answer("Не знаю такой пояс. Пример: Europe/Berlin или +3.")
            return
        await state.clear()
        await message.answer(tz_done(message.from_user.id, name), reply_markup=only_menu())

    # --- всё остальное ---

    @router.message()
    async def fallback(message: Message) -> None:
        await message.answer(MENU_TEXT, reply_markup=main_menu())

    return router


_OFFSET = re.compile(r"^(?:utc|gmt)?\s*([+-])\s*(\d{1,2})$", re.IGNORECASE)


def parse_tz(text: str) -> str | None:
    """Название пояса или целое смещение от UTC → имя для zoneinfo."""
    value = text.strip()
    match = _OFFSET.match(value)
    if match:
        hours = int(match.group(2))
        if hours > 14:
            return None
        if hours == 0:
            return "UTC"
        # В базе поясов знак у Etc/GMT перевёрнут: UTC+3 — это Etc/GMT-3.
        return f"Etc/GMT{'-' if match.group(1) == '+' else '+'}{hours}"
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None
    return value


def reminder_message(habit: Habit) -> tuple[str, InlineKeyboardMarkup]:
    kb = InlineKeyboardBuilder()
    if habit.is_bad:
        kb.button(text="Не было 💪", callback_data=MarkCb(id=habit.id, back=0, src="r", on=0))
        kb.button(text="Было", callback_data=MarkCb(id=habit.id, back=0, src="r", on=1))
        return f"⏰ {habit.icon} <b>{escape(habit.name)}</b> — как сегодня?", kb.as_markup()
    kb.button(text="✅ Сделано", callback_data=MarkCb(id=habit.id, back=0, src="r", on=1))
    return f"⏰ Пора: {habit.icon} <b>{escape(habit.name)}</b>", kb.as_markup()


async def send_reminder(bot: Bot, habit: Habit) -> None:
    text, kb = reminder_message(habit)
    await bot.send_message(habit.user_id, text, reply_markup=kb)
