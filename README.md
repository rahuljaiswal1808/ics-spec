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

---

## Status

ICS is an **initial public draft (v0.1)**.

Feedback is invited before semantics are considered stable. To submit feedback, open an issue in the project repository or comment directly on the relevant section of `ICS-v0.1.md`.
