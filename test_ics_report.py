#!/usr/bin/env python3
"""
Test suite for ics_report — M7.

Covers:
  • FileReport.passed() — normal and strict modes
  • FileReport.passed() — read_error overrides everything
  • FileReport.counts — validation_error_count, lint_error_count, lint_warning_count
  • FileReport.to_dict() structure
  • SuiteReport aggregates — total, passed_count, failed_count, all_passed
  • SuiteReport.to_console() content
  • SuiteReport.to_markdown() content and structure
  • SuiteReport.to_json() — valid JSON, correct structure
  • report_text() API — integrates validate + lint
  • report() API — multi-file, glob expansion, missing-file handling
  • CLI — exit codes 0/1, --format json, --format markdown, --strict, --stdin

Usage:
    python test_ics_report.py
    python test_ics_report.py -v
"""

import json
import os
import sys
import tempfile
import unittest
from io import StringIO
from unittest.mock import patch

from ics_linter import LintIssue, SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_INFO
from ics_report import (
    FileReport,
    SuiteReport,
    report,
    report_text,
)
from ics_validator import Violation


# ---------------------------------------------------------------------------
# Helpers — sample ICS documents
# ---------------------------------------------------------------------------

def _ics(
    capability: str = "",
    output_contract: str = "",
    immutable: str = "",
    session: str = "",
    task: str = "",
) -> str:
    parts: list[str] = []

    def _layer(name: str, content: str) -> str:
        return f"###ICS:{name}###\n{content.strip()}\n###END:{name}###"

    # Canonical ICS layer order: IMMUTABLE_CONTEXT, CAPABILITY_DECLARATION,
    # SESSION_STATE, TASK_PAYLOAD, OUTPUT_CONTRACT
    if immutable:
        parts.append(_layer("IMMUTABLE_CONTEXT", immutable))
    if capability:
        parts.append(_layer("CAPABILITY_DECLARATION", capability))
    if session:
        parts.append(_layer("SESSION_STATE", session))
    if task:
        parts.append(_layer("TASK_PAYLOAD", task))
    if output_contract:
        parts.append(_layer("OUTPUT_CONTRACT", output_contract))
    return "\n\n".join(parts)


# Minimal fully-compliant document used in many tests
_PASSING_DOC = _ics(
    capability=(
        "ALLOW action:read_files from:user_request\n"
        "DENY  action:write_files unless:authorized\n"
    ),
    output_contract=(
        "format: JSON\n"
        "schema: {\"type\": \"object\", \"properties\": {\"result\": {\"type\": \"string\"}}}\n"
        "variance: none\n"
        "on_failure: ERROR: followed by reason\n"
    ),
    immutable="project: acme-123",
    session="user_role: viewer",
    task="query: list all records",
)

# Document that will fail validation (no layers at all → missing required layers)
_FAILING_DOC = "this is not a valid ICS document"


# ---------------------------------------------------------------------------
# Helpers — synthetic FileReport builders
# ---------------------------------------------------------------------------

def _violation(msg: str = "test violation") -> Violation:
    return Violation(step=1, rule="§test", message=msg)


def _lint_error(msg: str = "test error") -> LintIssue:
    return LintIssue(
        rule_id="L001", severity=SEVERITY_ERROR,
        layer="OUTPUT_CONTRACT", message=msg, hint="fix it",
    )


def _lint_warning(msg: str = "test warning") -> LintIssue:
    return LintIssue(
        rule_id="L002", severity=SEVERITY_WARNING,
        layer="OUTPUT_CONTRACT", message=msg, hint="consider fixing",
    )


def _pass_file(path: str = "ok.ics") -> FileReport:
    return FileReport(path=path, valid=True,
                      validation_violations=[], lint_issues=[])


def _fail_validation(path: str = "bad.ics") -> FileReport:
    return FileReport(path=path, valid=False,
                      validation_violations=[_violation()], lint_issues=[])


def _fail_lint_error(path: str = "lint.ics") -> FileReport:
    return FileReport(path=path, valid=True,
                      validation_violations=[], lint_issues=[_lint_error()])


def _warn_only(path: str = "warn.ics") -> FileReport:
    return FileReport(path=path, valid=True,
                      validation_violations=[], lint_issues=[_lint_warning()])


def _read_error(path: str = "missing.ics") -> FileReport:
    return FileReport(path=path, valid=False, validation_violations=[],
                      lint_issues=[], read_error="[Errno 2] No such file")


# ---------------------------------------------------------------------------
# 1. FileReport.passed()
# ---------------------------------------------------------------------------

class TestFileReportPassed(unittest.TestCase):

    def test_clean_file_passes(self):
        self.assertTrue(_pass_file().passed())
        self.assertTrue(_pass_file().passed(strict=True))

    def test_validation_failure_fails_always(self):
        fr = _fail_validation()
        self.assertFalse(fr.passed())
        self.assertFalse(fr.passed(strict=True))

    def test_lint_error_fails(self):
        fr = _fail_lint_error()
        self.assertFalse(fr.passed())
        self.assertFalse(fr.passed(strict=True))

    def test_lint_warning_passes_normal(self):
        fr = _warn_only()
        self.assertTrue(fr.passed(strict=False))

    def test_lint_warning_fails_strict(self):
        fr = _warn_only()
        self.assertFalse(fr.passed(strict=True))

    def test_read_error_always_fails(self):
        fr = _read_error()
        self.assertFalse(fr.passed())
        self.assertFalse(fr.passed(strict=True))


# ---------------------------------------------------------------------------
# 2. FileReport counts
# ---------------------------------------------------------------------------

class TestFileReportCounts(unittest.TestCase):

    def test_zero_counts_when_clean(self):
        fr = _pass_file()
        self.assertEqual(fr.validation_error_count, 0)
        self.assertEqual(fr.lint_error_count, 0)
        self.assertEqual(fr.lint_warning_count, 0)

    def test_validation_error_count(self):
        fr = FileReport(path="x.ics", valid=False,
                        validation_violations=[_violation(), _violation()],
                        lint_issues=[])
        self.assertEqual(fr.validation_error_count, 2)

    def test_lint_error_and_warning_counts(self):
        fr = FileReport(path="x.ics", valid=True,
                        validation_violations=[],
                        lint_issues=[_lint_error(), _lint_warning(), _lint_warning()])
        self.assertEqual(fr.lint_error_count, 1)
        self.assertEqual(fr.lint_warning_count, 2)


# ---------------------------------------------------------------------------
# 3. FileReport.to_dict()
# ---------------------------------------------------------------------------

class TestFileReportToDict(unittest.TestCase):

    def test_keys_present(self):
        d = _pass_file("a.ics").to_dict()
        for key in ("path", "passed", "read_error", "valid",
                    "validation_violations", "lint_issues"):
            self.assertIn(key, d)

    def test_passed_true_for_clean(self):
        self.assertTrue(_pass_file().to_dict()["passed"])

    def test_passed_false_for_violation(self):
        self.assertFalse(_fail_validation().to_dict()["passed"])

    def test_violation_structure(self):
        fr = _fail_validation()
        d = fr.to_dict()
        self.assertEqual(len(d["validation_violations"]), 1)
        vd = d["validation_violations"][0]
        self.assertIn("step", vd)
        self.assertIn("rule", vd)
        self.assertIn("message", vd)

    def test_lint_issue_structure(self):
        fr = _fail_lint_error()
        d = fr.to_dict()
        self.assertEqual(len(d["lint_issues"]), 1)
        ld = d["lint_issues"][0]
        for key in ("rule_id", "severity", "layer", "message", "hint"):
            self.assertIn(key, ld)

    def test_strict_changes_passed_for_warning(self):
        fr = _warn_only()
        self.assertTrue(fr.to_dict(strict=False)["passed"])
        self.assertFalse(fr.to_dict(strict=True)["passed"])


# ---------------------------------------------------------------------------
# 4. SuiteReport aggregates
# ---------------------------------------------------------------------------

class TestSuiteReportAggregates(unittest.TestCase):

    def _suite(self, *file_reports, strict=False) -> SuiteReport:
        return SuiteReport(files=list(file_reports), strict=strict)

    def test_total(self):
        suite = self._suite(_pass_file(), _fail_validation())
        self.assertEqual(suite.total, 2)

    def test_all_pass(self):
        suite = self._suite(_pass_file("a.ics"), _pass_file("b.ics"))
        self.assertEqual(suite.passed_count, 2)
        self.assertEqual(suite.failed_count, 0)
        self.assertTrue(suite.all_passed)

    def test_mixed_pass_fail(self):
        suite = self._suite(_pass_file(), _fail_validation())
        self.assertEqual(suite.passed_count, 1)
        self.assertEqual(suite.failed_count, 1)
        self.assertFalse(suite.all_passed)

    def test_strict_mode_counts_warning_as_failure(self):
        suite = self._suite(_pass_file(), _warn_only(), strict=True)
        self.assertEqual(suite.passed_count, 1)
        self.assertEqual(suite.failed_count, 1)

    def test_strict_mode_warning_passes_normal(self):
        suite = self._suite(_pass_file(), _warn_only(), strict=False)
        self.assertEqual(suite.passed_count, 2)
        self.assertEqual(suite.failed_count, 0)

    def test_empty_suite(self):
        suite = SuiteReport(files=[])
        self.assertEqual(suite.total, 0)
        self.assertTrue(suite.all_passed)


# ---------------------------------------------------------------------------
# 5. SuiteReport.to_console()
# ---------------------------------------------------------------------------

class TestSuiteReportConsole(unittest.TestCase):

    def test_pass_shown(self):
        suite = SuiteReport(files=[_pass_file("ok.ics")])
        out = suite.to_console()
        self.assertIn("PASS", out)
        self.assertIn("ok.ics", out)

    def test_fail_shown(self):
        suite = SuiteReport(files=[_fail_validation("bad.ics")])
        out = suite.to_console()
        self.assertIn("FAIL", out)
        self.assertIn("bad.ics", out)

    def test_summary_line(self):
        suite = SuiteReport(files=[_pass_file(), _fail_validation()])
        out = suite.to_console()
        self.assertIn("2 file(s)", out)
        self.assertIn("1 passed", out)
        self.assertIn("1 failed", out)

    def test_violation_detail_shown(self):
        fr = _fail_validation("bad.ics")
        suite = SuiteReport(files=[fr])
        out = suite.to_console()
        self.assertIn("STRUCTURE", out)
        self.assertIn("test violation", out)

    def test_lint_issue_detail_shown(self):
        fr = _fail_lint_error("lint.ics")
        suite = SuiteReport(files=[fr])
        out = suite.to_console()
        self.assertIn("ERROR", out)
        self.assertIn("L001", out)

    def test_read_error_shown(self):
        suite = SuiteReport(files=[_read_error("missing.ics")])
        out = suite.to_console()
        self.assertIn("FAIL", out)
        self.assertIn("read error", out)

    def test_strict_suffix_in_summary(self):
        suite = SuiteReport(files=[_pass_file()], strict=True)
        out = suite.to_console()
        self.assertIn("strict", out)


# ---------------------------------------------------------------------------
# 6. SuiteReport.to_markdown()
# ---------------------------------------------------------------------------

class TestSuiteReportMarkdown(unittest.TestCase):

    def test_has_heading(self):
        suite = SuiteReport(files=[_pass_file()])
        md = suite.to_markdown()
        self.assertIn("# ICS Report", md)

    def test_has_summary_table(self):
        suite = SuiteReport(files=[_pass_file(), _fail_validation()])
        md = suite.to_markdown()
        self.assertIn("## Summary", md)
        self.assertIn("Files checked", md)
        self.assertIn("Passed", md)
        self.assertIn("Failed", md)

    def test_has_results_table(self):
        suite = SuiteReport(files=[_pass_file()])
        md = suite.to_markdown()
        self.assertIn("## Results", md)
        self.assertIn("PASS", md)

    def test_failures_section_present_when_failures(self):
        suite = SuiteReport(files=[_fail_validation("bad.ics")])
        md = suite.to_markdown()
        self.assertIn("## Failures", md)
        self.assertIn("bad.ics", md)

    def test_failures_section_absent_when_all_pass(self):
        suite = SuiteReport(files=[_pass_file()])
        md = suite.to_markdown()
        self.assertNotIn("## Failures", md)

    def test_lint_hint_in_failures(self):
        suite = SuiteReport(files=[_fail_lint_error("lint.ics")])
        md = suite.to_markdown()
        self.assertIn("fix it", md)

    def test_strict_note_present(self):
        suite = SuiteReport(files=[_pass_file()], strict=True)
        md = suite.to_markdown()
        self.assertIn("strict mode", md)


# ---------------------------------------------------------------------------
# 7. SuiteReport.to_json()
# ---------------------------------------------------------------------------

class TestSuiteReportJSON(unittest.TestCase):

    def _suite(self) -> SuiteReport:
        return SuiteReport(files=[_pass_file("a.ics"), _fail_validation("b.ics")])

    def test_valid_json(self):
        suite = self._suite()
        json.loads(suite.to_json())  # must not raise

    def test_top_level_keys(self):
        d = json.loads(self._suite().to_json())
        for key in ("generated_at", "strict", "summary", "files"):
            self.assertIn(key, d)

    def test_summary_keys(self):
        d = json.loads(self._suite().to_json())
        for key in ("total", "passed", "failed", "all_passed"):
            self.assertIn(key, d["summary"])

    def test_summary_values(self):
        d = json.loads(self._suite().to_json())
        self.assertEqual(d["summary"]["total"], 2)
        self.assertEqual(d["summary"]["passed"], 1)
        self.assertEqual(d["summary"]["failed"], 1)
        self.assertFalse(d["summary"]["all_passed"])

    def test_files_array_length(self):
        d = json.loads(self._suite().to_json())
        self.assertEqual(len(d["files"]), 2)

    def test_strict_field(self):
        s = SuiteReport(files=[], strict=True)
        d = json.loads(s.to_json())
        self.assertTrue(d["strict"])


# ---------------------------------------------------------------------------
# 8. report_text() — integration
# ---------------------------------------------------------------------------

class TestReportText(unittest.TestCase):

    def test_passing_document(self):
        fr = report_text(_PASSING_DOC, path="good.ics")
        self.assertEqual(fr.path, "good.ics")
        self.assertTrue(fr.valid)
        self.assertFalse(fr.read_error)
        self.assertTrue(fr.passed())

    def test_failing_document(self):
        fr = report_text(_FAILING_DOC, path="bad.ics")
        self.assertFalse(fr.valid)
        self.assertGreater(len(fr.validation_violations), 0)
        self.assertFalse(fr.passed())

    def test_default_path_label(self):
        fr = report_text(_PASSING_DOC)
        self.assertEqual(fr.path, "<stdin>")


# ---------------------------------------------------------------------------
# 9. report() — multi-file, glob, missing files
# ---------------------------------------------------------------------------

class TestReportMultiFile(unittest.TestCase):

    def _write_tmp(self, content: str) -> str:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ics", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            return f.name

    def tearDown(self):
        pass  # tempfiles cleaned per-test with explicit unlink

    def test_all_pass(self):
        p = self._write_tmp(_PASSING_DOC)
        try:
            suite = report([p])
            self.assertTrue(suite.all_passed)
        finally:
            os.unlink(p)

    def test_mixed_results(self):
        p_good = self._write_tmp(_PASSING_DOC)
        p_bad  = self._write_tmp(_FAILING_DOC)
        try:
            suite = report([p_good, p_bad])
            self.assertEqual(suite.total, 2)
            self.assertEqual(suite.passed_count, 1)
            self.assertEqual(suite.failed_count, 1)
        finally:
            os.unlink(p_good)
            os.unlink(p_bad)

    def test_missing_file_becomes_read_error(self):
        suite = report(["/nonexistent/path/does_not_exist.ics"])
        self.assertEqual(suite.total, 1)
        self.assertIsNotNone(suite.files[0].read_error)
        self.assertFalse(suite.all_passed)

    def test_glob_expansion(self):
        d = tempfile.mkdtemp()
        paths = []
        for i in range(3):
            p = os.path.join(d, f"doc{i}.ics")
            with open(p, "w") as f:
                f.write(_PASSING_DOC)
            paths.append(p)
        try:
            suite = report([os.path.join(d, "*.ics")])
            self.assertEqual(suite.total, 3)
            self.assertTrue(suite.all_passed)
        finally:
            for p in paths:
                os.unlink(p)
            os.rmdir(d)

    def test_strict_flag_propagated(self):
        p = self._write_tmp(_PASSING_DOC)
        try:
            suite = report([p], strict=True)
            self.assertTrue(suite.strict)
        finally:
            os.unlink(p)


# ---------------------------------------------------------------------------
# 10. CLI exit codes and --format
# ---------------------------------------------------------------------------

class TestCLI(unittest.TestCase):

    def _run(self, content: str, extra_args: list[str] | None = None
             ) -> tuple[int, str]:
        """Run ics-report CLI with one temp file, return (exit_code, stdout)."""
        from ics_report import main

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ics", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            path = f.name

        captured = StringIO()
        argv = ["ics-report", path] + (extra_args or [])
        try:
            with patch("sys.argv", argv), \
                 patch("sys.stdout", captured), \
                 self.assertRaises(SystemExit) as cm:
                main()
            return cm.exception.code, captured.getvalue()
        finally:
            os.unlink(path)

    def _run_stdin(self, content: str, extra_args: list[str] | None = None
                   ) -> tuple[int, str]:
        from ics_report import main

        captured = StringIO()
        argv = ["ics-report", "--stdin"] + (extra_args or [])
        with patch("sys.argv", argv), \
             patch("sys.stdin", StringIO(content)), \
             patch("sys.stdout", captured), \
             self.assertRaises(SystemExit) as cm:
            main()
        return cm.exception.code, captured.getvalue()

    def test_exit_0_passing_doc(self):
        code, _ = self._run(_PASSING_DOC)
        self.assertEqual(code, 0)

    def test_exit_1_failing_doc(self):
        code, _ = self._run(_FAILING_DOC)
        self.assertEqual(code, 1)

    def test_json_format_valid(self):
        code, out = self._run(_PASSING_DOC, ["--format", "json"])
        self.assertEqual(code, 0)
        d = json.loads(out)
        self.assertIn("summary", d)

    def test_markdown_format_has_heading(self):
        _, out = self._run(_PASSING_DOC, ["--format", "markdown"])
        self.assertIn("# ICS Report", out)

    def test_strict_flag_exit_0_when_no_warnings(self):
        code, _ = self._run(_PASSING_DOC, ["--strict"])
        self.assertEqual(code, 0)

    def test_stdin_exit_0(self):
        code, _ = self._run_stdin(_PASSING_DOC)
        self.assertEqual(code, 0)

    def test_stdin_exit_1_on_invalid(self):
        code, _ = self._run_stdin(_FAILING_DOC)
        self.assertEqual(code, 1)

    def test_no_args_exits_2(self):
        from ics_report import main
        with patch("sys.argv", ["ics-report"]), \
             patch("sys.stdout", StringIO()), \
             patch("sys.stderr", StringIO()), \
             self.assertRaises(SystemExit) as cm:
            main()
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
