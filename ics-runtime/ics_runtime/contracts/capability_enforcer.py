"""CapabilityEnforcer — post-execution scanner for DENY/REQUIRE violations."""

from __future__ import annotations

import re

from ics_runtime.contracts.violation import ContractViolation

# Patterns for common PII types used by deny_pii heuristics
_PII_PATTERNS = [
    (r"\b\d{3}-\d{2}-\d{4}\b", "SSN pattern"),
    (r"\b\d{4}[\s-]\d{4}[\s-]\d{4}[\s-]\d{4}\b", "credit card pattern"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "email address"),
]


def _parse_directives(capability_text: str) -> list[dict]:
    """Extract ALLOW/DENY/REQUIRE lines from capability text.

    Returns a list of dicts: ``{"directive": "DENY", "rule": "..."}``.
    """
    directives: list[dict] = []
    for line in capability_text.splitlines():
        stripped = line.strip()
        for keyword in ("DENY", "REQUIRE", "ALLOW"):
            if stripped.upper().startswith(keyword):
                rule = stripped[len(keyword):].lstrip(":").strip()
                directives.append({"directive": keyword, "rule": rule})
                break
    return directives


class CapabilityEnforcer:
    """Parse DENY/REQUIRE directives and scan LLM output post-execution.

    This is the enforcement gap the ICS spec leaves to the caller.

    Enforcement is two-pass:
    1. ``on_failure`` detection — if the model itself reports a block
       (starts with ``on_failure_prefix``) we record the violation with
       ``severity="blocked"`` (highest confidence).
    2. Heuristic scanning — DENY directives containing PII/bulk/float
       keywords trigger pattern-based scans of the response text.

    Tool calls are checked via ``check_tool_call()`` before execution.
    """

    def __init__(
        self,
        capability_text: str,
        on_failure_prefix: str = "BLOCKED:",
    ) -> None:
        self._directives = _parse_directives(capability_text)
        self._on_failure_prefix = on_failure_prefix
        self._deny_rules = [d["rule"] for d in self._directives if d["directive"] == "DENY"]
        self._require_rules = [d["rule"] for d in self._directives if d["directive"] == "REQUIRE"]

    # ------------------------------------------------------------------
    # Output scanning
    # ------------------------------------------------------------------

    def scan_output(self, response_text: str) -> list[ContractViolation]:
        """Scan the final LLM response text for capability violations."""
        violations: list[ContractViolation] = []

        # Pass 1: on_failure prefix detection
        stripped = response_text.strip()
        if stripped.startswith(self._on_failure_prefix):
            rule = self._extract_blocked_rule(stripped)
            violations.append(ContractViolation(
                rule=rule or f"DENY (model self-reported block)",
                kind="capability",
                severity="blocked",
                evidence=stripped[:200],
            ))
            return violations  # Model blocked itself; skip heuristics

        # Pass 2: heuristic DENY scanning
        for rule in self._deny_rules:
            rule_lower = rule.lower()

            # PII heuristic
            if any(kw in rule_lower for kw in ("pii", "ssn", "email", "account number", "phone")):
                for pattern, label in _PII_PATTERNS:
                    m = re.search(pattern, response_text)
                    if m:
                        violations.append(ContractViolation(
                            rule=f"DENY {rule}",
                            kind="capability",
                            severity="detected",
                            evidence=m.group(0),
                        ))

            # Bulk export heuristic
            if any(kw in rule_lower for kw in ("bulk", "export", "all records")):
                if re.search(r"\ball records\b|\bexport all\b|\bCSV\b", response_text, re.I):
                    violations.append(ContractViolation(
                        rule=f"DENY {rule}",
                        kind="capability",
                        severity="detected",
                        evidence=response_text[:120],
                    ))

            # Float arithmetic on monetary values heuristic
            if "float" in rule_lower and "monetar" in rule_lower:
                if re.search(r"\bfloat\(|\.0\b.*\$|\$.*\.0\b", response_text, re.I):
                    violations.append(ContractViolation(
                        rule=f"DENY {rule}",
                        kind="capability",
                        severity="detected",
                        evidence=response_text[:120],
                    ))

        return violations

    def check_tool_call(self, tool_name: str, arguments: dict) -> list[ContractViolation]:
        """Check a proposed tool call against DENY directives.

        Called by ``Session`` before a tool is dispatched.  Returns violations
        (empty = approved to proceed).
        """
        violations: list[ContractViolation] = []
        for rule in self._deny_rules:
            rule_lower = rule.lower()
            if "bulk" in rule_lower or "export" in rule_lower:
                for k, v in arguments.items():
                    if isinstance(v, list) and len(v) > 50:
                        violations.append(ContractViolation(
                            rule=f"DENY {rule}",
                            kind="capability",
                            severity="blocked",
                            evidence=f"tool={tool_name} arg={k} count={len(v)}",
                        ))
        return violations

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_blocked_rule(self, text: str) -> str | None:
        """Try to parse the rule name from a BLOCKED: prefix response."""
        # e.g. "BLOCKED: 'DENY logging PII data' — ..."
        m = re.search(r"['\"]DENY ([^'\"]+)['\"]", text)
        if m:
            return f"DENY {m.group(1)}"
        # Fallback: everything after the prefix on the first line
        first_line = text.splitlines()[0]
        return first_line[len(self._on_failure_prefix):].strip() or None
