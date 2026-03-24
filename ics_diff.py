#!/usr/bin/env python3
"""
ICS Compatibility Checker — M6

Compares two ICS documents and classifies every change as BREAKING,
ADDITIVE, or NEUTRAL.  The comparison is structural — it uses the M3
constraint parser to diff directive sets rather than doing raw text diffs,
so reordering directives or changing whitespace does not produce spurious
results.

Change classification
─────────────────────
CAPABILITY_DECLARATION:
  ALLOW added        → ADDITIVE      (model gains a permission)
  ALLOW removed      → BREAKING      (permission withdrawn)
  DENY added         → BREAKING      (new restriction on the model)
  DENY removed       → ADDITIVE      (restriction lifted)
  REQUIRE added      → BREAKING      (new obligation imposed)
  REQUIRE removed    → ADDITIVE      (obligation lifted)

OUTPUT_CONTRACT:
  format changed     → BREAKING      (caller must update its parser)
  schema changed     → BREAKING      (caller must update its schema check)
  variance tightened → BREAKING      (e.g. something → "none")
  variance loosened  → ADDITIVE      (e.g. "none" → something)
  on_failure changed → NEUTRAL       (caller error-handling may be affected
                                      but the output contract itself is unchanged)

IMMUTABLE_CONTEXT:
  content changed    → NEUTRAL       (context facts updated)

SESSION_STATE / TASK_PAYLOAD:
  content changed    → NEUTRAL       (per-invocation data)

Usage (programmatic):
    from ics_diff import diff

    result = diff(open("v1.ics").read(), open("v2.ics").read())
    print(result.report())
    if result.is_breaking:
        sys.exit(1)

Usage (CLI):
    ics-diff v1.ics v2.ics
    ics-diff v1.ics v2.ics --json
    ics-diff v1.ics v2.ics --breaking-only

Exit codes:
    0  no changes, or only ADDITIVE/NEUTRAL changes
    1  one or more BREAKING changes found
    2  usage / parse error
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ics_validator import parse_layers, Layer
from ics_constraint_parser import (
    ParseError,
    Directive,
    parse_capability_block,
    parse_output_contract,
)


# ---------------------------------------------------------------------------
# Change types
# ---------------------------------------------------------------------------

class ChangeType(str, Enum):
    BREAKING = "breaking"
    ADDITIVE = "additive"
    NEUTRAL  = "neutral"


_CHANGE_LABEL = {
    ChangeType.BREAKING: "BREAKING",
    ChangeType.ADDITIVE: "ADDITIVE",
    ChangeType.NEUTRAL:  "NEUTRAL ",
}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ContractChange:
    """A single detected change between two ICS documents."""

    layer:  str
    kind:   ChangeType
    what:   str
    before: Optional[str]
    after:  Optional[str]

    def __str__(self) -> str:
        label = _CHANGE_LABEL[self.kind]
        lines = [f"  [{label}] {self.layer}: {self.what}"]
        if self.before is not None and self.after is not None:
            # Inline display for short values; block display for multi-line
            if "\n" not in str(self.before) and "\n" not in str(self.after):
                lines.append(f"             before: {self.before}")
                lines.append(f"             after:  {self.after}")
        return "\n".join(lines)


@dataclass
class DiffResult:
    """All changes found when comparing two ICS documents."""

    changes: list[ContractChange] = field(default_factory=list)

    # ── Convenience properties ────────────────────────────────────────────

    @property
    def is_breaking(self) -> bool:
        return any(c.kind == ChangeType.BREAKING for c in self.changes)

    @property
    def breaking(self) -> list[ContractChange]:
        return [c for c in self.changes if c.kind == ChangeType.BREAKING]

    @property
    def additive(self) -> list[ContractChange]:
        return [c for c in self.changes if c.kind == ChangeType.ADDITIVE]

    @property
    def neutral(self) -> list[ContractChange]:
        return [c for c in self.changes if c.kind == ChangeType.NEUTRAL]

    # ── Reporting ─────────────────────────────────────────────────────────

    def summary(self) -> str:
        if not self.changes:
            return "No changes."
        parts: list[str] = []
        if self.breaking:
            parts.append(f"{len(self.breaking)} breaking")
        if self.additive:
            parts.append(f"{len(self.additive)} additive")
        if self.neutral:
            parts.append(f"{len(self.neutral)} neutral")
        return ", ".join(parts) + " change(s)"

    def report(self, breaking_only: bool = False) -> str:
        shown = self.breaking if breaking_only else self.changes
        if not shown:
            return "No changes." if not breaking_only else "No breaking changes."
        lines = [str(c) for c in shown]
        lines.append(self.summary())
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "breaking": self.is_breaking,
            "summary":  self.summary(),
            "changes": [
                {
                    "layer":  c.layer,
                    "kind":   c.kind.value,
                    "what":   c.what,
                    "before": c.before,
                    "after":  c.after,
                }
                for c in self.changes
            ],
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _layer_map(ics_text: str) -> dict[str, Layer]:
    layers, _ = parse_layers(ics_text)
    return {layer.name: layer for layer in layers}


def _directive_key(d: Directive) -> tuple:
    """Canonical key for directive identity (case-normalised, whitespace-collapsed)."""
    return (
        d.keyword,
        " ".join(d.action.lower().split()),
        d.qualifier_word,
        " ".join((d.qualifier_target or "").lower().split()),
        " ".join((d.condition or "").lower().split()),
    )


def _directive_add_kind(keyword: str) -> ChangeType:
    """Change kind when a directive is *added* in B."""
    if keyword == "ALLOW":
        return ChangeType.ADDITIVE   # more permission
    if keyword == "DENY":
        return ChangeType.BREAKING   # new restriction
    # REQUIRE
    return ChangeType.BREAKING       # new obligation


def _directive_remove_kind(keyword: str) -> ChangeType:
    """Change kind when a directive is *removed* from A."""
    if keyword == "ALLOW":
        return ChangeType.BREAKING   # permission withdrawn
    if keyword == "DENY":
        return ChangeType.ADDITIVE   # restriction lifted
    # REQUIRE
    return ChangeType.ADDITIVE       # obligation lifted


# ---------------------------------------------------------------------------
# Layer diffing functions
# ---------------------------------------------------------------------------

def _diff_capability(
    a: Optional[Layer],
    b: Optional[Layer],
) -> list[ContractChange]:
    layer_name = "CAPABILITY_DECLARATION"
    changes: list[ContractChange] = []

    # Handle layer presence changes
    if a is None and b is None:
        return changes
    if a is None:
        try:
            for d in parse_capability_block(b.content).directives:
                changes.append(ContractChange(
                    layer=layer_name,
                    kind=_directive_add_kind(d.keyword),
                    what=f"directive added: {d.raw}",
                    before=None, after=d.raw,
                ))
        except ParseError:
            changes.append(ContractChange(
                layer_name, ChangeType.BREAKING,
                "CAPABILITY_DECLARATION added (unparseable)", None, b.content,
            ))
        return changes

    if b is None:
        try:
            for d in parse_capability_block(a.content).directives:
                changes.append(ContractChange(
                    layer=layer_name,
                    kind=_directive_remove_kind(d.keyword),
                    what=f"directive removed: {d.raw}",
                    before=d.raw, after=None,
                ))
        except ParseError:
            changes.append(ContractChange(
                layer_name, ChangeType.BREAKING,
                "CAPABILITY_DECLARATION removed (unparseable)", a.content, None,
            ))
        return changes

    # Both present — compare directive sets
    try:
        set_a = {_directive_key(d): d
                 for d in parse_capability_block(a.content).directives}
        set_b = {_directive_key(d): d
                 for d in parse_capability_block(b.content).directives}
    except ParseError:
        # Fall back to content comparison if parser fails
        if a.content.strip() != b.content.strip():
            changes.append(ContractChange(
                layer_name, ChangeType.BREAKING,
                "CAPABILITY_DECLARATION changed (unparseable)",
                a.content, b.content,
            ))
        return changes

    for key, d in set_a.items():
        if key not in set_b:
            changes.append(ContractChange(
                layer=layer_name,
                kind=_directive_remove_kind(d.keyword),
                what=f"directive removed: {d.raw}",
                before=d.raw, after=None,
            ))

    for key, d in set_b.items():
        if key not in set_a:
            changes.append(ContractChange(
                layer=layer_name,
                kind=_directive_add_kind(d.keyword),
                what=f"directive added: {d.raw}",
                before=None, after=d.raw,
            ))

    return changes


def _diff_output_contract(
    a: Optional[Layer],
    b: Optional[Layer],
) -> list[ContractChange]:
    layer_name = "OUTPUT_CONTRACT"
    changes: list[ContractChange] = []

    if a is None and b is None:
        return changes

    if a is None:
        changes.append(ContractChange(
            layer_name, ChangeType.BREAKING,
            "OUTPUT_CONTRACT added", None,
            b.content[:120] if b else None,
        ))
        return changes

    if b is None:
        changes.append(ContractChange(
            layer_name, ChangeType.BREAKING,
            "OUTPUT_CONTRACT removed",
            a.content[:120], None,
        ))
        return changes

    # Parse both; fall back to raw comparison on parse error
    try:
        oc_a = parse_output_contract(a.content)
        oc_b = parse_output_contract(b.content)
    except ParseError:
        if a.content.strip() != b.content.strip():
            changes.append(ContractChange(
                layer_name, ChangeType.BREAKING,
                "OUTPUT_CONTRACT changed (unparseable)", None, None,
            ))
        return changes

    # format
    if oc_a.format.strip() != oc_b.format.strip():
        changes.append(ContractChange(
            layer=layer_name,
            kind=ChangeType.BREAKING,
            what=f"format changed: '{oc_a.format}' → '{oc_b.format}'",
            before=oc_a.format, after=oc_b.format,
        ))

    # schema
    if oc_a.schema.strip() != oc_b.schema.strip():
        changes.append(ContractChange(
            layer=layer_name,
            kind=ChangeType.BREAKING,
            what="schema changed",
            before=oc_a.schema.strip(), after=oc_b.schema.strip(),
        ))

    # variance — "none" → other is loosening (ADDITIVE); other direction is BREAKING
    va = oc_a.variance.strip().lower()
    vb = oc_b.variance.strip().lower()
    if va != vb:
        kind = ChangeType.ADDITIVE if va == "none" else ChangeType.BREAKING
        changes.append(ContractChange(
            layer=layer_name,
            kind=kind,
            what=f"variance changed: '{oc_a.variance.strip()}' → '{oc_b.variance.strip()}'",
            before=oc_a.variance.strip(), after=oc_b.variance.strip(),
        ))

    # on_failure — doesn't change the structural contract
    if oc_a.on_failure.strip() != oc_b.on_failure.strip():
        changes.append(ContractChange(
            layer=layer_name,
            kind=ChangeType.NEUTRAL,
            what="on_failure changed",
            before=oc_a.on_failure.strip(), after=oc_b.on_failure.strip(),
        ))

    return changes


def _diff_content_layer(
    name:   str,
    kind:   ChangeType,
    a:      Optional[Layer],
    b:      Optional[Layer],
) -> list[ContractChange]:
    """Diff a layer whose content changes are all the same kind (NEUTRAL)."""
    before = a.content.strip() if a else None
    after  = b.content.strip() if b else None

    if before == after:
        return []

    if before is None:
        return [ContractChange(name, kind, f"{name} added", None, after)]
    if after is None:
        return [ContractChange(name, kind, f"{name} removed", before, None)]
    return [ContractChange(name, kind, f"{name} content changed", before, after)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def diff(ics_a: str, ics_b: str) -> DiffResult:
    """
    Compare two ICS documents and return all detected changes.

    Args:
        ics_a: The *old* / baseline ICS document.
        ics_b: The *new* / updated ICS document.

    Returns:
        :class:`DiffResult` with zero or more :class:`ContractChange` entries.
        Changes are ordered: CAPABILITY_DECLARATION, OUTPUT_CONTRACT,
        IMMUTABLE_CONTEXT, SESSION_STATE, TASK_PAYLOAD.
    """
    map_a = _layer_map(ics_a)
    map_b = _layer_map(ics_b)

    changes: list[ContractChange] = []

    changes += _diff_capability(
        map_a.get("CAPABILITY_DECLARATION"),
        map_b.get("CAPABILITY_DECLARATION"),
    )
    changes += _diff_output_contract(
        map_a.get("OUTPUT_CONTRACT"),
        map_b.get("OUTPUT_CONTRACT"),
    )
    changes += _diff_content_layer(
        "IMMUTABLE_CONTEXT", ChangeType.NEUTRAL,
        map_a.get("IMMUTABLE_CONTEXT"),
        map_b.get("IMMUTABLE_CONTEXT"),
    )
    changes += _diff_content_layer(
        "SESSION_STATE", ChangeType.NEUTRAL,
        map_a.get("SESSION_STATE"),
        map_b.get("SESSION_STATE"),
    )
    changes += _diff_content_layer(
        "TASK_PAYLOAD", ChangeType.NEUTRAL,
        map_a.get("TASK_PAYLOAD"),
        map_b.get("TASK_PAYLOAD"),
    )

    return DiffResult(changes=changes)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="ics-diff",
        description=(
            "Compare two ICS documents and classify every change as "
            "BREAKING, ADDITIVE, or NEUTRAL."
        ),
    )
    parser.add_argument("file_a", help="Baseline ICS document")
    parser.add_argument("file_b", help="Updated ICS document")
    parser.add_argument("--json",          action="store_true",
                        help="Output results as JSON")
    parser.add_argument("--breaking-only", action="store_true",
                        help="Show only BREAKING changes")
    args = parser.parse_args()

    def _read(path: str) -> str:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError as exc:
            print(f"ics-diff: cannot read '{path}': {exc}", file=sys.stderr)
            sys.exit(2)

    text_a = _read(args.file_a)
    text_b = _read(args.file_b)

    result = diff(text_a, text_b)

    if args.json:
        import json
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"{args.file_a} → {args.file_b}")
        print(result.report(breaking_only=args.breaking_only))

    sys.exit(1 if result.is_breaking else 0)


if __name__ == "__main__":
    main()
