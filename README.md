# Instruction Contract Specification (ICS)

ICS defines a deterministic, token-aware contract for structuring instructions provided to Large Language Models (LLMs).

It treats instructions as **interfaces**, not conversations.

---

## What problem does ICS solve?

Most LLM inefficiency is not caused by poor prompt wording, but by **missing structure**.

In practice:
- immutable context is repeatedly restated
- constraints are implied instead of declared
- output expectations are negotiated after execution
- retries compensate for ambiguous inputs

ICS addresses these issues by standardizing:
- how context is layered by lifetime
- how capabilities and constraints are declared
- how tasks are specified
- how outputs are contractually defined

---

## What does ICS standardize?

ICS defines:
- a fixed set of instruction layers
- mandatory ordering and boundary syntax
- explicit capability declarations (ALLOW / DENY / REQUIRE)
- explicit output contracts
- conformance and validation rules

ICS does **not** define:
- model behavior
- tools or agents
- execution engines
- storage or memory mechanisms

---

## How do I use ICS?

ICS can be adopted incrementally. Each instruction is composed of five layers: stable domain facts that never change, permissions and constraints scoped to a capability surface, temporary session decisions, the specific task for the current invocation, and a declared output contract. Together they replace an ad-hoc prompt with a structured, verifiable interface.

Typical usage:
1. Encode stable facts as `IMMUTABLE_CONTEXT`
2. Declare permissions and constraints in `CAPABILITY_DECLARATION`
3. Track temporary decisions in `SESSION_STATE`
4. Express each task in `TASK_PAYLOAD`
5. Declare expected output in `OUTPUT_CONTRACT`

An instruction is ICS-compliant if it satisfies all rules in `ICS-v0.1.md`. See `APPENDIX-A.md` for a full worked example with annotations.

---

## Documents

| File | Purpose |
|------|---------|
| `ICS-v0.1.md` | The specification |
| `RATIONALE.md` | Why ICS exists and how to interpret it |
| `APPENDIX-A.md` | Full conformant example with annotations |

## Tools

| File | Purpose |
|------|---------|
| `ics_validator.py` | Reference validator — checks ICS compliance, reports violations |
| `ics_token_analyzer.py` | Token analyzer — proves §2.2/§2.4 savings claim offline (no API key needed) |
| `ics_live_test.py` | Live tester — validates savings with real Anthropic API calls using your key |

### Quick start: live token test

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# Run against the built-in APPENDIX-A example (3 invocations)
python ics_live_test.py

# Run against your own ICS instruction file
python ics_live_test.py path/to/my_instruction.txt --invocations 10

# Preview what would be sent without spending tokens
python ics_live_test.py --dry-run
```

The tester sends two requests per invocation — one **naive** (all layers flat, no caching) and one **ICS** (permanent layers marked `cache_control=ephemeral`) — and reads real token counts from the API response's `usage` field, including `cache_creation_input_tokens` and `cache_read_input_tokens`.

> **Note on cache activation:** Anthropic prompt caching requires the cached block to be ≥ 1024 tokens for most models. The built-in APPENDIX-A examples are small demonstration snippets and will not trigger cache hits. Supply a production-sized instruction file to observe real `cache_read_input_tokens` savings in the ICS column.

---

## Status

ICS is an **initial public draft (v0.1)**.

Feedback is invited before semantics are considered stable. To submit feedback, open an issue in the project repository or comment directly on the relevant section of `ICS-v0.1.md`.
