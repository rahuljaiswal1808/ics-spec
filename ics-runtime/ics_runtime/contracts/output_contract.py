"""OutputContract — declares the expected shape of LLM output."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel

from ics_runtime.contracts.violation import ContractViolation


@dataclass
class ValidationOutcome:
    """Result of OutputContract.validate()."""
    passed: bool
    parsed: Any = None                         # Pydantic model instance on success
    violations: list[ContractViolation] = None  # type: ignore[assignment]
    is_structured_failure: bool = False        # True if response matches a failure_mode

    def __post_init__(self) -> None:
        if self.violations is None:
            self.violations = []


class OutputContract:
    """Bridges the ICS OUTPUT_CONTRACT layer with runtime schema enforcement.

    The ``schema`` is validated post-execution by attempting to parse the
    LLM response text.  ``failure_modes`` are declared expected non-error
    states (e.g. ``"insufficient_data"``) that should NOT be treated as
    violations.

    Args:
        schema:        A Pydantic v2 ``BaseModel`` subclass.  The runtime
                       calls ``schema.model_validate_json(response_text)``
                       after extracting any JSON code-fence block.
        failure_modes: List of string prefixes/labels (e.g. ``["BLOCKED:",
                       "insufficient_data"]``).  A response starting with one
                       of these is treated as a structured failure, not a
                       violation.
        format_hint:   ``"json"`` (default) or ``"markdown"``.  Determines how
                       the response is parsed.
        strict:        If ``True``, raise ``ContractViolationError`` directly
                       from ``validate()``.  Defaults to ``False`` so that the
                       caller can decide.
    """

    def __init__(
        self,
        schema: type | None = None,
        failure_modes: list[str] | None = None,
        format_hint: Literal["json", "markdown"] = "json",
        strict: bool = False,
    ) -> None:
        self.schema = schema
        self.failure_modes = failure_modes or []
        self.format_hint = format_hint
        self.strict = strict

    # ------------------------------------------------------------------
    # ICS layer text generation
    # ------------------------------------------------------------------

    def to_ics_text(self) -> str:
        """Render this contract as OUTPUT_CONTRACT ICS layer content."""
        lines: list[str] = []
        if self.schema:
            try:
                schema_json = self.schema.model_json_schema()
                lines.append(f"FORMAT: json")
                lines.append(f"SCHEMA:\n{json.dumps(schema_json, indent=2)}")
            except Exception:
                lines.append(f"FORMAT: {self.format_hint}")
        else:
            lines.append(f"FORMAT: {self.format_hint}")
        if self.failure_modes:
            lines.append("FAILURE_MODES: " + ", ".join(self.failure_modes))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self, response_text: str) -> ValidationOutcome:
        """Validate ``response_text`` against this contract.

        Never raises — violations are recorded in the returned
        ``ValidationOutcome``.  Use ``RunResult.raise_on_violation()``
        if you want exceptions.
        """
        text = response_text.strip()

        # Check failure modes first
        for fm in self.failure_modes:
            if text.startswith(fm):
                return ValidationOutcome(passed=True, is_structured_failure=True)

        if self.schema is None:
            return ValidationOutcome(passed=True)

        if self.format_hint == "markdown":
            return ValidationOutcome(passed=True)

        # Attempt JSON extraction and schema validation
        try:
            json_text = _extract_json(text)
            parsed_dict = json.loads(json_text)
            instance = self.schema.model_validate(parsed_dict)
            return ValidationOutcome(passed=True, parsed=instance)
        except json.JSONDecodeError as exc:
            v = ContractViolation(
                rule=f"OUTPUT_CONTRACT: response is not valid JSON",
                kind="schema",
                severity="detected",
                evidence=text[:200],
            )
            return ValidationOutcome(passed=False, violations=[v])
        except Exception as exc:
            # Pydantic ValidationError or other parse failure
            violations = _pydantic_violations(exc, text)
            return ValidationOutcome(passed=False, violations=violations)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _extract_json(text: str) -> str:
    """Extract JSON from a plain string or a markdown ```json ... ``` fence."""
    # Try to strip ```json ... ``` fences
    m = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    # Fall through: assume the whole text is JSON
    return text


def _pydantic_violations(exc: Exception, response_text: str) -> list[ContractViolation]:
    violations: list[ContractViolation] = []
    try:
        for error in exc.errors():  # type: ignore[attr-defined]
            field_path = ".".join(str(loc) for loc in error.get("loc", []))
            violations.append(ContractViolation(
                rule=f"OUTPUT_CONTRACT schema: {error.get('msg', str(error))}",
                kind="schema",
                severity="detected",
                evidence=response_text[:120],
                field=field_path,
            ))
    except Exception:
        violations.append(ContractViolation(
            rule=f"OUTPUT_CONTRACT schema: {exc}",
            kind="schema",
            severity="detected",
            evidence=response_text[:120],
        ))
    return violations
