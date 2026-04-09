package io.ics.runtime.providers;

import java.util.List;
import java.util.Map;

/**
 * Normalised response from a provider completion call — provider-agnostic.
 *
 * <p>Each element of {@code toolCalls} is a map with keys:
 * <ul>
 *   <li>{@code id}    — provider-assigned call ID</li>
 *   <li>{@code name}  — tool name (sanitized, as the provider sees it)</li>
 *   <li>{@code input} — {@code Map<String,Object>} of parsed arguments</li>
 * </ul>
 */
public final class ProviderResponse {

    private final String text;
    private final int inputTokens;
    private final int outputTokens;
    private final int cacheCreationTokens;
    private final int cacheReadTokens;
    private final List<Map<String, Object>> toolCalls;
    private final Object raw;

    public ProviderResponse(
            String text,
            int inputTokens,
            int outputTokens,
            int cacheCreationTokens,
            int cacheReadTokens,
            List<Map<String, Object>> toolCalls,
            Object raw) {
        this.text                = text;
        this.inputTokens         = inputTokens;
        this.outputTokens        = outputTokens;
        this.cacheCreationTokens = cacheCreationTokens;
        this.cacheReadTokens     = cacheReadTokens;
        this.toolCalls           = toolCalls == null ? List.of() : List.copyOf(toolCalls);
        this.raw                 = raw;
    }

    public String getText()                          { return text; }
    public int getInputTokens()                      { return inputTokens; }
    public int getOutputTokens()                     { return outputTokens; }
    public int getCacheCreationTokens()              { return cacheCreationTokens; }
    public int getCacheReadTokens()                  { return cacheReadTokens; }
    public List<Map<String, Object>> getToolCalls()  { return toolCalls; }
    public Object getRaw()                           { return raw; }

    /** True when cache_read_tokens > 0 (prompt cache was hit). */
    public boolean isCacheHit() { return cacheReadTokens > 0; }
}
