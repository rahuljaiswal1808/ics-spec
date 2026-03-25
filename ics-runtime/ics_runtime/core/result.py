"""RunResult — the return value of Session.run()."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ics_runtime.contracts.violation import ContractViolation


@dataclass(frozen=True)
class ToolCallRecord:
    """A single tool invocation that occurred during a run."""
    tool_name: str
    input: dict
    output: Any
    duration_ms: int
    blocked: bool = False  # True if a ToolContract denied this call


@dataclass
class RunResult:
    """Structured output from a single Session.run() call.

    Contains the LLM response together with all observability fields so
    callers can track caching savings, contract violations, and cost in
    the same object they already handle.
    """

    # Core output
    text: str
    validated: bool
    violations: list["ContractViolation"] = field(default_factory=list)
    parsed: Any = None  # Pydantic model instance if OutputContract.schema is set

    # Cache metrics
    cache_hit: bool = False
    cache_write: bool = False
    tokens_saved: int = 0      # tokens read from cache (not re-billed at full rate)

    # Raw token counts
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0

    # Tool invocations
    tool_calls: list[ToolCallRecord] = field(default_factory=list)

    # Session context
    session_id: str = ""
    turn_number: int = 0

    # Performance
    latency_ms: int = 0
    model: str = ""
    provider: str = ""

    # Estimated cost (USD)
    cost_usd: float = 0.0

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def raise_on_violation(self) -> "RunResult":
        """Raise ContractViolationError if this result has violations.

        Enables a fluent pattern::

            result = session.run("...").raise_on_violation()
        """
        if self.violations:
            from ics_runtime.exceptions import ContractViolationError
            raise ContractViolationError(self.violations)
        return self

    @property
    def ok(self) -> bool:
        """True if the response is validated and has no violations."""
        return self.validated and not self.violations

    def summary(self) -> str:
        lines = [
            f"provider={self.provider} model={self.model}",
            f"tokens in={self.input_tokens} out={self.output_tokens} "
            f"cached={self.tokens_saved} write={self.cache_write_tokens}",
            f"cache_hit={self.cache_hit} validated={self.validated} "
            f"violations={len(self.violations)} tools={len(self.tool_calls)}",
            f"cost=${self.cost_usd:.5f} latency={self.latency_ms}ms",
        ]
        if self.violations:
            for v in self.violations:
                lines.append(f"  ⚠  {v}")
        return "\n".join(lines)
