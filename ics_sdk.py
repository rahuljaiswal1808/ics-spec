#!/usr/bin/env python3
"""
ICS SDK — Runtime enforcement wrapper for ICS-compliant LLM calls.

Wraps an Anthropic or OpenAI client so that every call is automatically
validated against the OUTPUT_CONTRACT declared in the ICS document.
Violations surface as structured exceptions rather than silent bad output.

Usage:
    import anthropic
    from ics_sdk import ICSClient

    client = ICSClient(anthropic.Anthropic())

    result = client.complete(
        ics=open("examples/payments-platform.ics").read(),
        user_message="Add retry logic to deliver()",
    )
    print(result.content)   # the LLM output
    print(result.blocked)   # True if the model returned BLOCKED:
    print(result.usage)     # {"input_tokens": ..., "output_tokens": ...}

Exceptions:
    InvalidContractError    — the ICS document itself is not spec-compliant
    ContractViolationError  — the model output violates OUTPUT_CONTRACT

Supported providers:
    anthropic   — anthropic.Anthropic (or anthropic.AsyncAnthropic is not supported)
    openai      — openai.OpenAI

Provider detection is duck-typed from the client's module name; no hard
imports of anthropic or openai are required at module load time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ics_validator import validate, validate_output, ValidationResult


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ICSError(Exception):
    """Base exception for ICS SDK errors."""


class InvalidContractError(ICSError):
    """Raised when the ICS document fails structural validation."""

    def __init__(self, message: str, validation_result: ValidationResult) -> None:
        super().__init__(message)
        self.validation_result = validation_result


class ContractViolationError(ICSError):
    """Raised when the LLM output violates the OUTPUT_CONTRACT."""

    def __init__(self, message: str, validation_result: ValidationResult) -> None:
        super().__init__(message)
        self.validation_result = validation_result


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ICSResult:
    """The outcome of a validated ICS call."""

    content: str
    """Raw LLM output text."""

    validation: ValidationResult
    """Output contract validation result (always present, even on success)."""

    blocked: bool
    """True if the model returned a BLOCKED: on_failure response."""

    model: str
    """Model identifier used for the call."""

    usage: dict = field(default_factory=dict)
    """Token usage reported by the provider: input_tokens, output_tokens."""


# ---------------------------------------------------------------------------
# Provider detection and call adapters
# ---------------------------------------------------------------------------

_ANTHROPIC_DEFAULT_MODEL = "claude-opus-4-6"
_OPENAI_DEFAULT_MODEL    = "gpt-4o"


def _detect_provider(client: Any) -> str:
    module = type(client).__module__
    if module.startswith("anthropic"):
        return "anthropic"
    if module.startswith("openai"):
        return "openai"
    raise ICSError(
        f"Unsupported client type '{type(client).__name__}'. "
        "Pass an anthropic.Anthropic or openai.OpenAI instance."
    )


def _default_model(provider: str) -> str:
    return _ANTHROPIC_DEFAULT_MODEL if provider == "anthropic" else _OPENAI_DEFAULT_MODEL


def _call_anthropic(
    client: Any,
    model: str,
    system: str,
    user_message: str,
    max_tokens: int,
    **kwargs: Any,
) -> tuple[str, dict]:
    response = client.messages.create(
        model=model,
        system=system,
        messages=[{"role": "user", "content": user_message}],
        max_tokens=max_tokens,
        **kwargs,
    )
    content = response.content[0].text
    usage = {
        "input_tokens":  response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return content, usage


def _call_openai(
    client: Any,
    model: str,
    system: str,
    user_message: str,
    max_tokens: int,
    **kwargs: Any,
) -> tuple[str, dict]:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user_message},
        ],
        max_tokens=max_tokens,
        **kwargs,
    )
    content = response.choices[0].message.content
    usage = {
        "input_tokens":  response.usage.prompt_tokens,
        "output_tokens": response.usage.completion_tokens,
    }
    return content, usage


_PROVIDER_CALLERS = {
    "anthropic": _call_anthropic,
    "openai":    _call_openai,
}


# ---------------------------------------------------------------------------
# ICSClient
# ---------------------------------------------------------------------------

class ICSClient:
    """
    LLM client wrapper that enforces ICS OUTPUT_CONTRACT on every call.

    Args:
        client:
            An ``anthropic.Anthropic`` or ``openai.OpenAI`` instance.
        model:
            Model identifier. Defaults to the provider's recommended model
            (claude-opus-4-6 for Anthropic, gpt-4o for OpenAI).
        max_tokens:
            Maximum tokens for the completion. Default: 4096.
        raise_on_violation:
            If ``True`` (default), raises ``ContractViolationError`` when the
            output fails OUTPUT_CONTRACT checks. If ``False``, returns the
            ``ICSResult`` with ``validation.compliant == False`` instead.
        validate_contract:
            If ``True`` (default), validates the ICS document against the spec
            before calling the model. Raises ``InvalidContractError`` if the
            document is not compliant.
    """

    def __init__(
        self,
        client: Any,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        raise_on_violation: bool = True,
        validate_contract: bool = True,
    ) -> None:
        self._client           = client
        self._provider         = _detect_provider(client)
        self._model            = model or _default_model(self._provider)
        self._max_tokens       = max_tokens
        self._raise_on_violation  = raise_on_violation
        self._validate_contract   = validate_contract

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def complete(
        self,
        ics: str,
        user_message: str,
        **kwargs: Any,
    ) -> ICSResult:
        """
        Call the LLM with the ICS document as the system prompt, then
        validate the output against the declared OUTPUT_CONTRACT.

        Args:
            ics:          Full ICS document text (all five layers).
            user_message: The user-turn message to send to the model.
            **kwargs:     Additional parameters forwarded verbatim to the
                          underlying provider API (e.g. ``temperature``).

        Returns:
            :class:`ICSResult` containing the content, validation result,
            blocked flag, model name, and token usage.

        Raises:
            InvalidContractError:   ICS document is not spec-compliant.
            ContractViolationError: Output violates OUTPUT_CONTRACT and
                                    ``raise_on_violation=True``.
        """
        if self._validate_contract:
            self._check_contract(ics)

        content, usage = _PROVIDER_CALLERS[self._provider](
            self._client, self._model, ics, user_message, self._max_tokens, **kwargs
        )

        output_result = validate_output(ics, content)
        blocked       = content.strip().startswith("BLOCKED:")

        result = ICSResult(
            content=content,
            validation=output_result,
            blocked=blocked,
            model=self._model,
            usage=usage,
        )

        if self._raise_on_violation and not output_result.compliant:
            raise ContractViolationError(
                f"Output violates OUTPUT_CONTRACT: "
                f"{output_result.violations[0].message}",
                output_result,
            )

        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _check_contract(self, ics: str) -> None:
        result = validate(ics)
        if not result.compliant:
            raise InvalidContractError(
                f"ICS document is not valid: {result.violations[0].message}",
                result,
            )
