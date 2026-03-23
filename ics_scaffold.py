#!/usr/bin/env python3
"""
ICS Scaffold Generator — M5

Generates well-formed, validator-compliant, linter-clean ICS documents
from structured inputs.  Every document returned by :func:`scaffold` is
guaranteed to pass ``ics-validate`` (structural check) and contain no
lint ERRORs (semantic check).

Four built-in templates cover the most common ICS patterns:

  code-diff     Unified-diff output for code modification tasks.
  json-review   Structured JSON output for review/audit tasks.
  json-output   Simple JSON output for general generation tasks.
  prose-report  Sectioned prose output for report generation tasks.

User-supplied directives are parsed by the M3 constraint parser before
insertion — malformed directives raise :class:`ScaffoldError` with the
parser's error message.

Usage (programmatic):
    from ics_scaffold import scaffold, ScaffoldOptions

    doc = scaffold(ScaffoldOptions(
        system   = "order management service",
        language = "Python 3.11",
        allows   = ["file modification WITHIN src/orders/"],
        denies   = ["modification of src/orders/api/"],
        requires = ["type annotations ON all new functions"],
        task     = "Split apply_discount() into two functions.",
    ), template="code-diff")
    print(doc)

Usage (CLI):
    ics-scaffold --template code-diff \\
        --system "payment service" \\
        --allow "file modification WITHIN src/" \\
        --deny  "modification of src/api/" \\
        --task  "Add retry logic to deliver()"
"""

from __future__ import annotations

import sys
import textwrap
from dataclasses import dataclass, field
from typing import Optional

from ics_constraint_parser import ParseError, parse_directive
from ics_validator import validate
from ics_linter import lint, SEVERITY_ERROR


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ScaffoldError(ValueError):
    """Raised when the scaffold cannot produce a valid document."""


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScaffoldTemplate:
    """Default OUTPUT_CONTRACT values for a common ICS usage pattern."""

    name: str
    description: str
    output_format: str
    default_schema: str
    default_variance: str
    default_on_failure: str


TEMPLATES: dict[str, ScaffoldTemplate] = {
    "code-diff": ScaffoldTemplate(
        name        = "code-diff",
        description = "Code modification task that produces a unified diff",
        output_format     = "unified diff",
        default_schema    = (
            "standard unified diff against current HEAD; "
            "one diff block per modified file"
        ),
        default_variance  = (
            "diff header timestamps MAY be omitted; "
            "no other variance permitted"
        ),
        default_on_failure = (
            "return a single line starting with BLOCKED: "
            "followed by the violated constraint"
        ),
    ),
    "json-review": ScaffoldTemplate(
        name        = "json-review",
        description = "Review / audit task with structured JSON output",
        output_format     = "JSON",
        default_schema    = textwrap.dedent("""\
            {
              "verdict":    "PASS" | "FAIL",
              "violations": [{"rule": "string", "severity": "ERROR" | "WARNING", "detail": "string"}],
              "notes":      ["string"]
            }"""),
        default_variance  = (
            '"violations" and "notes" MAY be empty arrays; '
            '"verdict" MUST always be present'
        ),
        default_on_failure = (
            'return { "status": "error", "reason": "<single-sentence description>" }'
        ),
    ),
    "json-output": ScaffoldTemplate(
        name        = "json-output",
        description = "General task with simple JSON output",
        output_format     = "JSON",
        default_schema    = '{ "result": "string", "status": "ok" | "error" }',
        default_variance  = "none",
        default_on_failure = (
            "return a single line starting with BLOCKED: "
            "followed by the violated constraint"
        ),
    ),
    "prose-report": ScaffoldTemplate(
        name        = "prose-report",
        description = "Report generation task with structured prose output",
        output_format     = "prose",
        default_schema    = (
            "structured report with: (1) Summary, (2) Findings, "
            "(3) Recommendations"
        ),
        default_variance  = (
            "section headers MAY be reformatted; "
            "section order and presence are required"
        ),
        default_on_failure = (
            "return a single line starting with BLOCKED: "
            "followed by the violated constraint"
        ),
    ),
}


# ---------------------------------------------------------------------------
# Scaffold options
# ---------------------------------------------------------------------------

@dataclass
class ScaffoldOptions:
    """
    Inputs for :func:`scaffold`.

    IMMUTABLE_CONTEXT:
        ``system``       — one-line description of the system (required).
        ``extra_context``— additional lines appended to IMMUTABLE_CONTEXT.

    CAPABILITY_DECLARATION (directive *action* strings — keyword is added):
        ``allows``   — actions to ALLOW (e.g. ``"file modification WITHIN src/"``).
        ``denies``   — actions to DENY.
        ``requires`` — actions to REQUIRE.

    You may also supply complete directive lines (``"ALLOW file modification
    WITHIN src/"``); the scaffold detects the keyword and will not double-add it.

    SESSION_STATE:
        ``session_state`` — defaults to ``"CLEAR"``.

    TASK_PAYLOAD:
        ``task`` — the task description.  May be left empty for templates.

    OUTPUT_CONTRACT overrides (all optional; template defaults are used otherwise):
        ``output_format``, ``output_schema``, ``variance``, ``on_failure``.
    """

    system:        str

    extra_context: str = ""

    allows:   list[str] = field(default_factory=list)
    denies:   list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)

    session_state: str = "CLEAR"
    task:          str = ""

    # OUTPUT_CONTRACT overrides (empty → use template default)
    output_format: str = ""
    output_schema: str = ""
    variance:      str = ""
    on_failure:    str = ""


# ---------------------------------------------------------------------------
# Internal builders
# ---------------------------------------------------------------------------

def _normalise_directive(raw: str, default_keyword: str) -> str:
    """
    Ensure *raw* is a complete directive line.

    If *raw* already starts with ALLOW/DENY/REQUIRE it is validated as-is.
    Otherwise *default_keyword* is prepended and the result is validated.
    Raises :class:`ScaffoldError` if the directive is grammatically invalid.
    """
    stripped = raw.strip()
    first = stripped.split()[0].upper() if stripped.split() else ""

    if first in ("ALLOW", "DENY", "REQUIRE"):
        line = stripped
    else:
        line = f"{default_keyword} {stripped}"

    try:
        parse_directive(line)
    except ParseError as exc:
        raise ScaffoldError(
            f"Invalid directive '{line}': {exc}"
        ) from exc

    return line


def _build_immutable(opts: ScaffoldOptions) -> str:
    lines = [f"System: {opts.system.strip()}"]
    if opts.extra_context.strip():
        lines.append(opts.extra_context.strip())
    return "\n".join(lines)


def _build_capability(opts: ScaffoldOptions) -> str:
    directives: list[str] = []

    for raw in opts.allows:
        directives.append(_normalise_directive(raw, "ALLOW"))
    for raw in opts.denies:
        directives.append(_normalise_directive(raw, "DENY"))
    for raw in opts.requires:
        directives.append(_normalise_directive(raw, "REQUIRE"))

    return "\n".join(directives) if directives else ""


def _build_output_contract(opts: ScaffoldOptions, tmpl: ScaffoldTemplate) -> str:
    fmt      = opts.output_format or tmpl.output_format
    schema   = opts.output_schema or tmpl.default_schema
    variance = opts.variance       or tmpl.default_variance
    on_fail  = opts.on_failure     or tmpl.default_on_failure

    # Indent multi-line schema values so the parser reads them correctly
    schema_lines = schema.strip().splitlines()
    if len(schema_lines) == 1:
        schema_block = f"schema:     {schema_lines[0]}"
    else:
        indented = "\n".join("            " + ln for ln in schema_lines[1:])
        schema_block = f"schema:     {schema_lines[0]}\n{indented}"

    # Same for on_failure
    fail_lines = on_fail.strip().splitlines()
    if len(fail_lines) == 1:
        fail_block = f"on_failure: {fail_lines[0]}"
    else:
        indented = "\n".join("            " + ln for ln in fail_lines[1:])
        fail_block = f"on_failure: {fail_lines[0]}\n{indented}"

    return (
        f"format:     {fmt}\n"
        f"{schema_block}\n"
        f"variance:   {variance}\n"
        f"{fail_block}"
    )


_LAYER_FMT = "###ICS:{name}###\n{content}\n###END:{name}###"


def _layer(name: str, content: str) -> str:
    return _LAYER_FMT.format(name=name, content=content)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scaffold(
    options: ScaffoldOptions,
    template: str = "code-diff",
) -> str:
    """
    Generate a complete, valid, linter-clean ICS document.

    Args:
        options:  :class:`ScaffoldOptions` specifying the document content.
        template: Name of the built-in template to use for OUTPUT_CONTRACT
                  defaults.  One of ``"code-diff"``, ``"json-review"``,
                  ``"json-output"``, ``"prose-report"``.  User-supplied
                  values in *options* always override template defaults.

    Returns:
        The generated ICS document as a string.

    Raises:
        :class:`ScaffoldError` if any directive is grammatically invalid,
        or if the assembled document fails structural validation or has
        lint ERRORs (which would indicate a bug in the scaffold itself).
    """
    if template not in TEMPLATES:
        raise ScaffoldError(
            f"Unknown template '{template}'. "
            f"Available: {', '.join(sorted(TEMPLATES))}"
        )
    tmpl = TEMPLATES[template]

    immutable  = _build_immutable(options)
    capability = _build_capability(options)
    session    = options.session_state.strip() or "CLEAR"
    task       = options.task.strip()
    oc         = _build_output_contract(options, tmpl)

    doc = "\n\n".join([
        _layer("IMMUTABLE_CONTEXT",    immutable),
        _layer("CAPABILITY_DECLARATION", capability),
        _layer("SESSION_STATE",        session),
        _layer("TASK_PAYLOAD",         task),
        _layer("OUTPUT_CONTRACT",      oc),
    ])

    # ── Structural validation ─────────────────────────────────────────────
    vr = validate(doc)
    if not vr.compliant:
        raise ScaffoldError(
            f"Scaffold produced an invalid document (this is a bug): "
            f"{vr.violations[0].message}"
        )

    # ── Lint check (hard-stop on errors only) ─────────────────────────────
    lr = lint(doc)
    errors = [i for i in lr.issues if i.severity == SEVERITY_ERROR]
    if errors:
        raise ScaffoldError(
            f"Scaffold produced a document with lint errors (this is a bug): "
            f"{errors[0].message}"
        )

    return doc


def list_templates() -> list[ScaffoldTemplate]:
    """Return all built-in templates, sorted by name."""
    return sorted(TEMPLATES.values(), key=lambda t: t.name)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="ics-scaffold",
        description="Generate a well-formed ICS document stub.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Built-in templates:
              code-diff     Unified-diff output for code modification tasks
              json-review   Structured JSON output for review/audit tasks
              json-output   Simple JSON output for general generation tasks
              prose-report  Sectioned prose for report generation tasks

            Examples:
              ics-scaffold --template code-diff \\
                  --system "payment service" \\
                  --allow "file modification WITHIN src/" \\
                  --deny  "modification of src/crypto/" \\
                  --task  "Add retry logic to deliver()"

              ics-scaffold --template json-review \\
                  --system "database migration review" \\
                  --allow "read access to migration file content" \\
                  --deny  "generation of migration file content" \\
                  --task  "Review migration 0047 for conformance"
        """),
    )
    parser.add_argument("--template", default="code-diff",
                        choices=list(TEMPLATES),
                        help="Output contract template (default: code-diff)")
    parser.add_argument("--system",   required=False, default="",
                        help="One-line system description")
    parser.add_argument("--context",  default="",
                        help="Extra lines to append to IMMUTABLE_CONTEXT")
    parser.add_argument("--allow",    action="append", dest="allows",
                        default=[], metavar="DIRECTIVE",
                        help="ALLOW directive action (repeatable)")
    parser.add_argument("--deny",     action="append", dest="denies",
                        default=[], metavar="DIRECTIVE",
                        help="DENY directive action (repeatable)")
    parser.add_argument("--require",  action="append", dest="requires",
                        default=[], metavar="DIRECTIVE",
                        help="REQUIRE directive action (repeatable)")
    parser.add_argument("--task",     default="",
                        help="TASK_PAYLOAD content")
    parser.add_argument("--format",   default="", dest="output_format",
                        help="Override output format")
    parser.add_argument("--schema",   default="", dest="output_schema",
                        help="Override output schema")
    parser.add_argument("--variance", default="",
                        help="Override variance")
    parser.add_argument("--on-failure", default="", dest="on_failure",
                        help="Override on_failure")
    parser.add_argument("--list-templates", action="store_true",
                        help="List available templates and exit")
    args = parser.parse_args()

    if args.list_templates:
        for tmpl in list_templates():
            print(f"  {tmpl.name:<14} {tmpl.description}")
        sys.exit(0)

    if not args.system:
        parser.error("--system is required")

    opts = ScaffoldOptions(
        system        = args.system,
        extra_context = args.context,
        allows        = args.allows,
        denies        = args.denies,
        requires      = args.requires,
        task          = args.task,
        output_format = args.output_format,
        output_schema = args.output_schema,
        variance      = args.variance,
        on_failure    = args.on_failure,
    )

    try:
        doc = scaffold(opts, template=args.template)
    except ScaffoldError as exc:
        print(f"ics-scaffold: {exc}", file=sys.stderr)
        sys.exit(1)

    print(doc)


if __name__ == "__main__":
    main()
