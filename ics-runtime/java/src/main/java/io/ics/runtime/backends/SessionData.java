package io.ics.runtime.backends;

import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Persisted envelope for a single session.
 *
 * <p>{@code entries} accumulates timestamped SESSION_STATE history lines.
 * {@code cleared} is set by {@code Session.clear()} and reset after the
 * next run emits the CLEAR sentinel into the prompt.
 */
public final class SessionData {

    private final String sessionId;
    private final List<String> entries;
    private final Map<String, Object> context;
    private boolean cleared;
    private final Instant createdAt;
    private Instant lastActive;
    private int turnCount;

    public SessionData(String sessionId) {
        this(sessionId, new HashMap<>());
    }

    public SessionData(String sessionId, Map<String, Object> context) {
        this.sessionId  = sessionId;
        this.entries    = new ArrayList<>();
        this.context    = new HashMap<>(context);
        this.cleared    = false;
        this.createdAt  = Instant.now();
        this.lastActive = Instant.now();
        this.turnCount  = 0;
    }

    public String getSessionId()              { return sessionId; }
    public List<String> getEntries()          { return entries; }
    public Map<String, Object> getContext()   { return context; }
    public boolean isCleared()                { return cleared; }
    public Instant getCreatedAt()             { return createdAt; }
    public Instant getLastActive()            { return lastActive; }
    public int getTurnCount()                 { return turnCount; }

    public void setCleared(boolean cleared)   { this.cleared = cleared; }
    public void setLastActive(Instant t)      { this.lastActive = t; }
    public void incrementTurnCount()          { this.turnCount++; }
    public void addEntry(String entry)        { this.entries.add(entry); }
    public void clearEntries()                { this.entries.clear(); }
}
