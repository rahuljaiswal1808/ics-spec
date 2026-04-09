package io.ics.runtime.contracts;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.ics.runtime.Violation;

import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Bridges the ICS OUTPUT_CONTRACT layer with runtime schema enforcement.
 *
 * <p>In Java, since we don't have Pydantic, JSON Schema validation is done by
 * checking required fields and basic types against a user-supplied schema map.
 * You can also register a custom {@link ResponseValidator} for full validation.
 *
 * <pre>{@code
 * OutputContract contract = OutputContract.builder()
 *     .requiredFields("decision", "score", "risk_category", "lead_id", "rationale")
 *     .failureMode("BLOCKED:")
 *     .failureMode("insufficient_data")
 *     .validator(json -> {
 *         String decision = (String) json.get("decision");
 *         if (!Set.of("QUALIFIED","NOT_QUALIFIED","REVIEW_REQUIRED").contains(decision)) {
 *             return List.of(new Violation("OUTPUT_CONTRACT: invalid decision value", ...));
 *         }
 *         return List.of();
 *     })
 *     .build();
 * }</pre>
 */
public final class OutputContract {

    @FunctionalInterface
    public interface ResponseValidator {
        /**
         * Validate the parsed JSON object.
         * @return List of violations (empty = pass)
         */
        List<Violation> validate(Map<String, Object> parsed);
    }

    private static final ObjectMapper JSON = new ObjectMapper();
    private static final Pattern JSON_FENCE = Pattern.compile(
            "```(?:json)?\\s*(\\{.*\\}|\\[.*\\])\\s*```", Pattern.DOTALL);

    private final List<String> requiredFields;
    private final List<String> failureModes;
    private final ResponseValidator validator;
    private final String formatHint;

    private OutputContract(Builder b) {
        this.requiredFields = List.copyOf(b.requiredFields);
        this.failureModes   = List.copyOf(b.failureModes);
        this.validator      = b.validator;
        this.formatHint     = b.formatHint;
    }

    // ── ICS layer text ──────────────────────────────────────────────────────

    /**
     * Render this contract as OUTPUT_CONTRACT ICS layer content for the prompt.
     */
    public String toIcsText() {
        StringBuilder sb = new StringBuilder();
        sb.append("FORMAT: ").append(formatHint).append("\n");
        if (!requiredFields.isEmpty()) {
            sb.append("REQUIRED_FIELDS: ").append(String.join(", ", requiredFields)).append("\n");
        }
        if (!failureModes.isEmpty()) {
            sb.append("FAILURE_MODES: ").append(String.join(", ", failureModes)).append("\n");
        }
        return sb.toString().trim();
    }

    // ── Validation ──────────────────────────────────────────────────────────

    /**
     * Validate {@code responseText} against this contract.
     * Never throws — violations are recorded in the returned {@link ValidationOutcome}.
     */
    public ValidationOutcome validate(String responseText) {
        String text = responseText.strip();

        // Check failure modes first
        for (String fm : failureModes) {
            if (text.startsWith(fm)) {
                return ValidationOutcome.structuredFailure();
            }
        }

        if (!"json".equals(formatHint)) {
            return ValidationOutcome.ok(null);
        }

        // Extract JSON
        Map<String, Object> parsed;
        try {
            String jsonText = extractJson(text);
            parsed = JSON.readValue(jsonText, new TypeReference<>(){});
        } catch (Exception e) {
            return ValidationOutcome.failed(List.of(new Violation(
                    "OUTPUT_CONTRACT: response is not valid JSON",
                    "schema", "detected", text.substring(0, Math.min(200, text.length())))));
        }

        // Check required fields
        List<Violation> violations = new ArrayList<>();
        for (String field : requiredFields) {
            if (!parsed.containsKey(field)) {
                violations.add(new Violation(
                        "OUTPUT_CONTRACT: missing required field '" + field + "'",
                        "schema", "detected",
                        text.substring(0, Math.min(120, text.length())),
                        field));
            }
        }

        // Custom validator
        if (violations.isEmpty() && validator != null) {
            violations.addAll(validator.validate(parsed));
        }

        if (!violations.isEmpty()) {
            return ValidationOutcome.failed(violations);
        }
        return ValidationOutcome.ok(parsed);
    }

    // ── Helpers ─────────────────────────────────────────────────────────────

    private static String extractJson(String text) {
        Matcher m = JSON_FENCE.matcher(text);
        if (m.find()) return m.group(1);
        return text;
    }

    // ── Builder ─────────────────────────────────────────────────────────────

    public static Builder builder() { return new Builder(); }

    public static final class Builder {
        private final List<String> requiredFields = new ArrayList<>();
        private final List<String> failureModes   = new ArrayList<>();
        private ResponseValidator validator = null;
        private String formatHint = "json";

        public Builder requiredFields(String... fields) {
            requiredFields.addAll(Arrays.asList(fields));
            return this;
        }
        public Builder failureMode(String prefix) {
            failureModes.add(prefix); return this;
        }
        public Builder validator(ResponseValidator v) {
            this.validator = v; return this;
        }
        public Builder formatHint(String v) {
            this.formatHint = v; return this;
        }
        public OutputContract build() { return new OutputContract(this); }
    }
}
