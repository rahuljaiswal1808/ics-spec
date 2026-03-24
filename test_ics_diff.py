#!/usr/bin/env python3
"""
Test suite for ics_diff — M6.

Covers:
  • Identical documents → no changes
  • CAPABILITY_DECLARATION: ALLOW/DENY/REQUIRE add/remove → correct ChangeType
  • CAPABILITY_DECLARATION: directive reordering → no changes
  • CAPABILITY_DECLARATION: case / whitespace normalisation → no changes
  • CAPABILITY_DECLARATION: layer added / removed entirely
  • OUTPUT_CONTRACT: format, schema, variance, on_failure changes
  • OUTPUT_CONTRACT: layer added / removed entirely
  • IMMUTABLE_CONTEXT / SESSION_STATE / TASK_PAYLOAD: NEUTRAL changes
  • DiffResult helpers: is_breaking, breaking/additive/neutral filters, summary
  • DiffResult.to_dict() structure
  • DiffResult.report() and report(breaking_only=True)
  • CLI exit codes (via main())

Usage:
    python test_ics_diff.py
    python test_ics_diff.py -v
"""

import json
import sys
import unittest
from io import StringIO
from unittest.mock import patch

from ics_diff import (
    ChangeType,
    ContractChange,
    DiffResult,
    diff,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make(
    capability: str = "",
    output_contract: str = "",
    immutable: str = "",
    session: str = "",
    task: str = "",
) -> str:
    """Assemble a minimal ICS document from optional layer content."""
    parts: list[str] = []

    def _layer(name: str, content: str) -> str:
        return f"###ICS:{name}###\n{content.strip()}\n###END:{name}###"

    if capability:
        parts.append(_layer("CAPABILITY_DECLARATION", capability))
    if output_contract:
        parts.append(_layer("OUTPUT_CONTRACT", output_contract))
    if immutable:
        parts.append(_layer("IMMUTABLE_CONTEXT", immutable))
    if session:
        parts.append(_layer("SESSION_STATE", session))
    if task:
        parts.append(_layer("TASK_PAYLOAD", task))
    return "\n\n".join(parts)


# A minimal but valid OUTPUT_CONTRACT block reused across tests
_OC_JSON = """\
format: JSON
schema: {"type": "object", "properties": {"result": {"type": "string"}}}
variance: none
on_failure: return_empty
"""

_OC_JSON_SCHEMA2 = """\
format: JSON
schema: {"type": "object", "properties": {"answer": {"type": "number"}}}
variance: none
on_failure: return_empty
"""

_OC_MARKDOWN = """\
format: MARKDOWN
schema: none
variance: none
on_failure: return_empty
"""


# ---------------------------------------------------------------------------
# 1. Identical documents
# ---------------------------------------------------------------------------

class TestNoDifferences(unittest.TestCase):

    def test_completely_empty_documents(self):
        result = diff("", "")
        self.assertEqual(result.changes, [])
        self.assertFalse(result.is_breaking)
        self.assertEqual(result.summary(), "No changes.")

    def test_identical_full_document(self):
        doc = _make(
            capability=(
                "ALLOW action:read_files from:user_request\n"
                "DENY  action:write_files unless:authorized\n"
            ),
            output_contract=_OC_JSON,
            immutable="project_id: acme-123",
            session="user_role: admin",
            task="query: list all records",
        )
        result = diff(doc, doc)
        self.assertEqual(result.changes, [])

    def test_whitespace_reordering_in_capability_no_change(self):
        a = _make(capability=(
            "ALLOW action:read_files from:user_request\n"
            "DENY  action:write_files unless:authorized\n"
        ))
        b = _make(capability=(
            "DENY  action:write_files unless:authorized\n"
            "ALLOW action:read_files from:user_request\n"
        ))
        result = diff(a, b)
        self.assertEqual(result.changes, [])

    def test_case_normalisation_no_change(self):
        # Keywords are already normalised by the parser; action is lowercased
        a = _make(capability="ALLOW action:Read_Files from:User_Request\n")
        b = _make(capability="ALLOW action:read_files from:user_request\n")
        result = diff(a, b)
        self.assertEqual(result.changes, [])


# ---------------------------------------------------------------------------
# 2. CAPABILITY_DECLARATION — ALLOW
# ---------------------------------------------------------------------------

class TestCapabilityAllow(unittest.TestCase):

    def test_allow_added_is_additive(self):
        a = _make(capability="DENY action:write_files\n")
        b = _make(capability=(
            "DENY  action:write_files\n"
            "ALLOW action:read_files from:user_request\n"
        ))
        result = diff(a, b)
        self.assertEqual(len(result.changes), 1)
        self.assertEqual(result.changes[0].kind, ChangeType.ADDITIVE)
        self.assertFalse(result.is_breaking)

    def test_allow_removed_is_breaking(self):
        a = _make(capability=(
            "DENY  action:write_files\n"
            "ALLOW action:read_files from:user_request\n"
        ))
        b = _make(capability="DENY action:write_files\n")
        result = diff(a, b)
        self.assertEqual(len(result.changes), 1)
        self.assertEqual(result.changes[0].kind, ChangeType.BREAKING)
        self.assertTrue(result.is_breaking)

    def test_allow_added_and_removed_simultaneously(self):
        a = _make(capability="ALLOW action:read_files from:user_request\n")
        b = _make(capability="ALLOW action:write_files from:user_request\n")
        result = diff(a, b)
        # read removed (BREAKING) + write added (ADDITIVE)
        kinds = {c.kind for c in result.changes}
        self.assertIn(ChangeType.BREAKING, kinds)
        self.assertIn(ChangeType.ADDITIVE, kinds)
        self.assertTrue(result.is_breaking)


# ---------------------------------------------------------------------------
# 3. CAPABILITY_DECLARATION — DENY
# ---------------------------------------------------------------------------

class TestCapabilityDeny(unittest.TestCase):

    def test_deny_added_is_breaking(self):
        a = _make(capability="ALLOW action:read_files from:user_request\n")
        b = _make(capability=(
            "ALLOW action:read_files from:user_request\n"
            "DENY  action:write_files unless:authorized\n"
        ))
        result = diff(a, b)
        self.assertEqual(len(result.changes), 1)
        self.assertEqual(result.changes[0].kind, ChangeType.BREAKING)
        self.assertTrue(result.is_breaking)

    def test_deny_removed_is_additive(self):
        a = _make(capability=(
            "ALLOW action:read_files from:user_request\n"
            "DENY  action:write_files unless:authorized\n"
        ))
        b = _make(capability="ALLOW action:read_files from:user_request\n")
        result = diff(a, b)
        self.assertEqual(len(result.changes), 1)
        self.assertEqual(result.changes[0].kind, ChangeType.ADDITIVE)
        self.assertFalse(result.is_breaking)


# ---------------------------------------------------------------------------
# 4. CAPABILITY_DECLARATION — REQUIRE
# ---------------------------------------------------------------------------

class TestCapabilityRequire(unittest.TestCase):

    def test_require_added_is_breaking(self):
        a = _make(capability="ALLOW action:read_files from:user_request\n")
        b = _make(capability=(
            "ALLOW   action:read_files from:user_request\n"
            "REQUIRE action:log_request when:always\n"
        ))
        result = diff(a, b)
        self.assertEqual(len(result.changes), 1)
        self.assertEqual(result.changes[0].kind, ChangeType.BREAKING)

    def test_require_removed_is_additive(self):
        a = _make(capability=(
            "ALLOW   action:read_files from:user_request\n"
            "REQUIRE action:log_request when:always\n"
        ))
        b = _make(capability="ALLOW action:read_files from:user_request\n")
        result = diff(a, b)
        self.assertEqual(len(result.changes), 1)
        self.assertEqual(result.changes[0].kind, ChangeType.ADDITIVE)


# ---------------------------------------------------------------------------
# 5. CAPABILITY_DECLARATION — layer added / removed entirely
# ---------------------------------------------------------------------------

class TestCapabilityLayerPresence(unittest.TestCase):

    def test_capability_layer_added(self):
        a = _make()
        b = _make(capability="ALLOW action:read_files from:user_request\n")
        result = diff(a, b)
        # ALLOW added → ADDITIVE
        self.assertTrue(all(c.kind == ChangeType.ADDITIVE for c in result.changes))

    def test_capability_layer_removed_with_deny(self):
        a = _make(capability="DENY action:write_files unless:authorized\n")
        b = _make()
        result = diff(a, b)
        # DENY removed → ADDITIVE
        self.assertTrue(all(c.kind == ChangeType.ADDITIVE for c in result.changes))

    def test_capability_layer_removed_with_allow(self):
        a = _make(capability="ALLOW action:read_files from:user_request\n")
        b = _make()
        result = diff(a, b)
        # ALLOW removed → BREAKING
        self.assertTrue(result.is_breaking)


# ---------------------------------------------------------------------------
# 6. OUTPUT_CONTRACT — field changes
# ---------------------------------------------------------------------------

class TestOutputContractFields(unittest.TestCase):

    def test_format_changed_is_breaking(self):
        a = _make(output_contract=_OC_JSON)
        b = _make(output_contract=_OC_MARKDOWN)
        result = diff(a, b)
        format_changes = [c for c in result.changes
                          if "format" in c.what and c.layer == "OUTPUT_CONTRACT"]
        self.assertTrue(any(c.kind == ChangeType.BREAKING for c in format_changes))
        self.assertTrue(result.is_breaking)

    def test_schema_changed_is_breaking(self):
        a = _make(output_contract=_OC_JSON)
        b = _make(output_contract=_OC_JSON_SCHEMA2)
        result = diff(a, b)
        schema_changes = [c for c in result.changes
                          if "schema" in c.what and c.layer == "OUTPUT_CONTRACT"]
        self.assertTrue(any(c.kind == ChangeType.BREAKING for c in schema_changes))
        self.assertTrue(result.is_breaking)

    def test_variance_tightened_is_breaking(self):
        oc_loose = "format: JSON\nschema: none\nvariance: minor_stylistic\non_failure: return_empty\n"
        oc_tight = "format: JSON\nschema: none\nvariance: none\non_failure: return_empty\n"
        a = _make(output_contract=oc_loose)
        b = _make(output_contract=oc_tight)
        result = diff(a, b)
        var_changes = [c for c in result.changes if "variance" in c.what]
        self.assertTrue(any(c.kind == ChangeType.BREAKING for c in var_changes))

    def test_variance_loosened_is_additive(self):
        oc_tight = "format: JSON\nschema: none\nvariance: none\non_failure: return_empty\n"
        oc_loose = "format: JSON\nschema: none\nvariance: minor_stylistic\non_failure: return_empty\n"
        a = _make(output_contract=oc_tight)
        b = _make(output_contract=oc_loose)
        result = diff(a, b)
        var_changes = [c for c in result.changes if "variance" in c.what]
        self.assertTrue(any(c.kind == ChangeType.ADDITIVE for c in var_changes))
        self.assertFalse(result.is_breaking)

    def test_on_failure_changed_is_neutral(self):
        oc_a = "format: JSON\nschema: none\nvariance: none\non_failure: return_empty\n"
        oc_b = "format: JSON\nschema: none\nvariance: none\non_failure: return_error_code\n"
        a = _make(output_contract=oc_a)
        b = _make(output_contract=oc_b)
        result = diff(a, b)
        of_changes = [c for c in result.changes if "on_failure" in c.what]
        self.assertTrue(all(c.kind == ChangeType.NEUTRAL for c in of_changes))
        self.assertFalse(result.is_breaking)

    def test_identical_output_contract_no_change(self):
        a = _make(output_contract=_OC_JSON)
        b = _make(output_contract=_OC_JSON)
        result = diff(a, b)
        oc_changes = [c for c in result.changes if c.layer == "OUTPUT_CONTRACT"]
        self.assertEqual(oc_changes, [])


# ---------------------------------------------------------------------------
# 7. OUTPUT_CONTRACT — layer added / removed
# ---------------------------------------------------------------------------

class TestOutputContractPresence(unittest.TestCase):

    def test_output_contract_added_is_breaking(self):
        a = _make()
        b = _make(output_contract=_OC_JSON)
        result = diff(a, b)
        oc_changes = [c for c in result.changes if c.layer == "OUTPUT_CONTRACT"]
        self.assertTrue(any(c.kind == ChangeType.BREAKING for c in oc_changes))

    def test_output_contract_removed_is_breaking(self):
        a = _make(output_contract=_OC_JSON)
        b = _make()
        result = diff(a, b)
        oc_changes = [c for c in result.changes if c.layer == "OUTPUT_CONTRACT"]
        self.assertTrue(any(c.kind == ChangeType.BREAKING for c in oc_changes))


# ---------------------------------------------------------------------------
# 8. NEUTRAL layers
# ---------------------------------------------------------------------------

class TestNeutralLayers(unittest.TestCase):

    def _neutral_test(self, layer_kw: str, content_a: str, content_b: str):
        kwargs_a = {layer_kw: content_a}
        kwargs_b = {layer_kw: content_b}
        a = _make(**kwargs_a)
        b = _make(**kwargs_b)
        result = diff(a, b)
        self.assertEqual(len(result.changes), 1)
        self.assertEqual(result.changes[0].kind, ChangeType.NEUTRAL)
        self.assertFalse(result.is_breaking)

    def test_immutable_context_changed_neutral(self):
        self._neutral_test("immutable", "project: alpha", "project: beta")

    def test_session_state_changed_neutral(self):
        self._neutral_test("session", "user_role: viewer", "user_role: editor")

    def test_task_payload_changed_neutral(self):
        self._neutral_test("task", "query: list", "query: search term=foo")

    def test_immutable_context_added_neutral(self):
        a = _make()
        b = _make(immutable="project: alpha")
        result = diff(a, b)
        self.assertEqual(len(result.changes), 1)
        self.assertEqual(result.changes[0].kind, ChangeType.NEUTRAL)

    def test_session_state_removed_neutral(self):
        a = _make(session="user_role: admin")
        b = _make()
        result = diff(a, b)
        self.assertEqual(len(result.changes), 1)
        self.assertEqual(result.changes[0].kind, ChangeType.NEUTRAL)

    def test_no_change_in_neutral_layer(self):
        a = _make(immutable="project: alpha")
        b = _make(immutable="project: alpha")
        result = diff(a, b)
        neutral_changes = [c for c in result.changes if c.layer == "IMMUTABLE_CONTEXT"]
        self.assertEqual(neutral_changes, [])


# ---------------------------------------------------------------------------
# 9. DiffResult helpers
# ---------------------------------------------------------------------------

class TestDiffResultHelpers(unittest.TestCase):

    def _make_result(self) -> DiffResult:
        return DiffResult(changes=[
            ContractChange("CAPABILITY_DECLARATION", ChangeType.BREAKING,
                           "ALLOW removed: ALLOW action:read", "ALLOW action:read", None),
            ContractChange("CAPABILITY_DECLARATION", ChangeType.ADDITIVE,
                           "DENY removed: DENY action:write", "DENY action:write", None),
            ContractChange("SESSION_STATE", ChangeType.NEUTRAL,
                           "SESSION_STATE content changed", "old", "new"),
        ])

    def test_is_breaking_true(self):
        self.assertTrue(self._make_result().is_breaking)

    def test_is_breaking_false_when_no_breaking(self):
        result = DiffResult(changes=[
            ContractChange("SESSION_STATE", ChangeType.NEUTRAL, "changed", "a", "b"),
        ])
        self.assertFalse(result.is_breaking)

    def test_breaking_filter(self):
        result = self._make_result()
        self.assertEqual(len(result.breaking), 1)
        self.assertEqual(result.breaking[0].kind, ChangeType.BREAKING)

    def test_additive_filter(self):
        result = self._make_result()
        self.assertEqual(len(result.additive), 1)

    def test_neutral_filter(self):
        result = self._make_result()
        self.assertEqual(len(result.neutral), 1)

    def test_summary_all_kinds(self):
        summary = self._make_result().summary()
        self.assertIn("breaking", summary)
        self.assertIn("additive", summary)
        self.assertIn("neutral", summary)

    def test_summary_no_changes(self):
        self.assertEqual(DiffResult().summary(), "No changes.")

    def test_report_contains_change_descriptions(self):
        result = self._make_result()
        report = result.report()
        self.assertIn("BREAKING", report)
        self.assertIn("ADDITIVE", report)
        self.assertIn("NEUTRAL", report)

    def test_report_breaking_only(self):
        result = self._make_result()
        report = result.report(breaking_only=True)
        self.assertIn("BREAKING", report)
        self.assertNotIn("ADDITIVE", report)
        self.assertNotIn("NEUTRAL", report)

    def test_report_no_breaking_breaking_only(self):
        result = DiffResult(changes=[
            ContractChange("SESSION_STATE", ChangeType.NEUTRAL, "changed", "a", "b"),
        ])
        report = result.report(breaking_only=True)
        self.assertIn("No breaking changes", report)


# ---------------------------------------------------------------------------
# 10. DiffResult.to_dict()
# ---------------------------------------------------------------------------

class TestDiffResultToDict(unittest.TestCase):

    def test_structure(self):
        a = _make(capability="ALLOW action:read_files from:user_request\n")
        b = _make(capability=(
            "ALLOW action:read_files from:user_request\n"
            "DENY  action:write_files unless:authorized\n"
        ))
        result = diff(a, b)
        d = result.to_dict()
        self.assertIn("breaking", d)
        self.assertIn("summary", d)
        self.assertIn("changes", d)
        self.assertIsInstance(d["changes"], list)
        for change in d["changes"]:
            self.assertIn("layer", change)
            self.assertIn("kind", change)
            self.assertIn("what", change)
            self.assertIn("before", change)
            self.assertIn("after", change)

    def test_serialisable_to_json(self):
        a = _make(capability="ALLOW action:read_files from:user_request\n")
        b = _make(capability="DENY  action:write_files unless:authorized\n")
        result = diff(a, b)
        # Should not raise
        json_str = json.dumps(result.to_dict())
        parsed = json.loads(json_str)
        self.assertIn("breaking", parsed)

    def test_no_changes_dict(self):
        result = diff("", "")
        d = result.to_dict()
        self.assertFalse(d["breaking"])
        self.assertEqual(d["changes"], [])


# ---------------------------------------------------------------------------
# 11. ContractChange.__str__
# ---------------------------------------------------------------------------

class TestContractChangeStr(unittest.TestCase):

    def test_str_inline_values(self):
        c = ContractChange(
            "OUTPUT_CONTRACT", ChangeType.BREAKING,
            "format changed: 'JSON' → 'MARKDOWN'",
            "JSON", "MARKDOWN",
        )
        s = str(c)
        self.assertIn("BREAKING", s)
        self.assertIn("OUTPUT_CONTRACT", s)
        self.assertIn("format changed", s)


# ---------------------------------------------------------------------------
# 12. CLI — exit codes
# ---------------------------------------------------------------------------

class TestCLIExitCodes(unittest.TestCase):
    """Smoke-test the CLI main() for correct exit codes."""

    def _run_main(self, content_a: str, content_b: str,
                  extra_args: list[str] | None = None) -> int:
        import tempfile, os
        from ics_diff import main as ics_main

        with tempfile.NamedTemporaryFile(mode="w", suffix=".ics",
                                         delete=False, encoding="utf-8") as fa:
            fa.write(content_a)
            path_a = fa.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ics",
                                         delete=False, encoding="utf-8") as fb:
            fb.write(content_b)
            path_b = fb.name

        args = ["ics-diff", path_a, path_b] + (extra_args or [])
        try:
            with patch("sys.argv", args), \
                 patch("sys.stdout", StringIO()), \
                 self.assertRaises(SystemExit) as cm:
                ics_main()
            return cm.exception.code
        finally:
            os.unlink(path_a)
            os.unlink(path_b)

    def test_exit_0_no_changes(self):
        doc = _make(capability="ALLOW action:read_files from:user_request\n")
        self.assertEqual(self._run_main(doc, doc), 0)

    def test_exit_0_additive_only(self):
        a = _make(capability="DENY action:write_files unless:authorized\n")
        b = _make(capability=(
            "DENY  action:write_files unless:authorized\n"
            "ALLOW action:read_files from:user_request\n"
        ))
        self.assertEqual(self._run_main(a, b), 0)

    def test_exit_1_breaking(self):
        a = _make(capability="ALLOW action:read_files from:user_request\n")
        b = _make(capability="DENY  action:write_files unless:authorized\n")
        self.assertEqual(self._run_main(a, b), 1)

    def test_json_flag_produces_valid_json(self):
        import tempfile, os
        from ics_diff import main as ics_main

        doc = _make(capability="ALLOW action:read_files from:user_request\n")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ics",
                                         delete=False, encoding="utf-8") as fa:
            fa.write(doc)
            path_a = fa.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ics",
                                         delete=False, encoding="utf-8") as fb:
            fb.write(doc)
            path_b = fb.name

        captured = StringIO()
        try:
            with patch("sys.argv", ["ics-diff", path_a, path_b, "--json"]), \
                 patch("sys.stdout", captured), \
                 self.assertRaises(SystemExit):
                ics_main()
            output = captured.getvalue()
            parsed = json.loads(output)
            self.assertIn("breaking", parsed)
        finally:
            os.unlink(path_a)
            os.unlink(path_b)


# ---------------------------------------------------------------------------
# 13. Multi-layer compound scenarios
# ---------------------------------------------------------------------------

class TestCompoundScenarios(unittest.TestCase):

    def test_additive_capability_neutral_context_not_breaking(self):
        a = _make(
            capability="DENY action:write_files unless:authorized\n",
            immutable="version: 1",
        )
        b = _make(
            capability=(
                "DENY  action:write_files unless:authorized\n"
                "ALLOW action:read_files from:user_request\n"
            ),
            immutable="version: 2",
        )
        result = diff(a, b)
        self.assertFalse(result.is_breaking)
        self.assertGreater(len(result.changes), 0)

    def test_breaking_capability_plus_neutral_session(self):
        a = _make(
            capability="ALLOW action:read_files from:user_request\n",
            session="user_role: admin",
        )
        b = _make(
            capability=(
                "ALLOW action:read_files from:user_request\n"
                "DENY  action:write_files unless:authorized\n"
            ),
            session="user_role: viewer",
        )
        result = diff(a, b)
        self.assertTrue(result.is_breaking)
        neutral = [c for c in result.changes if c.kind == ChangeType.NEUTRAL]
        self.assertTrue(len(neutral) >= 1)

    def test_multiple_breaking_counted_correctly(self):
        a = _make(
            capability=(
                "ALLOW action:read_files from:user_request\n"
                "ALLOW action:execute_tools from:user_request\n"
            ),
            output_contract=_OC_JSON,
        )
        b = _make(
            output_contract=_OC_MARKDOWN,
        )
        result = diff(a, b)
        self.assertTrue(result.is_breaking)
        self.assertGreaterEqual(len(result.breaking), 2)


if __name__ == "__main__":
    unittest.main()
