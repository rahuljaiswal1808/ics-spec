package io.ics.runtime.backends;

import java.util.concurrent.ConcurrentHashMap;

/**
 * In-process, thread-safe session backend (default).
 *
 * <p>Data is lost when the JVM exits.  Use {@link SQLiteBackend} for persistence.
 */
public final class MemoryBackend implements SessionBackend {

    private final ConcurrentHashMap<String, SessionData> store = new ConcurrentHashMap<>();

    @Override
    public SessionData load(String sessionId) {
        return store.get(sessionId);
    }

    @Override
    public void save(String sessionId, SessionData data) {
        store.put(sessionId, data);
    }

    @Override
    public void delete(String sessionId) {
        store.remove(sessionId);
    }

    @Override
    public boolean exists(String sessionId) {
        return store.containsKey(sessionId);
    }
}
