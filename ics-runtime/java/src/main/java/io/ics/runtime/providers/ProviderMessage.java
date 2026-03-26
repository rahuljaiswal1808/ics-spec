package io.ics.runtime.providers;

import java.util.List;
import java.util.Map;

/**
 * Normalised conversation message — provider-agnostic.
 *
 * <p>{@code content} may be:
 * <ul>
 *   <li>A {@link String} — simple text message</li>
 *   <li>A {@code List<Map<String,Object>>} — multi-part blocks
 *       (tool_use, tool_result, image, etc.)</li>
 * </ul>
 */
public final class ProviderMessage {

    private final String role;     // "user" | "assistant"
    private final Object content;  // String | List<Map<String,Object>>
    /** OpenAI tool_calls field on assistant messages. */
    private final List<Map<String, Object>> toolCalls;

    public ProviderMessage(String role, String content) {
        this.role      = role;
        this.content   = content;
        this.toolCalls = List.of();
    }

    public ProviderMessage(String role, List<Map<String, Object>> contentBlocks) {
        this.role      = role;
        this.content   = contentBlocks;
        this.toolCalls = List.of();
    }

    public ProviderMessage(String role, String content, List<Map<String, Object>> toolCalls) {
        this.role      = role;
        this.content   = content;
        this.toolCalls = toolCalls == null ? List.of() : List.copyOf(toolCalls);
    }

    public String getRole()                          { return role; }
    public Object getContent()                       { return content; }
    public List<Map<String, Object>> getToolCalls()  { return toolCalls; }

    /** True if content is a plain string (not a block list). */
    public boolean isTextContent() { return content instanceof String; }
    public String getTextContent() { return (String) content; }

    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> getBlockContent() {
        return (List<Map<String, Object>>) content;
    }
}
