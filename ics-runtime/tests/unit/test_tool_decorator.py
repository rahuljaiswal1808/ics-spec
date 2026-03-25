"""Unit tests for @tool decorator and ToolRegistry."""

import pytest
from ics_runtime.tools.decorator import tool
from ics_runtime.tools.registry import ToolRegistry
from ics_runtime.exceptions import ToolDeniedError


@tool(name="crm.lookup", description="Look up a lead by ID.")
def lookup_lead(lead_id: str) -> dict:
    return {"id": lead_id, "name": "Acme Corp"}


@tool(name="data.export", deny_bulk_export=True)
def export_data(ids: list) -> list:
    """Export records by ID list."""
    return ids


def test_decorator_attaches_schema():
    assert hasattr(lookup_lead, "ics_tool_schema")
    assert lookup_lead.ics_tool_schema.name == "crm.lookup"


def test_decorated_function_still_callable():
    result = lookup_lead(lead_id="L-1")
    assert result["name"] == "Acme Corp"


def test_wrapped_fn_preserved():
    assert hasattr(lookup_lead, "__wrapped__")
    result = lookup_lead.__wrapped__(lead_id="L-2")
    assert result["id"] == "L-2"


def test_registry_dispatch():
    reg = ToolRegistry([lookup_lead])
    result = reg.dispatch("crm.lookup", {"lead_id": "L-99"})
    assert result["id"] == "L-99"


def test_registry_anthropic_tools_format():
    reg = ToolRegistry([lookup_lead])
    tools = reg.to_anthropic_tools()
    assert len(tools) == 1
    # Anthropic also requires ^[a-zA-Z0-9_-]+$ — dots are sanitized
    assert "." not in tools[0]["name"]
    assert "crm" in tools[0]["name"]
    assert "input_schema" in tools[0]


def test_registry_openai_tools_format():
    reg = ToolRegistry([lookup_lead])
    tools = reg.to_openai_tools()
    assert tools[0]["type"] == "function"
    assert "function" in tools[0]
    # dots should be replaced
    assert "." not in tools[0]["function"]["name"]


def test_deny_bulk_export_blocks_wildcard():
    reg = ToolRegistry([export_data])
    with pytest.raises(ToolDeniedError, match="wildcard"):
        reg.dispatch("data.export", {"ids": "*"})


def test_deny_bulk_export_blocks_large_list():
    reg = ToolRegistry([export_data])
    with pytest.raises(ToolDeniedError, match="51 items"):
        reg.dispatch("data.export", {"ids": list(range(51))})


def test_deny_bulk_export_allows_small_list():
    reg = ToolRegistry([export_data])
    result = reg.dispatch("data.export", {"ids": [1, 2, 3]})
    assert result == [1, 2, 3]


def test_undecorated_function_raises():
    def plain(x: str) -> str:
        return x
    with pytest.raises(ValueError, match="not decorated"):
        ToolRegistry([plain])
