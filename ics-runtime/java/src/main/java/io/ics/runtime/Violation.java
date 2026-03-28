package io.ics.runtime;

/**
 * A single contract violation detected during or after an LLM call.
 *
 * <p>Violations are collected in {@link RunResult#getViolations()} and originate
 * from two sources:
 * <ul>
 *   <li>{@link io.ics.runtime.contracts.CapabilityEnforcer} — DENY/REQUIRE scans</li>
 *   <li>{@link io.ics.runtime.contracts.OutputContract}    — JSON schema failures</li>
 * </ul>
 */
public final class Violation {

    private final String rule;
    private final String kind;       // "capability" | "schema"
    private final String severity;   // "blocked" | "detected"
    private final String evidence;
    private final String field;      // nullable — schema field path

    public Violation(String rule, String kind, String severity, String evidence) {
        this(rule, kind, severity, evidence, null);
    }

    public Violation(String rule, String kind, String severity, String evidence, String field) {
        this.rule     = rule;
        this.kind     = kind;
        this.severity = severity;
        this.evidence = evidence;
        this.field    = field;
    }

    public String getRule()     { return rule; }
    public String getKind()     { return kind; }
    public String getSeverity() { return severity; }
    public String getEvidence() { return evidence; }
    public String getField()    { return field; }

    @Override
    public String toString() {
        return String.format("[%s/%s] %s  evidence=%s", kind, severity, rule,
                evidence == null ? "" : evidence.substring(0, Math.min(80, evidence.length())));
    }
}
