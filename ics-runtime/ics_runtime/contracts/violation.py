"""ContractViolation — a structured record of a capability or schema violation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class ContractViolation:
    """A single violation detected during post-execution contract enforcement.

    Attributes:
        rule:     The directive that was violated, e.g. ``"DENY logging PII"``.
        kind:     ``"capability"`` (DENY/REQUIRE scan) or ``"schema"``
                  (OutputContract Pydantic validation failure).
        severity: ``"blocked"`` if the model itself reported the block (highest
                  confidence); ``"detected"`` if the runtime scanner found it.
        evidence: A short excerpt from the response that triggered the violation.
        field:    For schema violations, the Pydantic field path that failed.
    """

    rule: str
    kind: Literal["capability", "schema"] = "capability"
    severity: Literal["blocked", "detected"] = "detected"
    evidence: str = ""
    field: str = ""

    def __str__(self) -> str:
        parts = [f"[{self.kind}/{self.severity}] {self.rule}"]
        if self.field:
            parts.append(f"field={self.field!r}")
        if self.evidence:
            excerpt = self.evidence[:80].replace("\n", " ")
            parts.append(f'evidence="{excerpt}"')
        return " | ".join(parts)
