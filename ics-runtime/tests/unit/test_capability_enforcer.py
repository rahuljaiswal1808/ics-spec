"""Unit tests for CapabilityEnforcer."""

from ics_runtime.contracts.capability_enforcer import CapabilityEnforcer


_CAPABILITY = """
ALLOW: lead qualification
DENY: logging PII data (SSN, email, account numbers)
DENY: bulk export of lead records
REQUIRE: risk category on every qualified lead
"""


def test_clean_response_no_violations():
    enforcer = CapabilityEnforcer(_CAPABILITY)
    violations = enforcer.scan_output("Lead L-1 is qualified. Risk: MEDIUM.")
    assert violations == []


def test_blocked_prefix_detected():
    enforcer = CapabilityEnforcer(_CAPABILITY)
    violations = enforcer.scan_output(
        "BLOCKED: 'DENY logging PII data' — cannot include SSN in response."
    )
    assert len(violations) == 1
    assert violations[0].severity == "blocked"
    assert "DENY" in violations[0].rule


def test_pii_email_detected():
    enforcer = CapabilityEnforcer(_CAPABILITY)
    violations = enforcer.scan_output("Contact the lead at test.user@example.com for follow-up.")
    assert any("pii" in v.rule.lower() or "PII" in v.rule for v in violations)


def test_ssn_detected():
    enforcer = CapabilityEnforcer(_CAPABILITY)
    violations = enforcer.scan_output("SSN on file: 123-45-6789")
    assert len(violations) >= 1


def test_bulk_export_keyword_detected():
    enforcer = CapabilityEnforcer(_CAPABILITY)
    violations = enforcer.scan_output("Here is the export all leads CSV for your review.")
    assert any("bulk" in v.rule.lower() or "export" in v.rule.lower() for v in violations)


def test_check_tool_call_bulk_list():
    enforcer = CapabilityEnforcer(_CAPABILITY)
    violations = enforcer.check_tool_call("data.export", {"ids": list(range(100))})
    assert len(violations) >= 1
    assert violations[0].severity == "blocked"


def test_check_tool_call_small_args_ok():
    enforcer = CapabilityEnforcer(_CAPABILITY)
    violations = enforcer.check_tool_call("crm.lookup", {"lead_id": "L-1"})
    assert violations == []
