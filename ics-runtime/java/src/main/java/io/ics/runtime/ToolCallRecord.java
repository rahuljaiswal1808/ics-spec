package io.ics.runtime;

import java.util.Map;

/**
 * Immutable record of a single tool invocation that occurred during {@link Session#run}.
 */
public final class ToolCallRecord {

    private final String toolName;
    private final Map<String, Object> input;
    private final Object output;
    private final int durationMs;
    private final boolean blocked;

    public ToolCallRecord(
            String toolName,
            Map<String, Object> input,
            Object output,
            int durationMs,
            boolean blocked) {
        this.toolName   = toolName;
        this.input      = input;
        this.output     = output;
        this.durationMs = durationMs;
        this.blocked    = blocked;
    }

    public String getToolName()          { return toolName; }
    public Map<String, Object> getInput(){ return input; }
    public Object getOutput()            { return output; }
    public int getDurationMs()           { return durationMs; }
    public boolean isBlocked()           { return blocked; }

    @Override
    public String toString() {
        return String.format("ToolCall{name=%s, blocked=%b, ms=%d}", toolName, blocked, durationMs);
    }
}
