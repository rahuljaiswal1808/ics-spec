package io.ics.runtime.providers;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.openai.client.OpenAIClient;
import com.openai.client.okhttp.OpenAIOkHttpClient;
import com.openai.core.JsonValue;
import com.openai.models.ChatModel;
import com.openai.models.*;
import io.ics.runtime.ICSRuntimeException;

import java.util.*;

/**
 * OpenAI provider — calls {@code gpt-*} / {@code o1} models via the OpenAI SDK.
 *
 * <p>Stable ICS layers are placed first in the system prompt for prefix-caching
 * (OpenAI caches automatically for long prompts; no explicit markers needed).
 *
 * <p>Requires the {@code com.openai:openai-java} SDK on the classpath.
 * Set {@code OPENAI_API_KEY} in the environment or pass via the constructor.
 */
public class OpenAIProvider extends ProviderBase {

    private static final ObjectMapper JSON = new ObjectMapper();

    private final OpenAIClient client;

    public OpenAIProvider(String model, String apiKey) {
        super(model);
        OpenAIOkHttpClient.Builder builder = OpenAIOkHttpClient.builder();
        if (apiKey != null && !apiKey.isBlank()) {
            builder.apiKey(apiKey);
        }
        this.client = builder.build();
    }

    @Override
    public ProviderResponse complete(
            List<Map<String, Object>> systemBlocks,
            List<ProviderMessage> messages,
            List<Map<String, Object>> tools,
            int maxTokens) {

        ChatCompletionCreateParams.Builder params = ChatCompletionCreateParams.builder()
                .model(ChatModel.of(model))
                .maxCompletionTokens(maxTokens);

        // ── System prompt (join all blocks) ──────────────────────────────────
        if (!systemBlocks.isEmpty()) {
            StringBuilder sysText = new StringBuilder();
            for (Map<String, Object> blk : systemBlocks) {
                if (sysText.length() > 0) sysText.append("\n\n");
                sysText.append(blk.get("text"));
            }
            params.addMessage(ChatCompletionMessageParam.ofChatCompletionSystemMessageParam(
                    ChatCompletionSystemMessageParam.builder()
                            .content(ChatCompletionSystemMessageParam.Content.ofTextContent(sysText.toString()))
                            .build()));
        }

        // ── Messages ─────────────────────────────────────────────────────────
        for (ProviderMessage msg : messages) {
            String role = msg.getRole();
            if ("user".equals(role)) {
                if (msg.isTextContent()) {
                    params.addMessage(ChatCompletionMessageParam.ofChatCompletionUserMessageParam(
                            ChatCompletionUserMessageParam.builder()
                                    .content(ChatCompletionUserMessageParam.Content.ofTextContent(msg.getTextContent()))
                                    .build()));
                } else {
                    // Tool result blocks
                    for (Map<String, Object> blk : msg.getBlockContent()) {
                        if ("tool_result".equals(blk.get("type"))) {
                            String callId  = (String) blk.get("tool_call_id");
                            String content = (String) blk.get("content");
                            params.addMessage(ChatCompletionMessageParam.ofChatCompletionToolMessageParam(
                                    ChatCompletionToolMessageParam.builder()
                                            .toolCallId(callId)
                                            .content(ChatCompletionToolMessageParam.Content.ofTextContent(content))
                                            .build()));
                        }
                    }
                }
            } else if ("assistant".equals(role)) {
                if (!msg.getToolCalls().isEmpty()) {
                    // Assistant message with tool_calls
                    List<ChatCompletionMessageToolCall> tcs = new ArrayList<>();
                    for (Map<String, Object> tc : msg.getToolCalls()) {
                        String id   = (String) tc.get("id");
                        String name = (String) tc.get("name");
                        String args;
                        try {
                            args = JSON.writeValueAsString(tc.get("input"));
                        } catch (Exception e) {
                            args = "{}";
                        }
                        tcs.add(ChatCompletionMessageToolCall.builder()
                                .id(id)
                                .type(ChatCompletionMessageToolCall.Type.FUNCTION)
                                .function(ChatCompletionMessageToolCall.Function.builder()
                                        .name(name)
                                        .arguments(args)
                                        .build())
                                .build());
                    }
                    params.addMessage(ChatCompletionMessageParam.ofChatCompletionAssistantMessageParam(
                            ChatCompletionAssistantMessageParam.builder()
                                    .toolCalls(tcs)
                                    .build()));
                } else {
                    params.addMessage(ChatCompletionMessageParam.ofChatCompletionAssistantMessageParam(
                            ChatCompletionAssistantMessageParam.builder()
                                    .content(ChatCompletionAssistantMessageParam.Content.ofTextContent(
                                            msg.isTextContent() ? msg.getTextContent() : ""))
                                    .build()));
                }
            }
        }

        // ── Tools ─────────────────────────────────────────────────────────────
        if (tools != null && !tools.isEmpty()) {
            List<ChatCompletionTool> toolList = new ArrayList<>();
            for (Map<String, Object> t : tools) {
                @SuppressWarnings("unchecked")
                Map<String, Object> fn = (Map<String, Object>) t.get("function");
                String name = (String) fn.get("name");
                String desc = (String) fn.getOrDefault("description", "");
                @SuppressWarnings("unchecked")
                Map<String, Object> paramSchema = (Map<String, Object>) fn.get("parameters");

                toolList.add(ChatCompletionTool.builder()
                        .type(ChatCompletionTool.Type.FUNCTION)
                        .function(FunctionDefinition.builder()
                                .name(name)
                                .description(desc)
                                .parameters(FunctionParameters.builder()
                                        .putAdditionalProperty("type", JsonValue.from("object"))
                                        .putAdditionalProperty("properties", JsonValue.from(
                                                paramSchema.getOrDefault("properties", Map.of())))
                                        .putAdditionalProperty("required",
                                                JsonValue.from(paramSchema.getOrDefault("required", List.of())))
                                        .build())
                                .build())
                        .build());
            }
            params.tools(toolList);
        }

        // ── Call API ─────────────────────────────────────────────────────────
        ChatCompletion completion;
        try {
            completion = client.chat().completions().create(params.build());
        } catch (Exception e) {
            throw new ICSRuntimeException("OpenAI API error: " + e.getMessage(), e);
        }

        // ── Parse response ────────────────────────────────────────────────────
        ChatCompletion.Choice choice = completion.choices().get(0);
        ChatCompletionMessage responseMsg = choice.message();

        String text = responseMsg.content().orElse("");
        List<Map<String, Object>> toolCalls = new ArrayList<>();

        if (responseMsg.toolCalls().isPresent()) {
            for (ChatCompletionMessageToolCall tc : responseMsg.toolCalls().get()) {
                Map<String, Object> inputMap;
                try {
                    inputMap = JSON.readValue(tc.function().arguments(),
                            new TypeReference<Map<String, Object>>(){});
                } catch (Exception ex) {
                    inputMap = Map.of();
                }
                Map<String, Object> callMap = new LinkedHashMap<>();
                callMap.put("id",    tc.id());
                callMap.put("name",  tc.function().name());
                callMap.put("input", inputMap);
                toolCalls.add(callMap);
            }
        }

        CompletionUsage usage = completion.usage().orElse(null);
        int inputTokens  = usage != null ? (int) (long) usage.promptTokens()     : 0;
        int outputTokens = usage != null ? (int) (long) usage.completionTokens() : 0;
        int cacheRead    = 0;

        // OpenAI prefix cache — cached_tokens in prompt_tokens_details
        if (usage != null && usage.promptTokensDetails().isPresent()) {
            var details = usage.promptTokensDetails().get();
            if (details.cachedTokens().isPresent()) {
                cacheRead = (int) (long) details.cachedTokens().get();
            }
        }

        return new ProviderResponse(text, inputTokens, outputTokens, 0, cacheRead,
                toolCalls, completion);
    }

    @Override
    public ProviderMessage toolResultMessage(String toolCallId, String result) {
        List<Map<String, Object>> blocks = List.of(Map.of(
                "type",        "tool_result",
                "tool_call_id", toolCallId,
                "content",     result
        ));
        return new ProviderMessage("user", blocks);
    }
}
