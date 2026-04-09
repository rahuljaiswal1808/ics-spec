package io.ics.runtime.providers;

import java.util.List;
import java.util.Map;

/**
 * Abstract base for LLM provider adapters.
 *
 * <p>A provider is a thin adapter that translates ICS-normalised types into
 * SDK-specific API calls and maps responses back to {@link ProviderResponse}.
 * Implementations are synchronous; wrap with virtual threads / executors for
 * concurrency.
 */
public abstract class ProviderBase {

    protected final String model;

    protected ProviderBase(String model) {
        this.model = model;
    }

    public String getModel() { return model; }

    /**
     * Send a completion request to the provider.
     *
     * @param systemBlocks Provider-formatted system content (from {@link io.ics.runtime.prompt.PromptBuilder})
     * @param messages     Conversation history
     * @param tools        Provider-formatted tool definitions, or {@code null}
     * @param maxTokens    Maximum tokens to generate
     * @return Normalised {@link ProviderResponse}
     */
    public abstract ProviderResponse complete(
            List<Map<String, Object>> systemBlocks,
            List<ProviderMessage> messages,
            List<Map<String, Object>> tools,
            int maxTokens
    );

    /**
     * Build the tool-result message format expected by this provider.
     *
     * @param toolCallId The ID returned by the provider for the tool_use block
     * @param result     Serialized result string
     * @return A {@link ProviderMessage} to append to the conversation history
     */
    public abstract ProviderMessage toolResultMessage(String toolCallId, String result);
}
