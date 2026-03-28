package io.ics.runtime.backends;

/**
 * CRUD interface for session state storage.
 *
 * <p>Implementations:
 * <ul>
 *   <li>{@link MemoryBackend}  — in-process, thread-safe (default)</li>
 *   <li>{@link SQLiteBackend}  — persistent, file-based or in-memory</li>
 * </ul>
 */
public interface SessionBackend {

    /** Load session state, or {@code null} if not found. */
    SessionData load(String sessionId);

    /** Persist (create or overwrite) session state. */
    void save(String sessionId, SessionData data);

    /** Delete session state (no-op if not found). */
    void delete(String sessionId);

    /** Return true if the session exists in the store. */
    boolean exists(String sessionId);
}
