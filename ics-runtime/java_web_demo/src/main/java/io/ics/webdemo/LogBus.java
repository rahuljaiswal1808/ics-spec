package io.ics.webdemo;

import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.concurrent.ConcurrentLinkedDeque;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ConcurrentLinkedQueue;

/**
 * Thread-safe log event bus.
 *
 * <p>Mirrors the Python web_demo log bus ({@code _LOG_HISTORY} / {@code _LOG_SUBS}).
 * Any component may call {@code LogBus.emit()} to broadcast a log entry to all
 * active SSE subscribers and to the persistent history ring-buffer.
 */
public final class LogBus {

    private static final int MAX_HISTORY = 300;
    private static final DateTimeFormatter TS_FMT =
            DateTimeFormatter.ofPattern("HH:mm:ss.SSS").withZone(ZoneOffset.UTC);

    /** Immutable log entry. */
    public record LogEntry(String ts, String level, String msg) {}

    /** Ring-buffer of recent log entries (replayed to new SSE log subscribers). */
    private static final Deque<LogEntry> HISTORY = new ConcurrentLinkedDeque<>();

    /** Active SSE subscriber queues — one per connected client. */
    private static final List<Queue<LogEntry>> SUBSCRIBERS = new CopyOnWriteArrayList<>();

    private LogBus() {}

    // ── Emit ─────────────────────────────────────────────────────────────────

    public static void info(String msg)  { emit("info",  msg); }
    public static void ok(String msg)    { emit("ok",    msg); }
    public static void warn(String msg)  { emit("warn",  msg); }
    public static void error(String msg) { emit("error", msg); }

    public static void emit(String level, String msg) {
        LogEntry entry = new LogEntry(TS_FMT.format(Instant.now()), level, msg);

        // Maintain ring-buffer
        HISTORY.addLast(entry);
        while (HISTORY.size() > MAX_HISTORY) HISTORY.pollFirst();

        // Fan-out to subscribers
        for (Queue<LogEntry> q : SUBSCRIBERS) {
            q.offer(entry);   // non-blocking; drops if queue is full
        }
    }

    // ── Subscriber management ─────────────────────────────────────────────────

    /** Register a per-client queue. Returns the same queue for convenience. */
    public static Queue<LogEntry> subscribe() {
        Queue<LogEntry> q = new ConcurrentLinkedQueue<>();
        SUBSCRIBERS.add(q);
        return q;
    }

    public static void unsubscribe(Queue<LogEntry> q) {
        SUBSCRIBERS.remove(q);
    }

    /** Snapshot of the history for replay to new log SSE clients. */
    public static List<LogEntry> history() {
        return List.copyOf(HISTORY);
    }
}
