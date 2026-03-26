package io.ics.runtime;

import io.ics.runtime.backends.MemoryBackend;
import io.ics.runtime.backends.SessionBackend;
import io.ics.runtime.contracts.CapabilityEnforcer;
import io.ics.runtime.contracts.OutputContract;
import io.ics.runtime.prompt.PromptBuilder;
import io.ics.runtime.providers.AnthropicProvider;
import io.ics.runtime.providers.OpenAIProvider;
import io.ics.runtime.providers.ProviderBase;
import io.ics.runtime.tools.ToolDefinition;
import io.ics.runtime.tools.ToolRegistry;

import java.util.*;

/**
 * Top-level developer-facing object for ICS Runtime Java.
 *
 * <p>The Agent holds the static ICS configuration (immutable context, capability
 * declarations, tools, output contract) and spawns {@link Session} instances for
 * each conversation.  The Agent is stateless — all mutable state lives in the
 * Session.
 *
 * <p>Build via {@link Builder}:
 *
 * <pre>{@code
 * Agent agent = Agent.builder()
 *     .provider("anthropic")
 *     .immutable("You are a BFSI lead qualification assistant. ...")
 *     .capability("DENY: logging PII\nREQUIRE: risk category in every result")
 *     .model("claude-sonnet-4-6")
 *     .tool(crmLookupTool)
 *     .tool(eligibilityCheckTool)
 *     .outputContract(contract)
 *     .build();
 *
 * // One-shot run
 * RunResult r = agent.run("Qualify lead L-001");
 *
 * // Multi-turn session
 * try (Session session = agent.session(Map.of("lead_id", "L-001"))) {
 *     RunResult r1 = session.run("Qualify this lead");
 *     RunResult r2 = session.run("Explain the risk score");
 * }
 * }</pre>
 */
public final class Agent {

    private static final Map<String, String> DEFAULT_MODELS = Map.of(
            "anthropic", "claude-sonnet-4-6",
            "openai",    "gpt-4o"
    );

    private final String providerName;
    private final String immutable;
    private final String capability;
    private final String model;
    private final OutputContract outputContract;
    private final SessionBackend backend;
    private final ToolRegistry registry;
    private final ProviderBase provider;
    private final PromptBuilder promptBuilder;
    private final CapabilityEnforcer capabilityEnforcer;

    private Agent(Builder b) {
        this.providerName  = b.provider;
        this.immutable     = b.immutable != null ? b.immutable : b.system != null ? b.system : "";
        this.capability    = b.capability != null ? b.capability : "";
        this.model         = b.model != null ? b.model : DEFAULT_MODELS.getOrDefault(b.provider, "claude-sonnet-4-6");
        this.outputContract = b.outputContract;
        this.backend       = b.backend != null ? b.backend : new MemoryBackend();
        this.registry      = b.tools.isEmpty() ? null : new ToolRegistry(b.tools);
        this.provider      = buildProvider(b.provider, this.model, b.apiKey);
        this.promptBuilder = new PromptBuilder(b.provider);
        this.capabilityEnforcer = this.capability.isBlank() ? null : new CapabilityEnforcer(this.capability);
    }

    // ── Public API ───────────────────────────────────────────────────────────

    /**
     * Open a session and return it as an {@link AutoCloseable} (no-op close).
     *
     * @param sessionVars Variables injected into SESSION_STATE on the first turn
     */
    public Session session(Map<String, Object> sessionVars) {
        String sessionId = UUID.randomUUID().toString();
        return new Session(this, sessionId, sessionVars);
    }

    /** Convenience — open a session with no initial session vars. */
    public Session session() { return session(Map.of()); }

    /**
     * One-shot convenience — open a session, run one task, return the result.
     *
     * @param task        The user instruction
     * @param sessionVars Optional session context variables
     */
    public RunResult run(String task, Map<String, Object> sessionVars) {
        try (Session s = session(sessionVars)) {
            return s.run(task);
        }
    }

    /** Convenience — one-shot run with no session vars. */
    public RunResult run(String task) { return run(task, Map.of()); }

    // ── Package-visible accessors for Session ────────────────────────────────

    String getProviderName()               { return providerName; }
    String getImmutable()                  { return immutable; }
    String getCapability()                 { return capability; }
    String getModel()                      { return model; }
    OutputContract getOutputContract()     { return outputContract; }
    SessionBackend getBackend()            { return backend; }
    ToolRegistry getRegistry()             { return registry; }
    ProviderBase getProvider()             { return provider; }
    PromptBuilder getPromptBuilder()       { return promptBuilder; }
    CapabilityEnforcer getCapabilityEnforcer() { return capabilityEnforcer; }

    // ── Provider factory ─────────────────────────────────────────────────────

    private static ProviderBase buildProvider(String name, String model, String apiKey) {
        return switch (name) {
            case "anthropic" -> new AnthropicProvider(model, apiKey);
            case "openai"    -> new OpenAIProvider(model, apiKey);
            default -> throw new ICSRuntimeException(
                    "Unknown provider '" + name + "'. Supported: 'anthropic', 'openai'.");
        };
    }

    // ── Builder ──────────────────────────────────────────────────────────────

    public static Builder builder() { return new Builder(); }

    public static final class Builder {
        private String provider  = "anthropic";
        private String system    = null;
        private String immutable = null;
        private String capability = null;
        private String model     = null;
        private String apiKey    = null;
        private OutputContract outputContract = null;
        private SessionBackend backend = null;
        private final List<ToolDefinition> tools = new ArrayList<>();

        public Builder provider(String v)              { this.provider = v;   return this; }
        /** Alias for {@link #immutable(String)}. */
        public Builder system(String v)                { this.system = v;     return this; }
        public Builder immutable(String v)             { this.immutable = v;  return this; }
        public Builder capability(String v)            { this.capability = v; return this; }
        public Builder model(String v)                 { this.model = v;      return this; }
        public Builder apiKey(String v)                { this.apiKey = v;     return this; }
        public Builder outputContract(OutputContract v){ this.outputContract = v; return this; }
        public Builder backend(SessionBackend v)       { this.backend = v;    return this; }
        public Builder tool(ToolDefinition v)          { tools.add(v);        return this; }
        public Builder tools(List<ToolDefinition> v)   { tools.addAll(v);     return this; }
        public Agent build()                           { return new Agent(this); }
    }
}
