"""Unit tests for OutputContract."""

import json
import pytest
from pydantic import BaseModel
from typing import Literal
from ics_runtime.contracts.output_contract import OutputContract


class LeadResult(BaseModel):
    lead_id: str
    decision: Literal["QUALIFIED", "NOT_QUALIFIED"]
    score: int


def test_valid_json_passes():
    contract = OutputContract(schema=LeadResult)
    data = {"lead_id": "L-1", "decision": "QUALIFIED", "score": 87}
    outcome = contract.validate(json.dumps(data))
    assert outcome.passed
    assert outcome.parsed is not None
    assert outcome.parsed.lead_id == "L-1"


def test_json_in_code_fence_passes():
    contract = OutputContract(schema=LeadResult)
    data = {"lead_id": "L-2", "decision": "NOT_QUALIFIED", "score": 30}
    response = f"```json\n{json.dumps(data)}\n```"
    outcome = contract.validate(response)
    assert outcome.passed


def test_missing_field_produces_violation():
    contract = OutputContract(schema=LeadResult)
    outcome = contract.validate(json.dumps({"lead_id": "L-3", "decision": "QUALIFIED"}))
    assert not outcome.passed
    assert len(outcome.violations) >= 1
    assert any("score" in v.field for v in outcome.violations)


def test_invalid_json_produces_violation():
    contract = OutputContract(schema=LeadResult)
    outcome = contract.validate("This is not JSON at all.")
    assert not outcome.passed
    assert any("not valid JSON" in v.rule for v in outcome.violations)


def test_failure_mode_prefix_is_not_violation():
    contract = OutputContract(schema=LeadResult, failure_modes=["BLOCKED:", "insufficient_data"])
    outcome = contract.validate("BLOCKED: cannot qualify without income data.")
    assert outcome.passed
    assert outcome.is_structured_failure


def test_no_schema_always_passes():
    contract = OutputContract()
    outcome = contract.validate("anything goes here")
    assert outcome.passed


def test_to_ics_text_contains_format():
    contract = OutputContract(schema=LeadResult, failure_modes=["BLOCKED:"])
    text = contract.to_ics_text()
    assert "FORMAT: json" in text
    assert "FAILURE_MODES" in text
