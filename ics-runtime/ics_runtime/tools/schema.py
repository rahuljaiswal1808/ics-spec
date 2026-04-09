"""ToolSchema — metadata attached to @tool-decorated functions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolSchema:
    """Contract metadata for a single tool function.

    Attached as ``fn.ics_tool_schema`` by the ``@tool`` decorator.
    The runtime reads this when building provider tool lists and when
    enforcing deny flags before a tool call executes.
    """

    name: str
    description: str
    input_json_schema: dict    # JSON Schema for tool inputs (Pydantic-generated)
    deny_bulk_export: bool = False
    require_audit_log: bool = False
    max_calls_per_session: int | None = None
    # Additional deny flags stored as a dict for extensibility
    extra_deny_flags: dict[str, Any] = field(default_factory=dict)
