#!/usr/bin/env python3
"""
Test suite for ics_linter — M4.

Covers all nine lint rules (L001–L009), the clean-pass case,
and real-world conformant examples that must produce no issues.

Usage:
    python test_ics_linter.py
    python test_ics_linter.py -v
"""

import unittest

from ics_linter import (
    LintIssue,
    LintResult,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    SEVERITY_INFO,
    lint,
)


# ---------------------------------------------------------------------------
# Shared ICS document builder
# ---------------------------------------------------------------------------

def _build_ics(
    immutable:    str = "System: test service",
    capability:   str = "ALLOW read access\nDENY write access",
    session:      str = "CLEAR",
    task:         str = "Analyze the data.",
    oc_format:    str = "JSON",
    oc_schema:    str = '{ "result": "string" }',
    oc_variance:  str = "none",
    oc_on_failure: str = 'return a single line starting with BLOCKED:',
) -> str:
    return f"""\
###ICS:IMMUTABLE_CONTEXT###
{immutable}
###END:IMMUTABLE_CONTEXT###

###ICS:CAPABILITY_DECLARATION###
{capability}
###END:CAPABILITY_DECLARATION###

###ICS:SESSION_STATE###
{session}
###END:SESSION_STATE###

###ICS:TASK_PAYLOAD###
{task}
###END:TASK_PAYLOAD###

###ICS:OUTPUT_CONTRACT###
format:     {oc_format}
schema:     {oc_schema}
variance:   {oc_variance}
on_failure: {oc_on_failure}
###END:OUTPUT_CONTRACT###"""


# ---------------------------------------------------------------------------
# Helper assertions
# ---------------------------------------------------------------------------

def _issues_with_rule(result: LintResult, rule_id: str) -> list[LintIssue]:
    return [i for i in result.issues if i.rule_id == rule_id]


def _has_rule(result: LintResult, rule_id: str) -> bool:
    return bool(_issues_with_rule(result, rule_id))


# ---------------------------------------------------------------------------
# L001 — open-ended variance
# ---------------------------------------------------------------------------

class TestL001OpenVariance(unittest.TestCase):

    def test_some_flexibility_triggers(self):
        doc = _build_ics(oc_variance="some flexibility allowed")
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L001"))

    def test_discretion_triggers(self):
        doc = _build_ics(oc_variance="at the model's discretion")
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L001"))

    def test_as_needed_triggers(self):
        doc = _build_ics(oc_variance="output format as needed")
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L001"))

    def test_flexible_triggers(self):
        doc = _build_ics(oc_variance="flexible formatting permitted")
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L001"))

    def test_none_does_not_trigger(self):
        doc = _build_ics(oc_variance="none")
        result = lint(doc)
        self.assertFalse(_has_rule(result, "L001"))

    def test_enumerated_variance_does_not_trigger(self):
        doc = _build_ics(
            oc_variance="diff header timestamps MAY be omitted; no other variance permitted"
        )
        result = lint(doc)
        self.assertFalse(_has_rule(result, "L001"))

    def test_issue_is_warning(self):
        doc = _build_ics(oc_variance="some flexibility")
        issues = _issues_with_rule(lint(doc), "L001")
        self.assertEqual(issues[0].severity, SEVERITY_WARNING)

    def test_issue_mentions_variance_value(self):
        doc = _build_ics(oc_variance="some flexibility allowed")
        issues = _issues_with_rule(lint(doc), "L001")
        self.assertIn("some flexibility allowed", issues[0].message)


# ---------------------------------------------------------------------------
# L002 — schema is prose for structured format
# ---------------------------------------------------------------------------

class TestL002ProseSchema(unittest.TestCase):

    def test_natural_language_schema_for_json_triggers(self):
        doc = _build_ics(
            oc_format="JSON",
            oc_schema="a JSON object with the review results",
        )
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L002"))

    def test_structured_json_schema_does_not_trigger(self):
        doc = _build_ics(
            oc_format="JSON",
            oc_schema='{ "result": "string", "status": "ok" | "error" }',
        )
        result = lint(doc)
        self.assertFalse(_has_rule(result, "L002"))

    def test_unified_diff_standard_schema_does_not_trigger(self):
        doc = _build_ics(
            oc_format="unified diff",
            oc_schema="standard unified diff against current HEAD",
        )
        result = lint(doc)
        self.assertFalse(_has_rule(result, "L002"))

    def test_prose_format_does_not_trigger(self):
        doc = _build_ics(
            oc_format="prose",
            oc_schema="a summary of findings with violations listed",
        )
        result = lint(doc)
        self.assertFalse(_has_rule(result, "L002"))

    def test_yaml_prose_schema_triggers(self):
        doc = _build_ics(
            oc_format="YAML",
            oc_schema="a YAML document describing the migration plan",
        )
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L002"))

    def test_issue_is_warning(self):
        doc = _build_ics(
            oc_format="JSON",
            oc_schema="a description of the result",
        )
        issues = _issues_with_rule(lint(doc), "L002")
        self.assertEqual(issues[0].severity, SEVERITY_WARNING)


# ---------------------------------------------------------------------------
# L003 — on_failure has no machine-detectable signal
# ---------------------------------------------------------------------------

class TestL003NoSignal(unittest.TestCase):

    def test_no_signal_triggers(self):
        doc = _build_ics(oc_on_failure="explain what went wrong")
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L003"))

    def test_blocked_prefix_clears(self):
        doc = _build_ics(oc_on_failure="return a line starting with BLOCKED:")
        result = lint(doc)
        self.assertFalse(_has_rule(result, "L003"))

    def test_uppercase_prefix_clears(self):
        doc = _build_ics(oc_on_failure="return ERROR: followed by the reason")
        result = lint(doc)
        self.assertFalse(_has_rule(result, "L003"))

    def test_ambiguous_prefix_clears(self):
        doc = _build_ics(oc_on_failure='return AMBIGUOUS: with missing context')
        result = lint(doc)
        self.assertFalse(_has_rule(result, "L003"))

    def test_json_return_clears(self):
        doc = _build_ics(oc_on_failure='return { "status": "error", "reason": "..." }')
        result = lint(doc)
        self.assertFalse(_has_rule(result, "L003"))

    def test_prefix_keyword_clears(self):
        doc = _build_ics(oc_on_failure='prefix the response with BLOCKED:')
        result = lint(doc)
        self.assertFalse(_has_rule(result, "L003"))

    def test_issue_is_warning(self):
        doc = _build_ics(oc_on_failure="just describe the problem")
        issues = _issues_with_rule(lint(doc), "L003")
        self.assertEqual(issues[0].severity, SEVERITY_WARNING)


# ---------------------------------------------------------------------------
# L004 — vague fallback in on_failure
# ---------------------------------------------------------------------------

class TestL004VagueOnFailure(unittest.TestCase):

    def test_try_your_best_triggers(self):
        doc = _build_ics(oc_on_failure="try your best to complete the task")
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L004"))

    def test_best_effort_triggers(self):
        doc = _build_ics(oc_on_failure="make a best effort attempt")
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L004"))

    def test_do_your_best_triggers(self):
        doc = _build_ics(oc_on_failure="do your best to produce output")
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L004"))

    def test_specific_instruction_does_not_trigger(self):
        doc = _build_ics(
            oc_on_failure="return a single line starting with BLOCKED: <reason>"
        )
        result = lint(doc)
        self.assertFalse(_has_rule(result, "L004"))

    def test_issue_is_warning(self):
        doc = _build_ics(oc_on_failure="try your best")
        issues = _issues_with_rule(lint(doc), "L004")
        self.assertEqual(issues[0].severity, SEVERITY_WARNING)


# ---------------------------------------------------------------------------
# L005 — empty CAPABILITY_DECLARATION
# ---------------------------------------------------------------------------

class TestL005EmptyCapability(unittest.TestCase):

    def test_empty_block_triggers(self):
        doc = _build_ics(capability="")
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L005"))

    def test_blank_lines_only_triggers(self):
        doc = _build_ics(capability="   \n\n   ")
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L005"))

    def test_comment_only_triggers(self):
        doc = _build_ics(capability="# TODO: add directives")
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L005"))

    def test_single_directive_does_not_trigger(self):
        doc = _build_ics(capability="ALLOW read access")
        result = lint(doc)
        self.assertFalse(_has_rule(result, "L005"))

    def test_issue_is_warning(self):
        doc = _build_ics(capability="")
        issues = _issues_with_rule(lint(doc), "L005")
        self.assertEqual(issues[0].severity, SEVERITY_WARNING)


# ---------------------------------------------------------------------------
# L006 — empty TASK_PAYLOAD
# ---------------------------------------------------------------------------

class TestL006EmptyTaskPayload(unittest.TestCase):

    def test_empty_task_triggers(self):
        doc = _build_ics(task="")
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L006"))

    def test_whitespace_only_triggers(self):
        doc = _build_ics(task="   \n\n")
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L006"))

    def test_non_empty_task_does_not_trigger(self):
        doc = _build_ics(task="Refactor apply_discount().")
        result = lint(doc)
        self.assertFalse(_has_rule(result, "L006"))

    def test_issue_is_warning(self):
        doc = _build_ics(task="")
        issues = _issues_with_rule(lint(doc), "L006")
        self.assertEqual(issues[0].severity, SEVERITY_WARNING)


# ---------------------------------------------------------------------------
# L007 — implied constraints in TASK_PAYLOAD
# ---------------------------------------------------------------------------

class TestL007ImpliedConstraints(unittest.TestCase):

    def test_dont_break_triggers(self):
        doc = _build_ics(task="Refactor apply_discount(). Don't break the API layer.")
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L007"))

    def test_must_not_triggers(self):
        doc = _build_ics(task="Add logging.\nMust not modify the database schema.")
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L007"))

    def test_never_triggers(self):
        doc = _build_ics(task="Update the handler. Never touch prod configs.")
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L007"))

    def test_do_not_triggers(self):
        doc = _build_ics(task="Do not modify any test files.\nRefactor orders.py.")
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L007"))

    def test_pure_imperative_does_not_trigger(self):
        doc = _build_ics(
            task=(
                "Split apply_discount() into two functions: "
                "apply_percentage_discount() and apply_flat_discount()."
            )
        )
        result = lint(doc)
        self.assertFalse(_has_rule(result, "L007"))

    def test_issue_is_warning(self):
        doc = _build_ics(task="Do not modify the API layer.")
        issues = _issues_with_rule(lint(doc), "L007")
        self.assertEqual(issues[0].severity, SEVERITY_WARNING)

    def test_hint_mentions_capability_declaration(self):
        doc = _build_ics(task="Don't modify the DB schema.")
        issues = _issues_with_rule(lint(doc), "L007")
        self.assertIn("CAPABILITY_DECLARATION", issues[0].hint)


# ---------------------------------------------------------------------------
# L008 — duplicate directives
# ---------------------------------------------------------------------------

class TestL008DuplicateDirectives(unittest.TestCase):

    def test_identical_allow_triggers(self):
        doc = _build_ics(
            capability=(
                "ALLOW read access\n"
                "ALLOW read access\n"
                "DENY write access"
            )
        )
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L008"))

    def test_different_directives_do_not_trigger(self):
        doc = _build_ics(
            capability=(
                "ALLOW read access\n"
                "ALLOW write access\n"
                "DENY delete access"
            )
        )
        result = lint(doc)
        self.assertFalse(_has_rule(result, "L008"))

    def test_case_insensitive_action_match(self):
        doc = _build_ics(
            capability=(
                "ALLOW Read Access\n"
                "ALLOW read access"
            )
        )
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L008"))

    def test_issue_is_info(self):
        doc = _build_ics(
            capability="ALLOW read access\nALLOW read access"
        )
        issues = _issues_with_rule(lint(doc), "L008")
        self.assertEqual(issues[0].severity, SEVERITY_INFO)


# ---------------------------------------------------------------------------
# L009 — conflicting ALLOW + DENY for same target
# ---------------------------------------------------------------------------

class TestL009ConflictingDirectives(unittest.TestCase):

    def test_allow_and_deny_same_action_triggers(self):
        doc = _build_ics(
            capability=(
                "ALLOW read access\n"
                "DENY read access"
            )
        )
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L009"))

    def test_allow_and_deny_same_qualified_target_triggers(self):
        doc = _build_ics(
            capability=(
                "ALLOW modification of .tf files WITHIN infra/staging/\n"
                "DENY modification of .tf files WITHIN infra/staging/"
            )
        )
        result = lint(doc)
        self.assertTrue(_has_rule(result, "L009"))

    def test_allow_deny_different_targets_do_not_trigger(self):
        doc = _build_ics(
            capability=(
                "ALLOW modification of .tf files WITHIN infra/staging/\n"
                "DENY modification of .tf files WITHIN infra/prod/"
            )
        )
        result = lint(doc)
        self.assertFalse(_has_rule(result, "L009"))

    def test_issue_is_error(self):
        doc = _build_ics(
            capability="ALLOW read access\nDENY read access"
        )
        issues = _issues_with_rule(lint(doc), "L009")
        self.assertEqual(issues[0].severity, SEVERITY_ERROR)
        self.assertTrue(lint(doc).has_errors)

    def test_issue_message_contains_both_directives(self):
        doc = _build_ics(
            capability="ALLOW read access\nDENY read access"
        )
        issues = _issues_with_rule(lint(doc), "L009")
        self.assertIn("ALLOW", issues[0].message)
        self.assertIn("DENY", issues[0].message)


# ---------------------------------------------------------------------------
# Clean pass — conformant documents produce no issues
# ---------------------------------------------------------------------------

class TestCleanPass(unittest.TestCase):

    def test_appendix_a_example_1_clean(self):
        doc = """\
###ICS:IMMUTABLE_CONTEXT###
System: order management service
Language: Python 3.11
Repo structure:
  src/orders/       — business logic
  src/orders/api/   — HTTP handlers
  tests/            — pytest test suite
Invariant: all monetary values stored as integer cents
###END:IMMUTABLE_CONTEXT###

###ICS:CAPABILITY_DECLARATION###
ALLOW   file modification WITHIN src/orders/
ALLOW   file creation WITHIN src/orders/ IF new file has corresponding test
DENY    modification of src/orders/api/
DENY    modification of any file WITHIN tests/
DENY    introduction of new external dependencies
REQUIRE type annotations ON all new functions
REQUIRE docstring ON all new public functions
###END:CAPABILITY_DECLARATION###

###ICS:SESSION_STATE###
[2024-01-15T09:30Z] Confirmed: discount logic currently lives in apply_discount() in src/orders/pricing.py
[2024-01-15T09:45Z] Decision: percentage and flat discounts to be handled by separate functions
###END:SESSION_STATE###

###ICS:TASK_PAYLOAD###
Split apply_discount() into two functions: apply_percentage_discount() and apply_flat_discount().
Preserve existing call sites by having apply_discount() delegate to the appropriate function
based on the discount type field.
###END:TASK_PAYLOAD###

###ICS:OUTPUT_CONTRACT###
format:     unified diff
schema:     standard unified diff against current HEAD; one diff block per modified file
variance:   diff header comments are permitted; no other variance allowed
on_failure: return a single line with the prefix BLOCKED: followed by the violated constraint
###END:OUTPUT_CONTRACT###"""
        result = lint(doc)
        self.assertFalse(
            result.has_issues,
            f"Expected no issues, got:\n{result.report()}"
        )

    def test_appendix_b_database_review_clean(self):
        doc = """\
###ICS:IMMUTABLE_CONTEXT###
System: analytics data warehouse
Engine: PostgreSQL 15
###END:IMMUTABLE_CONTEXT###

###ICS:CAPABILITY_DECLARATION###
ALLOW   read access to migration file content
ALLOW   read access to schema definitions WITHIN public.*
DENY    generation of migration file content
DENY    modification of migration files
REQUIRE flagging of any migration that lacks a reversible downgrade()
###END:CAPABILITY_DECLARATION###

###ICS:SESSION_STATE###
[2025-02-14T09:00Z] Review target: PR #412
###END:SESSION_STATE###

###ICS:TASK_PAYLOAD###
Review migration 0047_add_user_preferences.py for conformance with the migration
conventions and invariants declared in IMMUTABLE_CONTEXT.
###END:TASK_PAYLOAD###

###ICS:OUTPUT_CONTRACT###
format:     JSON
schema: {
  "migration": "string",
  "verdict":   "PASS" | "FAIL",
  "violations": [
    {
      "rule":     "string",
      "severity": "ERROR" | "WARNING",
      "detail":   "string"
    }
  ]
}
variance:   "violations" MAY be an empty array if verdict is PASS
on_failure: Return { "status": "error", "reason": "<single-sentence description>" }
###END:OUTPUT_CONTRACT###"""
        result = lint(doc)
        self.assertFalse(
            result.has_issues,
            f"Expected no issues, got:\n{result.report()}"
        )

    def test_minimal_conformant_doc_clean(self):
        doc = _build_ics()
        result = lint(doc)
        self.assertFalse(
            result.has_issues,
            f"Expected no issues, got:\n{result.report()}"
        )


# ---------------------------------------------------------------------------
# LintResult helpers
# ---------------------------------------------------------------------------

class TestLintResult(unittest.TestCase):

    def test_has_errors_false_when_only_warnings(self):
        doc = _build_ics(oc_variance="some flexibility")
        result = lint(doc)
        self.assertFalse(result.has_errors)
        self.assertTrue(result.has_issues)

    def test_has_errors_true_when_error_present(self):
        doc = _build_ics(capability="ALLOW read access\nDENY read access")
        result = lint(doc)
        self.assertTrue(result.has_errors)

    def test_report_shows_no_issues_when_clean(self):
        doc = _build_ics()
        report = lint(doc).report()
        self.assertIn("No issues", report)

    def test_report_contains_rule_ids(self):
        doc = _build_ics(oc_variance="some flexibility")
        report = lint(doc).report()
        self.assertIn("L001", report)

    def test_to_dict_structure(self):
        doc = _build_ics(oc_variance="some flexibility")
        d = lint(doc).to_dict()
        self.assertIn("issues", d)
        self.assertIsInstance(d["issues"], list)
        issue = d["issues"][0]
        self.assertIn("rule_id", issue)
        self.assertIn("severity", issue)
        self.assertIn("layer", issue)
        self.assertIn("message", issue)
        self.assertIn("hint", issue)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
