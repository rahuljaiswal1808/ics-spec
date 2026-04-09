package io.ics.runtime;

import java.util.List;

/** Thrown by {@link RunResult#raiseOnViolation()} when contract violations exist. */
public class ContractViolationException extends ICSRuntimeException {

    private final List<Violation> violations;

    public ContractViolationException(List<Violation> violations) {
        super("Contract violations detected: " + violations.size());
        this.violations = List.copyOf(violations);
    }

    public List<Violation> getViolations() { return violations; }
}
