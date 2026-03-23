#!/usr/bin/env python3
"""
Test suite for ics_scaffold — M5.

Covers:
  • scaffold() returns valid, lint-clean documents for all 4 templates
  • Directive normalisation (action-only vs full-keyword inputs)
  • Directive validation (malformed inputs raise ScaffoldError)
  • Template overrides (per-field and full)
  • Multi-line schema indentation
  • Empty / minimal inputs
  • list_templates() API
  • Output structure (all 5 layers present, correct content)

Usage:
    python test_ics_scaffold.py
    python test_ics_scaffold.py -v
"""

import unittest

from ics_linter import lint
from ics_validator import validate
from ics_scaffold import (
    ScaffoldError,
    ScaffoldOptions,
    ScaffoldTemplate,
    TEMPLATES,
    list_templates,
    scaffold,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal(template: str = "code-diff", **kwargs) -> str:
    opts = ScaffoldOptions(system="test service", **kwargs)
    return scaffold(opts, template=template)


def _always_valid(doc: str, msg: str = "") -> None:
    """Assert the document passes structural validation."""
    vr = validate(doc)
    assert vr.compliant, f"Document not valid{': ' + msg if msg else ''}\n{vr.report()}"


def _always_lint_clean(doc: str, msg: str = "") -> None:
    """Assert the document has no lint ERRORs."""
    lr = lint(doc)
    errors = [i for i in lr.issues if i.severity == "error"]
    assert not errors, (
        f"Document has lint errors{': ' + msg if msg else ''}\n"
        + "\n".join(i.message for i in errors)
    )


# ---------------------------------------------------------------------------
# Template coverage
# ---------------------------------------------------------------------------

class TestAllTemplates(unittest.TestCase):

    def _check(self, template: str) -> None:
        doc = _minimal(template)
        _always_valid(doc, template)
        _always_lint_clean(doc, template)

    def test_code_diff_valid_and_clean(self):
        self._check("code-diff")

    def test_json_review_valid_and_clean(self):
        self._check("json-review")

    def test_json_output_valid_and_clean(self):
        self._check("json-output")

    def test_prose_report_valid_and_clean(self):
        self._check("prose-report")

    def test_all_known_templates_pass(self):
        for name in TEMPLATES:
            with self.subTest(template=name):
                self._check(name)

    def test_unknown_template_raises(self):
        with self.assertRaises(ScaffoldError) as ctx:
            _minimal(template="no-such-template")
        self.assertIn("no-such-template", str(ctx.exception))


# ---------------------------------------------------------------------------
# Document structure
# ---------------------------------------------------------------------------

class TestDocumentStructure(unittest.TestCase):

    def setUp(self):
        self.doc = _minimal(
            allows  = ["file modification WITHIN src/"],
            denies  = ["modification of src/api/"],
            requires= ["type annotations ON all new functions"],
            task    = "Refactor apply_discount().",
        )

    def test_all_five_layers_present(self):
        for layer in ("IMMUTABLE_CONTEXT", "CAPABILITY_DECLARATION",
                      "SESSION_STATE", "TASK_PAYLOAD", "OUTPUT_CONTRACT"):
            self.assertIn(f"###ICS:{layer}###", self.doc)
            self.assertIn(f"###END:{layer}###", self.doc)

    def test_system_in_immutable_context(self):
        self.assertIn("System: test service", self.doc)

    def test_allow_directive_present(self):
        self.assertIn("ALLOW file modification WITHIN src/", self.doc)

    def test_deny_directive_present(self):
        self.assertIn("DENY modification of src/api/", self.doc)

    def test_require_directive_present(self):
        self.assertIn("REQUIRE type annotations ON all new functions", self.doc)

    def test_task_payload_present(self):
        self.assertIn("Refactor apply_discount().", self.doc)

    def test_layer_order_correct(self):
        layers = [
            "IMMUTABLE_CONTEXT",
            "CAPABILITY_DECLARATION",
            "SESSION_STATE",
            "TASK_PAYLOAD",
            "OUTPUT_CONTRACT",
        ]
        positions = [self.doc.index(f"###ICS:{l}###") for l in layers]
        self.assertEqual(positions, sorted(positions))


# ---------------------------------------------------------------------------
# Directive normalisation
# ---------------------------------------------------------------------------

class TestDirectiveNormalisation(unittest.TestCase):

    def test_action_only_allows_prefixed(self):
        doc = _minimal(allows=["read access"])
        self.assertIn("ALLOW read access", doc)

    def test_action_only_denies_prefixed(self):
        doc = _minimal(denies=["write access"])
        self.assertIn("DENY write access", doc)

    def test_action_only_requires_prefixed(self):
        doc = _minimal(requires=["logging enabled"])
        self.assertIn("REQUIRE logging enabled", doc)

    def test_full_allow_directive_accepted(self):
        doc = _minimal(allows=["ALLOW file modification WITHIN src/"])
        self.assertIn("ALLOW file modification WITHIN src/", doc)

    def test_full_deny_directive_accepted(self):
        doc = _minimal(denies=["DENY modification of prod tables"])
        self.assertIn("DENY modification of prod tables", doc)

    def test_directive_with_qualifier_preserved(self):
        doc = _minimal(allows=["file modification WITHIN src/orders/"])
        self.assertIn("WITHIN src/orders/", doc)

    def test_directive_with_if_clause_preserved(self):
        doc = _minimal(
            allows=["file creation WITHIN src/ IF new file has a test"]
        )
        self.assertIn("IF new file has a test", doc)

    def test_multiple_allows(self):
        doc = _minimal(allows=["read access", "write access WITHIN staging/"])
        self.assertIn("ALLOW read access", doc)
        self.assertIn("ALLOW write access WITHIN staging/", doc)


# ---------------------------------------------------------------------------
# Directive validation
# ---------------------------------------------------------------------------

class TestDirectiveValidation(unittest.TestCase):

    def test_malformed_allow_raises(self):
        with self.assertRaises(ScaffoldError) as ctx:
            _minimal(allows=["WITHIN"])   # qualifier with no action
        self.assertIn("Invalid directive", str(ctx.exception))

    def test_qualifier_without_target_raises(self):
        with self.assertRaises(ScaffoldError):
            _minimal(allows=["file writes WITHIN"])

    def test_if_without_condition_raises(self):
        with self.assertRaises(ScaffoldError):
            _minimal(allows=["file writes IF"])

    def test_unknown_keyword_as_action_is_accepted(self):
        # "PERMIT read" — PERMIT is not a keyword, treated as action under ALLOW
        doc = _minimal(allows=["PERMIT read access"])
        # The scaffold prepends ALLOW → "ALLOW PERMIT read access"
        self.assertIn("ALLOW PERMIT read access", doc)


# ---------------------------------------------------------------------------
# OUTPUT_CONTRACT overrides
# ---------------------------------------------------------------------------

class TestOutputContractOverrides(unittest.TestCase):

    def test_format_override(self):
        opts = ScaffoldOptions(system="s", output_format="YAML")
        doc  = scaffold(opts)
        self.assertIn("format:     YAML", doc)

    def test_schema_override(self):
        opts = ScaffoldOptions(system="s", output_schema='{ "x": "string" }')
        doc  = scaffold(opts)
        self.assertIn('{ "x": "string" }', doc)

    def test_variance_override(self):
        opts = ScaffoldOptions(system="s", variance="none")
        doc  = scaffold(opts)
        self.assertIn("variance:   none", doc)

    def test_on_failure_override(self):
        opts = ScaffoldOptions(system="s",
                               on_failure='return ERROR: <reason>')
        doc  = scaffold(opts)
        self.assertIn("return ERROR: <reason>", doc)

    def test_template_defaults_used_when_no_override(self):
        opts = ScaffoldOptions(system="s")
        doc  = scaffold(opts, template="json-review")
        self.assertIn('"verdict"', doc)
        self.assertIn('"PASS" | "FAIL"', doc)

    def test_override_takes_precedence_over_template(self):
        opts = ScaffoldOptions(system="s", output_format="CSV")
        doc  = scaffold(opts, template="json-review")
        self.assertIn("format:     CSV", doc)
        self.assertNotIn("format:     JSON", doc)


# ---------------------------------------------------------------------------
# Multi-line schema indentation
# ---------------------------------------------------------------------------

class TestMultiLineSchema(unittest.TestCase):

    def test_json_review_schema_is_valid_oc(self):
        doc = _minimal(template="json-review")
        _always_valid(doc, "json-review multiline schema")

    def test_multiline_override_schema_produces_valid_doc(self):
        multi = '{\n  "a": "string",\n  "b": "number"\n}'
        opts = ScaffoldOptions(system="s", output_schema=multi)
        doc  = scaffold(opts)
        _always_valid(doc, "custom multiline schema")

    def test_multiline_schema_content_preserved(self):
        multi = '{\n  "key": "value"\n}'
        opts = ScaffoldOptions(system="s", output_schema=multi)
        doc  = scaffold(opts)
        self.assertIn('"key": "value"', doc)


# ---------------------------------------------------------------------------
# Minimal / edge case inputs
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):

    def test_system_only_produces_valid_doc(self):
        doc = scaffold(ScaffoldOptions(system="minimal system"))
        _always_valid(doc)
        _always_lint_clean(doc)

    def test_extra_context_appears_in_immutable(self):
        opts = ScaffoldOptions(
            system="s",
            extra_context="Language: Python 3.11\nRepo: src/"
        )
        doc = scaffold(opts)
        self.assertIn("Language: Python 3.11", doc)

    def test_session_state_default_is_clear(self):
        doc = _minimal()
        # Grab SESSION_STATE content
        start = doc.index("###ICS:SESSION_STATE###") + len("###ICS:SESSION_STATE###\n")
        end   = doc.index("###END:SESSION_STATE###")
        content = doc[start:end].strip()
        self.assertEqual(content, "CLEAR")

    def test_custom_session_state(self):
        opts = ScaffoldOptions(
            system="s",
            session_state="[2025-01-01T00:00Z] Task started"
        )
        doc = scaffold(opts)
        self.assertIn("[2025-01-01T00:00Z] Task started", doc)

    def test_empty_allows_denies_requires(self):
        doc = scaffold(ScaffoldOptions(system="s"))
        # No directives — L005 is a WARNING not ERROR so doc still passes
        lr = lint(doc)
        errors = [i for i in lr.issues if i.severity == "error"]
        self.assertEqual(errors, [])

    def test_task_content_preserved(self):
        task = "Analyse the migration script.\nReport all violations."
        doc  = _minimal(task=task)
        self.assertIn("Analyse the migration script.", doc)
        self.assertIn("Report all violations.", doc)


# ---------------------------------------------------------------------------
# list_templates
# ---------------------------------------------------------------------------

class TestListTemplates(unittest.TestCase):

    def test_returns_list(self):
        self.assertIsInstance(list_templates(), list)

    def test_all_templates_included(self):
        names = {t.name for t in list_templates()}
        self.assertEqual(names, set(TEMPLATES.keys()))

    def test_sorted_by_name(self):
        names = [t.name for t in list_templates()]
        self.assertEqual(names, sorted(names))

    def test_each_is_scaffold_template(self):
        for t in list_templates():
            self.assertIsInstance(t, ScaffoldTemplate)


# ---------------------------------------------------------------------------
# Real-world pattern: payments platform style
# ---------------------------------------------------------------------------

class TestRealWorldPatterns(unittest.TestCase):

    def test_payments_platform_code_diff(self):
        opts = ScaffoldOptions(
            system        = "payments platform",
            extra_context = "Language: Python 3.11\nRepo: src/payments/",
            allows  = [
                "file modification WITHIN src/payments/",
                "file creation WITHIN src/payments/ IF new file has a test",
            ],
            denies  = [
                "modification of src/payments/crypto/",
                "introduction of new external dependencies",
                "deletion of any file",
            ],
            requires = [
                "type annotations ON all new functions",
            ],
            task = "Add retry logic with exponential back-off to deliver().",
        )
        doc = scaffold(opts, template="code-diff")
        _always_valid(doc)
        _always_lint_clean(doc)
        self.assertIn("ALLOW file modification WITHIN src/payments/", doc)
        self.assertIn("DENY modification of src/payments/crypto/", doc)

    def test_database_review_json(self):
        opts = ScaffoldOptions(
            system   = "analytics data warehouse",
            allows   = [
                "read access to migration file content",
                "read access to schema definitions WITHIN public.*",
            ],
            denies   = [
                "generation of migration file content",
                "modification of migration files",
            ],
            requires = [
                "flagging of any migration that lacks a reversible downgrade()",
            ],
            task = "Review migration 0047 for conformance with IMMUTABLE_CONTEXT conventions.",
        )
        doc = scaffold(opts, template="json-review")
        _always_valid(doc)
        _always_lint_clean(doc)
        self.assertIn('"verdict"', doc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
