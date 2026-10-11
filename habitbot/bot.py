"""Команды и кнопки бота."""

from __future__ import annotations

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
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .calendar_image import MONTHS, render_month, shift_month
from .db import BAD, GOOD, Habit, Storage, streak
from .reminders import parse_time, zone

MAX_NAME = 60
MAX_HABITS = 30

COMMANDS = [
    BotCommand(command="today", description="Отметить привычки за сегодня"),
    BotCommand(command="habits", description="Мои привычки и календарь"),
    BotCommand(command="add", description="Добавить привычку"),
    BotCommand(command="tz", description="Часовой пояс для напоминаний"),
    BotCommand(command="help", description="Как пользоваться"),
]

HELP = (
    "Я помогаю следить за привычками.\n\n"
    "• <b>Полезные</b> — отмечайте дни, когда сделали. Зелёный день в календаре — сделано.\n"
    "• <b>Вредные</b> — отмечайте дни, когда сорвались. Красный день — было, зелёный — не было.\n\n"
    "/add — добавить привычку\n"
    "/today — отметить за сегодня\n"
    "/habits — список, календарь, напоминания, удаление\n"
    "/tz — часовой пояс (по нему приходят напоминания и считается «сегодня»)\n"
    "/cancel — отменить ввод"
)


class Add(StatesGroup):
    name = State()
    kind = State()
    time = State()


class Remind(StatesGroup):
    time = State()


class Tz(StatesGroup):
    value = State()


class HabitCb(CallbackData, prefix="h"):
    action: str  # open | cal | rem | norem | del | delok | list
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


class KindCb(CallbackData, prefix="k"):
    kind: str


def build_router(storage: Storage) -> Router:
    router = Router()

    def today_of(user_id: int) -> date:
        return datetime.now(zone(storage.get_tz(user_id))).date()

    # --- тексты и клавиатуры ---

    def mark_label(habit: Habit, marked: bool) -> str:
        if habit.is_bad:
            return f"🚫 {habit.name} — было" if marked else f"▫️ {habit.name} — не было"
        return f"✅ {habit.name} — сделано" if marked else f"▫️ {habit.name}"

    def today_view(user_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
        habits = storage.list_habits(user_id)
        if not habits:
            return "Привычек пока нет. Добавьте первую: /add", None
        day = today_of(user_id)
        kb = InlineKeyboardBuilder()
        for habit in habits:
            kb.button(
                text=mark_label(habit, storage.is_marked(habit.id, day)),
                callback_data=MarkCb(id=habit.id, back=0, src="t"),
            )
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
            return "Привычек пока нет. Добавьте первую: /add", None
        kb = InlineKeyboardBuilder()
        for habit in habits:
            kb.button(
                text=f"{habit.icon} {habit.name}",
                callback_data=HabitCb(action="open", id=habit.id),
            )
        kb.adjust(1)
        return "<b>Мои привычки</b>\n✅ полезные · 🚫 вредные", kb.as_markup()

    def streak_line(habit: Habit, day: date) -> str:
        days = streak(habit, storage.all_marks(habit.id), day)
        if habit.is_bad:
            return f"Без срывов: <b>{days} дн.</b>"
        return f"Серия: <b>{days} дн.</b> подряд"

    def card_view(habit: Habit) -> tuple[str, InlineKeyboardMarkup]:
        day = today_of(habit.user_id)
        yesterday = day - timedelta(days=1)
        kind = "вредная 🚫" if habit.is_bad else "полезная ✅"
        remind = f"каждый день в {habit.remind_at}" if habit.remind_at else "нет"
        text = (
            f"<b>{escape(habit.name)}</b>\n"
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
        kb.button(text="🗑 Удалить", callback_data=HabitCb(action="del", id=habit.id))
        kb.button(text="← К списку", callback_data=HabitCb(action="list"))
        kb.adjust(1, 1, 2, 2) if yesterday >= habit.created_on else kb.adjust(1, 2, 2)
        return text, kb.as_markup()

    def calendar_view(
        habit: Habit, year: int, month: int
    ) -> tuple[bytes, str, InlineKeyboardMarkup]:
        day = today_of(habit.user_id)
        marks = storage.all_marks(habit.id)
        image = render_month(
            name=habit.name,
            is_bad=habit.is_bad,
            year=year,
            month=month,
            marks=marks,
            created_on=habit.created_on,
            today=day,
        )
        in_month = sum(1 for d in marks if (d.year, d.month) == (year, month))
        word = "Срывов" if habit.is_bad else "Отмечено дней"
        caption = (
            f"<b>{escape(habit.name)}</b> · {MONTHS[month]} {year}\n"
            f"{word} за месяц: <b>{in_month}</b>. {streak_line(habit, day)}"
        )
        prev_y, prev_m = shift_month(year, month, -1)
        next_y, next_m = shift_month(year, month, 1)
        kb = InlineKeyboardBuilder()
        # Раньше месяца, когда привычку завели, и позже текущего листать незачем.
        if (prev_y, prev_m) >= (habit.created_on.year, habit.created_on.month):
            kb.button(text="◀", callback_data=CalCb(id=habit.id, year=prev_y, month=prev_m))
        if (next_y, next_m) <= (day.year, day.month):
            kb.button(text="▶", callback_data=CalCb(id=habit.id, year=next_y, month=next_m))
        return image, caption, kb.as_markup()

    # --- общие команды ---

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        storage.ensure_user(message.from_user.id)
        await message.answer("Привет! " + HELP)

    @router.message(Command("help"))
    async def help_(message: Message) -> None:
        await message.answer(HELP)

    @router.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Отменено.")

    @router.message(Command("today"))
    async def today(message: Message, state: FSMContext) -> None:
        await state.clear()
        text, kb = today_view(message.from_user.id)
        await message.answer(text, reply_markup=kb)

    @router.message(Command("habits"))
    async def habits(message: Message, state: FSMContext) -> None:
        await state.clear()
        text, kb = list_view(message.from_user.id)
        await message.answer(text, reply_markup=kb)

    # --- добавление ---

    @router.message(Command("add"))
    async def add(message: Message, state: FSMContext) -> None:
        if len(storage.list_habits(message.from_user.id)) >= MAX_HABITS:
            await message.answer(f"Уже {MAX_HABITS} привычек — удалите лишние в /habits.")
            return
        await state.set_state(Add.name)
        await message.answer("Как называется привычка? Например: «Зарядка» или «Сладкое».")

    @router.message(Add.name, F.text)
    async def add_name(message: Message, state: FSMContext) -> None:
        name = " ".join(message.text.split())
        if not name or name.startswith("/"):
            await message.answer("Пришлите название текстом или /cancel.")
            return
        if len(name) > MAX_NAME:
            await message.answer(f"Слишком длинно — до {MAX_NAME} символов.")
            return
        await state.update_data(name=name)
        await state.set_state(Add.kind)
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Полезная", callback_data=KindCb(kind=GOOD))
        kb.button(text="🚫 Вредная", callback_data=KindCb(kind=BAD))
        await message.answer(
            "Это полезная привычка или вредная?\n"
            "Полезную отмечают, когда сделали. Вредную — когда сорвались.",
            reply_markup=kb.as_markup(),
        )

    @router.callback_query(Add.kind, KindCb.filter())
    async def add_kind(query: CallbackQuery, callback_data: KindCb, state: FSMContext) -> None:
        await state.update_data(kind=callback_data.kind)
        await state.set_state(Add.time)
        kb = InlineKeyboardBuilder()
        kb.button(text="Без напоминания", callback_data=HabitCb(action="norem"))
        hint = (
            "Во сколько спрашивать, как прошёл день? Например 21:30."
            if callback_data.kind == BAD
            else "Во сколько напоминать? Например 8:00."
        )
        await query.message.edit_text(hint, reply_markup=kb.as_markup())
        await query.answer()

    async def finish_add(user_id: int, state: FSMContext, remind_at: str | None) -> Habit:
        data = await state.get_data()
        await state.clear()
        return storage.add_habit(user_id, data["name"], data["kind"], remind_at, today_of(user_id))

    @router.callback_query(Add.time, HabitCb.filter(F.action == "norem"))
    async def add_no_time(query: CallbackQuery, state: FSMContext) -> None:
        habit = await finish_add(query.from_user.id, state, None)
        text, kb = card_view(habit)
        await query.message.edit_text("Добавлено.\n\n" + text, reply_markup=kb)
        await query.answer()

    @router.message(Add.time, F.text)
    async def add_time(message: Message, state: FSMContext) -> None:
        remind_at = parse_time(message.text)
        if remind_at is None:
            await message.answer("Не понял время. Пришлите в виде ЧЧ:ММ, например 21:30.")
            return
        habit = await finish_add(message.from_user.id, state, remind_at)
        text, kb = card_view(habit)
        tz = storage.get_tz(message.from_user.id)
        await message.answer(
            f"Добавлено. Часовой пояс напоминаний: {tz} (сменить — /tz).\n\n" + text,
            reply_markup=kb,
        )

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
    async def open_card(query: CallbackQuery, callback_data: HabitCb) -> None:
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
        image, caption, kb = calendar_view(habit, callback_data.year, callback_data.month)
        await query.message.edit_media(
            InputMediaPhoto(
                media=BufferedInputFile(image, filename="calendar.png"),
                caption=caption,
                parse_mode="HTML",
            ),
            reply_markup=kb,
        )
        await query.answer()

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
                f"<b>{escape(habit.name)}</b>\n{note}\n{streak_line(habit, today_of(habit.user_id))}"
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
        kb = InlineKeyboardBuilder()
        if habit.remind_at:
            kb.button(text="Убрать напоминание", callback_data=HabitCb(action="norem", id=habit.id))
        kb.button(text="Отмена", callback_data=HabitCb(action="open", id=habit.id))
        kb.adjust(1)
        current = f"Сейчас: {habit.remind_at}. " if habit.remind_at else ""
        await query.message.edit_text(
            f"{current}Пришлите время напоминания для «{escape(habit.name)}» — ЧЧ:ММ, "
            f"например 21:30.\nЧасовой пояс: {storage.get_tz(habit.user_id)}.",
            reply_markup=kb.as_markup(),
        )
        await query.answer()

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

    @router.callback_query(Remind.time, HabitCb.filter(F.action == "open"))
    async def cancel_reminder(
        query: CallbackQuery, callback_data: HabitCb, state: FSMContext
    ) -> None:
        await state.clear()
        await open_card(query, callback_data)

    @router.message(Remind.time, F.text)
    async def set_reminder(message: Message, state: FSMContext) -> None:
        remind_at = parse_time(message.text)
        if remind_at is None:
            await message.answer("Не понял время. Пришлите в виде ЧЧ:ММ или /cancel.")
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

    @router.message(Command("tz"))
    async def tz(message: Message, state: FSMContext) -> None:
        current = storage.get_tz(message.from_user.id)
        now = datetime.now(zone(current)).strftime("%H:%M")
        await state.set_state(Tz.value)
        await message.answer(
            f"Сейчас пояс <b>{current}</b>, у вас {now}.\n"
            "Чтобы сменить, пришлите название (Europe/Moscow, Asia/Yekaterinburg) "
            "или смещение от UTC (+3, -5). Оставить как есть — /cancel."
        )

    @router.message(Tz.value, F.text)
    async def set_tz(message: Message, state: FSMContext) -> None:
        name = parse_tz(message.text)
        if name is None:
            await message.answer("Не знаю такой пояс. Пример: Europe/Moscow или +3. Или /cancel.")
            return
        await state.clear()
        storage.set_tz(message.from_user.id, name)
        now = datetime.now(zone(name)).strftime("%H:%M")
        await message.answer(f"Готово: {name}, у вас сейчас {now}.")

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
        return f"⏰ <b>{escape(habit.name)}</b> — как сегодня?", kb.as_markup()
    kb.button(text="✅ Сделано", callback_data=MarkCb(id=habit.id, back=0, src="r", on=1))
    return f"⏰ Пора: <b>{escape(habit.name)}</b>", kb.as_markup()


async def send_reminder(bot: Bot, habit: Habit) -> None:
    text, kb = reminder_message(habit)
    await bot.send_message(habit.user_id, text, reply_markup=kb)
