"""Перенос данных из файла SQLite в PostgreSQL.

    uv run python -m habitbot.migrate habits.db postgresql://user:pass@host/db

Переносит все таблицы с теми же идентификаторами и сверяет число строк. В непустую базу
не пишет: повторный запуск ничего не испортит.
"""

from __future__ import annotations

import sqlite3
import sys

from .db import TABLES, Storage, is_postgres


def migrate(sqlite_path: str, database_url: str) -> dict[str, int]:
    """Копирует таблицы; возвращает число перенесённых строк по каждой."""
    if not is_postgres(database_url):
        raise SystemExit("Второй аргумент — адрес PostgreSQL вида postgresql://…")
    source = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    target = Storage(database_url).conn  # создаёт схему, если её ещё нет

    occupied = {
        table: target.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        for table in TABLES
    }
    if any(occupied.values()):
        raise SystemExit(f"В базе назначения уже есть данные: {occupied}. Перенос отменён.")

    copied: dict[str, int] = {}
    with target.raw.transaction():  # всё или ничего
        for table in TABLES:  # порядок важен: привычки ссылаются на пользователей и т. д.
            rows = source.execute(f"SELECT * FROM {table}").fetchall()
            for row in rows:
                columns = row.keys()
                target.raw.execute(
                    f"INSERT INTO {table} ({', '.join(columns)}) "
                    f"VALUES ({', '.join(['%s'] * len(columns))})",
                    tuple(row),
                )
            copied[table] = len(rows)
        # Счётчики идентификаторов продолжают с наибольшего перенесённого.
        for table in ("habits", "motivation_messages"):
            target.raw.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
            )

    for table in TABLES:
        actual = target.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        if actual != copied[table]:
            raise SystemExit(f"{table}: перенесено {copied[table]}, в базе {actual}")
    return copied


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    for name, count in migrate(sys.argv[1], sys.argv[2]).items():
        print(f"{name}: {count}")
