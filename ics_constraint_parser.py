#!/usr/bin/env python3
"""
ICS Constraint Language Parser — M3

Provides a formal grammar and a parser that turns raw ICS text blocks into
structured ASTs. Two grammars are specified and implemented:

  1. CAPABILITY_DECLARATION directive grammar (§3.2)
  2. OUTPUT_CONTRACT field grammar (§3.5)

Both parsers raise :class:`ParseError` on malformed input and return typed
dataclasses on success.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

§3.2  CAPABILITY_DECLARATION Grammar (EBNF)
───────────────────────────────────────────

  capability_block ::= (blank_line | comment | directive)*

  blank_line  ::= WS* NL
  comment     ::= WS* "#" text NL

  directive   ::= keyword WS+ action
                  [WS+ qualifier]
                  [WS+ cond_clause]
                  NL

  keyword     ::= "ALLOW" | "DENY" | "REQUIRE"
                  -- case-insensitive

  action      ::= word (WS+ word)*
                  -- one or more words; terminates at the first QWORD or "IF",
                     or at end of input; whitespace is normalised to single space

  qualifier   ::= QWORD WS+ target
  QWORD       ::= "WITHIN" | "ON" | "WITH" | "UNLESS"
                  -- matched as whole tokens (case-insensitive)
  target      ::= word (WS+ word)*
                  -- terminates at "IF" or end of input

  cond_clause ::= "IF" WS+ condition
  condition   ::= word (WS+ word)*
                  -- consumes the remainder of the line

  word        ::= [^ \\t]+
  WS          ::= " " | "\\t"
  NL          ::= "\\n"

Notes:
  • QWORD and "IF" are reserved only as standalone tokens; they are NOT
    reserved when they appear as substrings of a longer word.  For example,
    "modification" contains "ON" but is not a qualifier.
  • UNLESS is an exception qualifier: the directive applies unless the
    condition specified by UNLESS is met.  Its semantics are defined by the
    ICS spec (§3.2); this parser records it as a qualifier without
    interpreting it.
  • At most one QWORD and one IF clause are permitted per directive.  If more
    than one QWORD appears, only the first is treated as the qualifier; the
    remainder are absorbed into the target.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

§3.5  OUTPUT_CONTRACT Grammar (EBNF)
──────────────────────────────────────

  oc_block    ::= oc_field (NL oc_field)*

  oc_field    ::= field_name COLON WS* first_line
                  (NL continuation)*

  field_name  ::= "format" | "schema" | "variance" | "on_failure"
                  -- case-insensitive; additional fields are collected in
                     OutputContract.extra_fields and trigger a warning

  COLON       ::= ":"
  first_line  ::= text       -- content on the same line as field_name:
  continuation ::= WS+ text  -- must be indented by at least one space or tab;
                               a non-indented line that doesn't start a new
                               field is also treated as continuation (for
                               leniency with inline schema blocks like JSON)

  text        ::= [^\\n]*
  WS          ::= " " | "\\t"
  NL          ::= "\\n"

Required fields: format, schema, variance, on_failure.
Missing required fields raise :class:`ParseError`.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Usage:
    from ics_constraint_parser import (
        parse_directive,
        parse_capability_block,
        parse_output_contract,
        Directive,
        OutputContract,
        ParseError,
    )

    d = parse_directive("ALLOW file modification WITHIN src/ IF the file is a .py file")
    # Directive(keyword='ALLOW', action='file modification',
    #           qualifier_word='WITHIN', qualifier_target='src/',
    #           condition='the file is a .py file', raw='...')

    oc = parse_output_contract(open("mycontract.ics").read())
    # OutputContract(format='JSON', schema='{...}', variance='none', on_failure='...')
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

KEYWORDS:  frozenset[str] = frozenset({"ALLOW", "DENY", "REQUIRE"})
QUALIFIERS: frozenset[str] = frozenset({"WITHIN", "ON", "WITH", "UNLESS"})
OC_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"format", "schema", "variance", "on_failure"}
)

# Matches the start of an OUTPUT_CONTRACT field line, e.g. "format:" or "on_failure:  "
_OC_FIELD_RE = re.compile(
    r"^(format|schema|variance|on_failure)\s*:",
    re.IGNORECASE,
)

# Detects any unknown field that still looks like "key:" at line start
_OC_ANY_FIELD_RE = re.compile(r"^([a-z_][a-z0-9_]*)\s*:", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ParseError(ValueError):
    """Raised when input does not conform to the ICS constraint grammar."""

    def __init__(self, message: str, line: Optional[int] = None) -> None:
        self.line = line
        loc = f" (line {line})" if line is not None else ""
        super().__init__(f"{message}{loc}")


# ---------------------------------------------------------------------------
# AST types
# ---------------------------------------------------------------------------

@dataclass
class Directive:
    """Parsed representation of a single CAPABILITY_DECLARATION directive."""

    keyword: str
    """One of ``"ALLOW"``, ``"DENY"``, ``"REQUIRE"`` (upper-cased)."""

    action: str
    """The action text — tokens between the keyword and the qualifier/IF/end."""

    qualifier_word: Optional[str]
    """Qualifier keyword (``"WITHIN"``, ``"ON"``, ``"WITH"``, or ``"UNLESS"``),
    or ``None`` if no qualifier is present."""

    qualifier_target: Optional[str]
    """Text following the qualifier keyword up to ``IF`` or end-of-line,
    or ``None`` if no qualifier is present."""

    condition: Optional[str]
    """Text following ``IF``, or ``None`` if no ``IF`` clause is present."""

    raw: str
    """Original line text (stripped of surrounding whitespace)."""


@dataclass
class ParsedCapabilityBlock:
    """All directives parsed from a CAPABILITY_DECLARATION block."""

    directives: list[Directive] = field(default_factory=list)


@dataclass
class OutputContract:
    """Parsed OUTPUT_CONTRACT with all four required fields."""

    format: str
    """Output format declaration (e.g. ``"JSON"``, ``"unified diff"``)."""

    schema: str
    """Schema body — may be multi-line for structured formats."""

    variance: str
    """Permitted variance from the schema."""

    on_failure: str
    """Required model behaviour when the task cannot be completed."""

    extra_fields: dict[str, str] = field(default_factory=dict)
    """Any non-standard fields present in the block (preserved verbatim)."""

    warnings: list[str] = field(default_factory=list)
    """Non-fatal issues found during parsing (e.g. unrecognised field names)."""


# ---------------------------------------------------------------------------
# §3.2  Directive parser
# ---------------------------------------------------------------------------

def parse_directive(line: str) -> Directive:
    """
    Parse a single CAPABILITY_DECLARATION directive line.

    Args:
        line: A single text line.  Leading/trailing whitespace is stripped.

    Returns:
        :class:`Directive` AST node.

    Raises:
        :class:`ParseError` on any grammar violation.
    """
    raw = line.strip()
    tokens = raw.split()

    if not tokens:
        raise ParseError("Empty directive line")

    keyword_upper = tokens[0].upper()
    if keyword_upper not in KEYWORDS:
        raise ParseError(
            f"Unknown directive keyword '{tokens[0]}'; "
            f"expected one of: {', '.join(sorted(KEYWORDS))}"
        )

    rest = tokens[1:]
    if not rest:
        raise ParseError(f"Directive '{keyword_upper}' has no action")

    # Locate the first qualifier keyword (whole-token match, case-insensitive)
    qual_idx: Optional[int] = next(
        (i for i, t in enumerate(rest) if t.upper() in QUALIFIERS),
        None,
    )

    # Locate "IF" — must appear after the qualifier (if any)
    search_from = (qual_idx + 1) if qual_idx is not None else 0
    if_idx: Optional[int] = next(
        (
            search_from + i
            for i, t in enumerate(rest[search_from:])
            if t.upper() == "IF"
        ),
        None,
    )

    # ── action ────────────────────────────────────────────────────────────
    action_end = (
        qual_idx if qual_idx is not None
        else (if_idx if if_idx is not None else len(rest))
    )
    if action_end == 0:
        first_token = rest[0] if rest else ""
        raise ParseError(
            f"Action must be non-empty between '{keyword_upper}' and '{first_token}'"
        )
    action = " ".join(rest[:action_end])

    # ── qualifier ─────────────────────────────────────────────────────────
    qualifier_word:   Optional[str] = None
    qualifier_target: Optional[str] = None

    if qual_idx is not None:
        qualifier_word = rest[qual_idx].upper()
        target_end = if_idx if if_idx is not None else len(rest)
        if qual_idx + 1 >= target_end:
            raise ParseError(
                f"Qualifier '{qualifier_word}' must be followed by a non-empty target"
            )
        qualifier_target = " ".join(rest[qual_idx + 1 : target_end])

    # ── condition ─────────────────────────────────────────────────────────
    condition: Optional[str] = None

    if if_idx is not None:
        if if_idx + 1 >= len(rest):
            raise ParseError("'IF' must be followed by a non-empty condition")
        condition = " ".join(rest[if_idx + 1 :])

    return Directive(
        keyword=keyword_upper,
        action=action,
        qualifier_word=qualifier_word,
        qualifier_target=qualifier_target,
        condition=condition,
        raw=raw,
    )


def parse_capability_block(block_text: str) -> ParsedCapabilityBlock:
    """
    Parse the content of an entire CAPABILITY_DECLARATION block.

    Blank lines and comment lines (starting with ``#``) are silently skipped.

    Args:
        block_text: Raw text content between ``###ICS:CAPABILITY_DECLARATION###``
                    and ``###END:CAPABILITY_DECLARATION###`` markers (exclusive).

    Returns:
        :class:`ParsedCapabilityBlock` containing all :class:`Directive` nodes.

    Raises:
        :class:`ParseError` with the 1-based line number if any directive
        fails to parse.
    """
    directives: list[Directive] = []

    for lineno, raw_line in enumerate(block_text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            directives.append(parse_directive(stripped))
        except ParseError as exc:
            raise ParseError(str(exc).split(" (line")[0], line=lineno) from exc

    return ParsedCapabilityBlock(directives=directives)


# ---------------------------------------------------------------------------
# §3.5  OUTPUT_CONTRACT parser
# ---------------------------------------------------------------------------

def parse_output_contract(block_text: str) -> OutputContract:
    """
    Parse the content of an OUTPUT_CONTRACT block.

    Multi-line field values are supported: a line is treated as a
    continuation of the current field if it does not start a new
    ``field_name:`` declaration.

    Args:
        block_text: Raw text content between ``###ICS:OUTPUT_CONTRACT###``
                    and ``###END:OUTPUT_CONTRACT###`` markers (exclusive).

    Returns:
        :class:`OutputContract` with all four required fields populated.

    Raises:
        :class:`ParseError` if any required field is missing or if
        ``format`` is empty.
    """
    lines = block_text.splitlines()
    raw_fields: dict[str, list[str]] = {}   # field_name -> list of value lines
    warnings: list[str] = []

    current_name: Optional[str] = None
    current_value_lines: list[str] = []

    def _flush() -> None:
        if current_name is not None:
            raw_fields[current_name] = current_value_lines[:]

    for lineno, line in enumerate(lines, start=1):
        # Any "key:" line (known or unknown) starts a new field.
        mu = _OC_ANY_FIELD_RE.match(line)
        if mu:
            _flush()
            field_key = mu.group(1).lower()
            current_name = field_key
            first_val = line[mu.end():].strip()
            current_value_lines = [first_val] if first_val else []
            if field_key not in OC_REQUIRED_FIELDS:
                warnings.append(
                    f"Line {lineno}: unrecognised field '{field_key}'"
                )
            continue

        # Continuation line (or pre-field content — skip if no field open yet)
        if current_name is not None:
            current_value_lines.append(line)

    _flush()

    # Separate unknown fields into extra_fields
    extra_fields: dict[str, str] = {}
    for k in list(raw_fields):
        if k not in OC_REQUIRED_FIELDS:
            extra_fields[k] = _join_value(raw_fields.pop(k))

    # Validate required fields
    missing = OC_REQUIRED_FIELDS - set(raw_fields.keys())
    if missing:
        raise ParseError(
            "OUTPUT_CONTRACT is missing required field(s): "
            + ", ".join(f"'{f}'" for f in sorted(missing))
        )

    fmt      = _join_value(raw_fields["format"])
    schema   = _join_value(raw_fields["schema"])
    variance = _join_value(raw_fields["variance"])
    on_fail  = _join_value(raw_fields["on_failure"])

    if not fmt:
        raise ParseError("OUTPUT_CONTRACT 'format' field must not be empty")

    return OutputContract(
        format=fmt,
        schema=schema,
        variance=variance,
        on_failure=on_fail,
        extra_fields=extra_fields,
        warnings=warnings,
    )


def _join_value(lines: list[str]) -> str:
    """Join multi-line field value, stripping trailing blank lines."""
    # Strip trailing empty lines
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0].strip()
    # Multi-line: preserve internal structure (e.g. JSON schema)
    return "\n".join(lines).strip()
