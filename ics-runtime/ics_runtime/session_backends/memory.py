"""In-process dict-based session backend — the default."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from ics_runtime.session_backends.base import SessionBackend, SessionData


class MemoryBackend(SessionBackend):
    """Stores sessions in a dict for the lifetime of the current process.

    Thread-safe via a ``threading.Lock``.  Use Redis or SQLite backends
    when you need persistence across process restarts.
    """

    def __init__(self) -> None:
        self._store: dict[str, SessionData] = {}
        self._lock = threading.Lock()

    def load(self, session_id: str) -> SessionData | None:
        with self._lock:
            return self._store.get(session_id)

    def save(self, session_id: str, data: SessionData) -> None:
        data.last_active = datetime.now(timezone.utc)
        with self._lock:
            self._store[session_id] = data

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._store.pop(session_id, None)

    def exists(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._store

    def all_sessions(self) -> list[SessionData]:
        with self._lock:
            return list(self._store.values())
