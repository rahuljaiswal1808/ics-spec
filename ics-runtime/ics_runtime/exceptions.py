"""ICS Runtime exceptions."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ics_runtime.contracts.violation import ContractViolation


class ICSRuntimeError(Exception):
    """Base exception for all ICS Runtime errors."""


class ContractViolationError(ICSRuntimeError):
    """Raised by RunResult.raise_on_violation() when violations are present."""

    def __init__(self, violations: list["ContractViolation"]) -> None:
        self.violations = violations
        rules = ", ".join(v.rule for v in violations)
        super().__init__(f"Contract violated: {rules}")


class RetryExhaustedError(ICSRuntimeError):
    """Raised when all provider retry attempts are exhausted."""

    def __init__(self, attempts: int, last_error: Exception) -> None:
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(f"Provider failed after {attempts} attempts: {last_error}")


class ProviderAuthError(ICSRuntimeError):
    """Raised when the provider returns an authentication error."""


class ToolDeniedError(ICSRuntimeError):
    """Raised when a tool call is blocked by a ToolContract deny flag."""

    def __init__(self, tool_name: str, reason: str) -> None:
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"Tool '{tool_name}' denied: {reason}")


class MaxToolRoundsError(ICSRuntimeError):
    """Raised when the tool execution loop exceeds max_tool_rounds."""

    def __init__(self, rounds: int) -> None:
        self.rounds = rounds
        super().__init__(f"Tool loop exceeded {rounds} rounds without a final text response")
