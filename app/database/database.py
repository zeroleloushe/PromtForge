"""SQLite: проекты, генерации, версии, модели, стили. Схема совместима с прежней БД."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager

from app.utils import paths
from app.utils.log import get_logger

log = get_logger("database")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS generations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    date TEXT NOT NULL,
    model_id TEXT,
    model_name TEXT,
    user_request TEXT,
    selected_styles TEXT,
    "references" TEXT,
    negative_prompt TEXT,
    final_prompt TEXT,
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS generation_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    date TEXT NOT NULL,
    prompt TEXT NOT NULL,
    edit_request TEXT,
    selected_styles TEXT,
    model_name TEXT,
    FOREIGN KEY (generation_id) REFERENCES generations (id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS models (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, api_base TEXT, model TEXT,
    temperature REAL, max_tokens INTEGER, enabled INTEGER, updated TEXT
);
CREATE TABLE IF NOT EXISTS styles (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
    priority INTEGER, enabled INTEGER, updated TEXT
);
CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    model_id TEXT,
    messages TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_updated ON chat_sessions (updated DESC);
CREATE INDEX IF NOT EXISTS idx_versions_generation ON generation_versions (generation_id);
CREATE INDEX IF NOT EXISTS idx_generations_date ON generations (date DESC);
"""


class Database:
    """Потокобезопасная обёртка: отдельное соединение на каждую операцию."""

    def __init__(self, path=None):
        self._path = path or paths.DB_PATH
        self._lock = threading.RLock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        with self._lock:
            con = sqlite3.connect(self._path, timeout=10)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys = ON")
            try:
                yield con
                con.commit()
            except Exception:
                con.rollback()
                raise
            finally:
                con.close()
