#!/usr/bin/env python3
"""
Test suite for ics_constraint_parser — M3.

Covers:
  • Directive parser  (parse_directive, parse_capability_block)
  • OutputContract parser  (parse_output_contract)
  • All grammar edge-cases and error paths
  • Real-world examples drawn from APPENDIX-A and APPENDIX-B

Usage:
    python test_ics_constraint_parser.py
    python test_ics_constraint_parser.py -v
"""

import unittest

from ics_constraint_parser import (
    Directive,
    OutputContract,
    ParseError,
    ParsedCapabilityBlock,
    parse_capability_block,
    parse_directive,
    parse_output_contract,
)


# ---------------------------------------------------------------------------
# parse_directive — happy paths
# ---------------------------------------------------------------------------

class TestParseDirectiveBasic(unittest.TestCase):

    def test_allow_simple(self):
        d = parse_directive("ALLOW read access")
        self.assertEqual(d.keyword, "ALLOW")
        self.assertEqual(d.action, "read access")
        self.assertIsNone(d.qualifier_word)
        self.assertIsNone(d.qualifier_target)
        self.assertIsNone(d.condition)

    def test_deny_simple(self):
        d = parse_directive("DENY file deletion")
        self.assertEqual(d.keyword, "DENY")
        self.assertEqual(d.action, "file deletion")

    def test_require_simple(self):
        d = parse_directive("REQUIRE all outputs are JSON")
        self.assertEqual(d.keyword, "REQUIRE")
        self.assertEqual(d.action, "all outputs are JSON")

    def test_keyword_case_insensitive(self):
        for kw in ("allow", "Allow", "ALLOW", "aLlOw"):
            d = parse_directive(f"{kw} read access")
            self.assertEqual(d.keyword, "ALLOW")

    def test_deny_keyword_normalised(self):
        self.assertEqual(parse_directive("deny write access").keyword, "DENY")

    def test_require_keyword_normalised(self):
        self.assertEqual(parse_directive("require logging enabled").keyword, "REQUIRE")

    def test_leading_whitespace_stripped(self):
        d = parse_directive("   ALLOW read access   ")
        self.assertEqual(d.keyword, "ALLOW")
        self.assertEqual(d.action, "read access")
        self.assertEqual(d.raw, "ALLOW read access")

    def test_raw_preserves_original(self):
        line = "DENY modification of .tf files WITHIN infra/prod/"
        d = parse_directive(line)
        self.assertEqual(d.raw, line)


class TestParseDirectiveQualifiers(unittest.TestCase):

    def test_within_qualifier(self):
        d = parse_directive("ALLOW modification of .tf files WITHIN infra/staging/")
        self.assertEqual(d.qualifier_word, "WITHIN")
        self.assertEqual(d.qualifier_target, "infra/staging/")
        self.assertEqual(d.action, "modification of .tf files")

    def test_on_qualifier(self):
        d = parse_directive("ALLOW write access ON public.users")
        self.assertEqual(d.qualifier_word, "ON")
        self.assertEqual(d.qualifier_target, "public.users")

    def test_with_qualifier(self):
        d = parse_directive("REQUIRE tagging of all new AWS resources WITH Environment and Owner tags")
        self.assertEqual(d.qualifier_word, "WITH")
        self.assertEqual(d.qualifier_target, "Environment and Owner tags")
        self.assertEqual(d.action, "tagging of all new AWS resources")

    def test_unless_qualifier(self):
        d = parse_directive("REQUIRE naming of all new resources UNLESS following the {env}-{service}-{resource_type} pattern")
        self.assertEqual(d.qualifier_word, "UNLESS")
        self.assertIn("{env}", d.qualifier_target)

    def test_qualifier_case_insensitive(self):
        d = parse_directive("ALLOW file writes within src/")
        self.assertEqual(d.qualifier_word, "WITHIN")

    def test_qualifier_word_not_substring(self):
        # "modification" contains "ON" but should NOT be treated as a qualifier
        d = parse_directive("DENY modification of prod tables")
        self.assertIsNone(d.qualifier_word)
        self.assertEqual(d.action, "modification of prod tables")

    def test_within_word_not_substring(self):
        # "WITHIN_CONFIG" is not the keyword "WITHIN"
        d = parse_directive("ALLOW reads WITHIN_CONFIG files")
        self.assertIsNone(d.qualifier_word)
        self.assertEqual(d.action, "reads WITHIN_CONFIG files")

    def test_multi_word_target(self):
        d = parse_directive("ALLOW new file creation WITHIN infra/modules/ reusable")
        self.assertEqual(d.qualifier_word, "WITHIN")
        self.assertEqual(d.qualifier_target, "infra/modules/ reusable")


class TestParseDirectiveConditions(unittest.TestCase):

    def test_if_clause_simple(self):
        d = parse_directive("ALLOW read access IF user is authenticated")
        self.assertIsNone(d.qualifier_word)
        self.assertEqual(d.condition, "user is authenticated")

    def test_if_clause_case_insensitive(self):
        d = parse_directive("ALLOW read access if user is authenticated")
        self.assertEqual(d.condition, "user is authenticated")

    def test_qualifier_and_if(self):
        d = parse_directive(
            "ALLOW new file creation WITHIN infra/modules/ "
            "IF the new file is a reusable module with no provider block"
        )
        self.assertEqual(d.qualifier_word, "WITHIN")
        self.assertEqual(d.qualifier_target, "infra/modules/")
        self.assertEqual(
            d.condition,
            "the new file is a reusable module with no provider block",
        )
        self.assertEqual(d.action, "new file creation")

    def test_if_not_substring(self):
        # "interface" contains "if" but should NOT start an IF clause
        d = parse_directive("DENY modification of interface files")
        self.assertIsNone(d.condition)
        self.assertEqual(d.action, "modification of interface files")

    def test_condition_multi_word(self):
        d = parse_directive("ALLOW writes IF the request has a valid API token and role is admin")
        self.assertEqual(d.condition, "the request has a valid API token and role is admin")


# ---------------------------------------------------------------------------
# parse_directive — error paths
# ---------------------------------------------------------------------------

class TestParseDirectiveErrors(unittest.TestCase):

    def test_empty_string_raises(self):
        with self.assertRaises(ParseError):
            parse_directive("")

    def test_whitespace_only_raises(self):
        with self.assertRaises(ParseError):
            parse_directive("   ")

    def test_unknown_keyword_raises(self):
        with self.assertRaises(ParseError) as ctx:
            parse_directive("PERMIT file access")
        self.assertIn("PERMIT", str(ctx.exception))

    def test_keyword_only_raises(self):
        with self.assertRaises(ParseError):
            parse_directive("ALLOW")

    def test_qualifier_without_target_raises(self):
        with self.assertRaises(ParseError) as ctx:
            parse_directive("ALLOW file writes WITHIN")
        self.assertIn("WITHIN", str(ctx.exception))

    def test_qualifier_without_target_before_if_raises(self):
        with self.assertRaises(ParseError):
            parse_directive("ALLOW file writes WITHIN IF user is admin")

    def test_if_without_condition_raises(self):
        with self.assertRaises(ParseError) as ctx:
            parse_directive("ALLOW read access IF")
        self.assertIn("IF", str(ctx.exception))

    def test_qualifier_then_if_without_condition_raises(self):
        with self.assertRaises(ParseError):
            parse_directive("ALLOW writes WITHIN src/ IF")


# ---------------------------------------------------------------------------
# parse_capability_block
# ---------------------------------------------------------------------------

class TestParseCapabilityBlock(unittest.TestCase):

    _BLOCK = """\
ALLOW   modification of .tf files WITHIN infra/staging/
ALLOW   modification of .tf files WITHIN infra/modules/
ALLOW   new file creation WITHIN infra/modules/ IF the new file is a reusable module with no provider block
DENY    modification of .tf files WITHIN infra/prod/
DENY    modification of .tf files WITHIN infra/shared/
DENY    removal of deletion_protection attributes
REQUIRE naming of all new resources UNLESS following the {env}-{service}-{resource_type} pattern
REQUIRE tagging of all new AWS resources WITH Environment and Owner tags"""

    def test_returns_parsed_capability_block(self):
        result = parse_capability_block(self._BLOCK)
        self.assertIsInstance(result, ParsedCapabilityBlock)

    def test_all_directives_parsed(self):
        result = parse_capability_block(self._BLOCK)
        self.assertEqual(len(result.directives), 8)

    def test_keywords_correct(self):
        result = parse_capability_block(self._BLOCK)
        keywords = [d.keyword for d in result.directives]
        self.assertEqual(keywords.count("ALLOW"), 3)
        self.assertEqual(keywords.count("DENY"), 3)
        self.assertEqual(keywords.count("REQUIRE"), 2)

    def test_blank_lines_skipped(self):
        block = "\n\nALLOW read access\n\nDENY write access\n\n"
        result = parse_capability_block(block)
        self.assertEqual(len(result.directives), 2)

    def test_comment_lines_skipped(self):
        block = "# This is a comment\nALLOW read access\n# Another comment\nDENY writes"
        result = parse_capability_block(block)
        self.assertEqual(len(result.directives), 2)

    def test_empty_block(self):
        result = parse_capability_block("")
        self.assertEqual(result.directives, [])

    def test_error_carries_line_number(self):
        block = "ALLOW read access\nBADKEY something\nDENY write"
        with self.assertRaises(ParseError) as ctx:
            parse_capability_block(block)
        self.assertEqual(ctx.exception.line, 2)

    def test_if_directive_parsed_correctly(self):
        block = "ALLOW new file creation WITHIN infra/modules/ IF the new file is a reusable module with no provider block"
        result = parse_capability_block(block)
        d = result.directives[0]
        self.assertEqual(d.qualifier_word, "WITHIN")
        self.assertEqual(d.qualifier_target, "infra/modules/")
        self.assertIsNotNone(d.condition)

    def test_database_review_example(self):
        block = """\
ALLOW   read access to migration file content
ALLOW   read access to schema definitions WITHIN public.*
DENY    generation of migration file content
DENY    modification of migration files
REQUIRE flagging of any migration that lacks a reversible downgrade()
REQUIRE flagging of any non-nullable column addition to a table with existing data"""
        result = parse_capability_block(block)
        self.assertEqual(len(result.directives), 6)
        self.assertEqual(result.directives[2].keyword, "DENY")
        self.assertEqual(result.directives[4].action, "flagging of any migration that lacks a reversible downgrade()")


# ---------------------------------------------------------------------------
# parse_output_contract — happy paths
# ---------------------------------------------------------------------------

_OC_JSON_BLOCK = """\
format:     JSON
schema:     { "result": "string" }
variance:   none
on_failure: return a single line starting with BLOCKED:"""

_OC_DIFF_BLOCK = """\
format:     unified diff
schema:     standard unified diff against the current HEAD;
            one diff block per modified file
variance:   diff header timestamps MAY be omitted; no other variance permitted
on_failure: return plain text with prefix "BLOCKED:" followed by a single sentence
            identifying which CAPABILITY_DECLARATION constraint prevents execution"""

_OC_MULTILINE_SCHEMA = """\
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
  ],
  "notes": ["string"]
}
variance:   "violations" MAY be an empty array if verdict is PASS;
            "notes" MAY be omitted if empty;
            "verdict" MUST be present even when violations is empty
on_failure: Return { "status": "error", "reason": "<single-sentence description>" }"""


class TestParseOutputContractBasic(unittest.TestCase):

    def test_returns_output_contract(self):
        oc = parse_output_contract(_OC_JSON_BLOCK)
        self.assertIsInstance(oc, OutputContract)

    def test_format_parsed(self):
        oc = parse_output_contract(_OC_JSON_BLOCK)
        self.assertEqual(oc.format, "JSON")

    def test_schema_parsed(self):
        oc = parse_output_contract(_OC_JSON_BLOCK)
        self.assertEqual(oc.schema, '{ "result": "string" }')

    def test_variance_parsed(self):
        oc = parse_output_contract(_OC_JSON_BLOCK)
        self.assertEqual(oc.variance, "none")

    def test_on_failure_parsed(self):
        oc = parse_output_contract(_OC_JSON_BLOCK)
        self.assertIn("BLOCKED", oc.on_failure)

    def test_unified_diff_format(self):
        oc = parse_output_contract(_OC_DIFF_BLOCK)
        self.assertEqual(oc.format, "unified diff")

    def test_multiline_on_failure(self):
        oc = parse_output_contract(_OC_DIFF_BLOCK)
        self.assertIn("CAPABILITY_DECLARATION", oc.on_failure)

    def test_no_extra_fields_by_default(self):
        oc = parse_output_contract(_OC_JSON_BLOCK)
        self.assertEqual(oc.extra_fields, {})

    def test_no_warnings_for_valid_block(self):
        oc = parse_output_contract(_OC_JSON_BLOCK)
        self.assertEqual(oc.warnings, [])


class TestParseOutputContractMultiLine(unittest.TestCase):

    def test_multiline_schema_parsed(self):
        oc = parse_output_contract(_OC_MULTILINE_SCHEMA)
        self.assertIn('"migration"', oc.schema)
        self.assertIn('"verdict"', oc.schema)
        self.assertIn('"violations"', oc.schema)

    def test_multiline_schema_preserves_json_keys(self):
        oc = parse_output_contract(_OC_MULTILINE_SCHEMA)
        self.assertIn('"PASS" | "FAIL"', oc.schema)

    def test_multiline_variance(self):
        oc = parse_output_contract(_OC_MULTILINE_SCHEMA)
        self.assertIn("MAY be an empty array", oc.variance)
        self.assertIn("MUST be present", oc.variance)

    def test_multiline_on_failure(self):
        oc = parse_output_contract(_OC_MULTILINE_SCHEMA)
        self.assertIn("error", oc.on_failure)


class TestParseOutputContractFieldOrder(unittest.TestCase):

    def test_fields_can_appear_in_any_order(self):
        block = """\
on_failure: BLOCKED: <reason>
variance:   none
schema:     {}
format:     JSON"""
        oc = parse_output_contract(block)
        self.assertEqual(oc.format, "JSON")
        self.assertEqual(oc.schema, "{}")
        self.assertEqual(oc.variance, "none")
        self.assertIn("BLOCKED", oc.on_failure)


class TestParseOutputContractExtraFields(unittest.TestCase):

    def test_extra_field_collected(self):
        block = """\
format:     JSON
schema:     {}
variance:   none
on_failure: BLOCKED: <reason>
notes:      This is an informational field"""
        oc = parse_output_contract(block)
        self.assertIn("notes", oc.extra_fields)

    def test_extra_field_triggers_warning(self):
        block = """\
format:     JSON
schema:     {}
variance:   none
on_failure: BLOCKED: <reason>
custom_key: some value"""
        oc = parse_output_contract(block)
        self.assertTrue(any("custom_key" in w for w in oc.warnings))


# ---------------------------------------------------------------------------
# parse_output_contract — error paths
# ---------------------------------------------------------------------------

class TestParseOutputContractErrors(unittest.TestCase):

    def test_missing_format_raises(self):
        block = """\
schema:     {}
variance:   none
on_failure: BLOCKED: <reason>"""
        with self.assertRaises(ParseError) as ctx:
            parse_output_contract(block)
        self.assertIn("format", str(ctx.exception))

    def test_missing_schema_raises(self):
        block = """\
format:     JSON
variance:   none
on_failure: BLOCKED: <reason>"""
        with self.assertRaises(ParseError) as ctx:
            parse_output_contract(block)
        self.assertIn("schema", str(ctx.exception))

    def test_missing_variance_raises(self):
        block = """\
format:     JSON
schema:     {}
on_failure: BLOCKED: <reason>"""
        with self.assertRaises(ParseError) as ctx:
            parse_output_contract(block)
        self.assertIn("variance", str(ctx.exception))

    def test_missing_on_failure_raises(self):
        block = """\
format:     JSON
schema:     {}
variance:   none"""
        with self.assertRaises(ParseError) as ctx:
            parse_output_contract(block)
        self.assertIn("on_failure", str(ctx.exception))

    def test_empty_format_raises(self):
        block = """\
format:
schema:     {}
variance:   none
on_failure: BLOCKED: <reason>"""
        with self.assertRaises(ParseError) as ctx:
            parse_output_contract(block)
        self.assertIn("format", str(ctx.exception))

    def test_missing_multiple_fields_lists_all(self):
        block = "format:     JSON"
        with self.assertRaises(ParseError) as ctx:
            parse_output_contract(block)
        msg = str(ctx.exception)
        self.assertIn("schema", msg)
        self.assertIn("variance", msg)
        self.assertIn("on_failure", msg)

    def test_completely_empty_block_raises(self):
        with self.assertRaises(ParseError):
            parse_output_contract("")

    def test_only_blank_lines_raises(self):
        with self.assertRaises(ParseError):
            parse_output_contract("\n\n\n")


# ---------------------------------------------------------------------------
# Real-world examples from APPENDIX-A and APPENDIX-B
# ---------------------------------------------------------------------------

class TestRealWorldExamples(unittest.TestCase):

    def test_appendix_b_terraform_capability(self):
        block = """\
ALLOW   modification of .tf files WITHIN infra/staging/
ALLOW   modification of .tf files WITHIN infra/modules/
ALLOW   new file creation WITHIN infra/modules/ IF the new file is a reusable module with no provider block
DENY    modification of .tf files WITHIN infra/prod/
DENY    modification of .tf files WITHIN infra/shared/
DENY    removal of deletion_protection attributes
DENY    introduction of hardcoded AWS account IDs
DENY    addition of provider blocks WITHIN infra/modules/
DENY    addition of backend configuration WITHIN infra/modules/
REQUIRE naming of all new resources UNLESS following the {env}-{service}-{resource_type} pattern
REQUIRE tagging of all new AWS resources WITH Environment and Owner tags"""
        result = parse_capability_block(block)
        self.assertEqual(len(result.directives), 11)
        # Verify the conditional ALLOW
        cond_allow = result.directives[2]
        self.assertEqual(cond_allow.qualifier_word, "WITHIN")
        self.assertEqual(cond_allow.qualifier_target, "infra/modules/")
        self.assertIsNotNone(cond_allow.condition)
        # Verify UNLESS qualifier
        unless_d = result.directives[9]
        self.assertEqual(unless_d.qualifier_word, "UNLESS")

    def test_appendix_b_terraform_output_contract(self):
        block = """\
format:     unified diff
schema:     standard unified diff against the current HEAD of infra/staging/ecs/worker.tf;
            one diff block per modified file; no changes outside infra/staging/ecs/worker.tf
variance:   diff header timestamps MAY be omitted; no other variance permitted
on_failure: return plain text with prefix "BLOCKED:" followed by a single sentence
            identifying which CAPABILITY_DECLARATION constraint prevents execution,
            or "AMBIGUOUS:" followed by a single sentence identifying what information
            is missing and which layer it should appear in"""
        oc = parse_output_contract(block)
        self.assertEqual(oc.format, "unified diff")
        self.assertIn("infra/staging/ecs/worker.tf", oc.schema)
        self.assertIn("AMBIGUOUS:", oc.on_failure)

    def test_appendix_b_database_review_output_contract(self):
        block = """\
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
  ],
  "notes": ["string"]
}
variance:   "violations" MAY be an empty array if verdict is PASS;
            "notes" MAY be omitted if empty;
            "verdict" MUST be present even when violations is empty
on_failure: Return { "status": "error", "reason": "<single-sentence description>" }"""
        oc = parse_output_contract(block)
        self.assertEqual(oc.format, "JSON")
        self.assertIn('"verdict"', oc.schema)
        self.assertIn("PASS", oc.schema)
        self.assertIn("MUST be present", oc.variance)

    def test_payments_platform_style_capability(self):
        """Typical payments-platform.ics style directives."""
        block = """\
ALLOW   read access to any file
ALLOW   modification of .py files WITHIN src/
ALLOW   modification of .py files WITHIN tests/
DENY    modification of .py files WITHIN src/core/crypto/
DENY    deletion of any file
REQUIRE all new functions to have a corresponding test WITHIN tests/"""
        result = parse_capability_block(block)
        self.assertEqual(len(result.directives), 6)
        allows  = [d for d in result.directives if d.keyword == "ALLOW"]
        denies  = [d for d in result.directives if d.keyword == "DENY"]
        requires = [d for d in result.directives if d.keyword == "REQUIRE"]
        self.assertEqual(len(allows), 3)
        self.assertEqual(len(denies), 2)
        self.assertEqual(len(requires), 1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
