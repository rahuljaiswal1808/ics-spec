# Instruction Contract Specification (ICS) v0.1

## 1. Scope

The Instruction Contract Specification (ICS) defines a deterministic, token-aware structure for instructions provided to Large Language Models (LLMs).

ICS standardizes:
- how context is layered
- how capabilities and constraints are declared
- how tasks are specified
- how outputs are contractually defined

ICS does **not** define:
- model architectures
- tool invocation protocols
- agent orchestration
- user interfaces
- reasoning techniques

ICS is model-agnostic and framework-independent.

---

## 2. Design Principles

### 2.1 Instructions Are Interfaces
Instructions to LLMs are treated as formal interfaces, not conversations. Ambiguity is considered an interface failure.

**Definition of ambiguity:**  
An instruction is ambiguous if a conforming implementation could produce structurally distinct, valid outputs (including differences in required fields, ordering where ordering is significant, or declared output sections) without violating any stated constraint. Ambiguity MUST be resolved before execution, not during.

---

### 2.2 Token Cost Is a First-Class Constraint
Token consumption MUST be minimized through structure, reuse, and explicit separation of context lifetimes.

---

### 2.3 Determinism Is an Interface Property
While model outputs may be probabilistic, the instruction interface itself MUST be deterministic and unambiguous.

An instruction interface is considered deterministic if:
1. all inputs are explicitly typed or enumerated
2. all constraints are stated rather than implied
3. the output shape is fully declared prior to execution

---

### 2.4 Context Has Distinct Lifetimes
Context with different lifetimes MUST NOT be flattened into a single instruction stream.

The four defined lifetimes are:
- **permanent** (IMMUTABLE_CONTEXT)
- **session** (SESSION_STATE)
- **invocation** (TASK_PAYLOAD)
- **response** (OUTPUT_CONTRACT)

The OUTPUT_CONTRACT is response-scoped but MUST be declared prior to execution.

---

### 2.5 Output Shape Must Be Declared Upfront
An instruction without an explicit output contract is considered incomplete.

---

## 3. Instruction Layers

An ICS-compliant instruction MUST be composed of the following layers, in the order specified.  
Each layer MUST be delimited using the boundary syntax defined in Section 3.6.

---

### 3.1 IMMUTABLE_CONTEXT

**Purpose**  
Defines long-lived facts that remain constant across multiple tasks.

**Characteristics**
- Stable across sessions
- Eligible for caching
- Must not be restated in other layers

**Examples**
- Product domain rules
- Repository structure
- Architectural invariants

**Rules**
- MUST NOT include task-specific instructions
- MUST NOT be restated, redefined, or contradicted in later layers

---

### 3.2 CAPABILITY_DECLARATION

**Purpose**  
Defines what the model is allowed to do and the constraints under which it operates.

**Characteristics**
- Defines permitted actions
- Defines invariants and prohibitions

**Required structure:**  
Each capability declaration MUST use one of the following directive types:

| Directive | Meaning |
|-----------|---------|
| `ALLOW`   | The action is explicitly permitted |
| `DENY`    | The action is explicitly forbidden |
| `REQUIRE` | The action must always be performed |

Each directive MUST be followed by a scope and MAY include a condition.

**Syntax**

```text
ALLOW   <action> [<qualifier>] [IF <condition>]
DENY    <action> [<qualifier>] [IF <condition>]
REQUIRE <action> [<qualifier>] [IF <condition>]
```

**Scope grammar (normative)**

```
directive  ::= KEYWORD WS+ action (WS+ qualifier)? (WS+ "IF" WS+ condition)?
KEYWORD    ::= "ALLOW" | "DENY" | "REQUIRE"
action     ::= WORD (WS+ WORD)*
qualifier  ::= QWORD WS+ target
QWORD      ::= "WITHIN" | "ON" | "WITH" | "UNLESS"
target     ::= WORD (WS+ WORD)*
condition  ::= WORD (WS+ WORD)*
WORD       ::= [A-Za-z0-9_./-]+
WS         ::= " " | "\t"
```

Rules:
- `action` MUST be non-empty.
- If a qualifier keyword (`WITHIN`, `ON`, `WITH`, `UNLESS`) appears, it MUST be
  followed by a non-empty `target`. A bare qualifier keyword with no target is
  malformed.
- If `IF` appears, it MUST be followed by a non-empty `condition`. A bare `IF`
  with no condition is malformed.
- Qualifier keywords appearing inside `action` text (i.e., not at a word
  boundary following the action) MUST be treated as part of the action, not as
  a qualifier introducer. Implementations SHOULD use the first occurrence of a
  qualifier keyword as the qualifier boundary.

**Examples**

```text
ALLOW   file modification WITHIN src/
DENY    file deletion
DENY    output exceeding 4096 tokens
REQUIRE backward compatibility WITH api/v1/
```

**Rules**
- MUST declare all relevant constraints
- MUST be treated as authoritative
- MUST NOT be overridden or extended by TASK_PAYLOAD
- Undeclared actions are DENIED by default

---

### 3.3 SESSION_STATE

**Purpose**  
Captures temporary assumptions or decisions relevant to the current session.

**Definition of session:**  
A session begins with the first ICS-compliant invocation and ends when SESSION_STATE is explicitly cleared or a new IMMUTABLE_CONTEXT is declared. Declaring a new IMMUTABLE_CONTEXT implicitly clears any prior SESSION_STATE, equivalent to the `CLEAR` sentinel. Session persistence is caller-managed; ICS does not define storage mechanisms.

**Clearing a session:**  
To explicitly end a session, the caller MUST include a SESSION_STATE layer containing only the following sentinel value:

```text
###ICS:SESSION_STATE###
CLEAR
###END:SESSION_STATE###
```

A SESSION_STATE layer containing `CLEAR` MUST be treated as an empty session state, and any prior session state MUST be discarded. A new session begins with the next invocation. A SESSION_STATE layer containing `CLEAR` alongside any other content MUST be rejected as malformed.

**Characteristics**
- Short-lived and session-scoped
- Mutable across calls within a session
- Explicitly discardable via the `CLEAR` sentinel

**Examples**
- Decisions made so far
- Temporary flags
- Intermediate conclusions

**Rules**
- MUST NOT restate, redefine, or contradict IMMUTABLE_CONTEXT
- MUST be discardable without loss of correctness
- Each entry SHOULD carry an identifier or timestamp for traceability

---

### 3.4 TASK_PAYLOAD

**Purpose**  
Defines the specific task to be executed.

**Characteristics**
- The only layer expected to change per invocation
- Minimal and precise

**Examples**
- Code modification requests
- Analysis tasks
- Transformation instructions

**Rules**
- MUST NOT restate context from preceding layers
- MUST be executable without clarification
- MUST NOT override or extend CAPABILITY_DECLARATION

**Definition:**  
A TASK_PAYLOAD is executable without clarification if all required inputs, constraints, and success conditions are fully defined within the instruction layers.

---

### 3.5 OUTPUT_CONTRACT

**Purpose**  
Defines the required shape and constraints of the output. Output validation is the responsibility of the calling system.

**Characteristics**
- Declares format and structure
- Defines validity conditions
- Defines failure behavior

**Required fields**

| Field        | Description                                          |
|--------------|------------------------------------------------------|
| `format`     | Output encoding (e.g., JSON, unified diff, prose)    |
| `schema`     | Structure definition (e.g., JSON Schema, field list) |
| `variance`   | Explicitly permitted variance                        |
| `on_failure` | Required behavior when output cannot conform         |

**Rules**
- All four fields MUST be present
- Variance MUST be explicitly enumerated; any variance not listed is forbidden
- Free-form prose is invalid unless `format: prose` is explicitly declared
- If an output does not conform, it MUST be treated as invalid, MUST NOT be partially accepted, and the declared `on_failure` behavior MUST be applied

**Example**

```text
format:     JSON
schema:     { "status": "string", "changes": ["string"], "warnings": ["string"] }
variance:   "warnings" field MAY be omitted if empty
on_failure: Return { "status": "error", "reason": "<description>" }
```

---

### 3.6 Layer Boundary Syntax

Each layer MUST be wrapped using the following boundary syntax:

```text
###ICS:<LAYER_NAME>###
<layer content>
###END:<LAYER_NAME>###
```

Valid layer names:  
`IMMUTABLE_CONTEXT`, `CAPABILITY_DECLARATION`, `SESSION_STATE`, `TASK_PAYLOAD`, `OUTPUT_CONTRACT`

---

## 4. Contract Rules

### 4.1 Ordering
Instruction layers MUST appear in the order defined in Section 3. Parsers encountering layers out of order MUST reject the instruction as non-conformant.

---

### 4.2 Non-Redefinition
No layer MAY restate, redefine, or contradict a constraint or invariant defined in a preceding layer.

---

### 4.3 Explicitness
All constraints MUST be explicitly stated. Implicit assumptions are invalid. Default behaviors MUST be declared in CAPABILITY_DECLARATION if relied upon.

---

### 4.4 Prose Restrictions
Natural language prose SHOULD be minimized.

- CAPABILITY_DECLARATION MUST use directive syntax
- OUTPUT_CONTRACT MUST use the structured field format
- TASK_PAYLOAD and SESSION_STATE MAY use prose where structured alternatives would not reduce ambiguity

---

### 4.5 Retry Semantics
Repeated clarification due to ambiguity indicates a contract violation, not a model failure. Callers MUST resolve ambiguity by updating the appropriate layer rather than re-prompting with corrective prose.

---

## 5. Conformance

### 5.1 Compliance Definition
An instruction is **ICS-compliant** if and only if:
- all five layers are present
- layers are delimited using Section 3.6 syntax
- layers appear in the correct order
- all rules in Sections 3–4 are satisfied

Partial compliance is not defined.

---

### 5.2 Validation Procedure

A conformance checker MUST verify, in order:

1. All five layer boundary tags are present and well-formed
2. Layers appear in the canonical order
3. SESSION_STATE, if containing `CLEAR`, contains no other content; if it does, the instruction MUST be rejected as malformed
4. No layer restates, redefines, or contradicts a preceding layer
5. CAPABILITY_DECLARATION uses only `ALLOW`, `DENY`, or `REQUIRE` directives
6. OUTPUT_CONTRACT contains all four required fields

A reference validator implementation is outside the scope of this document.

---

## 6. Non-Goals

ICS explicitly does not address:
- how models reason internally
- how tools are invoked
- how agents coordinate
- how instructions are authored or edited
- how outputs are rendered or validated at runtime

These concerns are left to implementations and higher-level frameworks.

---

## 7. Versioning

This specification follows semantic versioning.

| Change type                                             | Version increment |
|---------------------------------------------------------|-------------------|
| Layer definitions, contract rules, conformance criteria | Major             |
| New optional fields or non-breaking clarifications      | Minor             |
| Editorial or typographical fixes                        | Patch             |

---

## Status

This document defines **ICS v0.1**.  
Feedback is invited before semantics are considered stable.
