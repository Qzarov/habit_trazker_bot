import random
from datetime import date, datetime, timedelta
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
from habitbot.db import BAD, GOOD, Habit, Motivation, Storage, best_clean_streak, streak
from habitbot.motivation import (
    AFTER_MISS,
    AUTO,
    MAX_CHARS,
    PRESETS,
    STYLES,
    Motivator,
    build_messages,
    choose_style,
    clean,
    preset,
    stage,
)
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


def test_answer_has_three_states(storage: Storage) -> None:
    habit = storage.add_habit(1, "Зарядка", GOOD, None, TODAY)
    assert storage.get_answer(habit.id, TODAY) is None  # нет данных
    assert storage.cycle_answer(habit.id, TODAY) is True
    assert storage.is_marked(habit.id, TODAY)
    # «Нет» хранится отдельно от «нет данных», но отметкой не считается.
    assert storage.cycle_answer(habit.id, TODAY) is False
    assert storage.get_answer(habit.id, TODAY) is False
    assert storage.all_answers(habit.id) == {TODAY: False}
    assert storage.all_marks(habit.id) == set()
    assert storage.cycle_answer(habit.id, TODAY) is None
    assert storage.all_answers(habit.id) == {}

    storage.set_answer(habit.id, TODAY, False)
    storage.set_answer(habit.id, TODAY, True)  # ответ за день один — новый заменяет прежний
    assert storage.all_answers(habit.id) == {TODAY: True}


def test_explicit_no_does_not_change_streaks(storage: Storage) -> None:
    good = storage.add_habit(1, "Зарядка", GOOD, None, date(2026, 10, 1))
    bad = storage.add_habit(1, "Сладкое", BAD, None, date(2026, 10, 1))
    for habit in (good, bad):
        storage.set_answer(habit.id, TODAY - timedelta(days=1), True)
        storage.set_answer(habit.id, TODAY, False)
    assert streak(good, storage.all_marks(good.id), TODAY) == 1  # по вчера, как без ответа
    assert streak(bad, storage.all_marks(bad.id), TODAY) == 1


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
        "answers": {date(2026, 10, 6): True}, "created_on": date(2026, 10, 1), "today": TODAY,
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
    assert day_style(date(2026, 10, 6), is_bad=False, answer=True, **kw) == "hit"
    assert day_style(date(2026, 10, 6), is_bad=False, answer=False, **kw) == "miss"
    assert day_style(date(2026, 10, 6), is_bad=True, answer=True, **kw) == "miss"
    assert day_style(date(2026, 10, 6), is_bad=True, answer=False, **kw) == "hit"
    # День без ответа — «нет данных»: ни хороший, ни плохой, у обоих типов привычек.
    assert day_style(date(2026, 10, 6), is_bad=False, answer=None, **kw) == "unknown"
    assert day_style(date(2026, 10, 6), is_bad=True, answer=None, **kw) == "unknown"
    assert day_style(TODAY, is_bad=True, answer=None, **kw) == "unknown"
    assert day_style(TODAY, is_bad=True, answer=True, **kw) == "miss"
    assert day_style(TODAY, is_bad=True, answer=False, **kw) == "hit"
    # До заведения и в будущем — не в счёт.
    assert day_style(date(2026, 10, 4), is_bad=False, answer=None, **kw) == "idle"
    assert day_style(date(2026, 10, 12), is_bad=True, answer=None, **kw) == "idle"


def test_render_month_tells_no_from_no_data() -> None:
    kw = {
        "name": "Сладкое", "is_bad": True, "year": 2026, "month": 10,
        "created_on": date(2026, 10, 1), "today": TODAY,
    }  # fmt: skip
    assert render_month(answers={}, **kw) != render_month(answers={date(2026, 10, 6): False}, **kw)


def test_render_month_is_png() -> None:
    image = render_month(
        name="Зарядка", is_bad=False, year=2026, month=10,
        answers={date(2026, 10, 6): True, date(2026, 10, 8): False}, created_on=date(2026, 10, 5), today=TODAY,
    )  # fmt: skip
    assert image.startswith(b"\x89PNG") and len(image) > 5000


def test_render_month_with_emoji_differs() -> None:
    kw = {
        "name": "Зарядка", "is_bad": False, "year": 2026, "month": 10,
        "answers": {date(2026, 10, 6): True}, "created_on": date(2026, 10, 5), "today": TODAY,
    }  # fmt: skip
    plain = render_month(**kw)
    with_emoji = render_month(emoji="💪", **kw)
    assert with_emoji.startswith(b"\x89PNG")
    if can_draw("💪"):  # на машине без шрифта эмодзи картинка остаётся прежней
        assert with_emoji != plain


def test_old_marks_become_yes_answers(tmp_path) -> None:
    """База до появления ответа «нет»: колонка добавляется, прежние отметки — это «да»."""
    import sqlite3

    path = str(tmp_path / "old.db")
    current = Storage(path)
    habit = current.add_habit(1, "Зарядка", GOOD, None, TODAY)
    current.conn.close()
    old = sqlite3.connect(path)
    old.executescript(
        "DROP TABLE marks;"
        "CREATE TABLE marks (habit_id INTEGER NOT NULL, day TEXT NOT NULL,"
        " PRIMARY KEY (habit_id, day));"
    )
    old.execute("INSERT INTO marks VALUES (?, ?)", (habit.id, TODAY.isoformat()))
    old.commit()
    old.close()

    storage = Storage(path)
    assert storage.all_answers(habit.id) == {TODAY: True}
    storage.set_answer(habit.id, TODAY, False)
    assert Storage(path).all_answers(habit.id) == {TODAY: False}  # повторное открытие не портит


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

    async def send(habit, day) -> None:
        assert day == now.astimezone(ZoneInfo(storage.get_tz(habit.user_id))).date()
        sent.append(habit.name)

    await tick(storage, send, now_utc=now)
    return sent


async def test_tick_sends_once_and_skips_answered(storage: Storage) -> None:
    good = storage.add_habit(1, "Зарядка", GOOD, "08:00", TODAY)
    storage.add_habit(1, "Сладкое", BAD, "08:00", TODAY)
    done = storage.add_habit(1, "Вода", GOOD, "08:00", TODAY)
    not_done = storage.add_habit(1, "Растяжка", GOOD, "08:00", TODAY)
    clean = storage.add_habit(1, "Кофе", BAD, "08:00", TODAY)
    slipped = storage.add_habit(1, "Чипсы", BAD, "08:00", TODAY)
    storage.add_habit(1, "Чтение", GOOD, "21:00", TODAY)
    # Ответили сами — любым ответом, у полезных и у вредных.
    storage.set_answer(done.id, TODAY, True)
    storage.set_answer(not_done.id, TODAY, False)
    storage.set_answer(clean.id, TODAY, False)
    storage.set_answer(slipped.id, TODAY, True)
    now = datetime(2026, 10, 11, 5, 0, tzinfo=ZoneInfo("UTC"))  # 08:00 по Москве

    # Напоминают только привычки без ответа за сегодня; «Чтению» ещё рано.
    assert await _run_tick(storage, now) == ["Зарядка", "Сладкое"]
    assert await _run_tick(storage, now) == []
    assert storage.get_habit(1, good.id).last_reminded == TODAY


async def test_tick_next_day_asks_about_yesterday(storage: Storage) -> None:
    created = TODAY - timedelta(days=2)
    yesterday = TODAY - timedelta(days=1)
    kw = {"remind_at": "08:00", "today": created, "remind_next_day": True}
    storage.add_habit(1, "Сладкое", BAD, **kw)
    said_no = storage.add_habit(1, "Кофе", BAD, **kw)
    done = storage.add_habit(1, "Зарядка", GOOD, **kw)
    storage.add_habit(1, "Чтение", GOOD, "08:00", TODAY, remind_next_day=True)  # заведена сегодня
    # За вчера уже есть ответ — любой, «да» или «нет»: день закончился, второй раз не спрашиваем.
    storage.set_answer(said_no.id, yesterday, False)
    storage.set_answer(done.id, yesterday, True)
    asked: list[tuple[str, date]] = []

    async def send(habit, day) -> None:
        asked.append((habit.name, day))

    now = datetime(2026, 10, 11, 5, 0, tzinfo=ZoneInfo("UTC"))  # 08:00 по Москве
    assert await tick(storage, send, now_utc=now) == 1
    assert asked == [("Сладкое", yesterday)]
    assert await tick(storage, send, now_utc=now) == 0  # в тот же день повторно не пишем


async def test_tick_skips_switched_off_reminder(storage: Storage) -> None:
    habit = storage.add_habit(1, "Зарядка", GOOD, "08:00", TODAY)
    now = datetime(2026, 10, 11, 5, 0, tzinfo=ZoneInfo("UTC"))  # 08:00 по Москве
    storage.set_remind_enabled(1, habit.id, False)
    assert await _run_tick(storage, now) == []
    assert storage.get_habit(1, habit.id).remind_at == "08:00"  # время сохранилось
    storage.set_remind_enabled(1, habit.id, True)
    assert await _run_tick(storage, now) == ["Зарядка"]


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
    source.set_answer(first.id, TODAY, True)
    source.set_answer(second.id, date(2026, 10, 9), False)
    source.save_share("key", 1, "file", "подпись")
    source.set_motivation_style(1, "streak")
    logged = source.log_motivation(1, first.id, TODAY, "streak", 1, False, "серия", "preset")
    source.conn.close()

    make_storage()  # очищает тестовую базу
    assert migrate(path, TEST_DATABASE_URL) == {
        "users": 2, "habits": 3, "marks": 2, "shares": 1, "motivation_messages": 1,
    }  # fmt: skip

    target = Storage(TEST_DATABASE_URL)
    assert target.get_motivation(1).style == "streak"
    assert target.get_motivation_message(1, logged).text == "серия"
    # Новая запись журнала после переноса получает свободный номер.
    assert target.log_motivation(1, first.id, TODAY, "streak", 0, False, "ещё") == logged + 1
    assert [(h.id, h.name, h.icon, h.remind_at) for h in target.list_habits(1)] == [
        (first.id, "Зарядка", "💪", "08:00")
    ]
    assert target.all_marks(first.id) == {TODAY}
    assert target.all_answers(second.id) == {date(2026, 10, 9): False}
    [invited] = target.invited(1)
    assert (invited.user_id, invited.name, invited.username) == (5_000_000_000, "Анна", "anna")
    assert target.get_share("key", 1) == ("file", "подпись")
    # Новая привычка получает идентификатор после перенесённых, а не с единицы.
    assert target.add_habit(1, "Новая", GOOD, None, TODAY).id > second.id

    # В непустую базу повторный перенос не пишет.
    with pytest.raises(SystemExit, match="уже есть данные"):
        migrate(path, TEST_DATABASE_URL)


# --- мотивация ---


def test_tone_and_feedback_storage(storage: Storage) -> None:
    storage.ensure_user(1)
    assert storage.get_motivation(1) == Motivation(style=None, why=None, tone=0)
    for _ in range(5):
        storage.shift_tone(1, 1)
    assert storage.get_motivation(1).tone == 2  # не выше предела
    for _ in range(9):
        storage.shift_tone(1, -1)
    assert storage.get_motivation(1).tone == -2

    habit = storage.add_habit(1, "Зарядка", GOOD, "08:00", TODAY)
    first = storage.log_motivation(1, habit.id, TODAY, "streak", 0, False, "раз")
    storage.log_motivation(1, habit.id, TODAY, "support", 0, True, "два", "preset")
    assert storage.set_motivation_feedback(1, first, "ok")
    assert not storage.set_motivation_feedback(1, first, "harder")  # один раз
    # «Нет данных» не засчитывается, любой ответ — засчитывается всем сообщениям дня.
    storage.set_answer(habit.id, TODAY, None)
    assert storage.motivation_stats(1) == {"streak": (1, 0, 1), "support": (1, 0, 0)}
    storage.set_answer(habit.id, TODAY, False)
    assert storage.motivation_stats(1) == {"streak": (1, 1, 1), "support": (1, 1, 0)}
    assert storage.recent_motivation(habit.id) == ["два", "раз"]


def test_choose_style_prefers_chosen_and_experiments() -> None:
    rng = random.Random(1)
    chosen = Motivation(style="streak", why=None, tone=0)
    picks = [choose_style(chosen, {}, ("ease",), rng) for _ in range(2000)]
    share = sum(1 for style, _ in picks if style == "streak") / len(picks)
    assert 0.65 < share < 0.75
    # Эксперименты — другие стили, и они помечены.
    assert all(exp == (style != "streak") for style, exp in picks)
    assert {style for style, _ in picks} == set(STYLES)

    # «Подбирай сам»: стиль, после которого отмечают, выбирается чаще; это не эксперимент.
    auto = Motivation(style=AUTO, why=None, tone=0)
    stats = {"progress": (20, 19, 0), "curiosity": (20, 1, 0)}
    picks = [choose_style(auto, stats, (), rng) for _ in range(3000)]
    assert not any(exp for _, exp in picks)
    count = {s: sum(1 for p, _ in picks if p == s) for s in ("progress", "curiosity")}
    assert count["progress"] > 3 * count["curiosity"]


def test_stage_by_age() -> None:
    habit = lambda days: Habit(1, 1, "x", GOOD, None, None, TODAY - timedelta(days=days), None)
    assert "ease" in stage(habit(3), TODAY)[1]
    assert "progress" in stage(habit(20), TODAY)[1]
    assert "identity" in stage(habit(60), TODAY)[1]


def test_build_messages_carries_context() -> None:
    habit = Habit(1, 1, "Сладкое", BAD, None, None, TODAY - timedelta(days=10), None)
    answers = {TODAY - timedelta(days=1): True, TODAY - timedelta(days=3): False}
    motivation = Motivation(style="meaning", why="ради зубов", tone=2)
    system, user = build_messages(habit, TODAY, TODAY, motivation, "meaning", answers, ["старое"])
    assert "стыда" in system["content"]
    text = user["content"]
    for part in ("«Сладкое»", "вредная", "ради зубов", "смысл и ценности", "жёстко", "старое"):
        assert part in text
    assert "срыв" in text and "держался" in text
    # Вчера был срыв — просим поддержку.
    assert "Вчера был пропуск или срыв" in text


def test_clean_model_answer() -> None:
    assert clean("  «Давай, ты сможешь!»  ") == "Давай, ты сможешь!"
    assert clean("   ") is None
    long = clean("слово " * 200)
    assert len(long) <= MAX_CHARS + 1 and long.endswith("…")


def test_preset_fallback() -> None:
    habit = Habit(1, 1, "Качалка", GOOD, None, None, TODAY - timedelta(days=5), None)
    answers = {TODAY - timedelta(days=2): True, TODAY - timedelta(days=1): True}
    text = preset(habit, TODAY, TODAY, "streak", answers, [])
    assert "{" not in text and text in [
        t.format(name="Качалка", streak=2, best=0, week=2, week_next=3)
        for t in PRESETS["streak"]["good"]
    ]
    # Недавнюю фразу не повторяем, если есть другая.
    assert preset(habit, TODAY, TODAY, "streak", answers, [text]) != text
    # После пропуска — поддержка, а не стиль.
    missed = {TODAY - timedelta(days=1): False}
    assert "Качалка" in preset(habit, TODAY, TODAY, "streak", missed, [])
    assert preset(habit, TODAY, TODAY, "streak", missed, []) in [
        t.format(name="Качалка") for t in AFTER_MISS["good"]
    ]


async def test_compose_uses_model_and_falls_back_to_presets(storage: Storage) -> None:
    habit = storage.add_habit(1, "Чтение", GOOD, "21:00", TODAY)
    storage.set_motivation_style(1, "curiosity")
    motivator = Motivator(storage, api_key="key", rng=random.Random(3))
    sent: list[list[dict[str, str]]] = []

    async def model(messages):
        sent.append(messages)
        return "  «Какую главу откроешь сегодня?»  "

    motivator.complete = model
    message_id, text = await motivator.compose(habit, TODAY, TODAY)
    assert text == "Какую главу откроешь сегодня?" and len(sent) == 1
    assert storage.get_motivation_message(1, message_id).source == "llm"

    # Модель недоступна или ответила пустым — готовая фраза.
    for answer in (None, "  "):

        async def broken(messages, answer=answer):
            return answer

        motivator.complete = broken
        message_id, text = await motivator.compose(habit, TODAY, TODAY)
        assert "Чтение" in text
        assert storage.get_motivation_message(1, message_id).source == "preset"
