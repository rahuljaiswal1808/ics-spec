#!/usr/bin/env python3
"""
ICS Linter — M4

Semantic analysis beyond the structural validator.  The validator checks
*whether* a document conforms to the ICS spec; the linter checks *how well*
it uses the spec — catching documented anti-patterns, ambiguous contracts,
and contradictory directives.

Lint rules
──────────
  L001  variance is open-ended
          "some flexibility", "discretion", "as needed", or "allowed"
          with no enumerated constraint makes the contract meaningless.

  L002  schema is a prose description, not a definition
          For JSON or structured formats the schema field must define
          structure (contain { / [ / key: patterns), not describe it
          in natural language.

  L003  on_failure has no machine-detectable signal
          The calling system must be able to detect failure programmatically.
          on_failure must either name a detectable prefix (BLOCKED:,
          AMBIGUOUS:, ERROR: …) or explicitly say "starts with X:".

  L004  on_failure uses vague fallback language
          "try your best", "do your best", "best effort", "as best you can"
          define no behaviour a calling system can act on.

  L005  CAPABILITY_DECLARATION has no directives
          An empty capability block grants no permissions and sets no
          constraints — any model action is undefined.

  L006  TASK_PAYLOAD is empty
          A blank task payload gives the model nothing to do.

  L007  TASK_PAYLOAD contains implied constraints
          Lines that look like ALLOW/DENY/REQUIRE directives in TASK_PAYLOAD
          violate §3.2: constraints belong in CAPABILITY_DECLARATION where
          they are authoritative and apply to every invocation.

  L008  Duplicate directives
          Identical (keyword, action, qualifier, target) tuples in
          CAPABILITY_DECLARATION are redundant and may indicate copy-paste
          errors.

  L009  Conflicting ALLOW and DENY for the same target
          An explicit ALLOW and an explicit DENY with identical action and
          qualifier targets create an unresolvable ambiguity.

Usage:
    python ics_linter.py myfile.ics
    python ics_linter.py --stdin
    python ics_linter.py --test       # run built-in test suite

Exit codes:
    0  no issues found
    1  one or more issues found
    2  usage / parse error
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Optional

from ics_validator import parse_layers
from ics_constraint_parser import (
    ParseError,
    parse_capability_block,
    parse_output_contract,
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

SEVERITY_ERROR   = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO    = "info"


@dataclass
class LintIssue:
    rule_id:  str    # e.g. "L001"
    severity: str    # SEVERITY_* constant
    layer:    str    # layer name the issue is in
    message:  str    # human-readable description
    hint:     str    # how to fix it

    def __str__(self) -> str:
        return (
            f"  [{self.severity.upper():<7}] {self.rule_id}  "
            f"{self.layer}: {self.message}\n"
            f"           Hint: {self.hint}"
        )


@dataclass
class LintResult:
    issues: list[LintIssue] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == SEVERITY_ERROR for i in self.issues)

    @property
    def has_issues(self) -> bool:
        return bool(self.issues)

    def report(self) -> str:
        if not self.issues:
            return "No issues found."
        lines = []
        for issue in self.issues:
            lines.append(str(issue))
        count = len(self.issues)
        errors   = sum(1 for i in self.issues if i.severity == SEVERITY_ERROR)
        warnings = sum(1 for i in self.issues if i.severity == SEVERITY_WARNING)
        summary  = f"{count} issue(s)"
        if errors:
            summary += f" ({errors} error(s)"
            if warnings:
                summary += f", {warnings} warning(s)"
            summary += ")"
        elif warnings:
            summary += f" ({warnings} warning(s))"
        lines.append(summary)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "issues": [
                {
                    "rule_id":  i.rule_id,
                    "severity": i.severity,
                    "layer":    i.layer,
                    "message":  i.message,
                    "hint":     i.hint,
                }
                for i in self.issues
            ]
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lower(s: str) -> str:
    return s.lower()


# Phrases that indicate open-ended variance (L001)
_OPEN_VARIANCE_PHRASES = [
    "some flexibility",
    "as needed",
    "as appropriate",
    "at discretion",
    "discretion",
    "flexibility allowed",
    "flexible",
    "some variance",
    "some variation",
    "open-ended",
]

# Patterns that indicate a detectable signal in on_failure (L003).
# Matches things like: BLOCKED:, AMBIGUOUS:, ERROR:, starts with X:, prefix "X:"
_SIGNAL_PATTERN = re.compile(
    r"""
    (?:
        [A-Z]{2,}:          # uppercase word followed by colon (BLOCKED:, ERROR:)
        |
        starts?\s+with      # "start with" / "starts with"
        |
        prefix              # "prefix ..."
        |
        return\s+[{"\[']    # return {, return ", return [, return '
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Vague on_failure phrases (L004)
_VAGUE_ON_FAILURE_PHRASES = [
    "try your best",
    "do your best",
    "best effort",
    "as best you can",
    "try to",
    "attempt to",
]

# Patterns suggesting implied constraints in TASK_PAYLOAD (L007).
# Matches constraint language anywhere in a line (sentence-boundary aware).
_TASK_CONSTRAINT_RE = re.compile(
    r"(?:^|\.\s+|\s)(?:don'?t|do not|must not|never|avoid|refrain from|ensure that)\b",
    re.IGNORECASE,
)

# Markers that suggest a structured schema definition (L002)
_SCHEMA_STRUCTURE_MARKERS = re.compile(
    r"""
    (?:
        \{              # JSON object
        | \[            # JSON array
        | "[^"]+"\s*:   # JSON key: "key":
        | \w+\s*\|      # enum value (string | string)
        | standard\s    # "standard unified diff"
        | unified\s     # "unified diff"
    )
    """,
    re.VERBOSE,
)

# Formats that require a structured schema definition
_STRUCTURED_FORMATS = {"json", "yaml", "xml", "toml", "csv", "unified diff"}


# ---------------------------------------------------------------------------
# Individual rule checkers
# ---------------------------------------------------------------------------

def _check_output_contract(oc_layer_content: str) -> list[LintIssue]:
    issues: list[LintIssue] = []

    try:
        oc = parse_output_contract(oc_layer_content)
    except ParseError:
        # Structural issues are the validator's responsibility; skip lint
        return issues

    layer = "OUTPUT_CONTRACT"

    # ── L001  open-ended variance ─────────────────────────────────────────
    variance_lower = _lower(oc.variance)
    for phrase in _OPEN_VARIANCE_PHRASES:
        if phrase in variance_lower:
            issues.append(LintIssue(
                rule_id  = "L001",
                severity = SEVERITY_WARNING,
                layer    = layer,
                message  = (
                    f"variance is open-ended: '{oc.variance.strip()}'"
                ),
                hint     = (
                    "Enumerate permitted variance explicitly "
                    "(e.g. 'diff header timestamps MAY be omitted; "
                    "no other variance permitted'). "
                    "Open-ended variance makes the contract unenforceable."
                ),
            ))
            break

    # ── L002  schema is prose for a structured format ─────────────────────
    fmt_lower = _lower(oc.format).strip()
    if any(fmt_lower.startswith(sf) for sf in _STRUCTURED_FORMATS):
        schema_stripped = oc.schema.strip()
        if schema_stripped and not _SCHEMA_STRUCTURE_MARKERS.search(schema_stripped):
            issues.append(LintIssue(
                rule_id  = "L002",
                severity = SEVERITY_WARNING,
                layer    = layer,
                message  = (
                    f"schema for format '{oc.format}' looks like a prose "
                    f"description, not a structural definition: "
                    f"'{schema_stripped[:80]}'"
                ),
                hint     = (
                    "Define the schema structurally "
                    "(e.g. for JSON, provide a JSON object with typed fields; "
                    "for unified diff, write 'standard unified diff'). "
                    "Prose descriptions are not machine-checkable."
                ),
            ))

    # ── L003  on_failure has no detectable signal ─────────────────────────
    if not _SIGNAL_PATTERN.search(oc.on_failure):
        issues.append(LintIssue(
            rule_id  = "L003",
            severity = SEVERITY_WARNING,
            layer    = layer,
            message  = (
                "on_failure defines no machine-detectable signal: "
                f"'{oc.on_failure.strip()[:80]}'"
            ),
            hint     = (
                "Include a detectable prefix the calling system can check "
                "(e.g. 'return a line starting with BLOCKED:' or "
                "'return {\"status\": \"error\", ...}'). "
                "Without a signal, the caller cannot distinguish "
                "failure from a valid output."
            ),
        ))

    # ── L004  vague fallback in on_failure ────────────────────────────────
    on_fail_lower = _lower(oc.on_failure)
    for phrase in _VAGUE_ON_FAILURE_PHRASES:
        if phrase in on_fail_lower:
            issues.append(LintIssue(
                rule_id  = "L004",
                severity = SEVERITY_WARNING,
                layer    = layer,
                message  = f"on_failure uses vague fallback language: '{phrase}'",
                hint     = (
                    "Replace vague language with a specific, testable "
                    "instruction (e.g. 'return a single line starting with "
                    "BLOCKED: followed by the violated constraint')."
                ),
            ))
            break

    return issues


def _check_capability_block(cap_layer_content: str) -> list[LintIssue]:
    issues: list[LintIssue] = []
    layer = "CAPABILITY_DECLARATION"

    try:
        parsed = parse_capability_block(cap_layer_content)
    except ParseError:
        return issues

    directives = parsed.directives

    # ── L005  no directives ───────────────────────────────────────────────
    if not directives:
        issues.append(LintIssue(
            rule_id  = "L005",
            severity = SEVERITY_WARNING,
            layer    = layer,
            message  = "CAPABILITY_DECLARATION contains no directives",
            hint     = (
                "Add ALLOW, DENY, and/or REQUIRE directives to declare the "
                "model's permission surface. Per §3.2, any action not "
                "explicitly ALLOW'd is DENY'd by default — an empty block "
                "leaves every action undefined."
            ),
        ))
        return issues

    # ── L008  duplicate directives ────────────────────────────────────────
    seen: set[tuple] = set()
    for d in directives:
        key = (d.keyword, d.action.lower(), d.qualifier_word,
               (d.qualifier_target or "").lower())
        if key in seen:
            issues.append(LintIssue(
                rule_id  = "L008",
                severity = SEVERITY_INFO,
                layer    = layer,
                message  = f"duplicate directive: {d.raw}",
                hint     = "Remove the duplicate directive.",
            ))
        else:
            seen.add(key)

    # ── L009  conflicting ALLOW + DENY for same target ────────────────────
    allows: dict[tuple, str] = {}
    denies: dict[tuple, str] = {}

    for d in directives:
        target_key = (
            d.action.lower(),
            d.qualifier_word,
            (d.qualifier_target or "").lower(),
        )
        if d.keyword == "ALLOW":
            allows[target_key] = d.raw
        elif d.keyword == "DENY":
            denies[target_key] = d.raw

    for key in allows:
        if key in denies:
            issues.append(LintIssue(
                rule_id  = "L009",
                severity = SEVERITY_ERROR,
                layer    = layer,
                message  = (
                    f"conflicting directives for the same target:\n"
                    f"    ALLOW: {allows[key]}\n"
                    f"    DENY:  {denies[key]}"
                ),
                hint     = (
                    "Remove one of the conflicting directives. "
                    "Per §3.2, DENY takes precedence over ALLOW when "
                    "both apply, but having both is almost certainly "
                    "a mistake."
                ),
            ))

    return issues


def _check_task_payload(tp_layer_content: str) -> list[LintIssue]:
    issues: list[LintIssue] = []
    layer = "TASK_PAYLOAD"

    # ── L006  empty TASK_PAYLOAD ──────────────────────────────────────────
    if not tp_layer_content.strip():
        issues.append(LintIssue(
            rule_id  = "L006",
            severity = SEVERITY_WARNING,
            layer    = layer,
            message  = "TASK_PAYLOAD is empty",
            hint     = "Provide a task description for the model to execute.",
        ))
        return issues

    # ── L007  implied constraints in TASK_PAYLOAD ─────────────────────────
    for lineno, line in enumerate(tp_layer_content.splitlines(), start=1):
        if _TASK_CONSTRAINT_RE.search(line):
            issues.append(LintIssue(
                rule_id  = "L007",
                severity = SEVERITY_WARNING,
                layer    = layer,
                message  = (
                    f"line {lineno} contains an implied constraint: "
                    f"'{line.strip()}'"
                ),
                hint     = (
                    "Move constraints to CAPABILITY_DECLARATION as DENY or "
                    "REQUIRE directives. TASK_PAYLOAD should describe what "
                    "to do, not what to avoid — prose constraints are not "
                    "authoritative and do not apply to future invocations."
                ),
            ))
            break  # one warning per TASK_PAYLOAD is enough

    return issues


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def lint(ics_text: str) -> LintResult:
    """
    Lint an ICS document for semantic anti-patterns.

    Structural issues (missing layers, wrong order) are the validator's
    responsibility.  :func:`lint` assumes the document is at least
    parseable and analyses content quality.

    Args:
        ics_text: Full ICS document text.

    Returns:
        :class:`LintResult` with zero or more :class:`LintIssue` objects.
    """
    result = LintResult()

    layers, parse_errors = parse_layers(ics_text)
    if parse_errors:
        # Can't lint what we can't parse; return an informational issue
        for err in parse_errors:
            result.issues.append(LintIssue(
                rule_id  = "L000",
                severity = SEVERITY_ERROR,
                layer    = "DOCUMENT",
                message  = f"document could not be parsed: {err}",
                hint     = "Fix structural issues first (run ics-validate).",
            ))
        return result

    layer_map = {layer.name: layer for layer in layers}

    if "OUTPUT_CONTRACT" in layer_map:
        result.issues.extend(
            _check_output_contract(layer_map["OUTPUT_CONTRACT"].content)
        )

    if "CAPABILITY_DECLARATION" in layer_map:
        result.issues.extend(
            _check_capability_block(layer_map["CAPABILITY_DECLARATION"].content)
        )

    if "TASK_PAYLOAD" in layer_map:
        result.issues.extend(
            _check_task_payload(layer_map["TASK_PAYLOAD"].content)
        )

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="ics-lint",
        description="ICS semantic linter — catches anti-patterns in ICS documents.",
    )
    parser.add_argument("file", nargs="?", help="ICS file to lint")
    parser.add_argument("--stdin",  action="store_true", help="Read from stdin")
    parser.add_argument("--json",   action="store_true", help="Output results as JSON")
    parser.add_argument("--test",   action="store_true", help="Run built-in test suite")
    args = parser.parse_args()

    if args.test:
        import unittest
        import test_ics_linter
        loader = unittest.TestLoader()
        suite  = loader.loadTestsFromModule(test_ics_linter)
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        sys.exit(0 if result.wasSuccessful() else 1)

    if args.stdin:
        text = sys.stdin.read()
    elif args.file:
        try:
            with open(args.file, encoding="utf-8") as f:
                text = f.read()
        except OSError as exc:
            print(f"ics-lint: cannot read '{args.file}': {exc}", file=sys.stderr)
            sys.exit(2)
    else:
        parser.print_help()
        sys.exit(2)

    result = lint(text)

    if args.json:
        import json
        print(json.dumps(result.to_dict(), indent=2))
    else:
        label = args.file or "<stdin>"
        print(f"{label}:")
        print(result.report())

    sys.exit(1 if result.has_issues else 0)


if __name__ == "__main__":
    main()
