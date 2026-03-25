"""Agent — the top-level developer-facing object for ICS Runtime."""

from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

from ics_runtime.contracts.capability_enforcer import CapabilityEnforcer
from ics_runtime.contracts.output_contract import OutputContract
from ics_runtime.core.session import Session, SessionContext
from ics_runtime.prompt.builder import PromptBuilder
from ics_runtime.session_backends.memory import MemoryBackend
from ics_runtime.session_backends.base import SessionBackend
from ics_runtime.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from ics_runtime.providers.base import ProviderBase


def _make_provider(provider: str, model: str, api_key: str | None, **kwargs: Any) -> "ProviderBase":
    if provider == "anthropic":
        from ics_runtime.providers.anthropic import AnthropicProvider
        return AnthropicProvider(model=model, api_key=api_key, **kwargs)
    elif provider == "openai":
        from ics_runtime.providers.openai import OpenAIProvider
        return OpenAIProvider(model=model, api_key=api_key, **kwargs)
    raise ValueError(
        f"Unknown provider '{provider}'. Supported: 'anthropic', 'openai'."
    )


_DEFAULT_MODELS = {
    "anthropic": "claude-3-5-sonnet-20241022",
    "openai": "gpt-4o",
}


class Agent:
    """Top-level developer-facing object.  Holds the static ICS configuration
    and spawns ``Session`` instances for each conversation.

    The Agent itself is stateless — all mutable conversation state lives in
    ``Session``.  Creating an Agent is cheap and can be done at module level.

    Args:
        provider:        ``"anthropic"`` or ``"openai"``.
        system:          Shorthand for ``immutable``.  Convenience for simple
                         agents without separate capability declarations.
        immutable:       Text for the IMMUTABLE_CONTEXT ICS layer.
        capability:      Text for the CAPABILITY_DECLARATION ICS layer.
                         ALLOW/DENY/REQUIRE directives go here.
        model:           Provider model identifier.  Defaults to the provider's
                         recommended model if omitted.
        tools:           List of ``@tool``-decorated callables.
        output_contract: ``OutputContract`` instance for post-execution
                         schema validation.
        session_backend: Storage backend for session state.  Defaults to
                         ``MemoryBackend`` (in-process, no persistence).
        api_key:         API key for the provider.  Falls back to the standard
                         environment variable (``ANTHROPIC_API_KEY`` /
                         ``OPENAI_API_KEY``) if not supplied.

    Example::

        agent = Agent(
            provider="anthropic",
            immutable="You are a BFSI lead qualification assistant.",
            capability="DENY: logging PII\\nREQUIRE: risk category on every result",
            tools=[lookup_lead],
            output_contract=OutputContract(schema=QualificationResult),
        )

        with agent.session(lead_id="L-42") as session:
            result = session.run("Qualify lead L-42")
            print(result.text)
            print(result.cache_hit)
    """

    def __init__(
        self,
        provider: str = "anthropic",
        *,
        system: str = "",
        immutable: str = "",
        capability: str = "",
        model: str | None = None,
        tools: list[Callable] | None = None,
        output_contract: OutputContract | None = None,
        session_backend: SessionBackend | None = None,
        api_key: str | None = None,
        **provider_kwargs: Any,
    ) -> None:
        self._provider_name = provider
        self._immutable = immutable or system
        self._capability = capability
        self._model = model or _DEFAULT_MODELS.get(provider, "claude-3-5-sonnet-20241022")
        self._output_contract = output_contract
        self._backend: SessionBackend = session_backend or MemoryBackend()

        # Tool registry (None if no tools)
        self._registry: ToolRegistry | None = (
            ToolRegistry(tools) if tools else None
        )

        # Provider adapter
        self._provider: ProviderBase = _make_provider(
            provider, self._model, api_key, **provider_kwargs
        )

        # Prompt builder
        self._prompt_builder = PromptBuilder(provider)

        # Capability enforcer (always present, even with empty capability text)
        self._capability_enforcer = CapabilityEnforcer(capability) if capability else None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def session(self, **session_vars: Any) -> SessionContext:
        """Return a context manager that yields a ``Session``.

        ``session_vars`` are injected into the SESSION_STATE ICS layer on
        the first turn, providing initial context without requiring the
        caller to pre-format ICS text.

        Usage::

            with agent.session(lead_id="L-42") as session:
                result = session.run("Qualify this lead")
        """
        return SessionContext(self, session_vars)

    def run(self, task: str, **session_vars: Any) -> "RunResult":  # type: ignore[name-defined]
        """One-shot convenience: open a session, run one task, return the result.

        Equivalent to::

            with agent.session(**session_vars) as session:
                return session.run(task)
        """
        from ics_runtime.core.result import RunResult
        import uuid
        session = Session(self, str(uuid.uuid4()), session_vars)
        return session.run(task)
