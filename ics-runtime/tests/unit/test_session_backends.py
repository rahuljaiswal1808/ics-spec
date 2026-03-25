"""Unit tests for session backends."""

from ics_runtime.session_backends.memory import MemoryBackend
from ics_runtime.session_backends.base import SessionData


def _make_data(session_id: str) -> SessionData:
    from datetime import datetime, timezone
    return SessionData(
        session_id=session_id,
        entries=["Turn 1: hello"],
        context={"lead_id": "L-1"},
        created_at=datetime.now(timezone.utc),
        last_active=datetime.now(timezone.utc),
    )


def test_memory_backend_save_and_load():
    backend = MemoryBackend()
    data = _make_data("sess-1")
    backend.save("sess-1", data)
    loaded = backend.load("sess-1")
    assert loaded is not None
    assert loaded.session_id == "sess-1"
    assert loaded.entries == ["Turn 1: hello"]


def test_memory_backend_exists():
    backend = MemoryBackend()
    assert not backend.exists("sess-x")
    backend.save("sess-x", _make_data("sess-x"))
    assert backend.exists("sess-x")


def test_memory_backend_delete():
    backend = MemoryBackend()
    backend.save("sess-2", _make_data("sess-2"))
    backend.delete("sess-2")
    assert not backend.exists("sess-2")
    assert backend.load("sess-2") is None


def test_memory_backend_load_missing_returns_none():
    backend = MemoryBackend()
    assert backend.load("nonexistent") is None


def test_memory_backend_thread_safety():
    import threading
    backend = MemoryBackend()
    errors: list[Exception] = []

    def write_session(i: int) -> None:
        try:
            sid = f"sess-{i}"
            backend.save(sid, _make_data(sid))
            loaded = backend.load(sid)
            assert loaded is not None
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=write_session, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Thread safety errors: {errors}"
    assert len(backend.all_sessions()) == 50
