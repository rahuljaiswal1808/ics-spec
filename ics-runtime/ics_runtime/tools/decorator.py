"""@tool decorator — attaches ICS tool contract metadata to a Python function."""

from __future__ import annotations

import inspect
import functools
from typing import Any, Callable

from ics_runtime.tools.schema import ToolSchema


def _build_json_schema(fn: Callable) -> dict:
    """Generate a JSON Schema dict from a function's type annotations.

    Supports basic types (str, int, float, bool, list, dict) and
    ``Optional[T]``.  For richer types, pass a Pydantic model as the
    function's first argument and annotate it.
    """
    try:
        import pydantic
        from pydantic import create_model
        hints = {}
        sig = inspect.signature(fn)
        for param_name, param in sig.parameters.items():
            ann = param.annotation
            if ann is inspect.Parameter.empty:
                ann = Any
            default = param.default
            if default is inspect.Parameter.empty:
                hints[param_name] = (ann, ...)
            else:
                hints[param_name] = (ann, default)
        if not hints:
            return {"type": "object", "properties": {}}
        DynamicModel = create_model(f"_{fn.__name__}_Input", **hints)
        schema = DynamicModel.model_json_schema()
        # Remove pydantic $defs nesting for simple schemas
        schema.pop("title", None)
        return schema
    except Exception:
        return {"type": "object", "properties": {}}


def tool(
    name: str,
    description: str | None = None,
    deny_bulk_export: bool = False,
    require_audit_log: bool = False,
    max_calls_per_session: int | None = None,
    **extra_deny_flags: Any,
) -> Callable[[Callable], Callable]:
    """Decorator that registers a Python function as an ICS-managed tool.

    The decorated function's behavior is **unchanged**; only metadata is
    attached.  The original function is preserved at ``decorated_fn.__wrapped__``
    so tests can call it directly without the runtime.

    Usage::

        @tool(name="crm.lookup", deny_bulk_export=True)
        def lookup_lead(lead_id: str) -> dict:
            \"\"\"Look up a lead by ID in the CRM.\"\"\"
            return {"name": "Acme", "revenue": 500_000_00}

    Args:
        name:                    ICS tool name (dot notation, e.g. ``"crm.lookup"``).
        description:             Human-readable description.  Defaults to the
                                 function's docstring first line.
        deny_bulk_export:        Block calls that look like bulk exports.
        require_audit_log:       Emit an audit log entry on every invocation.
        max_calls_per_session:   Hard cap on calls per session (None = unlimited).
        **extra_deny_flags:      Any additional ``deny_*=True`` flags.
    """

    def decorator(fn: Callable) -> Callable:
        doc = fn.__doc__ or ""
        desc = description or doc.strip().splitlines()[0] if doc.strip() else ""
        schema = _build_json_schema(fn)

        tool_schema = ToolSchema(
            name=name,
            description=desc,
            input_json_schema=schema,
            deny_bulk_export=deny_bulk_export,
            require_audit_log=require_audit_log,
            max_calls_per_session=max_calls_per_session,
            extra_deny_flags={k: v for k, v in extra_deny_flags.items() if k.startswith("deny_")},
        )

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        wrapper.__wrapped__ = fn          # type: ignore[attr-defined]
        wrapper.ics_tool_schema = tool_schema  # type: ignore[attr-defined]
        return wrapper

    return decorator
