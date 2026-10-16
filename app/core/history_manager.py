"""История генераций и версий (SQLite)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from app.database.database import Database
from app.utils.log import get_logger

log = get_logger("history")


@dataclass
class Version:
    id: int
    generation_id: int
    version: int
    date: str
    prompt: str
    edit_request: str
    selected_styles: list[str]
    model_name: str


@dataclass
class Generation:
    id: int
    date: str
    model_id: str
    model_name: str
    user_request: str
    selected_styles: list[str]
    references: list[dict]
    final_prompt: str


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _loads(s, default):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


class HistoryManager:
    def __init__(self, db: Database):
        self.db = db

    def create_generation(self, model_id: str, model_name: str, user_request: str,
                          styles: list[str], references: list[dict], prompt: str) -> tuple[int, Version]:
        now = _now()
        with self.db.connect() as con:
            cur = con.execute(
                'INSERT INTO generations (date, model_id, model_name, user_request, selected_styles, "references", final_prompt)'
                " VALUES (?,?,?,?,?,?,?)",
                (now, model_id, model_name, user_request, json.dumps(styles, ensure_ascii=False),
                 json.dumps(references, ensure_ascii=False), prompt),
            )
            gid = cur.lastrowid
            vid = con.execute(
                "INSERT INTO generation_versions (generation_id, version, date, prompt, edit_request, selected_styles, model_name)"
                " VALUES (?,?,?,?,?,?,?)",
                (gid, 1, now, prompt, "", json.dumps(styles, ensure_ascii=False), model_name),
            ).lastrowid
        log.info("Generation saved id=%s", gid)
        return gid, Version(vid, gid, 1, now, prompt, "", styles, model_name)

    def add_version(self, generation_id: int, prompt: str, edit_request: str,
                    styles: list[str], model_name: str) -> Version:
        now = _now()
        with self.db.connect() as con:
            n = con.execute("SELECT COALESCE(MAX(version),0)+1 FROM generation_versions WHERE generation_id=?",
                            (generation_id,)).fetchone()[0]
            vid = con.execute(
                "INSERT INTO generation_versions (generation_id, version, date, prompt, edit_request, selected_styles, model_name)"
                " VALUES (?,?,?,?,?,?,?)",
                (generation_id, n, now, prompt, edit_request, json.dumps(styles, ensure_ascii=False), model_name),
            ).lastrowid
            con.execute("UPDATE generations SET final_prompt=? WHERE id=?", (prompt, generation_id))
        log.info("Version saved generation=%s v%s", generation_id, n)
        return Version(vid, generation_id, n, now, prompt, edit_request, styles, model_name)

    def versions(self, generation_id: int) -> list[Version]:
        with self.db.connect() as con:
            rows = con.execute("SELECT * FROM generation_versions WHERE generation_id=? ORDER BY version",
                               (generation_id,)).fetchall()
        return [Version(r["id"], r["generation_id"], r["version"], r["date"], r["prompt"], r["edit_request"] or "",
                        _loads(r["selected_styles"], []), r["model_name"] or "") for r in rows]

    def list_generations(self, limit: int = 200) -> list[Generation]:
        with self.db.connect() as con:
            rows = con.execute("SELECT * FROM generations ORDER BY date DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [self._gen(r) for r in rows]

    def get_generation(self, generation_id: int) -> Generation | None:
        with self.db.connect() as con:
            r = con.execute("SELECT * FROM generations WHERE id=?", (generation_id,)).fetchone()
        return self._gen(r) if r else None

    @staticmethod
    def _gen(r) -> Generation:
        return Generation(r["id"], r["date"], r["model_id"] or "", r["model_name"] or "", r["user_request"] or "",
                          _loads(r["selected_styles"], []), _loads(r["references"], []), r["final_prompt"] or "")

    def delete_generation(self, generation_id: int) -> None:
        with self.db.connect() as con:
            con.execute("DELETE FROM generation_versions WHERE generation_id=?", (generation_id,))
            con.execute("DELETE FROM generations WHERE id=?", (generation_id,))

    def is_reference_used(self, path: str) -> bool:
        needle = json.dumps(path, ensure_ascii=False)[1:-1]
        with self.db.connect() as con:
            row = con.execute('SELECT 1 FROM generations WHERE "references" LIKE ? LIMIT 1',
                              (f"%{needle}%",)).fetchone()
        return row is not None
