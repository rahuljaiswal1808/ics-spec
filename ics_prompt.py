"""
ICS Prompt Library — Python

Thin wrappers for constructing ICS-layered prompts directly from code.
Each piece of your system prompt is labelled at the variable level, so
the compiled string the LLM receives is clean and undecorated.

Quick start
-----------
    import ics_prompt as ics

    # Static blocks — assign the layer once, at definition time
    PERSONA = ics.immutable("You are a senior financial analyst assistant.")
    RULES   = ics.capability(\"\"\"
        ALLOW  read-only market-data queries
        DENY   trading actions or account mutations
    \"\"\")
    FORMAT  = ics.output_contract(\"\"\"
        format:   structured markdown
        schema:   { "analysis": "string", "risks": ["string"] }
        variance: "risks" MAY be omitted for informational queries
        on_failure: plain-text apology with brief reason
    \"\"\")

    # Per-call blocks — decorate a factory function
    @ics.session
    def session_ctx(user_name: str, portfolio: str) -> str:
        return f"User: {user_name}.  Portfolio focus: {portfolio}."

    @ics.dynamic
    def task(user_message: str) -> str:
        return f"The user asked: {user_message}"

    # Compile everything into a single ICS-delimited prompt
    prompt = ics.compile(
        PERSONA,
        RULES,
        session_ctx(name, portfolio),
        task(msg),
        FORMAT,
    )

API reference
-------------
    immutable(text)         → ICSBlock  [IMMUTABLE_CONTEXT]
    capability(text)        → ICSBlock  [CAPABILITY_DECLARATION]
    session(text)           → ICSBlock  [SESSION_STATE]
    dynamic(text)           → ICSBlock  [TASK_PAYLOAD]
    output_contract(text)   → ICSBlock  [OUTPUT_CONTRACT]

    Each also works as a decorator on a factory function:
        @ics.dynamic
        def task(msg): return f"User asked: {msg}"
        # task(msg) now returns an ICSBlock

    compile(*blocks, warn=True) → str
        Renders blocks to ICS-delimited text.  Emits Python warnings for
        layer-order violations and template variables in cached blocks.

    validate(*blocks) → list[str]
        Returns the same warnings without rendering.

    parse(prompt) → list[ICSBlock]
        Parses an ICS-delimited string back into ICSBlocks (for testing).
"""

import re
import textwrap
import warnings
import functools
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Union


# ---------------------------------------------------------------------------
# Layer enum + canonical order
# ---------------------------------------------------------------------------

class ICSLayer(Enum):
    IMMUTABLE_CONTEXT      = "IMMUTABLE_CONTEXT"
    CAPABILITY_DECLARATION = "CAPABILITY_DECLARATION"
    SESSION_STATE          = "SESSION_STATE"
    TASK_PAYLOAD           = "TASK_PAYLOAD"
    OUTPUT_CONTRACT        = "OUTPUT_CONTRACT"


# Canonical layer order per spec Section 4.1
_LAYER_ORDER = [
    ICSLayer.IMMUTABLE_CONTEXT,
    ICSLayer.CAPABILITY_DECLARATION,
    ICSLayer.SESSION_STATE,
    ICSLayer.TASK_PAYLOAD,
    ICSLayer.OUTPUT_CONTRACT,
]

_CACHE_ELIGIBLE = {
    ICSLayer.IMMUTABLE_CONTEXT,
    ICSLayer.CAPABILITY_DECLARATION,
    ICSLayer.OUTPUT_CONTRACT,
}

# Template variable patterns (same as auto-classifier)
_TEMPLATE_VAR_RE = re.compile(
    r"\{\{[^}]+\}\}"                      # {{variable}}
    r"|(?<![/{])\{[A-Za-z_]\w*\}(?![/}])" # {variable} — not a URL path param
    r"|\$\{[^}]+\}"                        # ${variable}
    r"|<[A-Z][A-Z_]{2,}>"                 # <PLACEHOLDER>
)


# ---------------------------------------------------------------------------
# ICSBlock
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ICSBlock:
    """An immutable (layer, content) pair.  str() returns the raw content."""

    layer: ICSLayer
    content: str

    def __str__(self) -> str:
        return self.content

    def __repr__(self) -> str:
        preview = self.content[:50].replace("\n", " ").strip()
        return f"ICSBlock({self.layer.value}, {preview!r})"

    @property
    def cache_eligible(self) -> bool:
        return self.layer in _CACHE_ELIGIBLE


# ---------------------------------------------------------------------------
# Factory / decorator builder
# ---------------------------------------------------------------------------

def _make_layer_fn(layer: ICSLayer) -> Callable:
    """
    Return a callable that:
      - when called with a string  → wraps it in an ICSBlock
      - when used as a decorator   → wraps the function's return value
    """
    name = layer.name.lower()  # e.g. "immutable_context" → use short alias below

    def fn(text_or_func: Union[str, Callable]) -> Union["ICSBlock", Callable]:
        if callable(text_or_func):
            @functools.wraps(text_or_func)
            def wrapper(*args, **kwargs) -> ICSBlock:
                return ICSBlock(layer=layer, content=str(text_or_func(*args, **kwargs)))
            return wrapper
        return ICSBlock(layer=layer, content=textwrap.dedent(str(text_or_func)).strip())

    fn.__name__ = name
    fn.__qualname__ = name
    fn.__doc__ = (
        f"Tag text or a factory function as {layer.value}.\n\n"
        f"    As a function:    block = {name}('...')\n"
        f"    As a decorator:   @{name}\n"
        f"                      def my_block(...): return '...'\n"
    )
    return fn


# Public API — one name per layer (short aliases for developer ergonomics)
immutable       = _make_layer_fn(ICSLayer.IMMUTABLE_CONTEXT)
capability      = _make_layer_fn(ICSLayer.CAPABILITY_DECLARATION)
session         = _make_layer_fn(ICSLayer.SESSION_STATE)
dynamic         = _make_layer_fn(ICSLayer.TASK_PAYLOAD)
output_contract = _make_layer_fn(ICSLayer.OUTPUT_CONTRACT)


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------

def validate(*blocks: ICSBlock) -> list[str]:
    """
    Check blocks for common mistakes.  Returns warning strings; does not raise.

    Checks:
      1. Layer order — blocks should follow canonical ICS ordering.
      2. Template variables inside cache-eligible blocks.
    """
    issues: list[str] = []

    # Canonical subset order for layers actually present
    present = [l for l in _LAYER_ORDER if any(b.layer == l for b in blocks)]
    actual: list[ICSLayer] = []
    seen: set[ICSLayer] = set()
    for b in blocks:
        if b.layer not in seen:
            actual.append(b.layer)
            seen.add(b.layer)

    if actual != present:
        issues.append(
            "Layer order deviates from the canonical ICS ordering "
            f"({' → '.join(l.value for l in present)}). "
            "Some prompt-caching implementations depend on stable prefix order."
        )

    # Template variables in cache-eligible blocks
    for b in blocks:
        if b.layer in _CACHE_ELIGIBLE and _TEMPLATE_VAR_RE.search(b.content):
            issues.append(
                f"{b.layer.value} is cache-eligible but its content contains "
                "template variables — the rendered string will differ per call "
                "and should not be cached. "
                "Move dynamic interpolation into a session() or dynamic() block."
            )

    return issues


# ---------------------------------------------------------------------------
# Compile
# ---------------------------------------------------------------------------

def compile(*blocks: ICSBlock, warn: bool = True) -> str:
    """
    Render ICSBlocks into a single ICS-delimited prompt string.

    Args:
        *blocks: ICSBlock instances in the order they should appear.
        warn:    If True (default), emit Python warnings for validation issues.

    Returns:
        A string with ###ICS:LAYER### ... ###END:LAYER### delimiters,
        ready to send as a system prompt.

    Raises:
        TypeError: if a plain string is passed instead of an ICSBlock.
    """
    for i, block in enumerate(blocks):
        if not isinstance(block, ICSBlock):
            raise TypeError(
                f"Argument {i} is {type(block).__name__!r}, expected ICSBlock. "
                "Wrap it with ics.immutable(), ics.dynamic(), etc."
            )

    if warn:
        for issue in validate(*blocks):
            warnings.warn(issue, stacklevel=2)

    parts: list[str] = []
    for block in blocks:
        layer = block.layer.value
        parts.append(f"###ICS:{layer}###\n{block.content}\n###END:{layer}###")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Parse (round-trip / testing helper)
# ---------------------------------------------------------------------------

_PARSE_RE = re.compile(
    r"###ICS:([A-Z_]+)###\n(.*?)###END:\1###",
    re.DOTALL,
)

_LAYER_BY_NAME = {l.value: l for l in ICSLayer}


def parse(prompt: str) -> list[ICSBlock]:
    """
    Parse an ICS-delimited prompt string back into a list of ICSBlocks.

    Useful for testing round-trips and for tools that receive already-compiled
    prompts and need to inspect their structure.

    Unknown layer names are silently skipped.
    """
    blocks: list[ICSBlock] = []
    for layer_name, content in _PARSE_RE.findall(prompt):
        layer = _LAYER_BY_NAME.get(layer_name)
        if layer is not None:
            blocks.append(ICSBlock(layer=layer, content=content.strip()))
    return blocks
