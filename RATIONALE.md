# ICS Rationale

## Why This Spec Exists

Most LLM instructions are written the way people write notes to themselves — informally, incrementally, and with the assumption that context is shared. This works well enough in casual use, but it fails in systems where instructions are authored once, executed many times, and maintained by people who were not present when the original decisions were made.

The Instruction Contract Specification exists because instructions to LLMs are interfaces, and interfaces need to be treated as such. An interface that relies on shared context, implied constraints, or informal conventions is not an interface — it is a verbal agreement, and verbal agreements do not scale.

ICS is not a new idea applied to a new problem. It applies the same discipline that the software industry developed for APIs, schemas, and protocols to a layer that has, until now, mostly been treated as prose.

---

## The Problem

When instructions to LLMs are written without structure, several failure modes recur:

**Context collapse.** Long-lived facts, session-specific decisions, and per-task instructions are mixed into a single block of text. When anything changes, it is unclear what needs to be updated, what can be safely cached, and what must be re-sent on every invocation. The result is either token waste (restating everything) or subtle errors (using stale context).

**Implicit constraints.** Teams develop conventions — "don't touch the API layer," "always return JSON," "assume Python 3.11" — that live in someone's head or in a README rather than in the instruction itself. When the person who knows the convention leaves, or when the instruction is reused in a new context, the constraint disappears silently.

**Ambiguous output expectations.** Instructions frequently describe what to do without specifying what a correct result looks like. When the output is wrong, it is unclear whether the model failed, the instruction was ambiguous, or the evaluation criteria were never defined. All three are treated as the same problem and addressed with the same fix: rewriting the prompt by intuition.

**Retry as a first resort.** When an instruction produces an unexpected output, the default response is to rephrase and try again. This treats ambiguity as a model problem rather than an interface problem. It produces no durable fix, because the underlying contract was never made explicit.

These failure modes are not caused by the models. They are caused by the instructions. ICS addresses the instructions.

---

## What ICS Does

ICS defines a five-layer structure for instructions. Each layer has a distinct purpose, a defined lifetime, and explicit rules about what it may and may not contain.

The layers exist because not all information in an instruction has the same lifespan. Some things are true forever — the domain, the language, the architectural invariants. Some things are true for a session — the decisions made so far, the flags set, the intermediate conclusions reached. Some things are true only for a single invocation — the specific task to be performed. Mixing these together is the root cause of context collapse. The layers keep them separate.

The directive syntax for CAPABILITY_DECLARATION exists because permissions expressed in prose are not verifiable. "Don't modify the API layer" is a request. `DENY modification of src/orders/api/` is a constraint. The distinction matters when you want to validate an instruction programmatically, audit what a model was permitted to do, or hand an instruction to someone who was not involved in writing it.

The OUTPUT_CONTRACT exists because a task without a success condition is not a task — it is a suggestion. Requiring callers to declare the expected format, schema, permitted variance, and failure behavior before execution forces the contract to be explicit. It also makes evaluation mechanical: an output either satisfies the contract or it does not.

The CLEAR sentinel for SESSION_STATE exists because session boundaries are meaningful and need to be expressible. Without an explicit mechanism for ending a session, callers have no way to signal that prior state should no longer be considered. The sentinel makes the lifecycle of session state first-class rather than implicit.

---

## How ICS Should Be Interpreted

**ICS is a contract, not a style guide.** Compliance is binary. An instruction either satisfies all the rules or it does not. There is no partial compliance, no "mostly conformant," and no allowance for good intentions.

**The calling system is responsible for validation.** ICS defines what a compliant instruction looks like and what a conforming output requires. It does not define how validation is enforced at runtime. That is the caller's responsibility. An ICS-compliant instruction paired with a caller that ignores the OUTPUT_CONTRACT is not an ICS-compliant system — it is a compliant instruction used incorrectly.

**Ambiguity is a contract violation, not a model failure.** If an instruction produces unexpected or inconsistent outputs, the correct response is to examine the instruction for ambiguity, identify which layer is underspecified, and update that layer. Re-prompting without updating the instruction is not a fix. It produces a different output, not a correct one.

**The free-form scope grammar in CAPABILITY_DECLARATION is a deliberate deferral.** ICS v0.1 does not define a grammar for directive scopes. Two implementations may express the same constraint using different syntax and both claim compliance. This is intentional: a grammar narrow enough to be unambiguous would exclude the scope idioms that already exist in real codebases, forcing unnecessary rewrites at adoption time. The trade-off favours adoption breadth over machine-parseability in this version. Callers who share instructions across systems, or who want programmatic constraint evaluation, should define a local scope grammar and document it in their IMMUTABLE_CONTEXT. A normative scope grammar is deferred to ICS v0.2, where it will be specified as an optional conformance level so that callers who do not need it are not required to adopt it.

**Strictness is the point.** ICS will feel rigid compared to writing instructions informally. That is intentional. The rigidity is what makes instructions auditable, reusable, and maintainable. A spec that bends to accommodate informal usage is not a spec — it is a set of suggestions, and suggestions do not compose.

---

## What ICS Does Not Address

ICS is deliberately narrow. It defines the structure of an instruction, not the content. It does not prescribe how models reason, how tools are invoked, how agents coordinate, or how outputs are rendered. These concerns are real and important, but they belong to higher-level frameworks built on top of a stable instruction layer. ICS is intended to be that layer.

---

## Intended Audience

ICS is written for teams building systems in which LLM instructions are authored, versioned, and maintained as engineering artifacts — not for individuals writing one-off prompts. It assumes that the people reading this document are prepared to treat instruction design with the same rigor they apply to API design. If that assumption does not hold, ICS will feel like unnecessary overhead. If it does hold, ICS should feel like something that should have existed already.
