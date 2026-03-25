"""Unit tests for SQLiteBackend."""

import pytest
from ics_runtime.session_backends.sqlite_backend import SQLiteBackend
from ics_runtime.session_backends.base import SessionData
from datetime import datetime, timezone


def _data(sid: str) -> SessionData:
    now = datetime.now(timezone.utc)
    return SessionData(
        session_id=sid,
        entries=["Turn 1: hello"],
        context={"lead_id": "L-1"},
        created_at=now,
        last_active=now,
    )


@pytest.fixture()
def backend(tmp_path):
    return SQLiteBackend(db_path=tmp_path / "test.db")


def test_save_and_load(backend):
    backend.save("s1", _data("s1"))
    loaded = backend.load("s1")
    assert loaded is not None
    assert loaded.session_id == "s1"
    assert loaded.entries == ["Turn 1: hello"]
    assert loaded.context == {"lead_id": "L-1"}


def test_exists(backend):
    assert not backend.exists("s2")
    backend.save("s2", _data("s2"))
    assert backend.exists("s2")


def test_delete(backend):
    backend.save("s3", _data("s3"))
    backend.delete("s3")
    assert not backend.exists("s3")
    assert backend.load("s3") is None


def test_update_overwrites(backend):
    d = _data("s4")
    backend.save("s4", d)
    d.entries.append("Turn 2: world")
    d.turn_count = 2
    backend.save("s4", d)
    loaded = backend.load("s4")
    assert loaded.turn_count == 2
    assert len(loaded.entries) == 2


def test_in_memory_db():
    backend = SQLiteBackend(db_path=":memory:")
    backend.save("sm", _data("sm"))
    assert backend.load("sm") is not None
