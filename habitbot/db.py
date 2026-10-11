"""Хранилище: пользователи, привычки и отметки по дням (SQLite)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

GOOD = "good"
BAD = "bad"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    tz TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS habits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('good', 'bad')),
    emoji TEXT,
    remind_at TEXT,
    created_on TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    last_reminded TEXT
);
CREATE TABLE IF NOT EXISTS marks (
    habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    PRIMARY KEY (habit_id, day)
);
CREATE INDEX IF NOT EXISTS ix_habits_user ON habits(user_id, archived);
"""


@dataclass(frozen=True)
class Habit:
    id: int
    user_id: int
    name: str
    kind: str
    emoji: str | None
    remind_at: str | None
    created_on: date
    last_reminded: date | None

    @property
    def is_bad(self) -> bool:
        return self.kind == BAD

    @property
    def icon(self) -> str:
        """Эмодзи привычки; у заведённых до появления выбора — значок по типу."""
        return self.emoji or ("🚫" if self.is_bad else "✅")


def _habit(row: sqlite3.Row) -> Habit:
    return Habit(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        kind=row["kind"],
        emoji=row["emoji"],
        remind_at=row["remind_at"],
        created_on=date.fromisoformat(row["created_on"]),
        last_reminded=date.fromisoformat(row["last_reminded"]) if row["last_reminded"] else None,
    )


class Storage:
    """Синхронный SQLite: запросы крошечные, отдельный пул для бота на одного-двух людей не нужен."""

    def __init__(self, path: str, default_tz: str = "Europe/Moscow") -> None:
        self.default_tz = default_tz
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        # База, созданная до появления эмодзи: добавляем колонку на месте.
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(habits)")}
        if "emoji" not in columns:
            self.conn.execute("ALTER TABLE habits ADD COLUMN emoji TEXT")
        self.conn.commit()

    # --- пользователи ---

    def ensure_user(self, user_id: int) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO users (user_id, tz) VALUES (?, ?)", (user_id, self.default_tz)
        )
        self.conn.commit()

    def get_tz(self, user_id: int) -> str:
        row = self.conn.execute("SELECT tz FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row["tz"] if row else self.default_tz

    def set_tz(self, user_id: int, tz: str) -> None:
        self.ensure_user(user_id)
        self.conn.execute("UPDATE users SET tz = ? WHERE user_id = ?", (tz, user_id))
        self.conn.commit()

    # --- привычки ---

    def add_habit(
        self,
        user_id: int,
        name: str,
        kind: str,
        remind_at: str | None,
        today: date,
        emoji: str | None = None,
    ) -> Habit:
        self.ensure_user(user_id)
        cur = self.conn.execute(
            "INSERT INTO habits (user_id, name, kind, emoji, remind_at, created_on) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, name, kind, emoji, remind_at, today.isoformat()),
        )
        self.conn.commit()
        habit = self.get_habit(user_id, int(cur.lastrowid or 0))
        assert habit is not None
        return habit

    def list_habits(self, user_id: int) -> list[Habit]:
        rows = self.conn.execute(
            "SELECT * FROM habits WHERE user_id = ? AND archived = 0 ORDER BY id", (user_id,)
        ).fetchall()
        return [_habit(r) for r in rows]

    def get_habit(self, user_id: int, habit_id: int) -> Habit | None:
        """Привычка только своего владельца — чужой id из кнопки ничего не откроет."""
        row = self.conn.execute(
            "SELECT * FROM habits WHERE id = ? AND user_id = ? AND archived = 0",
            (habit_id, user_id),
        ).fetchone()
        return _habit(row) if row else None

    def delete_habit(self, user_id: int, habit_id: int) -> bool:
        cur = self.conn.execute(
            "UPDATE habits SET archived = 1 WHERE id = ? AND user_id = ?", (habit_id, user_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def set_emoji(self, user_id: int, habit_id: int, emoji: str) -> bool:
        cur = self.conn.execute(
            "UPDATE habits SET emoji = ? WHERE id = ? AND user_id = ?", (emoji, habit_id, user_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def set_reminder(self, user_id: int, habit_id: int, remind_at: str | None) -> bool:
        cur = self.conn.execute(
            "UPDATE habits SET remind_at = ?, last_reminded = NULL WHERE id = ? AND user_id = ?",
            (remind_at, habit_id, user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def habits_with_reminders(self) -> list[Habit]:
        rows = self.conn.execute(
            "SELECT * FROM habits WHERE archived = 0 AND remind_at IS NOT NULL"
        ).fetchall()
        return [_habit(r) for r in rows]

    def mark_reminded(self, habit_id: int, day: date) -> None:
        self.conn.execute(
            "UPDATE habits SET last_reminded = ? WHERE id = ?", (day.isoformat(), habit_id)
        )
        self.conn.commit()

    # --- отметки ---

    def is_marked(self, habit_id: int, day: date) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM marks WHERE habit_id = ? AND day = ?", (habit_id, day.isoformat())
        ).fetchone()
        return row is not None

    def set_mark(self, habit_id: int, day: date, on: bool) -> None:
        if on:
            self.conn.execute(
                "INSERT OR IGNORE INTO marks (habit_id, day) VALUES (?, ?)",
                (habit_id, day.isoformat()),
            )
        else:
            self.conn.execute(
                "DELETE FROM marks WHERE habit_id = ? AND day = ?", (habit_id, day.isoformat())
            )
        self.conn.commit()

    def toggle_mark(self, habit_id: int, day: date) -> bool:
        """Переключает отметку дня, возвращает новое состояние."""
        on = not self.is_marked(habit_id, day)
        self.set_mark(habit_id, day, on)
        return on

    def marks_between(self, habit_id: int, start: date, end: date) -> set[date]:
        rows = self.conn.execute(
            "SELECT day FROM marks WHERE habit_id = ? AND day BETWEEN ? AND ?",
            (habit_id, start.isoformat(), end.isoformat()),
        ).fetchall()
        return {date.fromisoformat(r["day"]) for r in rows}

    def all_marks(self, habit_id: int) -> set[date]:
        rows = self.conn.execute("SELECT day FROM marks WHERE habit_id = ?", (habit_id,)).fetchall()
        return {date.fromisoformat(r["day"]) for r in rows}


def streak(habit: Habit, marks: set[date], today: date) -> int:
    """Текущая серия в днях.

    Полезная привычка — сколько дней подряд она отмечена (сегодня ещё не отмечена — серия
    считается по вчера, она не «сгорает» с утра). Вредная — сколько дней подряд её не было,
    считая с дня создания.
    """
    if habit.is_bad:
        day, count = today, 0
        while day >= habit.created_on and day not in marks:
            count += 1
            day -= timedelta(days=1)
        return count
    day = today if today in marks else today - timedelta(days=1)
    count = 0
    while day in marks:
        count += 1
        day -= timedelta(days=1)
    return count
