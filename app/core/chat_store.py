"""ChatStore: сохранение чатов (сессий) в SQLite + подготовка истории для API."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from app.database.database import Database
from app.utils.log import get_logger

log = get_logger("chatstore")

MAX_SESSIONS = 200
TITLE_LEN = 48


@dataclass
class SessionInfo:
    id: int
    title: str
    updated: str
    model_id: str = ""


def make_title(history: list[dict]) -> str:
    for h in history:
        if h.get("role") == "user":
            txt = (h.get("display") or h.get("content") or "").strip().replace("\n", " ")
            if txt:
                return txt if len(txt) <= TITLE_LEN else txt[:TITLE_LEN - 1].rstrip() + "…"
    return "Новый чат"


def sanitize_history(history: list[dict]) -> list[dict]:
    """Приводит историю к виду, который примет API: у каждого tool_call есть ответ tool, нет «осиротевших» tool-сообщений.

    Если чат был прерван (STOP, сбой) между вызовом инструмента и его результатом — лишние tool_calls отбрасываются.
    """
    out: list[dict] = []
    i = 0
    while i < len(history):
        h = history[i]
        if h.get("role") == "assistant" and h.get("tool_calls"):
            ids = [c.get("id") for c in h["tool_calls"]]
            j = i + 1
            answers = []
            while j < len(history) and history[j].get("role") == "tool":
                answers.append(history[j])
                j += 1
            have = {a.get("tool_call_id") for a in answers}
            if all(x in have for x in ids):
                out.append(h)
                out.extend(a for a in answers if a.get("tool_call_id") in ids)
            else:
                stripped = {k: v for k, v in h.items() if k != "tool_calls"}
                if (stripped.get("content") or "").strip():
                    out.append(stripped)
            i = j
            continue
        if h.get("role") == "tool":      # осиротевший результат
            i += 1
            continue
        out.append(h)
        i += 1
    return out


class ChatStore:
    def __init__(self, db: Database):
        self.db = db

    def list(self, limit: int = 60) -> list[SessionInfo]:
        with self.db.connect() as con:
            rows = con.execute("SELECT id, title, updated, model_id FROM chat_sessions ORDER BY updated DESC LIMIT ?",
                               (limit,)).fetchall()
        return [SessionInfo(r["id"], r["title"], r["updated"], r["model_id"] or "") for r in rows]

    def load(self, session_id: int) -> list[dict]:
        with self.db.connect() as con:
            row = con.execute("SELECT messages FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return []
        try:
            data = json.loads(row["messages"])
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            log.warning("Чат %s: повреждённый JSON", session_id)
            return []

    def save(self, session_id: int | None, history: list[dict], model_id: str = "") -> int | None:
        """Создаёт или обновляет сессию. Пустой чат не сохраняется. Возвращает id."""
        if not history:
            return session_id
        now = datetime.now().isoformat(timespec="seconds")
        payload = json.dumps(history, ensure_ascii=False)
        title = make_title(history)
        try:
            with self.db.connect() as con:
                if session_id and con.execute("SELECT 1 FROM chat_sessions WHERE id = ?", (session_id,)).fetchone():
                    con.execute("UPDATE chat_sessions SET title = ?, updated = ?, model_id = ?, messages = ? WHERE id = ?",
                                (title, now, model_id, payload, session_id))
                    return session_id
                cur = con.execute("INSERT INTO chat_sessions (title, created, updated, model_id, messages) VALUES (?,?,?,?,?)",
                                  (title, now, now, model_id, payload))
                new_id = cur.lastrowid
                con.execute("DELETE FROM chat_sessions WHERE id NOT IN "
                            "(SELECT id FROM chat_sessions ORDER BY updated DESC LIMIT ?)", (MAX_SESSIONS,))
                return new_id
        except Exception as exc:  # noqa: BLE001
            log.error("Не удалось сохранить чат: %s", exc)
            return session_id

    def delete(self, session_id: int) -> None:
        try:
            with self.db.connect() as con:
                con.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        except Exception as exc:  # noqa: BLE001
            log.error("Не удалось удалить чат: %s", exc)
