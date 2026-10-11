"""Общее для тестов: хранилище на SQLite в памяти или, если задан TEST_DATABASE_URL, на PostgreSQL."""

from __future__ import annotations

import os

from habitbot.db import TABLES, Storage

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


def make_storage() -> Storage:
    """Чистое хранилище для одного теста."""
    if not TEST_DATABASE_URL:
        return Storage(":memory:")
    storage = Storage(TEST_DATABASE_URL)
    storage.conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
    return storage
