# src/agents/persistence.py
import json
import sqlite3
import threading
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Any, Optional

from .state import StructuringSession


class SQLiteSessionStore:
    """Drop-in SessionStore replacement persisting to SQLite.

    JSON-serialised sessions; queues remain in-memory (events are ephemeral).
    """

    def __init__(self, db_path: str = "vol_desk_sessions.db") -> None:
        self._lock = threading.RLock()
        self._db_path = db_path
        self._queues: dict[str, SimpleQueue] = {}
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    payload    TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, check_same_thread=False, isolation_level=None)

    def add(self, session: StructuringSession) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sessions(session_id, payload, updated_at) VALUES (?, ?, strftime('%s','now'))",
                (session.session_id, session.model_dump_json()),
            )
            self._queues.setdefault(session.session_id, SimpleQueue())

    def get(self, session_id: str) -> Optional[StructuringSession]:
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT payload FROM sessions WHERE session_id = ?", (session_id,))
            row = cur.fetchone()
        if row is None:
            return None
        return StructuringSession.model_validate_json(row[0])

    def update(self, session: StructuringSession) -> None:
        self.add(session)

    def list_ids(self) -> list[str]:
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT session_id FROM sessions ORDER BY updated_at DESC")
            return [r[0] for r in cur.fetchall()]

    def queue(self, session_id: str) -> Optional[SimpleQueue]:
        with self._lock:
            return self._queues.setdefault(session_id, SimpleQueue())

    def emit(self, session_id: str, event: dict[str, Any]) -> None:
        q = self.queue(session_id)
        if q is not None:
            q.put(event)

    def drain(self, session_id: str, timeout: float = 0.0) -> Optional[dict[str, Any]]:
        q = self.queue(session_id)
        if q is None:
            return None
        try:
            return q.get(timeout=timeout) if timeout > 0 else q.get_nowait()
        except Empty:
            return None
