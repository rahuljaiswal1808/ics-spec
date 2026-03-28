package io.ics.runtime.backends;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.sql.*;
import java.time.Instant;
import java.util.List;
import java.util.Map;

/**
 * SQLite-backed session storage.
 *
 * <p>Pass {@code ":memory:"} as the path for an in-process SQLite database
 * (uses a persistent connection so tables survive between calls, matching the
 * Python implementation's fix for the same problem).
 *
 * <p>Uses the JDBC SQLite driver bundled with the Anthropic/OpenAI SDKs via
 * the transitive dependency {@code org.xerial:sqlite-jdbc}.  Add it explicitly
 * if needed:
 * <pre>{@code
 * <dependency>
 *   <groupId>org.xerial</groupId>
 *   <artifactId>sqlite-jdbc</artifactId>
 *   <version>3.46.0.0</version>
 * </dependency>
 * }</pre>
 */
public final class SQLiteBackend implements SessionBackend {

    private static final ObjectMapper JSON = new ObjectMapper();

    private final String path;
    private final Connection persistentConn;   // non-null only for ":memory:"

    public SQLiteBackend(String path) {
        this.path = path;
        if (":memory:".equals(path)) {
            try {
                persistentConn = DriverManager.getConnection("jdbc:sqlite::memory:");
                initDb(persistentConn);
            } catch (SQLException e) {
                throw new RuntimeException("Failed to initialise in-memory SQLite", e);
            }
        } else {
            persistentConn = null;
            try (Connection c = connect()) {
                initDb(c);
            } catch (SQLException e) {
                throw new RuntimeException("Failed to initialise SQLite at " + path, e);
            }
        }
    }

    // ── SessionBackend ────────────────────────────────────────────────────────

    @Override
    public SessionData load(String sessionId) {
        String sql = "SELECT entries, context, cleared, created_at, last_active, turn_count " +
                     "FROM sessions WHERE session_id = ?";
        try (Connection c = connect();
             PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setString(1, sessionId);
            try (ResultSet rs = ps.executeQuery()) {
                if (!rs.next()) return null;

                SessionData data = new SessionData(sessionId);
                String entriesJson  = rs.getString("entries");
                String contextJson  = rs.getString("context");
                data.setCleared(rs.getBoolean("cleared"));
                data.setLastActive(Instant.parse(rs.getString("last_active")));
                // Restore turn_count via reflection-free approach: increment externally
                int tc = rs.getInt("turn_count");
                for (int i = 0; i < tc; i++) data.incrementTurnCount();

                if (entriesJson != null) {
                    List<String> entries = JSON.readValue(entriesJson, new TypeReference<>(){});
                    entries.forEach(data::addEntry);
                }
                if (contextJson != null) {
                    Map<String, Object> ctx = JSON.readValue(contextJson, new TypeReference<>(){});
                    data.getContext().putAll(ctx);
                }
                return data;
            }
        } catch (Exception e) {
            throw new RuntimeException("SQLite load failed for " + sessionId, e);
        }
    }

    @Override
    public void save(String sessionId, SessionData data) {
        String sql = "INSERT INTO sessions (session_id, entries, context, cleared, created_at, last_active, turn_count) " +
                     "VALUES (?, ?, ?, ?, ?, ?, ?) " +
                     "ON CONFLICT(session_id) DO UPDATE SET " +
                     "entries=excluded.entries, context=excluded.context, " +
                     "cleared=excluded.cleared, last_active=excluded.last_active, " +
                     "turn_count=excluded.turn_count";
        try (Connection c = connect();
             PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setString(1, sessionId);
            ps.setString(2, JSON.writeValueAsString(data.getEntries()));
            ps.setString(3, JSON.writeValueAsString(data.getContext()));
            ps.setBoolean(4, data.isCleared());
            ps.setString(5, data.getCreatedAt().toString());
            ps.setString(6, Instant.now().toString());
            ps.setInt(7, data.getTurnCount());
            ps.executeUpdate();
        } catch (Exception e) {
            throw new RuntimeException("SQLite save failed for " + sessionId, e);
        }
    }

    @Override
    public void delete(String sessionId) {
        try (Connection c = connect();
             PreparedStatement ps = c.prepareStatement("DELETE FROM sessions WHERE session_id = ?")) {
            ps.setString(1, sessionId);
            ps.executeUpdate();
        } catch (SQLException e) {
            throw new RuntimeException("SQLite delete failed for " + sessionId, e);
        }
    }

    @Override
    public boolean exists(String sessionId) {
        try (Connection c = connect();
             PreparedStatement ps = c.prepareStatement(
                     "SELECT 1 FROM sessions WHERE session_id = ?")) {
            ps.setString(1, sessionId);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next();
            }
        } catch (SQLException e) {
            return false;
        }
    }

    // ── Helpers ──────────────────────────────────────────────────────────────

    private Connection connect() throws SQLException {
        if (persistentConn != null) return persistentConn;
        return DriverManager.getConnection("jdbc:sqlite:" + path);
    }

    private static void initDb(Connection c) throws SQLException {
        try (Statement st = c.createStatement()) {
            st.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id  TEXT PRIMARY KEY,
                    entries     TEXT NOT NULL DEFAULT '[]',
                    context     TEXT NOT NULL DEFAULT '{}',
                    cleared     INTEGER NOT NULL DEFAULT 0,
                    created_at  TEXT NOT NULL,
                    last_active TEXT NOT NULL,
                    turn_count  INTEGER NOT NULL DEFAULT 0
                )""");
        }
    }
}
