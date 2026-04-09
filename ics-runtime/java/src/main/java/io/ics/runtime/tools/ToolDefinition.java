package io.ics.runtime.tools;

import java.util.*;
import java.util.function.Function;

/**
 * Defines a single tool available to the agent.
 *
 * <p>Build via the fluent {@link Builder}:
 *
 * <pre>{@code
 * ToolDefinition lookup = ToolDefinition.builder()
 *     .name("crm.lookup")
 *     .description("Look up CRM data for a lead by ID")
 *     .stringParam("lead_id", "Lead identifier, e.g. L-001", true)
 *     .denyBulkExport(true)
 *     .handler(args -> crmLookup((String) args.get("lead_id")))
 *     .build();
 * }</pre>
 *
 * <p>The handler receives a {@code Map<String,Object>} of parsed arguments and
 * returns any JSON-serialisable value.
 */
public final class ToolDefinition {

    /**
     * Supported parameter primitive types for schema generation.
     */
    public enum ParamType { STRING, NUMBER, BOOLEAN, INTEGER, OBJECT, ARRAY }

    /**
     * A single parameter descriptor.
     */
    public static final class Param {
        public final String name;
        public final ParamType type;
        public final String description;
        public final boolean required;

        public Param(String name, ParamType type, String description, boolean required) {
            this.name        = name;
            this.type        = type;
            this.description = description;
            this.required    = required;
        }
    }

    private final String name;
    private final String description;
    private final List<Param> params;
    private final boolean denyBulkExport;
    private final Function<Map<String, Object>, Object> handler;

    private ToolDefinition(Builder b) {
        this.name            = Objects.requireNonNull(b.name, "Tool name is required");
        this.description     = b.description != null ? b.description : "";
        this.params          = List.copyOf(b.params);
        this.denyBulkExport  = b.denyBulkExport;
        this.handler         = Objects.requireNonNull(b.handler, "Tool handler is required");
    }

    public String getName()                { return name; }
    public String getDescription()         { return description; }
    public List<Param> getParams()         { return params; }
    public boolean isDenyBulkExport()      { return denyBulkExport; }

    /** Execute the tool handler with the given argument map. */
    public Object invoke(Map<String, Object> args) { return handler.apply(args); }

    /**
     * Generate JSON Schema {@code {"type":"object","properties":{...},"required":[...]}}
     * for this tool's parameters.
     */
    public Map<String, Object> toJsonSchema() {
        Map<String, Object> properties = new LinkedHashMap<>();
        List<String> required          = new ArrayList<>();

        for (Param p : params) {
            Map<String, Object> prop = new LinkedHashMap<>();
            prop.put("type",        p.type.name().toLowerCase(Locale.ROOT));
            prop.put("description", p.description);
            properties.put(p.name, prop);
            if (p.required) required.add(p.name);
        }

        Map<String, Object> schema = new LinkedHashMap<>();
        schema.put("type",       "object");
        schema.put("properties", properties);
        if (!required.isEmpty()) schema.put("required", required);
        return schema;
    }

    // ── Builder ──────────────────────────────────────────────────────────────

    public static Builder builder() { return new Builder(); }

    public static final class Builder {
        private String name;
        private String description;
        private final List<Param> params = new ArrayList<>();
        private boolean denyBulkExport = false;
        private Function<Map<String, Object>, Object> handler;

        public Builder name(String v)        { this.name = v; return this; }
        public Builder description(String v) { this.description = v; return this; }
        public Builder denyBulkExport(boolean v) { this.denyBulkExport = v; return this; }
        public Builder handler(Function<Map<String, Object>, Object> v) {
            this.handler = v; return this;
        }

        public Builder param(String name, ParamType type, String description, boolean required) {
            params.add(new Param(name, type, description, required));
            return this;
        }

        /** Convenience — add a required STRING param. */
        public Builder stringParam(String name, String description, boolean required) {
            return param(name, ParamType.STRING, description, required);
        }

        /** Convenience — add a NUMBER param. */
        public Builder numberParam(String name, String description, boolean required) {
            return param(name, ParamType.NUMBER, description, required);
        }

        public ToolDefinition build() { return new ToolDefinition(this); }
    }
}
