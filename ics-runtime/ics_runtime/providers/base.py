"""Abstract provider interface normalizing Anthropic and OpenAI wire formats."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderMessage:
    """A single message in a conversation turn."""
    role: str  # "user" or "assistant"
    content: str | list[dict]  # str for simple text; list for tool_use/tool_result blocks
    tool_calls: list[dict] | None = None  # normalized tool calls for OpenAI assistant messages


@dataclass
class ProviderResponse:
    """Normalized response from any provider."""
    text: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    # Raw tool calls requested by the model (provider-specific format normalized here)
    tool_calls: list[dict] = field(default_factory=list)
    # Raw response object from the SDK (for debugging / future extension)
    raw: Any = None

    @property
    def cache_hit(self) -> bool:
        return self.cache_read_tokens > 0

    @property
    def total_input_tokens(self) -> int:
        """Tokens that were NOT served from cache (billed)."""
        return self.input_tokens

    @property
    def tokens_saved(self) -> int:
        """Input tokens served from cache (not re-billed at full price)."""
        return self.cache_read_tokens


class ProviderBase(abc.ABC):
    """Abstract base for LLM provider adapters.

    Each subclass translates between the ICS Runtime's internal message format
    and the provider's wire format (cache_control blocks, tool schemas, etc.).
    """

    model: str  # must be set by subclass

    @abc.abstractmethod
    def complete(
        self,
        *,
        system_blocks: list[dict],   # Pre-formatted system content blocks (with cache_control)
        messages: list[ProviderMessage],
        tools: list[dict] | None = None,
        max_tokens: int = 2048,
    ) -> ProviderResponse:
        """Call the LLM and return a normalized response."""
        ...

    @abc.abstractmethod
    def tool_result_message(
        self,
        tool_call_id: str,
        result: Any,
    ) -> ProviderMessage:
        """Build a provider-appropriate tool result message."""
        ...
