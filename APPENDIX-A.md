# APPENDIX-A: Conformant Examples

This document provides annotated ICS-compliant instructions. Each example is complete — all five layers are present and valid. Annotations explain the decision behind each layer's content, not just its structure.

Read this alongside `ICS-v0.1.md`. The spec defines the rules; this document shows what following them looks like in practice.

---

## Example 1: Code Refactoring Task

**Context:** A development team is using ICS to drive automated code modifications on an order management service. The same IMMUTABLE_CONTEXT and CAPABILITY_DECLARATION are reused across many invocations; only SESSION_STATE, TASK_PAYLOAD, and OUTPUT_CONTRACT change per task.

---

```text
###ICS:IMMUTABLE_CONTEXT###
System: order management service
Language: Python 3.11
Repo structure:
  src/orders/       — business logic
  src/orders/api/   — HTTP handlers
  tests/            — pytest test suite
Invariant: all monetary values stored as integer cents
###END:IMMUTABLE_CONTEXT###
```

> **Why this is here:** These are facts that are true for every task in this codebase — the language, the structure, the core invariant. They do not change between invocations and should be cached. Restating them in TASK_PAYLOAD would waste tokens and violate §4.2 (non-redefinition).

---

```text
###ICS:CAPABILITY_DECLARATION###
ALLOW   file modification WITHIN src/orders/
ALLOW   file creation WITHIN src/orders/ IF new file has corresponding test
DENY    modification of src/orders/api/
DENY    modification of any file WITHIN tests/
DENY    introduction of new external dependencies
REQUIRE type annotations ON all new functions
REQUIRE docstring ON all new public functions
###END:CAPABILITY_DECLARATION###
```

> **Why this is here:** Permissions are declared explicitly rather than implied. The `DENY modification of src/orders/api/` directive means the model cannot touch the HTTP layer even if a refactoring task might logically extend there — the constraint is absolute and authoritative (§3.2). The `REQUIRE` directives encode team conventions that would otherwise live in a README and be ignored.
>
> **Note on undeclared actions:** Per §3.2, any action not explicitly declared is DENIED by default. The model may not rename files, delete functions, or modify configuration files — not because those are listed, but because they are not `ALLOW`ed.

---

```text
###ICS:SESSION_STATE###
[2024-01-15T09:30Z] Confirmed: discount logic currently lives in apply_discount() in src/orders/pricing.py
[2024-01-15T09:45Z] Decision: percentage and flat discounts to be handled by separate functions
###END:SESSION_STATE###
```

> **Why this is here:** These are decisions reached during the current working session — facts that are true now but were not part of the original system definition. Putting them in IMMUTABLE_CONTEXT would violate §3.1 (task-specific instructions are forbidden there). Putting them in TASK_PAYLOAD would restate them on every invocation. SESSION_STATE is the correct lifetime for conclusions that accumulate within a session.
>
> Each entry carries a timestamp for traceability (§3.3). If this session ends, the SESSION_STATE is cleared — the IMMUTABLE_CONTEXT and CAPABILITY_DECLARATION remain valid for the next session.

---

```text
###ICS:TASK_PAYLOAD###
Split apply_discount() into two functions: apply_percentage_discount() and apply_flat_discount().
Preserve existing call sites by having apply_discount() delegate to the appropriate function
based on the discount type field.
###END:TASK_PAYLOAD###
```

> **Why this is here:** This is the only layer that changes per invocation. It states what to do without restating where the code lives (already in IMMUTABLE_CONTEXT), what is permitted (already in CAPABILITY_DECLARATION), or what was decided earlier (already in SESSION_STATE). The task is executable without clarification — all required inputs and constraints are defined in the preceding layers.
>
> Notice what is absent: the function's location, the language, the annotation requirements. These are not restated because they are already declared. Restating them here would violate §4.2.

---

```text
###ICS:OUTPUT_CONTRACT###
format:     unified diff
schema:     standard unified diff against current HEAD; one diff block per modified file
variance:   diff header comments are permitted; no other variance allowed
on_failure: return plain text block with prefix "BLOCKED:" followed by a single-sentence
            description of the blocking constraint
###END:OUTPUT_CONTRACT###
```

> **Why this is here:** A task without a declared success condition is incomplete (§2.5). The OUTPUT_CONTRACT specifies exactly what a valid output looks like — format, structure, permitted variance — and what the model must return if it cannot comply. The caller does not need to inspect the output to determine if the task was attempted; the `BLOCKED:` prefix makes failure states machine-detectable.
>
> The `variance` field is explicit: diff header comments are permitted, nothing else is. Any output not matching this contract is invalid and must not be partially accepted (§3.5).

---

## Example 2: Structured Analysis Task

**Context:** A team is using ICS to drive recurring analysis of API usage logs. The domain context and permissions are stable; the specific analysis target changes per invocation.

---

```text
###ICS:IMMUTABLE_CONTEXT###
System: API gateway analytics pipeline
Data source: structured JSON logs, one record per request
Log schema:
  endpoint:    string   — fully qualified path (e.g., /v2/orders/{id})
  method:      string   — HTTP method
  status:      integer  — HTTP response code
  duration_ms: integer  — response time in milliseconds
  caller_id:   string   — authenticated client identifier
Invariant: duration_ms is always present; other fields may be null for malformed requests
###END:IMMUTABLE_CONTEXT###
```

> **Why this is here:** The log schema is a permanent fact about the system. Any analysis task will need it, so it belongs here rather than being restated per invocation. The invariant about `duration_ms` is declared explicitly — an implicit assumption here would produce incorrect analyses when null-handling is omitted.

---

```text
###ICS:CAPABILITY_DECLARATION###
ALLOW   read access to log data
ALLOW   aggregation and statistical summarization
DENY    output of individual caller_id values
DENY    output of any field that could identify a specific caller
REQUIRE flagging of any result where sample size is below 100 records
###END:CAPABILITY_DECLARATION###
```

> **Why this is here:** The `DENY` directives encode a privacy constraint. Without them, an analysis task requesting "top callers by error rate" might produce a table of caller IDs — technically correct, but in violation of a data handling requirement. Declaring it here means the constraint applies to every task in this session, not just tasks where the author remembered to include it.
>
> The `REQUIRE` directive encodes a statistical validity guard. It is a team convention that would otherwise be forgotten on any given invocation.

---

```text
###ICS:SESSION_STATE###
[2024-01-20T14:00Z] Analysis window: 2024-01-13 to 2024-01-20 (rolling 7 days)
[2024-01-20T14:05Z] Confirmed: /v2/orders/{id} endpoint flagged for elevated error rate in prior run
###END:SESSION_STATE###
```

> **Why this is here:** The analysis window is a session-scoped decision — it applies to all tasks in this session but will change next week. The flagged endpoint is a conclusion from a prior invocation that is relevant context for this one. Both belong in SESSION_STATE rather than IMMUTABLE_CONTEXT because they will be discarded or updated when the session ends.

---

```text
###ICS:TASK_PAYLOAD###
Produce a latency percentile breakdown (p50, p90, p99) for the /v2/orders/{id} endpoint,
segmented by HTTP method. Flag any method where p99 exceeds 2000ms.
###END:TASK_PAYLOAD###
```

> **Why this is here:** The task is precise and self-contained given the preceding layers. The endpoint was flagged in SESSION_STATE; the schema is in IMMUTABLE_CONTEXT; the privacy and sample-size constraints are in CAPABILITY_DECLARATION. No context is restated.

---

```text
###ICS:OUTPUT_CONTRACT###
format:     JSON
schema: {
  "endpoint": "string",
  "breakdown": [
    {
      "method":  "string",
      "p50_ms":  "integer",
      "p90_ms":  "integer",
      "p99_ms":  "integer",
      "flagged": "boolean"
    }
  ],
  "warnings": ["string"]
}
variance:   "warnings" field MAY be omitted if empty; "flagged" MUST be present even if false
on_failure: Return { "status": "error", "reason": "<single-sentence description>" }
###END:OUTPUT_CONTRACT###
```

> **Why this is here:** The schema makes the output machine-readable and unambiguous. The `variance` field clarifies two edge cases that would otherwise be implicit: warnings can be omitted when empty, but `flagged` must always be present. Without the second clarification, a caller processing the output might assume absence means false — a silent bug. Declaring it explicitly closes that gap.

---

## Example 3: Session Reset

**Context:** The same team from Example 2 is starting a new analysis session with a different time window. The prior session state must be cleared before new decisions are recorded.

---

```text
###ICS:IMMUTABLE_CONTEXT###
System: API gateway analytics pipeline
Data source: structured JSON logs, one record per request
Log schema:
  endpoint:    string   — fully qualified path (e.g., /v2/orders/{id})
  method:      string   — HTTP method
  status:      integer  — HTTP response code
  duration_ms: integer  — response time in milliseconds
  caller_id:   string   — authenticated client identifier
Invariant: duration_ms is always present; other fields may be null for malformed requests
###END:IMMUTABLE_CONTEXT###

###ICS:CAPABILITY_DECLARATION###
ALLOW   read access to log data
ALLOW   aggregation and statistical summarization
DENY    output of individual caller_id values
DENY    output of any field that could identify a specific caller
REQUIRE flagging of any result where sample size is below 100 records
###END:CAPABILITY_DECLARATION###

###ICS:SESSION_STATE###
CLEAR
###END:SESSION_STATE###

###ICS:TASK_PAYLOAD###
Identify the five endpoints with the highest p99 latency over the past 30 days.
###END:TASK_PAYLOAD###

###ICS:OUTPUT_CONTRACT###
format:     JSON
schema:     { "endpoints": [{ "path": "string", "p99_ms": "integer" }] }
variance:   none
on_failure: Return { "status": "error", "reason": "<single-sentence description>" }
###END:OUTPUT_CONTRACT###
```

> **Why CLEAR is used here:** The prior session assumed a specific 7-day analysis window. Starting a new session with a 30-day window requires that prior assumption to be discarded. `CLEAR` signals explicitly that no prior session state applies — the next SESSION_STATE entry will start fresh. The IMMUTABLE_CONTEXT and CAPABILITY_DECLARATION are unchanged and carry forward as-is.
>
> Note that `variance: none` is a valid declaration. It is more explicit than omitting the field (which would be non-conformant) and more precise than leaving it open.

---

## Common Mistakes

The following patterns appear in real instructions but are non-conformant under ICS. Each is shown with the rule it violates and the correct approach.

---

**Restating context in TASK_PAYLOAD**

```text
# Non-conformant
###ICS:TASK_PAYLOAD###
In the order management service (Python 3.11, located in src/orders/), split apply_discount()...
###END:TASK_PAYLOAD###
```

> Violates §4.2. The system and language are already declared in IMMUTABLE_CONTEXT. Restating them here is redundant at best and, if they contradict the IMMUTABLE_CONTEXT, non-conformant.

---

**Implied constraints in TASK_PAYLOAD**

```text
# Non-conformant
###ICS:TASK_PAYLOAD###
Refactor apply_discount(). Don't break the API layer.
###END:TASK_PAYLOAD###
```

> Violates §3.2 and §4.3. Constraints belong in CAPABILITY_DECLARATION using directive syntax, not embedded in prose inside TASK_PAYLOAD. A constraint stated here is not authoritative and cannot be validated.

---

**Missing OUTPUT_CONTRACT fields**

```text
# Non-conformant
###ICS:OUTPUT_CONTRACT###
format:   JSON
schema:   { "result": "string" }
###END:OUTPUT_CONTRACT###
```

> Violates §3.5. All four required fields must be present. `variance` and `on_failure` are missing. An output contract without a declared failure behavior leaves the calling system with no defined response when the task cannot be completed.

---

**CLEAR with additional content**

```text
# Non-conformant
###ICS:SESSION_STATE###
CLEAR
[2024-01-20T15:00Z] New window: past 30 days
###END:SESSION_STATE###
```

> Violates §3.3. A SESSION_STATE layer containing `CLEAR` must contain only `CLEAR`. New session entries belong in the next invocation's SESSION_STATE, after the clear has been processed.
