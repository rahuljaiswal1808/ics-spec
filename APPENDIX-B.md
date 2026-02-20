# APPENDIX-B: Further Conformant Examples

This document extends APPENDIX-A with examples from domains outside software development. The same five layers and the same conformance rules apply regardless of domain. The goal is to show that ICS is not specific to code — it applies wherever an LLM is given a repeatable, structured task.

Read this alongside `ICS-v0.1.md` and `APPENDIX-A.md`.

---

## Example 1: Infrastructure Change (Terraform)

**Context:** A platform team uses ICS to drive Terraform modifications across a multi-account AWS environment. The infrastructure definitions and guard-rails are stable across many tasks; the specific change request varies per invocation.

This domain illustrates a case where the CAPABILITY_DECLARATION carries particularly high stakes — a misapplied change can affect production availability — and where `on_failure` must be unambiguous.

---

```text
###ICS:IMMUTABLE_CONTEXT###
System: multi-account AWS infrastructure
Managed by: Terraform >= 1.7, state stored in S3 (bucket: acme-tf-state), locked via DynamoDB
Account layout:
  prod/       — production workloads (account: 111122223333)
  staging/    — pre-production validation (account: 444455556666)
  shared/     — networking, DNS, IAM roles shared across accounts (account: 777788889999)
Repository layout:
  infra/prod/       — prod Terraform root modules
  infra/staging/    — staging Terraform root modules
  infra/shared/     — shared services Terraform root modules
  infra/modules/    — reusable modules (no root modules here)
Invariants:
  - All resources in infra/prod/ have deletion_protection = true where the provider supports it
  - No hardcoded AWS account IDs; all cross-account references use data sources or variables
  - infra/modules/ contains no provider blocks and no backend configuration
  - All resource names follow the pattern {env}-{service}-{resource_type}
###END:IMMUTABLE_CONTEXT###
```

> **Why this is here:** The account layout, naming conventions, and structural invariants are permanent facts — they apply to every Terraform task regardless of what is being changed. Declaring them once here avoids repeating them in every TASK_PAYLOAD and ensures they cannot be contradicted by a task-specific instruction.
>
> The invariants section is important: `deletion_protection = true` is a safety property that must be preserved even when a task says "update this resource." Stating it in IMMUTABLE_CONTEXT makes it visible to every step of the session, not just the steps where someone remembered to mention it.

---

```text
###ICS:CAPABILITY_DECLARATION###
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
REQUIRE tagging of all new AWS resources WITH Environment and Owner tags
###END:CAPABILITY_DECLARATION###
```

> **Why this is here:** The separation of `infra/prod/` from `infra/staging/` and `infra/modules/` is the primary blast-radius control. The `DENY modification of .tf files WITHIN infra/prod/` directive is absolute — no task, however it is phrased, can override it. This is the correct use of ICS: encoding constraints that must hold unconditionally, not constraints that hold "unless the task says otherwise."
>
> The `REQUIRE tagging` directive encodes an operational convention. Without it, new resources would periodically appear without tags, causing cost attribution failures discovered weeks later. Declaring it here applies it to every task in the session without requiring the author of each TASK_PAYLOAD to remember it.
>
> **Note on UNLESS:** The `REQUIRE naming ... UNLESS following the pattern` syntax is intentionally read as a constraint with an exception — the exception is that the naming IS following the pattern, so the REQUIRE applies to enforce that naming. This is a valid use of the `UNLESS` qualifier per §3.2.

---

```text
###ICS:SESSION_STATE###
[2025-03-10T10:00Z] Task: migrate staging ECS services from EC2 launch type to Fargate
[2025-03-10T10:08Z] Confirmed: affected services are api-gateway, worker, and scheduler in infra/staging/ecs/
[2025-03-10T10:15Z] Decision: migrate services one at a time; start with worker
[2025-03-10T10:22Z] Confirmed: worker service has no persistent volumes; migration is safe
###END:SESSION_STATE###
```

> **Why this is here:** The decision to migrate services one at a time is a session-level strategy — it was not in the original task specification but was reached during investigation. It belongs in SESSION_STATE rather than TASK_PAYLOAD so it persists across the multiple invocations this migration will require. The confirmation about persistent volumes is a fact discovered during this session that is relevant to subsequent steps.

---

```text
###ICS:TASK_PAYLOAD###
Update the worker ECS service definition in infra/staging/ecs/worker.tf to use
Fargate launch type. Set cpu = 512, memory = 1024. Retain all existing environment
variables, IAM role references, and log configuration unchanged.
###END:TASK_PAYLOAD###
```

> **Why this is here:** The task is precise and references only things the model needs to know for this invocation. The affected file is named explicitly. The constraints on what must be retained (environment variables, IAM roles, log configuration) are stated, not implied. No context is restated from IMMUTABLE_CONTEXT or SESSION_STATE.

---

```text
###ICS:OUTPUT_CONTRACT###
format:     unified diff
schema:     standard unified diff against the current HEAD of infra/staging/ecs/worker.tf;
            one diff block per modified file; no changes outside infra/staging/ecs/worker.tf
variance:   diff header timestamps MAY be omitted; no other variance permitted
on_failure: return plain text with prefix "BLOCKED:" followed by a single sentence
            identifying which CAPABILITY_DECLARATION constraint prevents execution,
            or "AMBIGUOUS:" followed by a single sentence identifying what information
            is missing and which layer it should appear in
###END:OUTPUT_CONTRACT###
```

> **Why this is here:** The scope constraint in the schema (`no changes outside infra/staging/ecs/worker.tf`) is part of the output contract, not the task. It closes the gap between what the task requests and what the output is permitted to contain. A model that decides to "helpfully" update a shared module while completing the task will produce an output that violates this contract.
>
> The `on_failure` field defines two failure modes with distinct prefixes: `BLOCKED:` for constraint violations and `AMBIGUOUS:` for underspecified inputs. This lets the calling system route failures differently — a blocked task may be escalated; an ambiguous task should result in a layer update.

---

## Example 2: Database Schema Migration Planning

**Context:** A data engineering team uses ICS to plan and review Alembic migration scripts before they are applied. The database schema and migration conventions are stable; the specific change being reviewed varies per invocation.

This domain shows ICS applied to a review task rather than a generation task — the model is assessing a proposed change against declared constraints, not producing code.

---

```text
###ICS:IMMUTABLE_CONTEXT###
System: analytics data warehouse
Engine: PostgreSQL 15, managed via Alembic (alembic.ini at repo root)
Schema layout:
  public.*       — operational tables, written by the application
  reporting.*    — derived tables and views, written by ETL jobs
  archive.*      — append-only historical tables; never updated or deleted from
Migration conventions:
  - Each migration file contains exactly one op.create_table, op.add_column,
    op.drop_column, or op.create_index call, or a single logical group of such
    calls if they are atomically required (e.g., add column + create index on it)
  - All migrations must be reversible: downgrade() must undo exactly what upgrade() does
  - Columns added to public.* tables must have a DEFAULT value or be nullable;
    non-nullable columns without defaults cannot be added to tables with existing rows
  - archive.* tables have no UPDATE or DELETE triggers and must not receive any
Invariants:
  - reporting.* objects are owned by the etl_user role; they must not be modified by migrations
  - All foreign keys reference the primary key of the parent table; no partial key references
  - Column names use snake_case; table names use snake_case and singular nouns
###END:IMMUTABLE_CONTEXT###
```

> **Why this is here:** The schema layout, migration conventions, and invariants are facts that hold for every review task. The constraint that non-nullable columns cannot be added to tables with existing rows without a default value is a PostgreSQL behavior fact that a model must know to review migrations correctly. Stating it here means it does not need to be re-explained in every TASK_PAYLOAD.

---

```text
###ICS:CAPABILITY_DECLARATION###
ALLOW   read access to migration file content
ALLOW   read access to schema definitions WITHIN public.*
ALLOW   read access to schema definitions WITHIN reporting.*
ALLOW   read access to schema definitions WITHIN archive.*
DENY    generation of migration file content
DENY    modification of migration files
DENY    output of suggestions that would alter reporting.* ownership
REQUIRE flagging of any migration that lacks a reversible downgrade()
REQUIRE flagging of any non-nullable column addition to a table with existing data
REQUIRE flagging of any migration that touches archive.* tables with UPDATE or DELETE operations
###END:CAPABILITY_DECLARATION###
```

> **Why this is here:** This CAPABILITY_DECLARATION defines a read-only review role. The `DENY generation of migration file content` and `DENY modification of migration files` directives exist because the model's role in this session is review, not authorship. A model that generates a "corrected" migration instead of flagging the problem with the proposed one has exceeded its declared capability surface.
>
> The three `REQUIRE` directives encode the three most common classes of migration error this team has encountered. They are stated as requirements rather than guidelines — the model must report on each of these, whether or not they are present in the migration being reviewed.

---

```text
###ICS:SESSION_STATE###
[2025-02-14T09:00Z] Review target: PR #412 — adds user_preferences table to public schema
[2025-02-14T09:05Z] Confirmed: public.users table has ~4.2M rows in production
[2025-02-14T09:10Z] Confirmed: migration file is infra/migrations/0047_add_user_preferences.py
###END:SESSION_STATE###
```

> **Why this is here:** The row count for `public.users` was retrieved during this session and is relevant to evaluating whether any foreign key or join operation in the migration would require a full table scan. The migration file path was confirmed to avoid any ambiguity about which file is under review. Both are session-scoped facts — they apply to this review only.

---

```text
###ICS:TASK_PAYLOAD###
Review migration 0047_add_user_preferences.py for conformance with the migration
conventions and invariants declared in IMMUTABLE_CONTEXT. Report all violations
found. If no violations are found, confirm conformance explicitly.
###END:TASK_PAYLOAD###
```

> **Why this is here:** The task is narrow and refers to the migration by name, which was confirmed in SESSION_STATE. It does not restate the conventions being checked (they are in IMMUTABLE_CONTEXT) or the review constraints (they are in CAPABILITY_DECLARATION). The instruction to "confirm conformance explicitly" closes the ambiguity about what a clean result looks like — absence of violations is not the same as an explicit confirmation.

---

```text
###ICS:OUTPUT_CONTRACT###
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
on_failure: Return { "status": "error", "reason": "<single-sentence description>" }
###END:OUTPUT_CONTRACT###
```

> **Why this is here:** The schema separates `violations` (rule failures, machine-processable) from `notes` (observations that do not constitute violations but are worth surfacing). This distinction prevents the model from downgrading a real violation to a note to produce a cleaner-looking output. The `verdict` field is required even when `violations` is empty to make the pass/fail signal unambiguous to the calling system without requiring it to inspect the violations array.
>
> The two allowed values for `verdict` are enumerated — not described as "a string indicating pass or fail." Enumerated values are machine-checkable; descriptions are not.

---

## Common Mistakes in Non-Code Domains

These mistakes appear specifically in domains where the instruction author is less accustomed to treating the model interaction as a formal interface.

---

**Encoding domain facts in SESSION_STATE instead of IMMUTABLE_CONTEXT**

```text
# Non-conformant
###ICS:SESSION_STATE###
[2025-02-14T09:00Z] Note: archive.* tables are append-only and must not receive updates
###END:SESSION_STATE###
```

> Violates §3.1 and §4.2. The append-only property of `archive.*` is a permanent invariant, not a session decision. Placing it in SESSION_STATE means it will be discarded when the session ends and must be re-stated next session — which it may not be. Permanent facts belong in IMMUTABLE_CONTEXT where they persist and cannot be overridden.

---

**Using prose constraints in TASK_PAYLOAD as a substitute for CAPABILITY_DECLARATION**

```text
# Non-conformant
###ICS:TASK_PAYLOAD###
Review migration 0047. Be careful not to suggest changes to the reporting schema —
that's owned by the ETL team and we don't touch it.
###END:TASK_PAYLOAD###
```

> Violates §3.2 and §4.3. "Be careful not to" is not a constraint — it is a suggestion. The model may or may not comply; the instruction is not enforceable or auditable. The correct form is `DENY output of suggestions that would alter reporting.* ownership` in CAPABILITY_DECLARATION, where it is authoritative (§3.2) and applies to every invocation in the session.

---

**Underspecified OUTPUT_CONTRACT for review tasks**

```text
# Non-conformant
###ICS:OUTPUT_CONTRACT###
format:   prose
schema:   a summary of any issues found
variance: some flexibility allowed
on_failure: try your best
###END:OUTPUT_CONTRACT###
```

> Violates §3.5 and §4.3. `variance: some flexibility allowed` is not a declaration — it is an abdication. Permitted variance must be enumerated; open-ended flexibility means any output is valid, which makes the contract meaningless. `on_failure: try your best` defines no behavior — the calling system has no way to detect or handle failure. Even for prose output, the contract must be specific: what sections are required, what constitutes a complete response, and what the model must return if it cannot produce a valid output.

---

**Omitting OUTPUT_CONTRACT schema for structured outputs in review tasks**

```text
# Non-conformant
###ICS:OUTPUT_CONTRACT###
format:     JSON
schema:     a JSON object with the review results
variance:   none
on_failure: Return { "status": "error" }
###END:OUTPUT_CONTRACT###
```

> Violates §3.5. The schema field must define the structure, not describe it. "A JSON object with the review results" tells the calling system nothing about what keys to expect, what types they have, or which fields are required. A conformant schema names every field, specifies its type, and enumerates its valid values where applicable.
