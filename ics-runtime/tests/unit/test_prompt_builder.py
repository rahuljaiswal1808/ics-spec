"""Unit tests for PromptBuilder — no API calls required."""

import pytest
from ics_runtime.prompt.builder import PromptBuilder


def test_anthropic_stable_layers_get_cache_control():
    b = PromptBuilder("anthropic")
    blocks = b.build_system(
        immutable="You are a test agent.",
        capability="ALLOW: testing",
    )
    assert len(blocks) >= 2
    for block in blocks:
        assert block["type"] == "text"
        assert "###ICS:" in block["text"]
    # Both stable layers should have cache_control
    texts_with_cc = [b for b in blocks if "cache_control" in b]
    assert len(texts_with_cc) == 2


def test_anthropic_dynamic_session_state_no_cache_control():
    b = PromptBuilder("anthropic")
    blocks = b.build_system(
        immutable="System",
        capability="ALLOW: all",
        session_state="Turn 1: asked about topic X",
    )
    session_blocks = [bl for bl in blocks if "SESSION_STATE" in bl["text"]]
    assert len(session_blocks) == 1
    assert "cache_control" not in session_blocks[0]


def test_anthropic_output_contract_gets_cache_control():
    b = PromptBuilder("anthropic")
    blocks = b.build_system(
        immutable="System",
        capability="ALLOW: all",
        output_contract="FORMAT: json",
    )
    oc_blocks = [bl for bl in blocks if "OUTPUT_CONTRACT" in bl["text"]]
    assert len(oc_blocks) == 1
    assert "cache_control" in oc_blocks[0]


def test_openai_returns_single_block():
    b = PromptBuilder("openai")
    blocks = b.build_system(
        immutable="You are a test agent.",
        capability="ALLOW: testing",
        session_state="Turn 1",
    )
    assert len(blocks) == 1
    assert blocks[0]["type"] == "text"
    # Stable layers must appear before dynamic layers
    text = blocks[0]["text"]
    immutable_pos = text.index("IMMUTABLE_CONTEXT")
    session_pos = text.index("SESSION_STATE")
    assert immutable_pos < session_pos


def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unknown provider"):
        PromptBuilder("gemini")


def test_empty_layers_excluded():
    b = PromptBuilder("anthropic")
    blocks = b.build_system(immutable="System", capability="ALLOW: all", session_state="")
    session_blocks = [bl for bl in blocks if "SESSION_STATE" in bl["text"]]
    assert len(session_blocks) == 0
