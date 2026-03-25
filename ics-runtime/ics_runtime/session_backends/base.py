"""Abstract session backend interface."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class SessionData:
    """Persisted envelope for a single session.

    ``entries`` accumulates the timestamped SESSION_STATE lines from each
    completed turn.  ``cleared`` is set to True by ``Session.clear()`` and
    reset to False after the next run has emitted the CLEAR sentinel.
    """

    session_id: str
    entries: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    cleared: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_active: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    turn_count: int = 0


class SessionBackend(abc.ABC):
    """Abstract CRUD interface for session state storage."""

    @abc.abstractmethod
    def load(self, session_id: str) -> SessionData | None: ...

    @abc.abstractmethod
    def save(self, session_id: str, data: SessionData) -> None: ...

    @abc.abstractmethod
    def delete(self, session_id: str) -> None: ...

    @abc.abstractmethod
    def exists(self, session_id: str) -> bool: ...
