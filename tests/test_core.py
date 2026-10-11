from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import TEST_DATABASE_URL, make_storage

from habitbot.bot import clean_emoji, parse_tz
from habitbot.calendar_image import (
    OverviewHabit,
    can_draw,
    day_style,
    days_word,
    render_month,
    render_overview,
    shift_month,
)
from habitbot.db import BAD, GOOD, Storage, best_clean_streak, streak
from habitbot.reminders import is_due, parse_time, tick

TODAY = date(2026, 10, 11)


@pytest.fixture
def storage() -> Storage:
    return make_storage()


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


def test_best_clean_streak(storage: Storage) -> None:
    habit = storage.add_habit(1, "Сладкое", BAD, None, date(2026, 10, 1))
    # Без срывов вовсе: лучшая серия — все дни с начала учёта, она же текущая.
    assert best_clean_streak(habit, set(), TODAY) == 11
    # Срывы 4-го и 9-го: отрезки 1–3 (3 дня), 5–8 (4 дня), 10–11 (2 дня).
    marks = {date(2026, 10, 4), date(2026, 10, 9)}
    assert best_clean_streak(habit, marks, TODAY) == 4
    assert streak(habit, marks, TODAY) == 2
    # Сорвался сегодня: текущая серия 0, лучшая — прошлый отрезок.
    assert best_clean_streak(habit, {TODAY}, TODAY) == 10
    # Срыв в первый же день и каждый день подряд.
    every = {date(2026, 10, d) for d in range(1, 12)}
    assert best_clean_streak(habit, every, TODAY) == 0
    # Отметки вне периода учёта не считаются; лучшая серия не меньше текущей.
    assert best_clean_streak(habit, {date(2026, 9, 20), date(2026, 10, 20)}, TODAY) == 11
    for sample in (set(), marks, {TODAY}, every):
        assert best_clean_streak(habit, sample, TODAY) >= streak(habit, sample, TODAY)


@pytest.mark.parametrize(
    ("count", "word"),
    [(0, "дней"), (1, "день"), (2, "дня"), (4, "дня"), (5, "дней"), (11, "дней"), (12, "дней"),
     (21, "день"), (22, "дня"), (25, "дней"), (101, "день"), (111, "дней")],
)  # fmt: skip
def test_days_word(count: int, word: str) -> None:
    assert days_word(count) == word


def test_render_month_with_stats_is_taller() -> None:
    kw = {
        "name": "Сладкое", "is_bad": True, "year": 2026, "month": 10,
        "marks": {date(2026, 10, 6)}, "created_on": date(2026, 10, 1), "today": TODAY,
    }  # fmt: skip
    plain = render_month(**kw)
    with_stats = render_month(stats=[(5, "без срыва сейчас"), (5, "лучшая серия")], **kw)
    assert with_stats.startswith(b"\x89PNG") and len(with_stats) > len(plain)


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
    # Старый пользователь остаётся, время его прихода неизвестно; новые колонки работают.
    assert storage.register(2, referred_by=1) is True
    [invited] = storage.invited(1)
    assert invited.user_id == 2 and invited.joined_at is not None
    storage.touch_user(2, "Анна", "anna")
    assert storage.invited(1)[0].username == "anna"
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


def test_render_overview() -> None:
    habits = [
        OverviewHabit("Зарядка", "💪", False, frozenset({date(2026, 10, 6), date(2026, 10, 8)})),
        OverviewHabit("Сладкое", "🍬", True, frozenset({date(2026, 10, 8)})),
    ]
    image = render_overview(year=2026, month=10, habits=habits, today=TODAY)
    # Со строкой серий у вредной привычки легенда выше — картинка другая.
    with_streaks = [habits[0], OverviewHabit("Сладкое", "🍬", True, habits[1].marks, 3, 9)]
    assert render_overview(year=2026, month=10, habits=with_streaks, today=TODAY) != image
    empty = render_overview(year=2026, month=10, habits=[], today=TODAY)
    assert image.startswith(b"\x89PNG") and empty.startswith(b"\x89PNG")
    assert image != empty
    # Больше значков, чем помещается в клетке, — не падает.
    crowd = [OverviewHabit(f"П{i}", "💧", False, frozenset({date(2026, 10, 6)})) for i in range(12)]
    assert render_overview(year=2026, month=10, habits=crowd, today=TODAY).startswith(b"\x89PNG")


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


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="нужен TEST_DATABASE_URL с PostgreSQL")
def test_migrate_sqlite_to_postgres(tmp_path) -> None:
    from habitbot.migrate import migrate

    path = str(tmp_path / "source.db")
    source = Storage(path)
    source.register(1)
    source.register(5_000_000_000, referred_by=1)  # идентификатор больше 32 бит
    source.touch_user(5_000_000_000, "Анна", "anna")
    first = source.add_habit(1, "Зарядка", GOOD, "08:00", TODAY, "💪")
    removed = source.add_habit(1, "Удалённая", BAD, None, TODAY)
    second = source.add_habit(5_000_000_000, "Сладкое", BAD, None, TODAY, "🍬")
    source.delete_habit(1, removed.id)
    source.set_mark(first.id, TODAY, True)
    source.set_mark(second.id, date(2026, 10, 9), True)
    source.save_share("key", 1, "file", "подпись")
    source.conn.close()

    make_storage()  # очищает тестовую базу
    assert migrate(path, TEST_DATABASE_URL) == {"users": 2, "habits": 3, "marks": 2, "shares": 1}

    target = Storage(TEST_DATABASE_URL)
    assert [(h.id, h.name, h.icon, h.remind_at) for h in target.list_habits(1)] == [
        (first.id, "Зарядка", "💪", "08:00")
    ]
    assert target.all_marks(first.id) == {TODAY}
    assert target.all_marks(second.id) == {date(2026, 10, 9)}
    [invited] = target.invited(1)
    assert (invited.user_id, invited.name, invited.username) == (5_000_000_000, "Анна", "anna")
    assert target.get_share("key", 1) == ("file", "подпись")
    # Новая привычка получает идентификатор после перенесённых, а не с единицы.
    assert target.add_habit(1, "Новая", GOOD, None, TODAY).id > second.id

    # В непустую базу повторный перенос не пишет.
    with pytest.raises(SystemExit, match="уже есть данные"):
        migrate(path, TEST_DATABASE_URL)
