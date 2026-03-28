package io.ics.runtime.prompt;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Assembles ICS layers into provider-specific system prompt payloads.
 *
 * <p>Mirrors {@code ics_runtime.prompt.builder.PromptBuilder} from Python.
 *
 * <ul>
 *   <li><b>Anthropic</b> — one {@code Map} per layer, with
 *       {@code cache_control: {type: ephemeral}} on stable layers
 *       (IMMUTABLE_CONTEXT, CAPABILITY_DECLARATION, OUTPUT_CONTRACT).</li>
 *   <li><b>OpenAI</b> — a single {@code Map} containing all layers joined
 *       into one string, with stable layers first for prefix-caching.</li>
 * </ul>
 */
public final class PromptBuilder {

    private static final Set<String> CACHE_ELIGIBLE =
            Set.of("IMMUTABLE_CONTEXT", "CAPABILITY_DECLARATION", "OUTPUT_CONTRACT");

    private final String provider;

    public PromptBuilder(String provider) {
        if (!provider.equals("anthropic") && !provider.equals("openai")) {
            throw new IllegalArgumentException(
                    "Unknown provider '" + provider + "'. Use 'anthropic' or 'openai'.");
        }
        this.provider = provider;
    }

    /**
     * Build provider-formatted system content blocks for a single turn.
     *
     * @param immutable      Text for the IMMUTABLE_CONTEXT layer
     * @param capability     Text for the CAPABILITY_DECLARATION layer
     * @param sessionState   Text for the SESSION_STATE layer (dynamic, not cached)
     * @param outputContract Text for the OUTPUT_CONTRACT layer
     * @return List of block maps for the provider SDK
     */
    public List<Map<String, Object>> buildSystem(
            String immutable,
            String capability,
            String sessionState,
            String outputContract) {

        List<String[]> stable = new ArrayList<>();   // [layerName, text]
        stable.add(new String[]{"IMMUTABLE_CONTEXT",       immutable});
        stable.add(new String[]{"CAPABILITY_DECLARATION",  capability});
        if (outputContract != null && !outputContract.isBlank()) {
            stable.add(new String[]{"OUTPUT_CONTRACT", outputContract});
        }

        List<String[]> dynamic = new ArrayList<>();
        if (sessionState != null && !sessionState.isBlank()) {
            dynamic.add(new String[]{"SESSION_STATE", sessionState});
        }

        return "anthropic".equals(provider)
                ? buildAnthropicBlocks(stable, dynamic)
                : buildOpenAIBlock(stable, dynamic);
    }

    // ── Anthropic: one block per layer ──────────────────────────────────────

    private List<Map<String, Object>> buildAnthropicBlocks(
            List<String[]> stable, List<String[]> dynamic) {

        List<Map<String, Object>> blocks = new ArrayList<>();

        for (String[] layer : stable) {
            String name = layer[0], text = layer[1];
            if (text == null || text.isBlank()) continue;
            Map<String, Object> block = new LinkedHashMap<>();
            block.put("type", "text");
            block.put("text", icsBlock(text, name));
            if (CACHE_ELIGIBLE.contains(name)) {
                block.put("cache_control", Map.of("type", "ephemeral"));
            }
            blocks.add(block);
        }

        for (String[] layer : dynamic) {
            String name = layer[0], text = layer[1];
            if (text == null || text.isBlank()) continue;
            Map<String, Object> block = new LinkedHashMap<>();
            block.put("type", "text");
            block.put("text", icsBlock(text, name));
            blocks.add(block);
        }

        return blocks;
    }

    // ── OpenAI: single text block (stable prefix first) ─────────────────────

    private List<Map<String, Object>> buildOpenAIBlock(
            List<String[]> stable, List<String[]> dynamic) {

        StringBuilder sb = new StringBuilder();
        for (String[] layer : stable) {
            String name = layer[0], text = layer[1];
            if (text == null || text.isBlank()) continue;
            if (sb.length() > 0) sb.append("\n\n");
            sb.append(icsBlock(text, name));
        }
        for (String[] layer : dynamic) {
            String name = layer[0], text = layer[1];
            if (text == null || text.isBlank()) continue;
            if (sb.length() > 0) sb.append("\n\n");
            sb.append(icsBlock(text, name));
        }

        return List.of(Map.of("type", "text", "text", sb.toString()));
    }

    // ── ICS wire-format wrapping ─────────────────────────────────────────────

    private static String icsBlock(String text, String layerName) {
        return "###ICS:" + layerName + "###\n" + text + "\n###END:" + layerName + "###";
    }
}
