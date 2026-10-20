"""Команды и кнопки бота."""

from __future__ import annotations

import calendar
import logging
import re
import secrets
from datetime import date, datetime, timedelta
from html import escape
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    CopyTextButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultCachedPhoto,
    InlineQueryResultsButton,
    InputMediaPhoto,
    LinkPreviewOptions,
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
from .db import BAD, GOOD, Habit, Storage, best_clean_streak, streak
from .motivation import ABOUT, AUTO, OFFERED, STYLES, Motivator
from .reminders import parse_time, zone

log = logging.getLogger(__name__)

MAX_NAME = 60
MAX_HABITS = 30
#: На сколько дней назад можно ставить отметки и листать календарь привычки.
BACKFILL_DAYS = 366
#: Сколько привычек перечислять в подписи при пересылке общего календаря (подпись ≤ 1024).
SHARE_HABITS = 10

# Подписи кнопок главного меню (инлайн-кнопки под сообщением).
BTN_TODAY = "📋 Сегодня"
BTN_HABITS = "📚 Привычки"
BTN_ADD = "➕ Добавить"
BTN_CAL = "🗓 Календарь"
BTN_TZ = "🌍 Часовой пояс"
BTN_HELP = "❓ Помощь"
BTN_MENU = "☰ Меню"
BTN_INVITE = "🤝 Пригласить"
BTN_SHARE = "📣 Поделиться"
BTN_MOTIVATION = "🔥 Мотивация"

#: Тон мотивации коротко — для экрана настроек.
TONE_WORDS = {-2: "очень мягко", -1: "мягко", 0: "обычный", 1: "бодро", 2: "жёстко"}
#: Кнопки отзыва под мотивацией: подпись, сдвиг тона, что записать.
FEEDBACK = [("🪶 Мягче", -1, "softer"), ("👍 Принято", 0, "ok"), ("💥 Жёстче", 1, "harder")]
MAX_WHY = 200

SHARE_TEXT = "Веду привычки в этом боте — отметки, напоминания и календарь. Присоединяйся!"

# Команды остаются запасным входом; в списке команд Telegram — только самое нужное.
COMMANDS = [
    BotCommand(command="start", description="Показать меню"),
    BotCommand(command="help", description="Как пользоваться"),
]

HELP = (
    "Я помогаю следить за привычками. Всё управление — кнопками под сообщениями.\n\n"
    "• <b>Полезные</b> — отвечайте «сделано» или «не сделано». Зелёный день в календаре — "
    "сделано, серый — не сделано.\n"
    "• <b>Вредные</b> — отвечайте «было» или «не было». Красный день — было, зелёный — не было.\n"
    "• День без ответа — «нет данных»: в календаре он в пунктирной рамке и не выдаётся ни за "
    "«было», ни за «не было».\n"
    "• Напоминание можно поставить на следующий день: тогда оно спрашивает про вчерашний "
    "день — удобно, если итог дня ясен только к ночи.\n"
    "• У каждой привычки свой эмодзи — им помечены отмеченные дни на картинке.\n\n"
    f"{BTN_TODAY} — отметить за сегодня\n"
    f"{BTN_HABITS} — список, календарь, напоминания, эмодзи\n"
    f"{BTN_CAL} — общая картинка месяца со всеми привычками\n"
    f"{BTN_ADD} — новая привычка\n"
    f"{BTN_MOTIVATION} — мотивация к напоминаниям в стиле, который тебе ближе\n"
    f"{BTN_TZ} — по нему приходят напоминания и считается «сегодня»\n"
    f"{BTN_INVITE} — ваша ссылка-приглашение для друзей"
)

# Палитры эмодзи: четыре ряда по шесть. Новые дописывают в конец — кнопки хранят номер в списке.
GOOD_EMOJI = [
    "💪", "🏃", "📚", "💧", "🧘", "🥗",
    "😴", "🦷", "🎯", "✍️", "🎸", "🧹",
    "🚶", "🚴", "🏊", "🍎", "💊", "🧠",
    "🏋️", "🧗", "🎹", "🌱", "🌅", "🙏",
]  # fmt: skip
BAD_EMOJI = [
    "🚬", "🍺", "🍬", "🍔", "📱", "☕",
    "🎮", "💸", "🤬", "🍕", "🍷", "🛋️",
    "🍟", "🥤", "🍫", "📺", "🎰", "🍩",
    "🤯", "🥃", "🛒", "💅", "🌙", "🍰",
]  # fmt: skip
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
    kb.button(text=BTN_INVITE, callback_data=NavCb(action="invite"))
    kb.button(text=BTN_MOTIVATION, callback_data=NavCb(action="mot"))
    kb.button(text=BTN_HELP, callback_data=NavCb(action="help"))
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup()


MENU_TEXT = "<b>Трекер привычек</b>\nЧто делаем?"

#: Пояснение к переключателю «на следующий день» на экранах выбора времени.
NEXT_DAY_HINT = (
    "«Когда: завтра» — спрошу на следующий день: в это время придёт вопрос про вчерашний день, "
    "и ответ запишется за него."
)

#: Значок ответа «нет» на кнопках.
NO_ICON = "➖"


def answer_icon(habit: Habit, answer: bool | None) -> str:
    """Значок ответа за день на кнопках."""
    if answer is None:
        return "▫️"
    if answer:
        return "❗️" if habit.is_bad else "✅"
    return "💪" if habit.is_bad else NO_ICON


def answer_word(habit: Habit, answer: bool | None) -> str:
    """Ответ за день словами: «было» и «не было» у вредной, «сделано» и «не сделано» у полезной."""
    if answer is None:
        return "нет данных"
    word = "было" if habit.is_bad else "сделано"
    return word if answer else f"не {word}"


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


class MotWhy(StatesGroup):
    text = State()


class Tz(StatesGroup):
    value = State()


class HabitCb(CallbackData, prefix="h"):
    action: str  # open | cal | rem | norem | remon | nextday | del | delok | list | emoji
    id: int = 0


class MarkCb(CallbackData, prefix="m"):
    id: int
    back: int  # 0 — сегодня, 1 — вчера
    src: str  # t — список «сегодня», c — карточка, r — напоминание
    on: int = -1  # -1 — следующий ответ по кругу, 1 — «да», 0 — «нет»


class RemCb(CallbackData, prefix="r"):
    """Ответ на напоминание. День записан в самой кнопке: нажатие после полуночи или через
    несколько дней попадёт в тот день, про который спрашивали."""

    id: int
    day: int  # ГГГГММДД
    on: int  # 1 — «да», 0 — «нет»


class RemFbCb(CallbackData, prefix="q"):
    """Ответ на напоминание с мотивацией: после ответа приходит вопрос о ней. Отдельный класс,
    а не поле в `RemCb`, — иначе перестанут разбираться кнопки уже присланных напоминаний."""

    id: int
    day: int  # ГГГГММДД
    on: int  # 1 — «да», 0 — «нет»
    m: int  # номер сообщения в журнале мотивации


class FbCb(CallbackData, prefix="f"):
    """Отзыв о мотивационном сообщении."""

    id: int  # номер сообщения в журнале мотивации
    v: int  # −1 — мягче, 0 — принято, 1 — жёстче


class MotCb(CallbackData, prefix="v"):
    # style | off | why | nowhy — экран настроек; pick | skip | ask — вопрос на старте;
    # info — пояснения к стилям (value: start — из вопроса на старте, иначе из настроек)
    action: str
    value: str = ""


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
    action: str  # menu | today | habits | cal | add | tz | invite | invmsg | mot | help | cancel


class OverviewCb(CallbackData, prefix="o"):
    year: int
    month: int


class ShareCb(CallbackData, prefix="s"):
    id: int  # привычка; 0 — общий календарь
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

    async def remember_profile(handler, event, data):
        """После обработки любого сообщения и нажатия освежает имя и ник пользователя."""
        result = await handler(event, data)
        user = getattr(event, "from_user", None)
        if user is not None and not user.is_bot:
            storage.touch_user(user.id, user.full_name, user.username)
        return result

    router.message.outer_middleware(remember_profile)
    router.callback_query.outer_middleware(remember_profile)
    router.inline_query.outer_middleware(remember_profile)

    def today_of(user_id: int) -> date:
        return datetime.now(zone(storage.get_tz(user_id))).date()

    # --- тексты и клавиатуры ---

    def mark_label(habit: Habit, answer: bool | None) -> str:
        # Коротко: кнопки стоят в две колонки, пояснение значков — в тексте над ними.
        return f"{answer_icon(habit, answer)} {habit.icon} {habit.name}"

    def empty_view() -> tuple[str, InlineKeyboardMarkup]:
        kb = InlineKeyboardBuilder()
        kb.button(text=BTN_ADD, callback_data=NavCb(action="add"))
        menu_button(kb)
        kb.adjust(2)
        return "Привычек пока нет. Добавьте первую.", kb.as_markup()

    def two_columns(count: int, *tail: int) -> list[int]:
        """Раскладка: `count` кнопок по две в ряд, затем ряды `tail`."""
        return [2] * (count // 2) + [1] * (count % 2) + list(tail)

    async def ref_link(bot: Bot, user_id: int) -> str:
        me = await bot.me()
        return f"https://t.me/{me.username}?start=ref{user_id}"

    async def share_url(bot: Bot, user_id: int) -> str:
        """Ссылка, открывающая в Telegram выбор чата с готовым текстом и приглашением."""
        link = await ref_link(bot, user_id)
        return f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(SHARE_TEXT)}"

    def menu_button(kb: InlineKeyboardBuilder) -> None:
        kb.button(text=BTN_MENU, callback_data=NavCb(action="menu"))

    def only_menu() -> InlineKeyboardMarkup:
        kb = InlineKeyboardBuilder()
        menu_button(kb)
        return kb.as_markup()

    def cancel_button(kb: InlineKeyboardBuilder) -> None:
        kb.button(text="Отмена", callback_data=NavCb(action="cancel"))

    def time_keyboard(*rows: list[tuple[str, CallbackData]]) -> InlineKeyboardMarkup:
        """Быстрый выбор времени и под ним ряды `rows` — в каждом кнопки «подпись, действие»."""
        kb = InlineKeyboardBuilder()
        for value in QUICK_TIMES:
            kb.button(text=value, callback_data=TimeCb(value=value.replace(":", "")))
        for row in rows:
            for text, data in row:
                kb.button(text=text, callback_data=data)
        kb.adjust(4, 4, *(len(row) for row in rows))
        return kb.as_markup()

    def emoji_keyboard(kind: str, back: CallbackData) -> InlineKeyboardMarkup:
        kb = InlineKeyboardBuilder()
        palette = BAD_EMOJI if kind == BAD else GOOD_EMOJI
        for idx, emoji in enumerate(palette):
            kb.button(text=emoji, callback_data=EmojiCb(kind=kind, idx=idx))
        kb.button(text="Отмена", callback_data=back)
        kb.adjust(6, 6, 6, 6, 1)
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
                text=mark_label(habit, storage.get_answer(habit.id, day)),
                callback_data=MarkCb(id=habit.id, back=0, src="t"),
            )
        menu_button(kb)
        kb.adjust(*two_columns(len(habits), 1))
        text = (
            f"<b>Сегодня, {day.day} {MONTHS[day.month].lower()}</b>\n"
            "Нажатие меняет ответ по кругу: да → нет → нет данных.\n"
            f"✅ — сделано, {NO_ICON} — не сделано, ❗️ — вредная была, 💪 — вредной не было, "
            "▫️ — нет данных."
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
        kb.adjust(*two_columns(len(habits), 2))
        return "<b>Мои привычки</b>\nНажмите на привычку, чтобы открыть её.", kb.as_markup()

    def streak_line(habit: Habit, day: date) -> str:
        days = streak(habit, storage.all_marks(habit.id), day)
        if habit.is_bad:
            best = best_clean_streak(habit, storage.all_marks(habit.id), day)
            return f"Без срывов: <b>{days} дн.</b> Лучшая серия: <b>{best} дн.</b>"
        return f"Серия: <b>{days} дн.</b> подряд"

    def card_view(habit: Habit) -> tuple[str, InlineKeyboardMarkup]:
        day = today_of(habit.user_id)
        yesterday = day - timedelta(days=1)
        kind = "вредная" if habit.is_bad else "полезная"
        if not habit.remind_at:
            remind = "нет"
        elif not habit.remind_enabled:
            remind = f"выключено (время — {habit.remind_at})"
        else:
            remind = f"каждый день в {habit.remind_at}"
            if habit.remind_next_day:
                remind += ", про вчерашний день"
        text = (
            f"{habit.icon} <b>{escape(habit.name)}</b>\n"
            f"Тип: {kind}\n"
            f"Напоминание: {remind}\n"
            f"{streak_line(habit, day)}\n"
        )
        kb = InlineKeyboardBuilder()
        days = [("Сегодня", day, 0)]
        if yesterday >= habit.created_on:
            days.append(("Вчера", yesterday, 1))
        # Ответ за день — словами в тексте и на кнопке. Кнопка на день одна: нажатие меняет
        # ответ по кругу — нет данных → «да» → «нет» → нет данных.
        for title, d, back in days:
            answer = storage.get_answer(habit.id, d)
            text += f"\n{title}: <b>{answer_word(habit, answer)}</b>"
            kb.button(
                text=f"{answer_icon(habit, answer)} {title}: {answer_word(habit, answer)}",
                callback_data=MarkCb(id=habit.id, back=back, src="c"),
            )
        kb.button(text="📅 Календарь", callback_data=HabitCb(action="cal", id=habit.id))
        kb.button(text="⏰ Напоминание", callback_data=HabitCb(action="rem", id=habit.id))
        kb.button(text="🎨 Эмодзи", callback_data=HabitCb(action="emoji", id=habit.id))
        kb.button(text="🗑 Удалить", callback_data=HabitCb(action="del", id=habit.id))
        kb.button(text="← К списку", callback_data=HabitCb(action="list"))
        kb.adjust(*([1] * len(days)), 2, 2, 1)
        return text, kb.as_markup()

    def calendar_view(
        habit: Habit, year: int, month: int, edit: bool = False
    ) -> tuple[bytes, str, InlineKeyboardMarkup]:
        day = today_of(habit.user_id)
        answers = storage.all_answers(habit.id)
        marks = {d for d, value in answers.items() if value}
        image = render_month(
            name=habit.name,
            is_bad=habit.is_bad,
            emoji=habit.icon,
            stats=[
                (streak(habit, marks, day), "без срыва сейчас"),
                (best_clean_streak(habit, marks, day), "лучшая серия"),
            ]
            if habit.is_bad
            else None,
            year=year,
            month=month,
            answers=answers,
            created_on=habit.created_on,
            today=day,
        )
        in_month = sum(1 for d in marks if (d.year, d.month) == (year, month))
        # Дни месяца, которые идут в счёт: с начала учёта привычки по сегодня.
        tracked = [
            d
            for d in calendar.Calendar().itermonthdates(year, month)
            if d.month == month and habit.created_on <= d <= day
        ]
        said_no = sum(1 for d in tracked if answers.get(d) is False)
        word = "Срывов" if habit.is_bad else "Отмечено дней"
        caption = (
            f"{habit.icon} <b>{escape(habit.name)}</b> · {MONTHS[month]} {year}\n"
            f"{word} за месяц: <b>{in_month}</b>. {streak_line(habit, day)}\n"
            f"{answer_word(habit, False).capitalize()}: <b>{said_no}</b> · "
            f"нет данных: <b>{len(tracked) - in_month - said_no}</b>"
        )
        kb = InlineKeyboardBuilder()
        rows: list[int] = []
        if edit:
            caption += (
                f"\n\nНажатие на число меняет ответ по кругу: «{answer_word(habit, True)}» "
                f"({habit.icon}) → «{answer_word(habit, False)}» ({NO_ICON}) → нет данных. "
                "Можно отмечать и дни до того, как привычка появилась в боте."
            )
            for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
                for d in week:
                    # Число на каждой клетке месяца; клетки соседних месяцев пустые. Можно ли
                    # отметить день (будущий — нельзя), решает обработчик нажатия.
                    inside = d.month == month
                    prefix = {True: f"{habit.icon} ", False: f"{NO_ICON} ", None: ""}[
                        answers.get(d)
                    ]
                    kb.button(
                        text=f"{prefix}{d.day}" if inside else " ",
                        callback_data=DayCb(
                            id=habit.id, year=year, month=month, day=d.day if inside else 0
                        ),
                    )
                rows.append(7)
        prev_y, prev_m = shift_month(year, month, -1)
        next_y, next_m = shift_month(year, month, 1)
        nav = 0
        # Назад — на год (на столько же можно ставить отметки), вперёд — до текущего месяца.
        oldest = day - timedelta(days=BACKFILL_DAYS)
        if (prev_y, prev_m) >= (oldest.year, oldest.month):
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
        kb.button(text=BTN_SHARE, callback_data=ShareCb(id=habit.id, year=year, month=month))
        menu_button(kb)
        kb.adjust(*rows, nav, 2)
        return image, caption, kb.as_markup()

    def overview_habit(habit: Habit, day: date) -> OverviewHabit:
        marks = storage.all_marks(habit.id)
        return OverviewHabit(
            name=habit.name,
            emoji=habit.icon,
            is_bad=habit.is_bad,
            marks=frozenset(marks),
            clean_days=streak(habit, marks, day) if habit.is_bad else None,
            best_days=best_clean_streak(habit, marks, day) if habit.is_bad else None,
        )

    def overview_view(
        user_id: int, year: int, month: int
    ) -> tuple[bytes, str, InlineKeyboardMarkup]:
        """Общий календарь: все привычки пользователя на одной картинке месяца."""
        day = today_of(user_id)
        habits = storage.list_habits(user_id)
        image = render_overview(
            year=year,
            month=month,
            habits=[overview_habit(h, day) for h in habits],
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
        nav = 0
        if (prev_y, prev_m) >= (first.year, first.month):
            kb.button(
                text=f"◀ {MONTHS[prev_m]}", callback_data=OverviewCb(year=prev_y, month=prev_m)
            )
            nav += 1
        if (next_y, next_m) <= (day.year, day.month):
            kb.button(
                text=f"{MONTHS[next_m]} ▶", callback_data=OverviewCb(year=next_y, month=next_m)
            )
            nav += 1
        kb.button(text=BTN_SHARE, callback_data=ShareCb(id=0, year=year, month=month))
        menu_button(kb)
        kb.adjust(*([nav] if nav else []), 2)
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
    async def start(message: Message, command: CommandObject, state: FSMContext) -> None:
        await state.clear()
        user_id = message.from_user.id
        inviter = None
        if command.args and command.args.startswith("ref") and command.args[3:].isdigit():
            inviter = int(command.args[3:])
        if storage.register(user_id, inviter) and inviter is not None:
            try:  # приглашавший мог заблокировать бота — это не повод падать
                await message.bot.send_message(
                    inviter,
                    "🤝 По вашей ссылке пришёл новый человек. "
                    f"Всего приглашено: {storage.invited_count(inviter)}.",
                )
            except Exception:
                log.exception("inviter %s not notified", inviter)
        # Первым сообщением убираем клавиатуру прежних версий, если она осталась на экране.
        await message.answer("Привет! " + HELP, reply_markup=ReplyKeyboardRemove())
        await message.answer(MENU_TEXT, reply_markup=main_menu())
        # Стиль мотивации спрашиваем один раз; уже выбравшим — не спрашиваем.
        if storage.get_motivation(user_id).style is None and storage.ask_motivation_once(user_id):
            text, kb = motivation_question()
            await message.answer(text, reply_markup=kb)

    @router.message(Command("help"))
    async def help_(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(HELP, reply_markup=only_menu())

    async def pretty_invitation(bot: Bot, user_id: int) -> str:
        """Приглашение со ссылкой, зашитой в название бота, — для показа и пересылки."""
        me = await bot.me()
        link = await ref_link(bot, user_id)
        return (
            f'Веду привычки в боте <a href="{link}">{escape(me.first_name)}</a> — отметки, '
            "напоминания и календарь. Присоединяйся!"
        )

    async def invite_view(bot: Bot, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        link = await ref_link(bot, user_id)
        kb = InlineKeyboardBuilder()
        kb.button(text="✉️ Прислать для пересылки", callback_data=NavCb(action="invmsg"))
        # Кнопки копирования кладут в буфер обычный текст: в нём ссылку спрятать нельзя.
        kb.button(
            text="📋 Скопировать текстом", copy_text=CopyTextButton(text=f"{SHARE_TEXT}\n{link}")
        )
        kb.button(text="🔗 Скопировать ссылку", copy_text=CopyTextButton(text=link))
        kb.button(text=BTN_SHARE, url=await share_url(bot, user_id))
        menu_button(kb)
        kb.adjust(1, 2, 2)
        text = (
            "<b>Пригласить друга</b>\n\n"
            f"{await pretty_invitation(bot, user_id)}\n\n"
            "Ссылка-приглашение зашита в название бота. Кнопка ниже пришлёт этот текст "
            "отдельным сообщением — его удобно переслать.\n\n"
            f"Пришло по ссылке: <b>{storage.invited_count(user_id)}</b>"
        )
        return text, kb.as_markup()

    @router.callback_query(NavCb.filter(F.action == "invmsg"))
    async def send_invitation(query: CallbackQuery) -> None:
        """Чистое сообщение-приглашение: при пересылке ссылка и кнопка сохраняются."""
        kb = InlineKeyboardBuilder()
        kb.button(text="Открыть трекер привычек", url=await ref_link(query.bot, query.from_user.id))
        await query.message.answer(
            await pretty_invitation(query.bot, query.from_user.id),
            reply_markup=kb.as_markup(),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        await query.answer("Перешлите это сообщение другу")

    @router.callback_query(
        NavCb.filter(F.action.in_({"menu", "cancel", "today", "habits", "help", "invite", "mot"}))
    )
    async def navigate(query: CallbackQuery, callback_data: NavCb, state: FSMContext) -> None:
        """Переходы по меню: тот же экран перерисовывается в том же сообщении."""
        await state.clear()
        user_id = query.from_user.id
        storage.ensure_user(user_id)
        if callback_data.action == "today":
            text, kb = today_view(user_id)
        elif callback_data.action == "habits":
            text, kb = list_view(user_id)
        elif callback_data.action == "help":
            text, kb = HELP, only_menu()
        elif callback_data.action == "invite":
            text, kb = await invite_view(query.bot, user_id)
        elif callback_data.action == "mot":
            text, kb = motivation_view(user_id)
        else:
            text, kb = MENU_TEXT, main_menu()
        if query.message.photo:
            # Сообщение с календарём в текст не превратить — меню приходит новым сообщением.
            await query.message.answer(text, reply_markup=kb)
        else:
            await query.message.edit_text(text, reply_markup=kb)
        await query.answer("Отменено" if callback_data.action == "cancel" else None)

    # --- поделиться календарём ---

    def month_count(marks: set[date], year: int, month: int) -> int:
        return sum(1 for d in marks if (d.year, d.month) == (year, month))

    def share_stats(user_id: int, habit: Habit | None, year: int, month: int) -> str:
        """Статистика для пересылаемого сообщения: по одной привычке или по всем."""
        day = today_of(user_id)
        if habit is not None:
            count = month_count(storage.all_marks(habit.id), year, month)
            word = "Срывов за месяц" if habit.is_bad else "Отмечено дней за месяц"
            return (
                f"{habit.icon} <b>{escape(habit.name)}</b> · {MONTHS[month]} {year}\n"
                f"{word}: <b>{count}</b>\n"
                f"{streak_line(habit, day)}"
            )
        lines = [f"<b>Мои привычки</b> · {MONTHS[month]} {year}"]
        habits = storage.list_habits(user_id)
        for h in habits[:SHARE_HABITS]:
            marks = storage.all_marks(h.id)
            count = month_count(marks, year, month)
            what = "срывов" if h.is_bad else "дней"
            line = f"{h.icon} {escape(h.name)} — {what}: <b>{count}</b>"
            if h.is_bad:
                line += (
                    f", без срыва {streak(h, marks, day)} дн., "
                    f"лучшая серия {best_clean_streak(h, marks, day)} дн."
                )
            lines.append(line)
        if len(habits) > SHARE_HABITS:
            lines.append(f"…и ещё {len(habits) - SHARE_HABITS}")
        return "\n".join(lines)

    @router.callback_query(ShareCb.filter())
    async def share_calendar(query: CallbackQuery, callback_data: ShareCb) -> None:
        """Присылает календарь отдельным сообщением для пересылки: картинка, статистика,
        приглашение. Ссылкой «поделиться» картинку приложить нельзя, поэтому — пересылка."""
        user_id = query.from_user.id
        habit = None
        if callback_data.id:
            habit = await habit_or_alert(query, callback_data.id)
            if habit is None:
                return
        if not query.message.photo:
            await query.answer("Откройте календарь заново.", show_alert=True)
            return
        caption = (
            f"{share_stats(user_id, habit, callback_data.year, callback_data.month)}\n\n"
            f"{await pretty_invitation(query.bot, user_id)}"
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="Открыть трекер привычек", url=await ref_link(query.bot, user_id))
        # Та же картинка, что на экране: берём её по идентификатору, без повторной отрисовки.
        file_id = query.message.photo[-1].file_id
        await query.message.answer_photo(file_id, caption=caption, reply_markup=kb.as_markup())
        # Второй способ — выбрать чат прямо из кнопки (встроенный режим бота). Кнопка отдельным
        # сообщением, чтобы не уезжать вместе с пересылкой.
        key = secrets.token_urlsafe(6)
        storage.save_share(key, user_id, file_id, caption)
        pick = InlineKeyboardBuilder()
        pick.button(text="📤 Выбрать чат", switch_inline_query=key)
        menu_button(pick)
        await query.message.answer(
            "Перешлите сообщение выше или выберите чат кнопкой — картинка, статистика и "
            "ссылка сохранятся.",
            reply_markup=pick.as_markup(),
        )
        await query.answer()

    @router.inline_query()
    async def inline_share(inline: InlineQuery) -> None:
        """Встроенный режим: по ключу из кнопки «Выбрать чат» отдаёт календарь владельцу."""
        share = storage.get_share(inline.query.strip(), inline.from_user.id)
        if share is None:
            await inline.answer(
                [],
                cache_time=0,
                is_personal=True,
                button=InlineQueryResultsButton(
                    text="Открыть трекер привычек", start_parameter="inline"
                ),
            )
            return
        file_id, caption = share
        kb = InlineKeyboardBuilder()
        kb.button(
            text="Открыть трекер привычек", url=await ref_link(inline.bot, inline.from_user.id)
        )
        await inline.answer(
            [
                InlineQueryResultCachedPhoto(
                    id=inline.query.strip(),
                    photo_file_id=file_id,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=kb.as_markup(),
                )
            ],
            cache_time=0,
            is_personal=True,
        )

    # --- добавление: название → тип → эмодзи → напоминание ---

    async def begin_add(user_id: int, state: FSMContext) -> tuple[str, InlineKeyboardMarkup]:
        await state.clear()
        if len(storage.list_habits(user_id)) >= MAX_HABITS:
            return (
                f"Уже {MAX_HABITS} привычек — больше добавить нельзя.",
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

    def next_day_button(on: bool, habit_id: int = 0) -> tuple[str, CallbackData]:
        return (
            f"Когда: {'завтра' if on else 'сегодня'}",
            HabitCb(action="nextday", id=habit_id),
        )

    def time_question(kind: str, next_day: bool = False) -> tuple[str, InlineKeyboardMarkup]:
        if next_day:
            hint = "Во сколько спрашивать про вчерашний день?"
        elif kind == BAD:
            hint = "Во сколько спрашивать, как прошёл день?"
        else:
            hint = "Во сколько напоминать?"
        kb = time_keyboard(
            [next_day_button(next_day)],
            [("Без напоминания", HabitCb(action="norem"))],
            [("Отмена", NavCb(action="cancel"))],
        )
        return (
            f"{hint} Выберите время или пришлите своё, например 21:30.\n{NEXT_DAY_HINT}",
            kb,
        )

    @router.callback_query(Add.time, HabitCb.filter(F.action == "nextday"))
    async def add_toggle_next_day(query: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        next_day = not data.get("next_day", False)
        await state.update_data(next_day=next_day)
        text, kb = time_question(data["kind"], next_day)
        await query.message.edit_text(text, reply_markup=kb)
        await query.answer()

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
            user_id,
            data["name"],
            data["kind"],
            remind_at,
            today_of(user_id),
            data.get("emoji"),
            remind_next_day=bool(remind_at) and data.get("next_day", False),
        )

    def added_text(habit: Habit) -> str:
        text, _ = card_view(habit)
        if habit.reminds:
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
        today = today_of(habit.user_id)
        if day > today:
            await query.answer("Этот день ещё не наступил.", show_alert=True)
            return
        if day < today - timedelta(days=BACKFILL_DAYS):
            await query.answer("Отметки ставятся не дальше года назад.", show_alert=True)
            return
        answer = storage.cycle_answer(habit.id, day)
        if answer is not None:
            # День раньше заведения привычки: учёт начинается с него, иначе календарь и серия
            # этот ответ не увидят.
            storage.backdate(habit.id, day)
            habit = storage.get_habit(habit.user_id, habit.id) or habit
        await show_calendar(query, habit, callback_data.year, callback_data.month, True)
        await query.answer(f"{day.day} {MONTHS[day.month].lower()}: {answer_word(habit, answer)}")

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
            f"Удалить «{escape(habit.name)}»? Она пропадёт из списка, календаря и напоминаний.",
            reply_markup=kb.as_markup(),
        )
        await query.answer()

    @router.callback_query(HabitCb.filter(F.action == "delok"))
    async def delete(query: CallbackQuery, callback_data: HabitCb) -> None:
        storage.delete_habit(query.from_user.id, callback_data.id)
        text, kb = list_view(query.from_user.id)
        await query.message.edit_text("Удалено.\n\n" + text, reply_markup=kb)
        await query.answer()

    # --- мотивация ---

    def feedback_rows(markup: InlineKeyboardMarkup | None) -> list[list]:
        """Ряды кнопок отзыва о мотивации в сообщении."""
        if markup is None:
            return []
        return [
            row
            for row in markup.inline_keyboard
            if any((b.callback_data or "").startswith("f:") for b in row)
        ]

    #: Как поменять мотивацию — после ответа на вопрос при первом запуске.
    MOTIVATION_HOW_TO = (
        f"Поменять стиль, написать, зачем тебе это, или выключить мотивацию — "
        f"в меню: «{BTN_MENU}» → «{BTN_MOTIVATION}». А тон подстраивается кнопками "
        "«Мягче» и «Жёстче»: после ответа на напоминание я спрошу, как тебе мотивация."
    )

    @router.callback_query(MotCb.filter(F.action.in_({"info", "ask"})))
    async def show_styles_info(query: CallbackQuery, callback_data: MotCb) -> None:
        if callback_data.action == "ask":
            text, kb = motivation_question()
        else:
            text, kb = styles_info(from_start=callback_data.value == "start")
        await query.message.edit_text(text, reply_markup=kb)
        await query.answer()

    @router.callback_query(MotCb.filter(F.action.in_({"pick", "skip"})))
    async def answer_motivation_question(query: CallbackQuery, callback_data: MotCb) -> None:
        user_id = query.from_user.id
        if callback_data.action == "skip":
            text = (
                "Хорошо, напоминания будут без мотивации.\n"
                f"Включить её можно в любой момент: «{BTN_MENU}» → «{BTN_MOTIVATION}»."
            )
        elif callback_data.value in STYLES or callback_data.value == AUTO:
            storage.set_motivation_style(user_id, callback_data.value)
            chosen = (
                "буду подбирать сам"
                if callback_data.value == AUTO
                else f"стиль «{STYLES[callback_data.value].title}»"
            )
            text = (
                f"Готово: {chosen}. Мотивация придёт с ближайшим напоминанием.\n\n"
                f"{MOTIVATION_HOW_TO}"
            )
        else:
            await query.answer()
            return
        await query.message.edit_text(text, reply_markup=only_menu())
        await query.answer()

    def motivation_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        motivation = storage.get_motivation(user_id)
        if motivation.style is None:
            current = "выключена"
        elif motivation.style == AUTO:
            current = "подбираю сам"
        else:
            current = STYLES[motivation.style].title
        lines = [
            "<b>Мотивация</b>",
            (
                "К напоминаниям я добавляю короткое сообщение в стиле, который тебе ближе. "
                "Иногда пробую другие стили и смотрю, после каких ты чаще отмечаешь привычку. "
                "После ответа на напоминание спрашиваю, как тебе мотивация: «Мягче», «Принято» "
                "или «Жёстче» — по ним я подстраиваю тон."
            ),
            "",
            f"Стиль: <b>{current}</b>",
            f"Тон: <b>{TONE_WORDS[motivation.tone]}</b>",
            "Зачем тебе это: "
            + (f"«{escape(motivation.why)}»" if motivation.why else "не указано"),
        ]
        stats = storage.motivation_stats(user_id)
        tried = [(s, sent, acted) for s, (sent, acted, _) in stats.items() if sent >= 3]
        if tried:
            best = max(tried, key=lambda t: t[2] / t[1])
            lines.append(
                f"Лучше всего работает: {STYLES[best[0]].title} — отметки после "
                f"{best[2]} из {best[1]} сообщений."
            )
        lines += ["", "Что тебе ближе?"]
        kb = InlineKeyboardBuilder()
        for name in OFFERED:
            mark = "✔️ " if motivation.style == name else ""
            kb.button(
                text=mark + STYLES[name].label, callback_data=MotCb(action="style", value=name)
            )
        auto = "✔️ " if motivation.style == AUTO else ""
        kb.button(text=f"{auto}🎲 Подбирай сам", callback_data=MotCb(action="style", value=AUTO))
        kb.button(text="✍️ Зачем мне это", callback_data=MotCb(action="why"))
        kb.button(text=BTN_ABOUT_STYLES, callback_data=MotCb(action="info"))
        rows = [2, 2, 2, 1, 1, 1]
        if motivation.style is not None:
            kb.button(text="🔕 Выключить", callback_data=MotCb(action="off"))
            rows.append(1)
        menu_button(kb)
        kb.adjust(*rows, 1)
        return "\n".join(lines), kb.as_markup()

    @router.callback_query(MotCb.filter(F.action.in_({"style", "off"})))
    async def pick_motivation(
        query: CallbackQuery, callback_data: MotCb, state: FSMContext
    ) -> None:
        await state.clear()
        user_id = query.from_user.id
        if callback_data.action == "off":
            storage.set_motivation_style(user_id, None)
            note = "Мотивация выключена"
        elif callback_data.value in STYLES or callback_data.value == AUTO:
            storage.set_motivation_style(user_id, callback_data.value)
            note = "Готово — с ближайшего напоминания"
        else:
            await query.answer()
            return
        text, kb = motivation_view(user_id)
        await query.message.edit_text(text, reply_markup=kb)
        await query.answer(note)

    @router.callback_query(MotCb.filter(F.action.in_({"why", "nowhy"})))
    async def ask_why(query: CallbackQuery, callback_data: MotCb, state: FSMContext) -> None:
        user_id = query.from_user.id
        if callback_data.action == "nowhy":
            await state.clear()
            storage.set_motivation_why(user_id, None)
            text, kb = motivation_view(user_id)
            await query.message.edit_text(text, reply_markup=kb)
            await query.answer("Убрал")
            return
        await state.set_state(MotWhy.text)
        kb = InlineKeyboardBuilder()
        if storage.get_motivation(user_id).why:
            kb.button(text="Убрать", callback_data=MotCb(action="nowhy"))
        kb.button(text="Отмена", callback_data=NavCb(action="mot"))
        kb.adjust(1)
        await query.message.edit_text(
            "Зачем тебе твои привычки? Напиши своими словами: ради здоровья, детей, спокойствия — "
            f"что угодно, до {MAX_WHY} символов. Я буду напоминать об этом в сообщениях.",
            reply_markup=kb.as_markup(),
        )
        await query.answer()

    @router.message(MotWhy.text, F.text)
    async def set_why(message: Message, state: FSMContext) -> None:
        why = " ".join(message.text.split())
        if not why or why.startswith("/"):
            await message.answer("Пришли ответ текстом.")
            return
        if len(why) > MAX_WHY:
            await message.answer(f"Слишком длинно — до {MAX_WHY} символов.")
            return
        await state.clear()
        storage.set_motivation_why(message.from_user.id, why)
        text, kb = motivation_view(message.from_user.id)
        await message.answer(text, reply_markup=kb)

    # --- отметки ---

    def answer_note(habit: Habit, on: bool | None, today: bool = True) -> str:
        """Короткое подтверждение записанного ответа."""
        if on is None:
            return "Ответ снят: нет данных"
        if habit.is_bad:
            if not on:
                return "Записал: не было 💪"
            return "Записал: было. Завтра новый день." if today else "Записал: было."
        return "Отмечено ✅" if on else "Записал: не сделано"

    @router.callback_query(RemCb.filter())
    @router.callback_query(RemFbCb.filter())
    async def answer_reminder(query: CallbackQuery, callback_data: RemCb | RemFbCb) -> None:
        habit = await habit_or_alert(query, callback_data.id)
        if habit is None:
            return
        today = today_of(habit.user_id)
        try:
            day = datetime.strptime(str(callback_data.day), "%Y%m%d").date()
        except ValueError:
            day = None
        if day is None or not habit.created_on <= day <= today:
            await query.answer("Этот день отметить нельзя.", show_alert=True)
            return
        on = bool(callback_data.on)
        storage.set_answer(habit.id, day, on)
        note = answer_note(habit, on, today=day == today)
        title = f"<b>{escape(habit.name)}</b>" + ("" if day == today else f" · {day:%d.%m}")
        text = f"{title}\n{note}\n{streak_line(habit, today)}"
        # Кнопки ответа больше не нужны; поправить ответ можно на карточке или в календаре.
        # Мотивация остаётся в сообщении; у напоминаний старого вида под ней остаются и кнопки
        # отзыва — пока отзыв не дали.
        feedback = feedback_rows(query.message.reply_markup)
        asked = getattr(callback_data, "m", 0)  # о мотивации спросим отдельным сообщением
        message = None
        if asked:
            message = storage.get_motivation_message(habit.user_id, asked)
            if message is not None:
                text += f"\n\n<i>{escape(message.text)}</i>"
        elif feedback:
            rated = FbCb.unpack(feedback[0][0].callback_data).id
            message = storage.get_motivation_message(habit.user_id, rated)
            if message is not None:
                text += f"\n\n<i>{escape(message.text)}</i>"
        kb = InlineKeyboardBuilder()
        for row in feedback:
            kb.row(*row)
        kb.row(*only_menu().inline_keyboard[0])
        await query.message.edit_text(text, reply_markup=kb.as_markup())
        await query.answer(note)
        if asked and message is not None and message.feedback is None:
            question, markup = feedback_question(asked)
            await query.message.answer(question, reply_markup=markup)

    @router.callback_query(FbCb.filter())
    async def rate_motivation(query: CallbackQuery, callback_data: FbCb) -> None:
        """Отзыв о мотивации: «мягче» и «жёстче» сдвигают тон следующих сообщений."""
        user_id = query.from_user.id
        label, delta, value = next(f for f in FEEDBACK if f[1] == max(-1, min(1, callback_data.v)))
        fresh = storage.set_motivation_feedback(user_id, callback_data.id, value)
        # Кнопки отзыва убираем в любом случае; остальные кнопки сообщения остаются. В отдельном
        # вопросе о мотивации других кнопок нет — там вместо них остаётся ответ.
        markup = query.message.reply_markup
        rows = [
            row
            for row in (markup.inline_keyboard if markup else [])
            if not any((b.callback_data or "").startswith("f:") for b in row)
        ]
        if rows:
            await query.message.edit_reply_markup(
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
            )
        else:
            await query.message.edit_text(f"Как тебе такая мотивация? — {label}")
        if not fresh:
            await query.answer("Отзыв уже учтён")
            return
        tone = storage.shift_tone(user_id, delta) if delta else storage.get_motivation(user_id).tone
        if delta < 0:
            note = "Понял, буду мягче" if tone > -2 else "Мягче уже некуда — так и оставлю"
        elif delta > 0:
            note = "Понял, буду жёстче" if tone < 2 else "Жёстче уже некуда — так и оставлю"
        else:
            note = "Спасибо, учту"
        await query.answer(note)

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
            on = storage.cycle_answer(habit.id, day)
        else:
            on = bool(callback_data.on)
            # На карточке повторное нажатие на выбранный ответ снимает его.
            if callback_data.src == "c" and storage.get_answer(habit.id, day) is on:
                on = None
            storage.set_answer(habit.id, day, on)

        note = answer_note(habit, on)

        if callback_data.src == "t":
            _, kb = today_view(query.from_user.id)
            await query.message.edit_reply_markup(reply_markup=kb)
        elif callback_data.src == "c":
            text, kb = card_view(habit)
            await query.message.edit_text(text, reply_markup=kb)
        else:  # ответ на напоминание, присланное до появления `RemCb`, — кнопки больше не нужны
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
        text, kb = reminder_view(habit)
        await query.message.edit_text(text, reply_markup=kb)
        await query.answer()

    def reminder_view(habit: Habit) -> tuple[str, InlineKeyboardMarkup]:
        # О каком дне спрашивать и выключатель — в одном ряду.
        switches = [next_day_button(habit.remind_next_day, habit.id)]
        if habit.remind_at:
            # Выключатель вместо удаления: время остаётся, включить обратно — одно нажатие.
            state = "✅ вкл" if habit.remind_enabled else "🛑 выкл"
            switches.append((state, HabitCb(action="remon", id=habit.id)))
        back = [("← К привычке", HabitCb(action="open", id=habit.id))]
        about = ", про вчерашний день" if habit.remind_next_day else ""
        off = "" if habit.remind_enabled else ", выключено"
        current = f"Сейчас: {habit.remind_at}{about}{off}. " if habit.remind_at else ""
        question = (
            "Во сколько спрашивать про вчерашний день"
            if habit.remind_next_day
            else "Во сколько напоминать"
        )
        text = (
            f"{current}{question} по «{escape(habit.name)}»? Выберите время или "
            f"пришлите своё, например 21:30.\n{NEXT_DAY_HINT}\n"
            f"Часовой пояс: {storage.get_tz(habit.user_id)}."
        )
        return text, time_keyboard(switches, back)

    @router.callback_query(Remind.time, HabitCb.filter(F.action == "remon"))
    async def toggle_reminder(query: CallbackQuery, callback_data: HabitCb) -> None:
        """Включает и выключает напоминание; время и «на следующий день» остаются прежними."""
        habit = await habit_or_alert(query, callback_data.id)
        if habit is None:
            return
        on = not habit.remind_enabled
        storage.set_remind_enabled(habit.user_id, habit.id, on)
        habit = storage.get_habit(habit.user_id, habit.id) or habit
        text, kb = reminder_view(habit)
        await query.message.edit_text(text, reply_markup=kb)
        await query.answer(
            f"Напоминание включено: {habit.remind_at}" if on else "Напоминание выключено"
        )

    @router.callback_query(Remind.time, HabitCb.filter(F.action == "nextday"))
    async def toggle_next_day(query: CallbackQuery, callback_data: HabitCb) -> None:
        """Переключатель действует сразу: время напоминания остаётся прежним."""
        habit = await habit_or_alert(query, callback_data.id)
        if habit is None:
            return
        on = not habit.remind_next_day
        storage.set_remind_next_day(habit.user_id, habit.id, on)
        habit = storage.get_habit(habit.user_id, habit.id) or habit
        text, kb = reminder_view(habit)
        await query.message.edit_text(text, reply_markup=kb)
        await query.answer(
            "Буду спрашивать про вчерашний день" if on else "Буду спрашивать в тот же день"
        )

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


BTN_ABOUT_STYLES = "ℹ️ Подробнее о стилях"


def styles_info(from_start: bool) -> tuple[str, InlineKeyboardMarkup]:
    """Пояснения к стилям с примерами. Из вопроса на старте стиль можно выбрать прямо здесь;
    из настроек — вернуться к ним."""
    lines = ["<b>Стили мотивации</b>"]
    for name in OFFERED:
        about, example = ABOUT[name]
        lines.append(f"\n{STYLES[name].label} — {about}.\n<i>«{example}»</i>")
    lines.append(
        "\n🎲 <b>Подбирай сам</b> — я пробую разные стили и чаще пишу в тех, после которых ты "
        "отмечаешь привычку.\n\nВ любом стиле я иногда пробую другие — так находится то, что "
        "работает. Тон подстраивается кнопками «Мягче» и «Жёстче»: о мотивации я спрашиваю после "
        "ответа на напоминание."
    )
    kb = InlineKeyboardBuilder()
    if from_start:
        for name in OFFERED:
            kb.button(text=STYLES[name].label, callback_data=MotCb(action="pick", value=name))
        kb.button(text="🎲 Подбирай сам", callback_data=MotCb(action="pick", value=AUTO))
        kb.button(text="← Назад", callback_data=MotCb(action="ask"))
        kb.adjust(2, 2, 2, 1, 1)
    else:
        kb.button(text="← Назад", callback_data=NavCb(action="mot"))
    return "\n".join(lines), kb.as_markup()


def motivation_question() -> tuple[str, InlineKeyboardMarkup]:
    """Вопрос о стиле мотивации при первом запуске."""
    kb = InlineKeyboardBuilder()
    for name in OFFERED:
        kb.button(text=STYLES[name].label, callback_data=MotCb(action="pick", value=name))
    kb.button(text="🎲 Подбирай сам", callback_data=MotCb(action="pick", value=AUTO))
    kb.button(text=BTN_ABOUT_STYLES, callback_data=MotCb(action="info", value="start"))
    kb.button(text="Пропустить", callback_data=MotCb(action="skip"))
    kb.adjust(2, 2, 2, 1, 1, 1)
    text = (
        "<b>Что тебя мотивирует?</b>\n"
        "К напоминаниям я могу добавлять короткое сообщение в близком тебе стиле:\n"
        "📈 цифры и прогресс · 🔥 серии дней · 🤝 поддержка · 🧭 смысл, зачем тебе это · "
        "🪞 «я такой человек» · ✨ интерес и новизна.\n"
        "Не знаешь — выбери «Подбирай сам»: я буду пробовать разное и смотреть, что работает."
    )
    return text, kb.as_markup()


def reminder_message(
    habit: Habit, day: date, motivation: tuple[int, str] | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    """Напоминание про день `day`: сегодняшний или, у напоминаний на следующий день, вчерашний.

    `motivation` — номер и текст мотивационного сообщения: текст идёт под вопросом, а вопрос
    о мотивации приходит отдельным сообщением после ответа.
    """
    stamp = int(day.strftime("%Y%m%d"))
    title = f"{habit.icon} <b>{escape(habit.name)}</b>"
    kb = InlineKeyboardBuilder()

    def answer(on: int) -> CallbackData:
        if motivation is None:
            return RemCb(id=habit.id, day=stamp, on=on)
        return RemFbCb(id=habit.id, day=stamp, on=on, m=motivation[0])

    if habit.is_bad:
        kb.button(text="Не было 💪", callback_data=answer(0))
        kb.button(text="Было", callback_data=answer(1))
        question = (
            f"как прошёл вчерашний день ({day:%d.%m})?" if habit.remind_next_day else "как сегодня?"
        )
        text = f"⏰ {title} — {question}"
    else:
        kb.button(text="✅ Сделано", callback_data=answer(1))
        kb.button(text="Не сделано", callback_data=answer(0))
        if habit.remind_next_day:
            text = f"⏰ {title} — получилось вчера ({day:%d.%m})?"
        else:
            text = f"⏰ Пора: {title}"
    if motivation is None:
        return text, kb.as_markup()
    return f"{text}\n\n<i>{escape(motivation[1])}</i>", kb.as_markup()


def feedback_question(message_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Вопрос о мотивации — отдельным сообщением после ответа на напоминание."""
    kb = InlineKeyboardBuilder()
    for label, delta, _ in FEEDBACK:
        kb.button(text=label, callback_data=FbCb(id=message_id, v=delta))
    kb.adjust(3)
    return "Как тебе такая мотивация? Отметь кнопкой — я подстрою тон.", kb.as_markup()


async def send_reminder(
    bot: Bot, habit: Habit, day: date, motivator: Motivator | None = None
) -> None:
    motivation = None
    if motivator is not None:
        try:
            today = datetime.now(zone(motivator.storage.get_tz(habit.user_id))).date()
            motivation = await motivator.compose(habit, day, today)
        except Exception:  # без мотивации напоминание всё равно должно уйти
            log.exception("motivation for habit %s failed", habit.id)
    text, kb = reminder_message(habit, day, motivation)
    await bot.send_message(habit.user_id, text, reply_markup=kb)
