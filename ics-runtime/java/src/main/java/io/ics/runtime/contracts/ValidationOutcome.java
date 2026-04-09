package io.ics.runtime.contracts;

import io.ics.runtime.Violation;

import java.util.List;

/**
 * Result of {@link OutputContract#validate(String)}.
 *
 * <p>Never throws — violations are surfaced here; callers decide whether to
 * propagate via {@link io.ics.runtime.RunResult#raiseOnViolation()}.
 */
public final class ValidationOutcome {

    private final boolean passed;
    private final Object parsed;                   // deserialized object (Map or custom class)
    private final List<Violation> violations;
    private final boolean isStructuredFailure;     // True if response matches a failure_mode

    public ValidationOutcome(boolean passed, Object parsed,
                             List<Violation> violations, boolean isStructuredFailure) {
        this.passed              = passed;
        this.parsed              = parsed;
        this.violations          = violations == null ? List.of() : List.copyOf(violations);
        this.isStructuredFailure = isStructuredFailure;
    }

    public static ValidationOutcome ok(Object parsed) {
        return new ValidationOutcome(true, parsed, List.of(), false);
    }

    public static ValidationOutcome structuredFailure() {
        return new ValidationOutcome(true, null, List.of(), true);
    }

    public static ValidationOutcome failed(List<Violation> violations) {
        return new ValidationOutcome(false, null, violations, false);
    }

    public boolean isPassed()              { return passed; }
    public Object getParsed()              { return parsed; }
    public List<Violation> getViolations() { return violations; }
    public boolean isStructuredFailure()   { return isStructuredFailure; }
}
