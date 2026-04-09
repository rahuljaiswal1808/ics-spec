"""ICS Prompt Builder — assembles ICS layers into provider-specific API payloads.

The builder is the single place where:
- ICS layer text is converted to provider-specific block format
- cache_control markers are injected (Anthropic) or layers are ordered (OpenAI)
- Tool contract text is appended to the CAPABILITY_DECLARATION layer
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Layers that are stable across calls and eligible for prompt caching.
# Matches the cache_eligible logic in ics-spec/ics_prompt.py.
_CACHE_ELIGIBLE_LAYERS = {"IMMUTABLE_CONTEXT", "CAPABILITY_DECLARATION", "OUTPUT_CONTRACT"}


def _ics_block(text: str, *, layer_name: str) -> str:
    """Wrap text in ICS wire format delimiters."""
    return f"###ICS:{layer_name}###\n{text}\n###END:{layer_name}###"


class PromptBuilder:
    """Build provider-specific system prompts from ICS layer texts.

    One PromptBuilder per provider type.  It is stateless — call
    ``build_system()`` on every turn with the current layer texts.

    Args:
        provider: ``"anthropic"`` or ``"openai"``
    """

    def __init__(self, provider: str) -> None:
        if provider not in ("anthropic", "openai"):
            raise ValueError(f"Unknown provider '{provider}'. Use 'anthropic' or 'openai'.")
        self.provider = provider

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_system(
        self,
        *,
        immutable: str,
        capability: str,
        session_state: str = "",
        output_contract: str = "",
    ) -> list[dict]:
        """Return provider-formatted system content blocks.

        Each element in the returned list is a ``dict`` suitable for
        passing directly to the provider SDK.

        Anthropic: ``[{"type": "text", "text": "...", "cache_control": {...}}, ...]``
        OpenAI:    ``[{"type": "text", "text": "<all layers joined>"}]``

        Args:
            immutable:       Text for the IMMUTABLE_CONTEXT layer.
            capability:      Text for the CAPABILITY_DECLARATION layer (may include
                             tool contract text appended by the caller).
            session_state:   Text for the SESSION_STATE layer (dynamic, not cached).
            output_contract: Text for the OUTPUT_CONTRACT layer (stable, cached).
        """
        # Build ordered layer texts: stable first, then dynamic
        stable = [
            ("IMMUTABLE_CONTEXT", immutable),
            ("CAPABILITY_DECLARATION", capability),
        ]
        if output_contract:
            stable.append(("OUTPUT_CONTRACT", output_contract))

        dynamic = []
        if session_state:
            dynamic.append(("SESSION_STATE", session_state))

        if self.provider == "anthropic":
            return self._anthropic_blocks(stable, dynamic)
        else:
            return self._openai_block(stable, dynamic)

    # ------------------------------------------------------------------
    # Anthropic: one block per layer with cache_control on stable layers
    # ------------------------------------------------------------------

    def _anthropic_blocks(
        self,
        stable: list[tuple[str, str]],
        dynamic: list[tuple[str, str]],
    ) -> list[dict]:
        blocks: list[dict] = []

        for layer_name, text in stable:
            if not text.strip():
                continue
            block: dict = {
                "type": "text",
                "text": _ics_block(text, layer_name=layer_name),
            }
            if layer_name in _CACHE_ELIGIBLE_LAYERS:
                block["cache_control"] = {"type": "ephemeral"}
            blocks.append(block)

        for layer_name, text in dynamic:
            if not text.strip():
                continue
            blocks.append({
                "type": "text",
                "text": _ics_block(text, layer_name=layer_name),
            })

        return blocks

    # ------------------------------------------------------------------
    # OpenAI: single text block (stable prefix first for auto-caching)
    # ------------------------------------------------------------------

    def _openai_block(
        self,
        stable: list[tuple[str, str]],
        dynamic: list[tuple[str, str]],
    ) -> list[dict]:
        parts: list[str] = []

        for layer_name, text in stable:
            if text.strip():
                parts.append(_ics_block(text, layer_name=layer_name))

        for layer_name, text in dynamic:
            if text.strip():
                parts.append(_ics_block(text, layer_name=layer_name))

        return [{"type": "text", "text": "\n\n".join(parts)}]
