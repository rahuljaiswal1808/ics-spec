package io.ics.runtime;

import io.ics.runtime.backends.SessionData;
import io.ics.runtime.contracts.CapabilityEnforcer;
import io.ics.runtime.contracts.ValidationOutcome;
import io.ics.runtime.observability.SessionMetrics;
import io.ics.runtime.providers.ProviderMessage;
import io.ics.runtime.providers.ProviderResponse;
import io.ics.runtime.tools.ToolDeniedException;

import java.time.Instant;
import java.util.*;

/**
 * A single conversation session.
 *
 * <p>Do not instantiate directly — use {@link Agent#session(Map)} or
 * {@link Agent#run(String, Map)}.
 *
 * <p>A session holds mutable conversation state (history entries, turn count)
 * persisted via the configured {@link io.ics.runtime.backends.SessionBackend}.
 * The {@link Agent} itself is stateless.
 */
public final class Session implements AutoCloseable {

    // Pricing per 1M tokens (USD) — in / out / cache_write / cache_read
    private static final Map<String, double[]> PRICING = new HashMap<>();
    static {
        //                                                in      out     cw      cr
        PRICING.put("claude-opus-4-6",            new double[]{15.0,  75.0,  18.75, 1.50});
        PRICING.put("claude-sonnet-4-6",          new double[]{ 3.0,  15.0,   3.75, 0.30});
        PRICING.put("claude-3-5-sonnet-20241022", new double[]{ 3.0,  15.0,   3.75, 0.30});
        PRICING.put("claude-3-5-haiku-20241022",  new double[]{ 0.80,  4.0,   1.0,  0.08});
        PRICING.put("gpt-4o",                     new double[]{ 2.50, 10.0,   0.0,  1.25});
        PRICING.put("gpt-4o-mini",                new double[]{ 0.15,  0.60,  0.0,  0.075});
        PRICING.put("o1",                         new double[]{15.0,  60.0,   0.0,  7.50});
    }
    private static final double[] FALLBACK_PRICE = {3.0, 15.0, 3.75, 0.30};

    private final Agent agent;
    private final String sessionId;
    private final SessionMetrics metrics;
    private boolean pendingClear = false;

    Session(Agent agent, String sessionId, Map<String, Object> sessionVars) {
        this.agent     = agent;
        this.sessionId = sessionId;
        this.metrics   = new SessionMetrics();

        if (!agent.getBackend().exists(sessionId)) {
            SessionData data = new SessionData(sessionId, sessionVars);
            agent.getBackend().save(sessionId, data);
        }
    }

    // ── Public API ───────────────────────────────────────────────────────────

    /**
     * Execute one turn and return a {@link RunResult}.
     *
     * @param task  The user instruction for this turn
     * @param maxToolRounds Maximum tool-call rounds before giving up (default 10)
     */
    public RunResult run(String task, int maxToolRounds) {
        long t0 = System.currentTimeMillis();

        SessionData data = agent.getBackend().load(sessionId);
        if (data == null) throw new ICSRuntimeException("Session '" + sessionId + "' not found");

        // Build SESSION_STATE text
        String sessionState = buildSessionState(data);

        // Output contract ICS text
        String ocText = agent.getOutputContract() != null
                ? agent.getOutputContract().toIcsText() : "";

        // Build system blocks
        List<Map<String, Object>> systemBlocks = agent.getPromptBuilder().buildSystem(
                agent.getImmutable(), agent.getCapability(), sessionState, ocText);

        // Initial messages
        List<ProviderMessage> messages = new ArrayList<>();
        messages.add(new ProviderMessage("user", task));

        // Provider-formatted tools
        List<Map<String, Object>> tools = agent.getRegistry() != null
                ? agent.getRegistry().toProviderTools(agent.getProviderName()) : null;

        // Tool loop
        List<ToolCallRecord> allToolCalls = new ArrayList<>();
        ProviderResponse provResponse = null;

        for (int round = 0; round <= maxToolRounds; round++) {
            provResponse = agent.getProvider().complete(systemBlocks, messages, tools, 4096);

            if (provResponse.getToolCalls().isEmpty()) break;  // final text response

            if (round >= maxToolRounds) {
                throw new ICSRuntimeException(
                        "Max tool rounds (" + maxToolRounds + ") exceeded without final response");
            }

            // Execute tool calls
            List<Map<String, Object>> assistantBlocks = new ArrayList<>();
            List<ToolCallRecord> roundCalls = new ArrayList<>();

            for (Map<String, Object> tc : provResponse.getToolCalls()) {
                String id   = (String) tc.get("id");
                String name = (String) tc.get("name");
                @SuppressWarnings("unchecked")
                Map<String, Object> input = (Map<String, Object>) tc.get("input");

                long tcStart = System.currentTimeMillis();
                ToolCallRecord record = executeToolCall(name, input != null ? input : Map.of());
                int tcMs = (int)(System.currentTimeMillis() - tcStart);
                roundCalls.add(record);
                allToolCalls.add(record);

                if ("anthropic".equals(agent.getProviderName())) {
                    Map<String, Object> block = new LinkedHashMap<>();
                    block.put("type",  "tool_use");
                    block.put("id",    id);
                    block.put("name",  name);
                    block.put("input", input);
                    assistantBlocks.add(block);
                }
            }

            // Append assistant message with tool_use blocks
            if ("anthropic".equals(agent.getProviderName())) {
                messages.add(new ProviderMessage("assistant", assistantBlocks));
            } else {
                // OpenAI: assistant message with tool_calls field
                messages.add(new ProviderMessage("assistant", "", provResponse.getToolCalls()));
            }

            // Append tool results
            for (int i = 0; i < provResponse.getToolCalls().size(); i++) {
                Map<String, Object> tc = provResponse.getToolCalls().get(i);
                ToolCallRecord rec     = roundCalls.get(i);
                String id              = (String) tc.get("id");
                String resultStr       = rec.isBlocked()
                        ? "BLOCKED: tool '" + rec.getToolName() + "' denied"
                        : jsonSerialize(rec.getOutput());
                messages.add(agent.getProvider().toolResultMessage(id, resultStr));
            }
        }

        // Post-execution enforcement
        String responseText = provResponse != null ? provResponse.getText() : "";
        List<Violation> violations = new ArrayList<>();
        boolean validated = true;
        Object parsed = null;

        CapabilityEnforcer enforcer = agent.getCapabilityEnforcer();
        if (enforcer != null) {
            violations.addAll(enforcer.scanOutput(responseText));
        }

        if (agent.getOutputContract() != null) {
            ValidationOutcome outcome = agent.getOutputContract().validate(responseText);
            validated = outcome.isPassed();
            parsed    = outcome.getParsed();
            violations.addAll(outcome.getViolations());
        }

        // Cost
        int inTok = provResponse != null ? provResponse.getInputTokens()         : 0;
        int outTok= provResponse != null ? provResponse.getOutputTokens()        : 0;
        int cwTok = provResponse != null ? provResponse.getCacheCreationTokens() : 0;
        int crTok = provResponse != null ? provResponse.getCacheReadTokens()     : 0;
        double cost = estimateCost(agent.getModel(), inTok, outTok, cwTok, crTok);

        int latencyMs = (int)(System.currentTimeMillis() - t0);

        // Update session state
        data.incrementTurnCount();
        int turnNumber = data.getTurnCount();
        String entry = "[" + Instant.now().toString().substring(0, 19) + "Z] Turn " + turnNumber
                       + ": " + task.substring(0, Math.min(80, task.length()));
        if (pendingClear) {
            data.clearEntries();
            data.setCleared(true);
            pendingClear = false;
        }
        data.addEntry(entry);
        data.setCleared(false);
        agent.getBackend().save(sessionId, data);

        RunResult result = RunResult.builder()
                .text(responseText)
                .validated(validated)
                .violations(violations)
                .parsed(parsed)
                .cacheHit(provResponse != null && provResponse.isCacheHit())
                .cacheWrite(cwTok > 0)
                .tokensSaved(crTok)
                .inputTokens(inTok)
                .outputTokens(outTok)
                .cacheWriteTokens(cwTok)
                .toolCalls(allToolCalls)
                .sessionId(sessionId)
                .turnNumber(turnNumber)
                .latencyMs(latencyMs)
                .model(agent.getModel())
                .provider(agent.getProviderName())
                .costUsd(cost)
                .build();

        metrics.record(result);
        return result;
    }

    /** Convenience overload with default 10 tool rounds. */
    public RunResult run(String task) { return run(task, 10); }

    /**
     * Mark session for CLEAR on the next run (ICS §3.3 semantics).
     * The history and context will be wiped at the start of the next turn.
     */
    public void clear() { pendingClear = true; }

    public String getSessionId() { return sessionId; }

    public int getTurnCount() {
        SessionData data = agent.getBackend().load(sessionId);
        return data != null ? data.getTurnCount() : 0;
    }

    public SessionMetrics getMetrics() { return metrics; }

    @Override
    public void close() { /* sessions are not auto-deleted; re-open via sessionId */ }

    // ── Internal helpers ─────────────────────────────────────────────────────

    private String buildSessionState(SessionData data) {
        List<String> parts = new ArrayList<>();
        if (data.isCleared()) parts.add("###CLEAR###");
        if (!data.getContext().isEmpty()) {
            StringBuilder ctx = new StringBuilder("Context:\n");
            data.getContext().forEach((k, v) -> ctx.append(k).append(": ").append(v).append("\n"));
            parts.add(ctx.toString().trim());
        }
        if (!data.getEntries().isEmpty()) {
            List<String> recent = data.getEntries();
            recent = recent.subList(Math.max(0, recent.size() - 20), recent.size());
            parts.add("History:\n" + String.join("\n", recent));
        }
        return String.join("\n\n", parts);
    }

    private ToolCallRecord executeToolCall(String name, Map<String, Object> input) {
        if (agent.getRegistry() == null) {
            return new ToolCallRecord(name, input,
                    "Error: no tool registry configured", 0, true);
        }
        try {
            long t0 = System.currentTimeMillis();
            Object output = agent.getRegistry().dispatch(name, input);
            int ms = (int)(System.currentTimeMillis() - t0);
            return new ToolCallRecord(name, input, output, ms, false);
        } catch (ToolDeniedException e) {
            return new ToolCallRecord(name, input, "BLOCKED: " + e.getReason(), 0, true);
        } catch (Exception e) {
            return new ToolCallRecord(name, input, "Error: " + e.getMessage(), 0, false);
        }
    }

    private static double estimateCost(String model, int in, int out, int cw, int cr) {
        double[] p = PRICING.getOrDefault(model, FALLBACK_PRICE);
        return in * p[0] / 1_000_000.0
             + out * p[1] / 1_000_000.0
             + cw  * p[2] / 1_000_000.0
             + cr  * p[3] / 1_000_000.0;
    }

    private static String jsonSerialize(Object obj) {
        if (obj == null) return "null";
        if (obj instanceof String s) return s;
        try {
            return new com.fasterxml.jackson.databind.ObjectMapper().writeValueAsString(obj);
        } catch (Exception e) {
            return obj.toString();
        }
    }
}
