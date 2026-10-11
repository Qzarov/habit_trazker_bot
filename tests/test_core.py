from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from habitbot.bot import clean_emoji, parse_tz
from habitbot.calendar_image import can_draw, day_style, render_month, shift_month
from habitbot.db import BAD, GOOD, Storage, streak
from habitbot.reminders import is_due, parse_time, tick

TODAY = date(2026, 10, 11)


@pytest.fixture
def storage() -> Storage:
    return Storage(":memory:")


def test_habits_are_private_to_owner(storage: Storage) -> None:
    mine = storage.add_habit(1, "Зарядка", GOOD, "08:00", TODAY)
    storage.add_habit(2, "Сладкое", BAD, None, TODAY)
    assert [h.name for h in storage.list_habits(1)] == ["Зарядка"]
    assert storage.get_habit(2, mine.id) is None
    assert storage.delete_habit(2, mine.id) is False
    assert storage.delete_habit(1, mine.id) is True
    assert storage.list_habits(1) == []


def test_toggle_mark(storage: Storage) -> None:
    habit = storage.add_habit(1, "Зарядка", GOOD, None, TODAY)
    assert storage.toggle_mark(habit.id, TODAY) is True
    assert storage.is_marked(habit.id, TODAY)
    assert storage.toggle_mark(habit.id, TODAY) is False
    assert storage.all_marks(habit.id) == set()


def test_good_streak_survives_unmarked_today(storage: Storage) -> None:
    habit = storage.add_habit(1, "Зарядка", GOOD, None, date(2026, 10, 1))
    marks = {date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 10)}
    assert streak(habit, marks, TODAY) == 3
    assert streak(habit, marks | {TODAY}, TODAY) == 4
    assert streak(habit, {date(2026, 10, 8)}, TODAY) == 0


def test_bad_streak_counts_clean_days_since_creation(storage: Storage) -> None:
    habit = storage.add_habit(1, "Сладкое", BAD, None, date(2026, 10, 5))
    assert streak(habit, set(), TODAY) == 7  # с 5-го по 11-е
    assert streak(habit, {date(2026, 10, 9)}, TODAY) == 2
    assert streak(habit, {TODAY}, TODAY) == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [("9:30", "09:30"), ("21.00", "21:00"), ("0745", "07:45"), ("7 05", "07:05"),
     ("24:00", None), ("12:60", None), ("утром", None), ("8", None)],
)  # fmt: skip
def test_parse_time(text: str, expected: str | None) -> None:
    assert parse_time(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Europe/Moscow", "Europe/Moscow"), ("+3", "Etc/GMT-3"), ("UTC-5", "Etc/GMT+5"),
     ("utc+0", "UTC"), ("+15", None), ("Марс", None)],
)  # fmt: skip
def test_parse_tz(text: str, expected: str | None) -> None:
    assert parse_tz(text) == expected


def test_offset_zone_matches_moscow() -> None:
    now = datetime(2026, 10, 11, 12, 0, tzinfo=ZoneInfo("UTC"))
    assert now.astimezone(ZoneInfo(parse_tz("+3"))).hour == 15


def test_day_style() -> None:
    created = date(2026, 10, 5)
    kw = {"created_on": created, "today": TODAY}
    assert day_style(date(2026, 10, 6), is_bad=False, marked=True, **kw) == "hit"
    assert day_style(date(2026, 10, 6), is_bad=False, marked=False, **kw) == "miss"
    assert day_style(date(2026, 10, 6), is_bad=True, marked=True, **kw) == "miss"
    assert day_style(date(2026, 10, 6), is_bad=True, marked=False, **kw) == "hit"
    # До заведения, в будущем и сегодня без отметки — не в счёт.
    assert day_style(date(2026, 10, 4), is_bad=False, marked=False, **kw) == "idle"
    assert day_style(date(2026, 10, 12), is_bad=True, marked=False, **kw) == "idle"
    assert day_style(TODAY, is_bad=False, marked=False, **kw) == "idle"
    assert day_style(TODAY, is_bad=True, marked=False, **kw) == "idle"
    assert day_style(TODAY, is_bad=True, marked=True, **kw) == "miss"


def test_render_month_is_png() -> None:
    image = render_month(
        name="Зарядка", is_bad=False, year=2026, month=10,
        marks={date(2026, 10, 6), date(2026, 10, 8)}, created_on=date(2026, 10, 5), today=TODAY,
    )  # fmt: skip
    assert image.startswith(b"\x89PNG") and len(image) > 5000


def test_render_month_with_emoji_differs() -> None:
    kw = {
        "name": "Зарядка", "is_bad": False, "year": 2026, "month": 10,
        "marks": {date(2026, 10, 6)}, "created_on": date(2026, 10, 5), "today": TODAY,
    }  # fmt: skip
    plain = render_month(**kw)
    with_emoji = render_month(emoji="💪", **kw)
    assert with_emoji.startswith(b"\x89PNG")
    if can_draw("💪"):  # на машине без шрифта эмодзи картинка остаётся прежней
        assert with_emoji != plain


def test_emoji_column_is_added_to_old_database(tmp_path) -> None:
    import sqlite3

    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE users (user_id INTEGER PRIMARY KEY, tz TEXT NOT NULL);"
        "CREATE TABLE habits (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,"
        " name TEXT NOT NULL, kind TEXT NOT NULL, remind_at TEXT, created_on TEXT NOT NULL,"
        " archived INTEGER NOT NULL DEFAULT 0, last_reminded TEXT);"
        "INSERT INTO users VALUES (1, 'UTC');"
        "INSERT INTO habits (user_id, name, kind, created_on) VALUES (1, 'Старая', 'good', '2026-10-01');"
    )
    old.commit()
    old.close()

    storage = Storage(path)
    [habit] = storage.list_habits(1)
    assert (habit.name, habit.emoji, habit.icon) == ("Старая", None, "✅")
    assert storage.set_emoji(1, habit.id, "💪")
    assert storage.list_habits(1)[0].icon == "💪"


@pytest.mark.parametrize(
    ("text", "ok"),
    [("💪", True), (" 🍬 ", True), ("привет", False), ("12", False), ("!", False), ("", False),
     ("💪💪💪💪💪💪💪💪", False)],
)  # fmt: skip
def test_clean_emoji(text: str, ok: bool) -> None:
    assert (clean_emoji(text) is not None) is ok


def test_shift_month() -> None:
    assert shift_month(2026, 1, -1) == (2025, 12)
    assert shift_month(2026, 12, 1) == (2027, 1)
    assert shift_month(2026, 10, -1) == (2026, 9)


def test_is_due_window(storage: Storage) -> None:
    habit = storage.add_habit(1, "Зарядка", GOOD, "08:00", TODAY)
    at = lambda h, m: datetime(2026, 10, 11, h, m, tzinfo=ZoneInfo("Europe/Moscow"))
    assert not is_due(habit, at(7, 59))
    assert is_due(habit, at(8, 0))
    assert is_due(habit, at(8, 29))
    assert not is_due(habit, at(8, 30))


async def _run_tick(storage: Storage, now: datetime) -> list[str]:
    sent: list[str] = []

    async def send(habit) -> None:
        sent.append(habit.name)

    await tick(storage, send, now_utc=now)
    return sent


async def test_tick_sends_once_and_skips_done(storage: Storage) -> None:
    good = storage.add_habit(1, "Зарядка", GOOD, "08:00", TODAY)
    storage.add_habit(1, "Сладкое", BAD, "08:00", TODAY)
    done = storage.add_habit(1, "Вода", GOOD, "08:00", TODAY)
    storage.add_habit(1, "Чтение", GOOD, "21:00", TODAY)
    storage.set_mark(done.id, TODAY, True)
    now = datetime(2026, 10, 11, 5, 0, tzinfo=ZoneInfo("UTC"))  # 08:00 по Москве

    # Сделанную полезную не дёргаем; вредную спрашиваем всегда; «Чтению» ещё рано.
    assert await _run_tick(storage, now) == ["Зарядка", "Сладкое"]
    assert await _run_tick(storage, now) == []
    assert storage.get_habit(1, good.id).last_reminded == TODAY


async def test_tick_respects_user_timezone(storage: Storage) -> None:
    storage.add_habit(1, "Зарядка", GOOD, "08:00", TODAY)
    storage.set_tz(1, "Asia/Yekaterinburg")  # UTC+5
    assert await _run_tick(storage, datetime(2026, 10, 11, 5, 0, tzinfo=ZoneInfo("UTC"))) == []
    assert await _run_tick(storage, datetime(2026, 10, 11, 3, 0, tzinfo=ZoneInfo("UTC"))) == [
        "Зарядка"
    ]
