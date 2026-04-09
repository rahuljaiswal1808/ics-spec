"""Anthropic provider — uses explicit cache_control blocks and tool_use."""

from __future__ import annotations

import json
from typing import Any

from ics_runtime.providers.base import ProviderBase, ProviderMessage, ProviderResponse

# Minimum tokens for a cache breakpoint to be worthwhile (Anthropic requires ≥4096)
_MIN_CACHE_TOKENS = 4096


class AnthropicProvider(ProviderBase):
    """Calls claude-* models via the Anthropic SDK.

    System content is passed as a list of blocks; stable layers receive
    ``cache_control: {"type": "ephemeral"}`` so they are written to the
    prompt cache on the first call and read cheaply on subsequent calls.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        api_key: str | None = None,
    ) -> None:
        try:
            import anthropic as _anthropic
        except ImportError as exc:
            raise ImportError(
                "anthropic package is required. Run: pip install 'anthropic>=0.25'"
            ) from exc

        self.model = model
        self._client = _anthropic.Anthropic(api_key=api_key)

    def complete(
        self,
        *,
        system_blocks: list[dict],
        messages: list[ProviderMessage],
        tools: list[dict] | None = None,
        max_tokens: int = 2048,
    ) -> ProviderResponse:
        import anthropic as _anthropic

        sdk_messages = [
            {"role": m.role, "content": m.content}
            for m in messages
        ]

        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=sdk_messages,
        )
        if tools:
            kwargs["tools"] = tools

        response = self._client.messages.create(**kwargs)

        # Extract text from content blocks
        text_parts: list[str] = []
        tool_calls: list[dict] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        usage = response.usage
        return ProviderResponse(
            text="\n".join(text_parts),
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0),
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0),
            tool_calls=tool_calls,
            raw=response,
        )

    def tool_result_message(
        self,
        tool_call_id: str,
        result: Any,
    ) -> ProviderMessage:
        content = result if isinstance(result, str) else json.dumps(result)
        return ProviderMessage(
            role="user",
            content=[
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": content,
                }
            ],
        )
