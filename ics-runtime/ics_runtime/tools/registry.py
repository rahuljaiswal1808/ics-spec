"""ToolRegistry — maps tool names to callables and provider-specific schemas."""

from __future__ import annotations

import re
from typing import Any, Callable

from ics_runtime.tools.schema import ToolSchema


def _sanitize_tool_name(name: str) -> str:
    """Sanitize a tool name to match provider requirements (^[a-zA-Z0-9_-]+$).

    Both Anthropic and OpenAI reject names containing dots or other special
    characters.  Non-conforming characters are replaced with double underscores
    so the mapping remains unambiguous and reversible via the name maps.
    """
    return re.sub(r"[^a-zA-Z0-9_-]", "__", name)


class ToolRegistry:
    """Holds all @tool-decorated functions for an Agent.

    Responsibilities:
    - Translate tool definitions to Anthropic and OpenAI wire formats
    - Dispatch ``tool_call`` requests from the model to real Python functions
    - Enforce deny flags before each call
    """

    def __init__(self, tools: list[Callable]) -> None:
        self._tools: dict[str, Callable] = {}
        self._schemas: dict[str, ToolSchema] = {}
        self._sanitized_name_map: dict[str, str] = {}  # sanitized_name -> ics_name

        for fn in tools:
            schema: ToolSchema | None = getattr(fn, "ics_tool_schema", None)
            if schema is None:
                raise ValueError(
                    f"Function '{fn.__name__}' was passed to Agent(tools=...) but is not "
                    f"decorated with @tool().  Add @tool(name='...') to the function."
                )
            self._tools[schema.name] = fn
            self._schemas[schema.name] = schema
            sanitized = _sanitize_tool_name(schema.name)
            self._sanitized_name_map[sanitized] = schema.name

    # ------------------------------------------------------------------
    # Provider schema conversion
    # ------------------------------------------------------------------

    def to_provider_tools(self, provider: str) -> list[dict]:
        if provider == "anthropic":
            return self.to_anthropic_tools()
        elif provider == "openai":
            return self.to_openai_tools()
        raise ValueError(f"Unknown provider '{provider}'")

    def to_anthropic_tools(self) -> list[dict]:
        return [
            {
                "name": _sanitize_tool_name(s.name),
                "description": s.description,
                "input_schema": s.input_json_schema,
            }
            for s in self._schemas.values()
        ]

    def to_openai_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": _sanitize_tool_name(s.name),
                    "description": s.description,
                    "parameters": s.input_json_schema,
                },
            }
            for s in self._schemas.values()
        ]

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    def dispatch(self, name: str, inp: dict[str, Any]) -> Any:
        """Look up and call a tool by its ICS or OpenAI-sanitized name.

        Enforces deny flags before calling the underlying function.

        Raises:
            KeyError:         if the tool name is not registered.
            ToolDeniedError:  if a deny flag blocks the call.
        """
        from ics_runtime.exceptions import ToolDeniedError

        # Resolve sanitized names (from either provider) back to ICS names
        ics_name = self._sanitized_name_map.get(name, name)

        if ics_name not in self._tools:
            raise KeyError(f"Tool '{name}' is not registered in this agent.")

        schema = self._schemas[ics_name]
        fn = self._tools[ics_name]

        # Enforce deny flags
        if schema.deny_bulk_export:
            # Heuristic: if any list-valued input has >50 items, block it
            for k, v in inp.items():
                if isinstance(v, list) and len(v) > 50:
                    raise ToolDeniedError(
                        ics_name,
                        f"deny_bulk_export: input field '{k}' has {len(v)} items (max 50)",
                    )
            # Block wildcard/glob patterns that suggest bulk export
            for k, v in inp.items():
                if isinstance(v, str) and any(c in v for c in ("*", "%", "all", "ALL")):
                    raise ToolDeniedError(
                        ics_name,
                        f"deny_bulk_export: wildcard pattern detected in '{k}'",
                    )

        # Call through __wrapped__ to skip functools.wraps overhead
        actual_fn = getattr(fn, "__wrapped__", fn)
        return actual_fn(**inp)

    def names(self) -> list[str]:
        return list(self._tools.keys())
