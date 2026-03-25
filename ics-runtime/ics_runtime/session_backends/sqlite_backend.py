"""SQLite session backend — file-based persistence without external deps."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from ics_runtime.session_backends.base import SessionBackend, SessionData


_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize(data: SessionData) -> str:
    return json.dumps({
        "session_id": data.session_id,
        "entries": data.entries,
        "context": data.context,
        "cleared": data.cleared,
        "created_at": data.created_at.isoformat(),
        "last_active": data.last_active.isoformat(),
        "turn_count": data.turn_count,
    })


def _deserialize(raw: str) -> SessionData:
    d = json.loads(raw)
    return SessionData(
        session_id=d["session_id"],
        entries=d.get("entries", []),
        context=d.get("context", {}),
        cleared=d.get("cleared", False),
        created_at=datetime.fromisoformat(d["created_at"]),
        last_active=datetime.fromisoformat(d["last_active"]),
        turn_count=d.get("turn_count", 0),
    )


class SQLiteBackend(SessionBackend):
    """Stores sessions in a SQLite database file.

    Suitable for single-process applications that need session persistence
    across restarts without running a Redis server.

    Args:
        db_path: Path to the SQLite file.  Use ``":memory:"`` for tests.
    """

    def __init__(self, db_path: str | Path = "ics_sessions.db") -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        # For :memory: databases keep a single persistent connection; each new
        # connect() call would create a fresh empty DB.
        if self._db_path == ":memory:":
            self._persistent_conn: sqlite3.Connection | None = sqlite3.connect(
                ":memory:", check_same_thread=False
            )
            self._persistent_conn.row_factory = sqlite3.Row
        else:
            self._persistent_conn = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self._persistent_conn is not None:
            return self._persistent_conn
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        with self._lock:
            conn.execute(_DDL)
            conn.commit()

    def load(self, session_id: str) -> SessionData | None:
        conn = self._connect()
        with self._lock:
            row = conn.execute(
                "SELECT data FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        return _deserialize(row["data"])

    def save(self, session_id: str, data: SessionData) -> None:
        now = _now()
        serialized = _serialize(data)
        conn = self._connect()
        with self._lock:
            conn.execute(
                """
                INSERT INTO sessions (session_id, data, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET data = excluded.data,
                    updated_at = excluded.updated_at
                """,
                (session_id, serialized, now, now),
            )
            conn.commit()

    def delete(self, session_id: str) -> None:
        conn = self._connect()
        with self._lock:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.commit()

    def exists(self, session_id: str) -> bool:
        conn = self._connect()
        with self._lock:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return row is not None
