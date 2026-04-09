package io.ics.runtime.contracts;

import io.ics.runtime.Violation;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Post-execution scanner for DENY/REQUIRE capability violations.
 *
 * <p>Mirrors {@code ics_runtime.contracts.capability_enforcer.CapabilityEnforcer}
 * from Python with the same two-pass approach:
 * <ol>
 *   <li>on_failure prefix detection — model self-reported block → severity=blocked</li>
 *   <li>Heuristic DENY scanning — PII patterns, bulk export keywords, code-level float arithmetic</li>
 * </ol>
 */
public final class CapabilityEnforcer {

    // PII patterns
    private static final Pattern SSN_PATTERN  = Pattern.compile("\\b\\d{3}-\\d{2}-\\d{4}\\b");
    private static final Pattern CC_PATTERN   = Pattern.compile("\\b\\d{4}[\\s-]\\d{4}[\\s-]\\d{4}[\\s-]\\d{4}\\b");
    private static final Pattern EMAIL_PATTERN = Pattern.compile("[A-Za-z0-9._%+\\-]+@[A-Za-z0-9.\\-]+\\.[A-Za-z]{2,}");

    // Bulk export
    private static final Pattern BULK_PATTERN = Pattern.compile("\\ball records\\b|\\bexport all\\b|\\bCSV\\b", Pattern.CASE_INSENSITIVE);

    // Code-level float arithmetic on monetary values (NOT plain decimals in prose)
    private static final Pattern FLOAT_CODE_PATTERN = Pattern.compile(
            "\\bfloat\\s*\\(|\\bDecimal\\s*\\(\\s*str\\s*\\(" +
            "|[*/]\\s*0\\.0[0-9]+\\b.*\\$|\\$.*[*/]\\s*0\\.0[0-9]+\\b" +
            "|\\b100\\.0\\b|\\bdivide\\b.{0,30}\\$",
            Pattern.CASE_INSENSITIVE | Pattern.DOTALL);

    // Blocked-rule extractor
    private static final Pattern BLOCKED_RULE = Pattern.compile("['\"]DENY ([^'\"]+)['\"]");

    private final List<Directive> directives = new ArrayList<>();
    private final String onFailurePrefix;

    public CapabilityEnforcer(String capabilityText) {
        this(capabilityText, "BLOCKED:");
    }

    public CapabilityEnforcer(String capabilityText, String onFailurePrefix) {
        this.onFailurePrefix = onFailurePrefix;
        parseDirectives(capabilityText);
    }

    // ── Output scanning ─────────────────────────────────────────────────────

    public List<Violation> scanOutput(String responseText) {
        List<Violation> violations = new ArrayList<>();
        String stripped = responseText.strip();

        // Pass 1: on_failure prefix
        if (stripped.startsWith(onFailurePrefix)) {
            String rule = extractBlockedRule(stripped);
            violations.add(new Violation(
                    rule != null ? rule : "DENY (model self-reported block)",
                    "capability", "blocked", stripped.substring(0, Math.min(200, stripped.length()))));
            return violations;   // skip heuristics
        }

        // Pass 2: heuristic DENY scanning
        for (Directive d : directives) {
            if (!"DENY".equals(d.keyword)) continue;
            String rule = d.rule.toLowerCase();

            // PII heuristic
            if (containsAny(rule, "pii", "ssn", "email", "account number", "phone")) {
                checkPattern(SSN_PATTERN,   responseText, "DENY " + d.rule, violations);
                checkPattern(CC_PATTERN,    responseText, "DENY " + d.rule, violations);
                checkPattern(EMAIL_PATTERN, responseText, "DENY " + d.rule, violations);
            }

            // Bulk export heuristic
            if (containsAny(rule, "bulk", "export", "all records")) {
                if (BULK_PATTERN.matcher(responseText).find()) {
                    violations.add(new Violation("DENY " + d.rule, "capability", "detected",
                            responseText.substring(0, Math.min(120, responseText.length()))));
                }
            }

            // Float arithmetic on monetary values (code-level only)
            if (rule.contains("float") && rule.contains("monetar")) {
                if (FLOAT_CODE_PATTERN.matcher(responseText).find()) {
                    violations.add(new Violation("DENY " + d.rule, "capability", "detected",
                            responseText.substring(0, Math.min(120, responseText.length()))));
                }
            }
        }

        return violations;
    }

    // ── Helpers ─────────────────────────────────────────────────────────────

    private void parseDirectives(String text) {
        if (text == null || text.isBlank()) return;
        for (String line : text.split("\\r?\\n")) {
            String s = line.strip().toUpperCase();
            for (String kw : new String[]{"DENY", "REQUIRE", "ALLOW"}) {
                if (s.startsWith(kw)) {
                    String rule = line.strip().substring(kw.length()).replaceFirst("^\\s*:\\s*", "").strip();
                    directives.add(new Directive(kw, rule));
                    break;
                }
            }
        }
    }

    private static void checkPattern(Pattern p, String text, String rule, List<Violation> out) {
        Matcher m = p.matcher(text);
        if (m.find()) {
            out.add(new Violation(rule, "capability", "detected", m.group()));
        }
    }

    private static boolean containsAny(String haystack, String... needles) {
        for (String n : needles) if (haystack.contains(n)) return true;
        return false;
    }

    private String extractBlockedRule(String text) {
        Matcher m = BLOCKED_RULE.matcher(text);
        if (m.find()) return "DENY " + m.group(1);
        String first = text.split("\\r?\\n")[0];
        String after = first.substring(Math.min(onFailurePrefix.length(), first.length())).strip();
        return after.isEmpty() ? null : after;
    }

    // ── Directive record ────────────────────────────────────────────────────

    private record Directive(String keyword, String rule) {}
}
