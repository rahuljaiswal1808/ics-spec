"""OpenAI provider — uses prefix caching (automatic) and function_calling."""

from __future__ import annotations

import json
from typing import Any

from ics_runtime.providers.base import ProviderBase, ProviderMessage, ProviderResponse


class OpenAIProvider(ProviderBase):
    """Calls gpt-* / o* models via the OpenAI SDK (v1+).

    OpenAI uses automatic prefix caching — no explicit cache_control markers
    are needed. The system content blocks are joined into a single system
    message; stable content must be at the top to benefit from caching.
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str | None = None,
    ) -> None:
        try:
            import openai as _openai
            if not hasattr(_openai, "AsyncOpenAI"):
                raise ImportError("openai v0.x detected. Run: pip install --upgrade 'openai>=1.0'")
        except ImportError as exc:
            raise ImportError(
                "openai package (v1+) is required. Run: pip install 'openai>=1.0'"
            ) from exc

        self.model = model
        import openai as _openai
        self._client = _openai.AsyncOpenAI(api_key=api_key)

    async def complete(
        self,
        *,
        system_blocks: list[dict],
        messages: list[ProviderMessage],
        tools: list[dict] | None = None,
        max_tokens: int = 2048,
    ) -> ProviderResponse:
        # Join all system blocks into a single system message (stable prefix first)
        system_text = "\n\n".join(
            b["text"] for b in system_blocks if b.get("type") == "text"
        )

        sdk_messages: list[dict] = [{"role": "system", "content": system_text}]
        for m in messages:
            if isinstance(m.content, str):
                sdk_messages.append({"role": m.role, "content": m.content})
            else:
                # tool result blocks → convert to OpenAI tool message format
                for block in m.content:
                    if block.get("type") == "tool_result":
                        sdk_messages.append({
                            "role": "tool",
                            "tool_call_id": block["tool_use_id"],
                            "content": block["content"],
                        })
                    elif block.get("type") == "tool_use":
                        # assistant message with tool call — handled below
                        pass

        # Convert ICS tool schemas (Anthropic format) to OpenAI function format
        oai_tools: list[dict] | None = None
        if tools:
            oai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                    },
                }
                for t in tools
            ]

        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            messages=sdk_messages,
        )
        if oai_tools:
            kwargs["tools"] = oai_tools

        response = await self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        msg = choice.message

        text = msg.content or ""
        tool_calls: list[dict] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": json.loads(tc.function.arguments or "{}"),
                })

        usage = response.usage
        # OpenAI reports cached tokens under prompt_tokens_details
        details = getattr(usage, "prompt_tokens_details", None)
        cache_read = getattr(details, "cached_tokens", 0) if details else 0

        return ProviderResponse(
            text=text,
            input_tokens=getattr(usage, "prompt_tokens", 0) - cache_read,
            output_tokens=getattr(usage, "completion_tokens", 0),
            cache_creation_tokens=0,  # OpenAI doesn't expose write cost separately
            cache_read_tokens=cache_read,
            tool_calls=tool_calls,
            raw=response,
        )

    def tool_result_message(
        self,
        tool_call_id: str,
        result: Any,
    ) -> ProviderMessage:
        content = result if isinstance(result, str) else json.dumps(result)
        # Stored as a list so Session can detect it's a tool result block
        return ProviderMessage(
            role="tool",
            content=[
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": content,
                }
            ],
        )
