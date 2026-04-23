package io.ics.runtime.providers;

import com.anthropic.client.AnthropicClient;
import com.anthropic.client.okhttp.AnthropicOkHttpClient;
import com.anthropic.core.JsonValue;
import com.anthropic.models.messages.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.ics.runtime.ICSRuntimeException;

import java.util.*;

/**
 * Anthropic provider — calls {@code claude-*} models with explicit
 * {@code cache_control} blocks on stable ICS layers.
 *
 * <p>Requires the {@code com.anthropic:anthropic-java} SDK on the classpath.
 * Set {@code ANTHROPIC_API_KEY} in the environment or pass via the constructor.
 */
public class AnthropicProvider extends ProviderBase {

    private static final ObjectMapper JSON = new ObjectMapper();

    private final AnthropicClient client;

    public AnthropicProvider(String model, String apiKey) {
        super(model);
        AnthropicOkHttpClient.Builder builder = AnthropicOkHttpClient.builder();
        if (apiKey != null && !apiKey.isBlank()) {
            builder.apiKey(apiKey);
        }
        // Falls back to ANTHROPIC_API_KEY env var if not provided
        this.client = builder.build();
    }

    @Override
    public ProviderResponse complete(
            List<Map<String, Object>> systemBlocks,
            List<ProviderMessage> messages,
            List<Map<String, Object>> tools,
            int maxTokens) {

        MessageCreateParams.Builder params = MessageCreateParams.builder()
                .model(Model.of(model))
                .maxTokens(maxTokens);

        // ── System blocks ────────────────────────────────────────────────────
        List<TextBlockParam> sysBlocks = new ArrayList<>();
        for (Map<String, Object> block : systemBlocks) {
            String text = (String) block.get("text");
            @SuppressWarnings("unchecked")
            Map<String, Object> cc = (Map<String, Object>) block.get("cache_control");

            TextBlockParam.Builder tb = TextBlockParam.builder().text(text);
            if (cc != null) {
                tb.cacheControl(CacheControlEphemeral.builder().build());
            }
            sysBlocks.add(tb.build());
        }
        if (!sysBlocks.isEmpty()) {
            params.system(MessageCreateParams.System.ofTextBlockParams(sysBlocks));
        }

        // ── Messages ─────────────────────────────────────────────────────────
        for (ProviderMessage msg : messages) {
            MessageParam.Role role = "user".equals(msg.getRole())
                    ? MessageParam.Role.USER
                    : MessageParam.Role.ASSISTANT;

            if (msg.isTextContent()) {
                if (role == MessageParam.Role.USER) {
                    params.addUserMessage(msg.getTextContent());
                } else {
                    params.addAssistantMessage(msg.getTextContent());
                }
            } else {
                // Multi-part block list (tool_use, tool_result, etc.)
                List<ContentBlockParam> blocks = new ArrayList<>();
                for (Map<String, Object> blk : msg.getBlockContent()) {
                    String type = (String) blk.get("type");
                    if ("tool_use".equals(type)) {
                        String id   = (String) blk.get("id");
                        String name = (String) blk.get("name");
                        @SuppressWarnings("unchecked")
                        Map<String, Object> input = (Map<String, Object>) blk.get("input");
                        blocks.add(ContentBlockParam.ofToolUse(ToolUseBlockParam.builder()
                                .id(id).name(name)
                                .input(JsonValue.from(input))
                                .build()));
                    } else if ("tool_result".equals(type)) {
                        String toolUseId = (String) blk.get("tool_use_id");
                        String content   = (String) blk.get("content");
                        blocks.add(ContentBlockParam.ofToolResult(ToolResultBlockParam.builder()
                                .toolUseId(toolUseId)
                                .content(content)
                                .build()));
                    }
                }
                params.addMessage(MessageParam.builder()
                        .role(role)
                        .content(MessageParam.Content.ofBlockParams(blocks))
                        .build());
            }
        }

        // ── Tools ─────────────────────────────────────────────────────────────
        if (tools != null && !tools.isEmpty()) {
            List<ToolUnion> toolParams = new ArrayList<>();
            for (Map<String, Object> t : tools) {
                String name = (String) t.get("name");
                String desc = (String) t.getOrDefault("description", "");
                @SuppressWarnings("unchecked")
                Map<String, Object> inputSchema = (Map<String, Object>) t.get("input_schema");

                toolParams.add(ToolUnion.ofTool(Tool.builder()
                        .name(name)
                        .description(desc)
                        .inputSchema(Tool.InputSchema.builder()
                                .type(JsonValue.from("object"))
                                .properties(JsonValue.from(
                                        inputSchema.getOrDefault("properties", Map.of())))
                                .putAdditionalProperty("required",
                                        JsonValue.from(inputSchema.getOrDefault("required", List.of())))
                                .build())
                        .build()));
            }
            params.tools(toolParams);
        }

        // ── Call API ─────────────────────────────────────────────────────────
        Message response;
        try {
            response = client.messages().create(params.build());
        } catch (Exception e) {
            throw new ICSRuntimeException("Anthropic API error: " + e.getMessage(), e);
        }

        // ── Parse response ────────────────────────────────────────────────────
        StringBuilder textParts = new StringBuilder();
        List<Map<String, Object>> toolCalls = new ArrayList<>();

        for (ContentBlock block : response.content()) {
            if (block.isText()) {
                TextBlock tb = block.asText();
                if (textParts.length() > 0) textParts.append("\n");
                textParts.append(tb.text());
            } else if (block.isToolUse()) {
                ToolUseBlock tu = block.asToolUse();
                Map<String, Object> inputMap;
                try {
                    inputMap = JSON.convertValue(tu._input(), Map.class);
                } catch (Exception ex) {
                    inputMap = Map.of();
                }
                Map<String, Object> tc = new LinkedHashMap<>();
                tc.put("id",    tu.id());
                tc.put("name",  tu.name());
                tc.put("input", inputMap);
                toolCalls.add(tc);
            }
        }

        Usage usage = response.usage();
        int inputTokens  = (int) usage.inputTokens();
        int outputTokens = (int) usage.outputTokens();
        int cacheWrite   = 0;
        int cacheRead    = 0;

        // Extended cache fields (Anthropic returns these when caching is active)
        try {
            Object cw = usage.getClass().getMethod("cacheCreationInputTokens").invoke(usage);
            if (cw instanceof Number n) cacheWrite = n.intValue();
        } catch (Exception ignored) {}
        try {
            Object cr = usage.getClass().getMethod("cacheReadInputTokens").invoke(usage);
            if (cr instanceof Number n) cacheRead = n.intValue();
        } catch (Exception ignored) {}

        return new ProviderResponse(
                textParts.toString(), inputTokens, outputTokens, cacheWrite, cacheRead,
                toolCalls, response);
    }

    @Override
    public ProviderMessage toolResultMessage(String toolCallId, String result) {
        List<Map<String, Object>> blocks = List.of(Map.of(
                "type",        "tool_result",
                "tool_use_id", toolCallId,
                "content",     result
        ));
        return new ProviderMessage("user", blocks);
    }
}
