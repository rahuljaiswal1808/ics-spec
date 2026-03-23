#!/usr/bin/env python3
"""
ICS CI Report — M7

Runs the full validation + lint pipeline across one or more ICS files and
produces an aggregate report suitable for CI pipelines, code-review bots,
and human inspection.

A file PASSES when:
  • ics_validator.validate()  → compliant == True
  • ics_linter.lint()         → no issues with severity == "error"

With --strict a file must also have zero warnings.

Output formats
──────────────
  console   (default) per-file PASS/FAIL with inline issue details
  json      machine-readable JSON; suitable for downstream tooling
  markdown  GitHub-flavoured Markdown; suitable for PR comments / wiki pages

Exit codes
──────────
  0   all files passed
  1   one or more files failed
  2   usage / IO error

Usage
─────
  ics-report  a.ics b.ics c.ics
  ics-report  *.ics --format markdown
  ics-report  *.ics --format json > report.json
  ics-report  *.ics --strict
  cat doc.ics | ics-report --stdin

Programmatic API
────────────────
  from ics_report import report, FileReport, SuiteReport

  suite = report(["a.ics", "b.ics"])
  print(suite.to_console())
  if not suite.all_passed:
      sys.exit(1)
"""

from __future__ import annotations

import glob as _glob
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ics_validator import validate, ValidationResult, Violation
from ics_linter import lint, LintIssue, SEVERITY_ERROR, SEVERITY_WARNING


# ---------------------------------------------------------------------------
# Per-file result
# ---------------------------------------------------------------------------

@dataclass
class FileReport:
    """Validation + lint result for a single ICS file."""

    path:                    str
    valid:                   bool
    validation_violations:   list[Violation]
    lint_issues:             list[LintIssue]
    read_error:              Optional[str] = None  # set if the file could not be read

    # ── Pass/fail ─────────────────────────────────────────────────────────

    def passed(self, strict: bool = False) -> bool:
        if self.read_error:
            return False
        if not self.valid:
            return False
        if strict:
            return not self.lint_issues
        return not any(i.severity == SEVERITY_ERROR for i in self.lint_issues)

    # ── Counts ────────────────────────────────────────────────────────────

    @property
    def validation_error_count(self) -> int:
        return len(self.validation_violations)

    @property
    def lint_error_count(self) -> int:
        return sum(1 for i in self.lint_issues if i.severity == SEVERITY_ERROR)

    @property
    def lint_warning_count(self) -> int:
        return sum(1 for i in self.lint_issues if i.severity == SEVERITY_WARNING)

    # ── Serialisation ─────────────────────────────────────────────────────

    def to_dict(self, strict: bool = False) -> dict:
        return {
            "path":       self.path,
            "passed":     self.passed(strict),
            "read_error": self.read_error,
            "valid":      self.valid,
            "validation_violations": [
                {"step": v.step, "rule": v.rule, "message": v.message}
                for v in self.validation_violations
            ],
            "lint_issues": [
                {
                    "rule_id":  i.rule_id,
                    "severity": i.severity,
                    "layer":    i.layer,
                    "message":  i.message,
                    "hint":     i.hint,
                }
                for i in self.lint_issues
            ],
        }


# ---------------------------------------------------------------------------
# Suite result
# ---------------------------------------------------------------------------

@dataclass
class SuiteReport:
    """Aggregate results for all checked files."""

    files:        list[FileReport]
    strict:       bool = False
    generated_at: str  = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    # ── Aggregate counts ──────────────────────────────────────────────────

    @property
    def total(self) -> int:
        return len(self.files)

    @property
    def passed_count(self) -> int:
        return sum(1 for f in self.files if f.passed(self.strict))

    @property
    def failed_count(self) -> int:
        return self.total - self.passed_count

    @property
    def all_passed(self) -> bool:
        return self.failed_count == 0

    # ── Console output ────────────────────────────────────────────────────

    def to_console(self) -> str:
        lines: list[str] = []
        for fr in self.files:
            status = "PASS" if fr.passed(self.strict) else "FAIL"
            marker = "✓" if status == "PASS" else "✗"
            lines.append(f"  {marker} [{status}]  {fr.path}")
            if fr.read_error:
                lines.append(f"         read error: {fr.read_error}")
                continue
            for v in fr.validation_violations:
                lines.append(f"         [STRUCTURE] {v.rule}: {v.message}")
            for issue in fr.lint_issues:
                sev = issue.severity.upper()
                lines.append(
                    f"         [{sev:<7}] {issue.rule_id}  "
                    f"{issue.layer}: {issue.message}"
                )
        # Divider + summary
        width = 60
        lines.append("─" * width)
        strict_tag = " (strict)" if self.strict else ""
        lines.append(
            f"  Checked {self.total} file(s){strict_tag}: "
            f"{self.passed_count} passed, {self.failed_count} failed"
        )
        return "\n".join(lines)

    # ── Markdown output ───────────────────────────────────────────────────

    def to_markdown(self) -> str:
        lines: list[str] = []
        strict_note = " *(strict mode)*" if self.strict else ""
        lines += [
            "# ICS Report",
            "",
            f"Generated: `{self.generated_at}`{strict_note}",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Files checked | {self.total} |",
            f"| Passed | {self.passed_count} |",
            f"| Failed | {self.failed_count} |",
            "",
            "## Results",
            "",
            "| File | Status | Validation | Lint errors | Lint warnings |",
            "|------|--------|-----------|------------|--------------|",
        ]
        for fr in self.files:
            status_icon = "✅ PASS" if fr.passed(self.strict) else "❌ FAIL"
            if fr.read_error:
                lines.append(
                    f"| `{fr.path}` | ❌ FAIL | read error | — | — |"
                )
                continue
            val_cell = "OK" if fr.valid else f"{fr.validation_error_count} error(s)"
            lines.append(
                f"| `{fr.path}` | {status_icon} "
                f"| {val_cell} "
                f"| {fr.lint_error_count} "
                f"| {fr.lint_warning_count} |"
            )

        # Failures detail section
        failures = [fr for fr in self.files if not fr.passed(self.strict)]
        if failures:
            lines += ["", "## Failures", ""]
            for fr in failures:
                lines.append(f"### `{fr.path}`")
                lines.append("")
                if fr.read_error:
                    lines.append(f"**Read error:** {fr.read_error}")
                    lines.append("")
                    continue
                if fr.validation_violations:
                    lines.append("**Validation errors:**")
                    lines.append("")
                    for v in fr.validation_violations:
                        lines.append(f"- `{v.rule}`: {v.message}")
                    lines.append("")
                if fr.lint_issues:
                    lines.append("**Lint issues:**")
                    lines.append("")
                    for issue in fr.lint_issues:
                        lines.append(
                            f"- `[{issue.severity.upper()}]` "
                            f"`{issue.rule_id}` {issue.layer}: {issue.message}  "
                        )
                        lines.append(f"  *Hint: {issue.hint}*")
                    lines.append("")

        return "\n".join(lines)

    # ── JSON output ───────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "strict":       self.strict,
            "summary": {
                "total":      self.total,
                "passed":     self.passed_count,
                "failed":     self.failed_count,
                "all_passed": self.all_passed,
            },
            "files": [f.to_dict(self.strict) for f in self.files],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def report_text(ics_text: str, path: str = "<stdin>") -> FileReport:
    """
    Run validate + lint on already-loaded ICS text.

    Args:
        ics_text: The ICS document content.
        path:     Display path used in reports (default ``<stdin>``).

    Returns:
        A :class:`FileReport` for this document.
    """
    v_result: ValidationResult = validate(ics_text)
    l_result = lint(ics_text)
    return FileReport(
        path=path,
        valid=v_result.compliant,
        validation_violations=list(v_result.violations),
        lint_issues=list(l_result.issues),
    )


def report(paths: list[str], strict: bool = False) -> SuiteReport:
    """
    Run validate + lint on each file and return an aggregate :class:`SuiteReport`.

    Glob patterns in *paths* are expanded automatically.

    Args:
        paths:  List of file paths or glob patterns.
        strict: When ``True``, warnings also count as failures.

    Returns:
        A :class:`SuiteReport` for all matched files.
    """
    expanded: list[str] = []
    for p in paths:
        matched = _glob.glob(p)
        expanded.extend(sorted(matched) if matched else [p])

    file_reports: list[FileReport] = []
    for path in expanded:
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError as exc:
            file_reports.append(FileReport(
                path=path,
                valid=False,
                validation_violations=[],
                lint_issues=[],
                read_error=str(exc),
            ))
            continue
        file_reports.append(report_text(text, path=path))

    return SuiteReport(files=file_reports, strict=strict)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="ics-report",
        description=(
            "Run validate + lint across one or more ICS files and produce "
            "an aggregate report."
        ),
    )
    parser.add_argument(
        "files", nargs="*", metavar="FILE",
        help="ICS file(s) or glob pattern(s) to check",
    )
    parser.add_argument(
        "--stdin", action="store_true",
        help="Read a single ICS document from stdin",
    )
    parser.add_argument(
        "--format", choices=["console", "json", "markdown"],
        default="console",
        help="Output format (default: console)",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Treat warnings as failures",
    )
    args = parser.parse_args()

    if args.stdin:
        text = sys.stdin.read()
        fr = report_text(text, path="<stdin>")
        suite = SuiteReport(files=[fr], strict=args.strict)
    elif args.files:
        suite = report(args.files, strict=args.strict)
    else:
        parser.print_help()
        sys.exit(2)

    if args.format == "json":
        print(suite.to_json())
    elif args.format == "markdown":
        print(suite.to_markdown())
    else:
        print(suite.to_console())

    sys.exit(0 if suite.all_passed else 1)


if __name__ == "__main__":
    main()
