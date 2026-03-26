package io.ics.runtime;

import java.util.List;

/**
 * Structured output from a single {@link Session#run(String)} call.
 *
 * <p>Contains the LLM response together with all observability fields so callers
 * can track caching savings, contract violations, and cost in one place.
 *
 * <p>Build via {@link RunResult.Builder}.
 */
public final class RunResult {

    // Core output
    private final String text;
    private final boolean validated;
    private final List<Violation> violations;
    private final Object parsed;           // Deserialized JSON object (Map or custom class)

    // Cache metrics
    private final boolean cacheHit;
    private final boolean cacheWrite;
    private final int tokensSaved;         // cache_read_tokens (re-billed at discount)

    // Raw token counts
    private final int inputTokens;
    private final int outputTokens;
    private final int cacheWriteTokens;

    // Tool invocations
    private final List<ToolCallRecord> toolCalls;

    // Session context
    private final String sessionId;
    private final int turnNumber;

    // Performance
    private final int latencyMs;
    private final String model;
    private final String provider;

    // Estimated cost (USD)
    private final double costUsd;

    private RunResult(Builder b) {
        this.text             = b.text;
        this.validated        = b.validated;
        this.violations       = List.copyOf(b.violations);
        this.parsed           = b.parsed;
        this.cacheHit         = b.cacheHit;
        this.cacheWrite       = b.cacheWrite;
        this.tokensSaved      = b.tokensSaved;
        this.inputTokens      = b.inputTokens;
        this.outputTokens     = b.outputTokens;
        this.cacheWriteTokens = b.cacheWriteTokens;
        this.toolCalls        = List.copyOf(b.toolCalls);
        this.sessionId        = b.sessionId;
        this.turnNumber       = b.turnNumber;
        this.latencyMs        = b.latencyMs;
        this.model            = b.model;
        this.provider         = b.provider;
        this.costUsd          = b.costUsd;
    }

    // ── Accessors ────────────────────────────────────────────────────────────

    public String getText()                    { return text; }
    public boolean isValidated()               { return validated; }
    public List<Violation> getViolations()     { return violations; }
    public Object getParsed()                  { return parsed; }
    public boolean isCacheHit()                { return cacheHit; }
    public boolean isCacheWrite()              { return cacheWrite; }
    public int getTokensSaved()                { return tokensSaved; }
    public int getInputTokens()                { return inputTokens; }
    public int getOutputTokens()               { return outputTokens; }
    public int getCacheWriteTokens()           { return cacheWriteTokens; }
    public List<ToolCallRecord> getToolCalls() { return toolCalls; }
    public String getSessionId()               { return sessionId; }
    public int getTurnNumber()                 { return turnNumber; }
    public int getLatencyMs()                  { return latencyMs; }
    public String getModel()                   { return model; }
    public String getProvider()                { return provider; }
    public double getCostUsd()                 { return costUsd; }

    /** True if the response is validated and has no violations. */
    public boolean isOk() { return validated && violations.isEmpty(); }

    /**
     * Fluent helper — throws {@link ContractViolationException} if there are violations.
     * Enables: {@code session.run("...").raiseOnViolation()}
     */
    public RunResult raiseOnViolation() {
        if (!violations.isEmpty()) {
            throw new ContractViolationException(violations);
        }
        return this;
    }

    public String summary() {
        StringBuilder sb = new StringBuilder();
        sb.append(String.format("provider=%s model=%s%n", provider, model));
        sb.append(String.format("tokens in=%d out=%d cached=%d write=%d%n",
                inputTokens, outputTokens, tokensSaved, cacheWriteTokens));
        sb.append(String.format("cache_hit=%b validated=%b violations=%d tools=%d%n",
                cacheHit, validated, violations.size(), toolCalls.size()));
        sb.append(String.format("cost=$%.5f latency=%dms", costUsd, latencyMs));
        for (Violation v : violations) {
            sb.append("\n  ⚠  ").append(v);
        }
        return sb.toString();
    }

    @Override
    public String toString() { return summary(); }

    // ── Builder ──────────────────────────────────────────────────────────────

    public static Builder builder() { return new Builder(); }

    public static final class Builder {
        private String text = "";
        private boolean validated = true;
        private List<Violation> violations = List.of();
        private Object parsed = null;
        private boolean cacheHit = false;
        private boolean cacheWrite = false;
        private int tokensSaved = 0;
        private int inputTokens = 0;
        private int outputTokens = 0;
        private int cacheWriteTokens = 0;
        private List<ToolCallRecord> toolCalls = List.of();
        private String sessionId = "";
        private int turnNumber = 0;
        private int latencyMs = 0;
        private String model = "";
        private String provider = "";
        private double costUsd = 0.0;

        public Builder text(String v)                        { this.text = v; return this; }
        public Builder validated(boolean v)                  { this.validated = v; return this; }
        public Builder violations(List<Violation> v)         { this.violations = v; return this; }
        public Builder parsed(Object v)                      { this.parsed = v; return this; }
        public Builder cacheHit(boolean v)                   { this.cacheHit = v; return this; }
        public Builder cacheWrite(boolean v)                 { this.cacheWrite = v; return this; }
        public Builder tokensSaved(int v)                    { this.tokensSaved = v; return this; }
        public Builder inputTokens(int v)                    { this.inputTokens = v; return this; }
        public Builder outputTokens(int v)                   { this.outputTokens = v; return this; }
        public Builder cacheWriteTokens(int v)               { this.cacheWriteTokens = v; return this; }
        public Builder toolCalls(List<ToolCallRecord> v)     { this.toolCalls = v; return this; }
        public Builder sessionId(String v)                   { this.sessionId = v; return this; }
        public Builder turnNumber(int v)                     { this.turnNumber = v; return this; }
        public Builder latencyMs(int v)                      { this.latencyMs = v; return this; }
        public Builder model(String v)                       { this.model = v; return this; }
        public Builder provider(String v)                    { this.provider = v; return this; }
        public Builder costUsd(double v)                     { this.costUsd = v; return this; }

        public RunResult build() { return new RunResult(this); }
    }
}
