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
| `APPENDIX-A.md` | Annotated examples: code refactoring, analytics, session reset |
| `APPENDIX-B.md` | Annotated examples: Terraform infrastructure, schema migration review |
| `APPENDIX-C.md` | Quality benchmark scenario catalogue (20 scenarios, payments-platform domain) |
| `experiments.md` | Empirical evidence for the token-savings claim (§2.2, §2.4) |
| `paper.tex` | LaTeX source for the ICS technical paper (compiled to `paper.pdf`) |

### Quick start: installation

```bash
# Stdlib only — validator and token analyzer, no external deps
pip install .

# With live API testing support
pip install ".[live]"

# With exact BPE token counting
pip install ".[all]"
```

Once installed, the three tools are available as CLI commands:

```bash
ics-validate  my_instruction.txt
ics-analyze   my_instruction.txt --invocations 10
ics-live-test my_instruction.txt --invocations 5   # requires ANTHROPIC_API_KEY
```

## Tools

The ICS toolchain covers the full document lifecycle: scaffolding → editing → validation → linting → diffing → CI reporting.

| Module | CLI command | Purpose |
|--------|-------------|---------|
| `ics_validator.py` | `ics-validate` | Structural compliance — checks all spec rules, reports violations |
| `ics_token_analyzer.py` | `ics-analyze` | Token analyzer — proves §2.2/§2.4 savings claim offline |
| `ics_live_test.py` | `ics-live-test` | Live tester — validates savings with real Anthropic API calls |
| `ics_quality_bench.py` | `ics-quality-bench` | Quality benchmark — runs 20 ICS scenarios, scores outputs |
| `ics_constraint_parser.py` | *(library)* | Parses `CAPABILITY_DECLARATION` and `OUTPUT_CONTRACT` into typed structures |
| `ics_linter.py` | `ics-lint` | Semantic linter — catches anti-patterns beyond structural validity (9 rules) |
| `ics_scaffold.py` | `ics-scaffold` | Document scaffolder — generates a skeleton ICS file with all five layers |
| `ics_diff.py` | `ics-diff` | Layer-aware diff — shows per-layer changes between two ICS revisions |
| `ics_report.py` | `ics-report` | **CI aggregate reporter** — validate + lint across N files; outputs console/JSON/Markdown |
| `ics_sdk.py` | *(library)* | Python SDK — programmatic document assembly and runtime injection |

See [`TOOLCHAIN.md`](TOOLCHAIN.md) for a full guide to every tool, their options, and how they compose in CI pipelines.

### Quick start: validate a file

```bash
pip install .
ics-validate my_instruction.ics
```

### Quick start: CI report across a directory

```bash
# Console summary — exit 0 if all pass, exit 1 if any fail
ics-report prompts/*.ics

# JSON output for downstream tooling
ics-report prompts/*.ics --format json > report.json

# Markdown suitable for a PR comment
ics-report prompts/*.ics --format markdown

# Treat warnings as failures
ics-report prompts/*.ics --strict
```

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

> **Note on cache activation:** Anthropic prompt caching requires the cached block to reach a minimum token threshold. For `claude-haiku-4-5-20251001` (and Claude 4-series models generally) the threshold is **≥ ~4,096 tokens**; older Claude 3 models used 1,024. The built-in APPENDIX-A examples are small demonstration snippets and will not trigger cache hits regardless of model. Use `examples/payments-platform.ics` (4,115 permanent-layer tokens, verified against the API) to observe real `cache_read_input_tokens` savings in the ICS column.

---

## Status

ICS is an **initial public draft (v0.1)**.

Feedback is invited before semantics are considered stable. To submit feedback, open an issue in the project repository or comment directly on the relevant section of `ICS-v0.1.md`.
