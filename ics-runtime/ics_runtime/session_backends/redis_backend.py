"""Redis session backend — for production deployments with TTL-based expiry."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ics_runtime.session_backends.base import SessionBackend, SessionData

_DEFAULT_TTL = 86_400  # 24 hours


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


class RedisBackend(SessionBackend):
    """Stores sessions in Redis with automatic TTL expiry.

    Requires ``redis-py``: ``pip install 'ics-runtime[redis]'``.

    Args:
        url:     Redis URL, e.g. ``"redis://localhost:6379/0"``.
        ttl:     Session TTL in seconds.  Default: 86400 (24 hours).
        prefix:  Key prefix.  Default: ``"ics:session:"``.
    """

    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        ttl: int = _DEFAULT_TTL,
        prefix: str = "ics:session:",
    ) -> None:
        try:
            import redis as _redis
        except ImportError as exc:
            raise ImportError(
                "redis package required. Run: pip install 'ics-runtime[redis]'"
            ) from exc

        self._client = _redis.Redis.from_url(url, decode_responses=True)
        self._ttl = ttl
        self._prefix = prefix

    def _key(self, session_id: str) -> str:
        return f"{self._prefix}{session_id}"

    def load(self, session_id: str) -> SessionData | None:
        raw = self._client.get(self._key(session_id))
        if raw is None:
            return None
        return _deserialize(raw)

    def save(self, session_id: str, data: SessionData) -> None:
        self._client.setex(self._key(session_id), self._ttl, _serialize(data))

    def delete(self, session_id: str) -> None:
        self._client.delete(self._key(session_id))

    def exists(self, session_id: str) -> bool:
        return bool(self._client.exists(self._key(session_id)))
