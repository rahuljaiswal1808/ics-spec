#!/usr/bin/env python3
"""
ICS SDK test suite.

All tests use unittest.mock — no real API keys or network calls required.

Usage:
    python test_ics_sdk.py
    python test_ics_sdk.py -v
"""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ics_sdk import (
    ContractViolationError,
    ICSClient,
    ICSError,
    ICSResult,
    InvalidContractError,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_VALID_ICS_JSON = """\
###ICS:IMMUTABLE_CONTEXT###
System: test service
###END:IMMUTABLE_CONTEXT###

###ICS:CAPABILITY_DECLARATION###
ALLOW read access
###END:CAPABILITY_DECLARATION###

###ICS:SESSION_STATE###
CLEAR
###END:SESSION_STATE###

###ICS:TASK_PAYLOAD###
Run the analysis.
###END:TASK_PAYLOAD###

###ICS:OUTPUT_CONTRACT###
format:     JSON
schema:     { "result": "string" }
variance:   none
on_failure: return a single line starting with BLOCKED:
###END:OUTPUT_CONTRACT###"""

_VALID_ICS_DIFF = """\
###ICS:IMMUTABLE_CONTEXT###
System: code service
###END:IMMUTABLE_CONTEXT###

###ICS:CAPABILITY_DECLARATION###
ALLOW file modification WITHIN src/
###END:CAPABILITY_DECLARATION###

###ICS:SESSION_STATE###
CLEAR
###END:SESSION_STATE###

###ICS:TASK_PAYLOAD###
Fix the bug.
###END:TASK_PAYLOAD###

###ICS:OUTPUT_CONTRACT###
format:     unified diff
schema:     standard unified diff
variance:   none
on_failure: respond with a single line: BLOCKED: <reason>. No markdown. No bold asterisks.
###END:OUTPUT_CONTRACT###"""

_VALID_JSON_OUTPUT = '{"result": "ok"}'

_VALID_DIFF_OUTPUT = """\
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,4 @@
 def foo():
-    pass
+    return 42
"""

_BLOCKED_OUTPUT = "BLOCKED: task requires modifying a denied path"


def _make_anthropic_client(response_text: str):
    """Build an object that passes SDK provider detection as anthropic.Anthropic."""
    # Define the class with the right __module__ so type(client).__module__
    # starts with "anthropic", matching _detect_provider's check.
    class _FakeAnthropic:
        pass
    _FakeAnthropic.__module__ = "anthropic._client"

    msg = SimpleNamespace(
        content=[SimpleNamespace(text=response_text)],
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )
    client = _FakeAnthropic()
    client.messages = MagicMock()
    client.messages.create.return_value = msg
    return client


def _make_openai_client(response_text: str):
    """Build an object that passes SDK provider detection as openai.OpenAI."""
    class _FakeOpenAI:
        pass
    _FakeOpenAI.__module__ = "openai._client"

    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=response_text))],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=60),
    )
    client = _FakeOpenAI()
    client.chat = MagicMock()
    client.chat.completions.create.return_value = completion
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestProviderDetection(unittest.TestCase):

    def test_anthropic_client_detected(self):
        raw = _make_anthropic_client(_VALID_JSON_OUTPUT)
        client = ICSClient(raw)
        self.assertEqual(client._provider, "anthropic")

    def test_openai_client_detected(self):
        raw = _make_openai_client(_VALID_JSON_OUTPUT)
        client = ICSClient(raw)
        self.assertEqual(client._provider, "openai")

    def test_unknown_client_raises(self):
        class _FakeUnknown:
            pass
        _FakeUnknown.__module__ = "mylib.client"
        with self.assertRaises(ICSError):
            ICSClient(_FakeUnknown())

    def test_default_model_anthropic(self):
        raw = _make_anthropic_client(_VALID_JSON_OUTPUT)
        client = ICSClient(raw)
        self.assertIn("claude", client._model)

    def test_default_model_openai(self):
        raw = _make_openai_client(_VALID_JSON_OUTPUT)
        client = ICSClient(raw)
        self.assertIn("gpt", client._model)

    def test_custom_model_respected(self):
        raw = _make_anthropic_client(_VALID_JSON_OUTPUT)
        client = ICSClient(raw, model="claude-haiku-4-5-20251001")
        self.assertEqual(client._model, "claude-haiku-4-5-20251001")


class TestAnthropicCallRouting(unittest.TestCase):

    def test_calls_messages_create(self):
        raw = _make_anthropic_client(_VALID_JSON_OUTPUT)
        client = ICSClient(raw)
        client.complete(_VALID_ICS_JSON, "do the thing")
        raw.messages.create.assert_called_once()

    def test_system_prompt_is_ics_document(self):
        raw = _make_anthropic_client(_VALID_JSON_OUTPUT)
        client = ICSClient(raw)
        client.complete(_VALID_ICS_JSON, "do the thing")
        call_kwargs = raw.messages.create.call_args
        self.assertEqual(call_kwargs.kwargs["system"], _VALID_ICS_JSON)

    def test_user_message_forwarded(self):
        raw = _make_anthropic_client(_VALID_JSON_OUTPUT)
        client = ICSClient(raw)
        client.complete(_VALID_ICS_JSON, "hello world")
        call_kwargs = raw.messages.create.call_args
        messages = call_kwargs.kwargs["messages"]
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["content"], "hello world")

    def test_extra_kwargs_forwarded(self):
        raw = _make_anthropic_client(_VALID_JSON_OUTPUT)
        client = ICSClient(raw)
        client.complete(_VALID_ICS_JSON, "do it", temperature=0.0)
        call_kwargs = raw.messages.create.call_args
        self.assertEqual(call_kwargs.kwargs["temperature"], 0.0)


class TestOpenAICallRouting(unittest.TestCase):

    def test_calls_chat_completions_create(self):
        raw = _make_openai_client(_VALID_JSON_OUTPUT)
        client = ICSClient(raw)
        client.complete(_VALID_ICS_JSON, "do the thing")
        raw.chat.completions.create.assert_called_once()

    def test_system_message_is_ics_document(self):
        raw = _make_openai_client(_VALID_JSON_OUTPUT)
        client = ICSClient(raw)
        client.complete(_VALID_ICS_JSON, "do the thing")
        call_kwargs = raw.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        sys_msg = next(m for m in messages if m["role"] == "system")
        self.assertEqual(sys_msg["content"], _VALID_ICS_JSON)

    def test_usage_mapped_correctly(self):
        raw = _make_openai_client(_VALID_JSON_OUTPUT)
        client = ICSClient(raw)
        result = client.complete(_VALID_ICS_JSON, "do the thing")
        self.assertEqual(result.usage["input_tokens"], 120)
        self.assertEqual(result.usage["output_tokens"], 60)


class TestSuccessfulCompletion(unittest.TestCase):

    def test_returns_ics_result(self):
        raw = _make_anthropic_client(_VALID_JSON_OUTPUT)
        result = ICSClient(raw).complete(_VALID_ICS_JSON, "run")
        self.assertIsInstance(result, ICSResult)

    def test_content_is_raw_output(self):
        raw = _make_anthropic_client(_VALID_JSON_OUTPUT)
        result = ICSClient(raw).complete(_VALID_ICS_JSON, "run")
        self.assertEqual(result.content, _VALID_JSON_OUTPUT)

    def test_validation_is_compliant(self):
        raw = _make_anthropic_client(_VALID_JSON_OUTPUT)
        result = ICSClient(raw).complete(_VALID_ICS_JSON, "run")
        self.assertTrue(result.validation.compliant)

    def test_blocked_false_on_normal_output(self):
        raw = _make_anthropic_client(_VALID_JSON_OUTPUT)
        result = ICSClient(raw).complete(_VALID_ICS_JSON, "run")
        self.assertFalse(result.blocked)

    def test_usage_populated(self):
        raw = _make_anthropic_client(_VALID_JSON_OUTPUT)
        result = ICSClient(raw).complete(_VALID_ICS_JSON, "run")
        self.assertIn("input_tokens", result.usage)
        self.assertIn("output_tokens", result.usage)

    def test_valid_diff_output_passes(self):
        raw = _make_anthropic_client(_VALID_DIFF_OUTPUT)
        result = ICSClient(raw).complete(_VALID_ICS_DIFF, "fix it")
        self.assertTrue(result.validation.compliant)


class TestContractViolation(unittest.TestCase):

    def test_invalid_json_raises_by_default(self):
        raw = _make_anthropic_client("this is not json")
        with self.assertRaises(ContractViolationError) as ctx:
            ICSClient(raw).complete(_VALID_ICS_JSON, "run")
        self.assertFalse(ctx.exception.validation_result.compliant)

    def test_invalid_diff_raises_by_default(self):
        raw = _make_anthropic_client("no diff markers here")
        with self.assertRaises(ContractViolationError):
            ICSClient(raw).complete(_VALID_ICS_DIFF, "fix it")

    def test_raise_on_violation_false_returns_result(self):
        raw = _make_anthropic_client("this is not json")
        client = ICSClient(raw, raise_on_violation=False)
        result = client.complete(_VALID_ICS_JSON, "run")
        self.assertIsInstance(result, ICSResult)
        self.assertFalse(result.validation.compliant)

    def test_violation_error_carries_validation_result(self):
        raw = _make_anthropic_client("not json at all")
        with self.assertRaises(ContractViolationError) as ctx:
            ICSClient(raw).complete(_VALID_ICS_JSON, "run")
        vr = ctx.exception.validation_result
        self.assertTrue(len(vr.violations) > 0)


class TestBlockedResponse(unittest.TestCase):

    def test_blocked_flag_set(self):
        raw = _make_anthropic_client(_BLOCKED_OUTPUT)
        result = ICSClient(raw).complete(_VALID_ICS_JSON, "run")
        self.assertTrue(result.blocked)

    def test_blocked_response_does_not_raise(self):
        raw = _make_anthropic_client(_BLOCKED_OUTPUT)
        # A well-formed BLOCKED: line should be compliant
        result = ICSClient(raw).complete(_VALID_ICS_JSON, "run")
        self.assertTrue(result.validation.compliant)

    def test_blocked_multiline_raises_when_single_required(self):
        multiline_blocked = "BLOCKED: reason\nExtra line."
        raw = _make_anthropic_client(multiline_blocked)
        with self.assertRaises(ContractViolationError):
            ICSClient(raw).complete(_VALID_ICS_DIFF, "fix it")


class TestInvalidContract(unittest.TestCase):

    # Missing OUTPUT_CONTRACT — rejected by validate() at step 1.
    _BROKEN_ICS_NO_OC = """\
###ICS:IMMUTABLE_CONTEXT###
System: test
###END:IMMUTABLE_CONTEXT###

###ICS:CAPABILITY_DECLARATION###
ALLOW read
###END:CAPABILITY_DECLARATION###

###ICS:SESSION_STATE###
CLEAR
###END:SESSION_STATE###

###ICS:TASK_PAYLOAD###
Do something.
###END:TASK_PAYLOAD###"""

    # Layers out of canonical order — rejected by validate() at step 2,
    # but OUTPUT_CONTRACT is present so validate_output() can still run.
    _BROKEN_ICS_OOO = """\
###ICS:IMMUTABLE_CONTEXT###
System: test
###END:IMMUTABLE_CONTEXT###

###ICS:TASK_PAYLOAD###
Do something.
###END:TASK_PAYLOAD###

###ICS:CAPABILITY_DECLARATION###
ALLOW read
###END:CAPABILITY_DECLARATION###

###ICS:SESSION_STATE###
CLEAR
###END:SESSION_STATE###

###ICS:OUTPUT_CONTRACT###
format:     JSON
schema:     {}
variance:   none
on_failure: return BLOCKED: <reason>
###END:OUTPUT_CONTRACT###"""

    def test_invalid_ics_raises_invalid_contract_error(self):
        raw = _make_anthropic_client(_VALID_JSON_OUTPUT)
        with self.assertRaises(InvalidContractError) as ctx:
            ICSClient(raw).complete(self._BROKEN_ICS_NO_OC, "run")
        self.assertFalse(ctx.exception.validation_result.compliant)

    def test_model_not_called_when_contract_invalid(self):
        raw = _make_anthropic_client(_VALID_JSON_OUTPUT)
        try:
            ICSClient(raw).complete(self._BROKEN_ICS_NO_OC, "run")
        except InvalidContractError:
            pass
        raw.messages.create.assert_not_called()

    def test_validate_contract_false_skips_check(self):
        raw = _make_anthropic_client(_VALID_JSON_OUTPUT)
        # validate_contract=False: skip ICS structural check, call model anyway.
        # _BROKEN_ICS_OOO has OUTPUT_CONTRACT so validate_output() succeeds.
        result = ICSClient(raw, validate_contract=False).complete(
            self._BROKEN_ICS_OOO, "run"
        )
        self.assertIsInstance(result, ICSResult)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
