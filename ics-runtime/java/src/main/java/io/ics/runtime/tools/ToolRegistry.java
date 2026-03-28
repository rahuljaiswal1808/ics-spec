package io.ics.runtime.tools;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.ics.runtime.ICSRuntimeException;

import java.util.*;
import java.util.regex.Pattern;

/**
 * Registry — maps {@link ToolDefinition}s to provider wire formats and
 * dispatches tool calls from the model back to Java handlers.
 *
 * <p>Tool names may contain dots (e.g. {@code crm.lookup}), which are sanitised
 * to double-underscores when sent to the provider API (both Anthropic and OpenAI
 * require {@code ^[a-zA-Z0-9_-]+$}).  The registry maintains a reverse map for
 * O(1) dispatch.
 */
public final class ToolRegistry {

    private static final Pattern SANITISE_PATTERN = Pattern.compile("[^a-zA-Z0-9_\\-]");
    private static final ObjectMapper JSON = new ObjectMapper();

    private final Map<String, ToolDefinition> tools = new LinkedHashMap<>();   // ics_name → def
    private final Map<String, String> wireNameMap   = new LinkedHashMap<>();   // wire_name → ics_name

    public ToolRegistry(List<ToolDefinition> defs) {
        for (ToolDefinition def : defs) {
            tools.put(def.getName(), def);
            wireNameMap.put(sanitise(def.getName()), def.getName());
        }
    }

    // ── Provider schema generation ───────────────────────────────────────────

    public List<Map<String, Object>> toProviderTools(String provider) {
        return "anthropic".equals(provider) ? toAnthropicTools() : toOpenAITools();
    }

    private List<Map<String, Object>> toAnthropicTools() {
        List<Map<String, Object>> result = new ArrayList<>();
        for (ToolDefinition def : tools.values()) {
            Map<String, Object> t = new LinkedHashMap<>();
            t.put("name",         sanitise(def.getName()));
            t.put("description",  def.getDescription());
            t.put("input_schema", def.toJsonSchema());
            result.add(t);
        }
        return result;
    }

    private List<Map<String, Object>> toOpenAITools() {
        List<Map<String, Object>> result = new ArrayList<>();
        for (ToolDefinition def : tools.values()) {
            Map<String, Object> fn = new LinkedHashMap<>();
            fn.put("name",        sanitise(def.getName()));
            fn.put("description", def.getDescription());
            fn.put("parameters",  def.toJsonSchema());

            Map<String, Object> t = new LinkedHashMap<>();
            t.put("type",     "function");
            t.put("function", fn);
            result.add(t);
        }
        return result;
    }

    // ── Dispatch ─────────────────────────────────────────────────────────────

    /**
     * Look up and invoke a tool by its wire (sanitised) or ICS name.
     *
     * @param name The name as received from the provider (may be sanitised)
     * @param args Parsed arguments from the provider
     * @return The tool's return value
     * @throws ToolDeniedException  if a deny flag blocks the call
     * @throws ICSRuntimeException  if the tool is not registered
     */
    public Object dispatch(String name, Map<String, Object> args) {
        // Resolve sanitised → ICS name
        String icsName = wireNameMap.getOrDefault(name, name);
        ToolDefinition def = tools.get(icsName);
        if (def == null) {
            throw new ICSRuntimeException("Tool '" + name + "' is not registered.");
        }

        // Enforce deny flags
        if (def.isDenyBulkExport()) {
            for (Map.Entry<String, Object> e : args.entrySet()) {
                Object v = e.getValue();
                if (v instanceof List<?> list && list.size() > 50) {
                    throw new ToolDeniedException(icsName,
                            "deny_bulk_export: field '" + e.getKey() + "' has " + list.size() + " items (max 50)");
                }
                if (v instanceof String s) {
                    for (String wc : new String[]{"*", "%", "all", "ALL"}) {
                        if (s.contains(wc)) {
                            throw new ToolDeniedException(icsName,
                                    "deny_bulk_export: wildcard '" + wc + "' in field '" + e.getKey() + "'");
                        }
                    }
                }
            }
        }

        return def.invoke(args);
    }

    public List<String> getNames() { return List.copyOf(tools.keySet()); }

    // ── Helpers ──────────────────────────────────────────────────────────────

    public static String sanitise(String name) {
        return SANITISE_PATTERN.matcher(name).replaceAll("__");
    }
}
