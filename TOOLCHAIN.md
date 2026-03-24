# ICS Toolchain Guide

This document covers every tool in the ICS toolchain, their CLI options, their programmatic APIs, and how they fit together in a typical development or CI workflow.

---

## Table of contents

1. [Overview](#overview)
2. [Installation](#installation)
3. [Tool reference](#tool-reference)
   - [M1 — Validator](#m1--validator-ics_validatorpy)
   - [M2 — Token analyzer](#m2--token-analyzer-ics_token_analyzerpy)
   - [M3 — Live tester](#m3--live-tester-ics_live_testpy)
   - [M4 — Linter](#m4--linter-ics_linterpy)
   - [M5 — Scaffold](#m5--scaffold-ics_scaffoldpy)
   - [M6 — Diff](#m6--diff-ics_diffpy)
   - [M7 — CI Report](#m7--ci-report-ics_reportpy)
4. [CI integration](#ci-integration)
5. [Programmatic API quick reference](#programmatic-api-quick-reference)

---

## Overview

The toolchain covers the full ICS document lifecycle:

```
scaffold → edit → validate → lint → diff (review) → report (CI)
```

Each tool is self-contained (stdlib only, except `ics_live_test.py` which
needs `anthropic`) and can be used independently or composed in pipelines.

---

## Installation

```bash
# Core toolchain — no external dependencies
pip install .

# With live API testing support
pip install ".[live]"

# With exact BPE token counting
pip install ".[all]"
```

After installation these CLI commands are available:

```
ics-validate      ics-analyze    ics-live-test   ics-quality-bench
ics-lint          ics-scaffold   ics-diff        ics-report
```

---

## Tool reference

### M1 — Validator (`ics_validator.py`)

Checks structural compliance against the ICS v0.1 specification.

**What it checks**

| Step | Rule |
|------|------|
| 1 | All five layers present |
| 2 | Layers in canonical order |
| 3 | `SESSION_STATE` contains no permanent facts |
| 4 | No layer boundary redefinition |
| 5 | `CAPABILITY_DECLARATION` uses valid ALLOW/DENY/REQUIRE syntax |
| 6 | `OUTPUT_CONTRACT` contains all required fields |
| 7 | No ALLOW/DENY overlap for the same target |

**CLI**

```bash
ics-validate my_file.ics          # validate a file
ics-validate --stdin              # read from stdin
ics-validate --test               # run built-in self-tests
```

**Exit codes:** `0` = compliant, `1` = violations found, `2` = usage error

**Programmatic API**

```python
from ics_validator import validate, ValidationResult

result: ValidationResult = validate(open("my_file.ics").read())
print(result.compliant)        # bool
print(result.violations)       # list[Violation]
```

---

### M2 — Token analyzer (`ics_token_analyzer.py`)

Proves the §2.2/§2.4 token-savings claim offline — no API key required.

**CLI**

```bash
ics-analyze my_file.ics                     # analyze a single file
ics-analyze my_file.ics --invocations 10    # model N invocations
ics-analyze my_file.ics --json              # machine-readable output
```

---

### M3 — Live tester (`ics_live_test.py`)

Validates token savings with real Anthropic API calls.

**CLI**

```bash
export ANTHROPIC_API_KEY=sk-ant-...
ics-live-test my_file.ics --invocations 5
ics-live-test --dry-run   # preview requests without spending tokens
```

---

### M4 — Linter (`ics_linter.py`)

Semantic analysis beyond structural validity — catches anti-patterns,
ambiguous contracts, and contradictory directives.

**Lint rules**

| Rule | Severity | Description |
|------|----------|-------------|
| L001 | warning | Open-ended variance ("some flexibility", "as needed", …) |
| L002 | warning | Schema is prose, not a definition |
| L003 | error   | `on_failure` has no machine-detectable signal |
| L004 | warning | `on_failure` uses vague fallback language |
| L005 | error   | `CAPABILITY_DECLARATION` has no directives |
| L006 | warning | `TASK_PAYLOAD` is empty |
| L007 | warning | `TASK_PAYLOAD` contains implied constraints |
| L008 | warning | Duplicate directives |
| L009 | error   | Conflicting ALLOW and DENY for the same target |

**CLI**

```bash
ics-lint my_file.ics
ics-lint --stdin
ics-lint --test
```

**Exit codes:** `0` = no issues, `1` = issues found, `2` = usage/parse error

**Programmatic API**

```python
from ics_linter import lint, LintResult

result: LintResult = lint(open("my_file.ics").read())
print(result.has_errors)    # bool — any severity == "error"
print(result.issues)        # list[LintIssue]
```

---

### M5 — Scaffold (`ics_scaffold.py`)

Generates a skeleton ICS document with all five layers pre-populated with
guided placeholder text.

**CLI**

```bash
ics-scaffold                          # print to stdout
ics-scaffold --output new_doc.ics     # write to file
ics-scaffold --domain payments        # domain-specific hints
```

---

### M6 — Diff (`ics_diff.py`)

Layer-aware diff between two ICS revisions. Instead of a raw line diff,
shows what changed *inside* each named layer.

**CLI**

```bash
ics-diff v1.ics v2.ics
ics-diff v1.ics v2.ics --format json
ics-diff v1.ics v2.ics --layer CAPABILITY_DECLARATION   # single layer
```

---

### M7 — CI Report (`ics_report.py`)

Runs the full **validate + lint** pipeline across one or more ICS files and
produces an aggregate report suitable for CI pipelines, PR bots, and human
review.

#### Pass/fail criteria

| Mode | A file passes when… |
|------|---------------------|
| Normal (default) | `validate()` compliant **and** no lint issues with severity `error` |
| `--strict` | `validate()` compliant **and** zero lint issues (warnings also fail) |

#### CLI

```bash
# Check a directory of ICS files
ics-report prompts/*.ics

# JSON report for downstream tooling
ics-report prompts/*.ics --format json

# Markdown for a PR comment or wiki
ics-report prompts/*.ics --format markdown

# Strict mode — warnings also count as failures
ics-report prompts/*.ics --strict

# Read a single document from stdin
cat doc.ics | ics-report --stdin
cat doc.ics | ics-report --stdin --format json
```

**Exit codes:** `0` = all passed, `1` = one or more failed, `2` = usage/IO error

#### Output formats

**Console (default)**

```
  ✓ [PASS]  prompts/search.ics
  ✗ [FAIL]  prompts/ingest.ics
         [STRUCTURE] §4.1: Layers are out of order. Found: …
         [ERROR  ] L009  CAPABILITY_DECLARATION: conflicting ALLOW/DENY for write_files
  ✓ [PASS]  prompts/export.ics
────────────────────────────────────────────────────────────
  Checked 3 file(s): 2 passed, 1 failed
```

**JSON (`--format json`)**

```json
{
  "generated_at": "2026-03-24T10:00:00+00:00",
  "strict": false,
  "summary": {
    "total": 3,
    "passed": 2,
    "failed": 1,
    "all_passed": false
  },
  "files": [
    {
      "path": "prompts/search.ics",
      "passed": true,
      "read_error": null,
      "valid": true,
      "validation_violations": [],
      "lint_issues": []
    },
    {
      "path": "prompts/ingest.ics",
      "passed": false,
      "read_error": null,
      "valid": false,
      "validation_violations": [
        { "step": 2, "rule": "§4.1", "message": "Layers are out of order. …" }
      ],
      "lint_issues": [
        {
          "rule_id": "L009",
          "severity": "error",
          "layer": "CAPABILITY_DECLARATION",
          "message": "conflicting ALLOW/DENY for write_files",
          "hint": "Remove one of the conflicting directives."
        }
      ]
    }
  ]
}
```

**Markdown (`--format markdown`)**

Produces GitHub-Flavoured Markdown with:
- A summary table (files checked / passed / failed)
- A per-file results table with status icons and counts
- A **Failures** section with inline violation and lint-issue detail,
  including fix hints

Suitable for automated PR comments or wiki pages.

#### Programmatic API

```python
from ics_report import report, report_text, FileReport, SuiteReport

# Single document (from text already in memory)
fr: FileReport = report_text(open("doc.ics").read(), path="doc.ics")
print(fr.valid)                  # bool
print(fr.lint_error_count)       # int
print(fr.passed())               # bool (normal mode)
print(fr.passed(strict=True))    # bool (strict mode)

# Multi-file (supports glob patterns)
suite: SuiteReport = report(["prompts/*.ics", "tests/*.ics"])
print(suite.all_passed)          # bool
print(suite.passed_count)        # int
print(suite.to_console())        # str — console output
print(suite.to_markdown())       # str — GFM output
print(suite.to_json())           # str — JSON output

# Strict mode
suite = report(["prompts/*.ics"], strict=True)
```

**`FileReport` attributes**

| Attribute / method | Type | Description |
|--------------------|------|-------------|
| `path` | `str` | File path (or `<stdin>`) |
| `valid` | `bool` | `validate()` returned compliant |
| `validation_violations` | `list[Violation]` | Structural violations |
| `lint_issues` | `list[LintIssue]` | Semantic issues from linter |
| `read_error` | `str \| None` | Set if the file could not be opened |
| `validation_error_count` | `int` | `len(validation_violations)` |
| `lint_error_count` | `int` | Issues with severity `"error"` |
| `lint_warning_count` | `int` | Issues with severity `"warning"` |
| `passed(strict=False)` | `bool` | Whether this file passes |
| `to_dict(strict=False)` | `dict` | JSON-serialisable representation |

**`SuiteReport` attributes**

| Attribute / method | Type | Description |
|--------------------|------|-------------|
| `files` | `list[FileReport]` | Per-file results |
| `strict` | `bool` | Whether strict mode is active |
| `generated_at` | `str` | ISO-8601 UTC timestamp |
| `total` | `int` | Number of files checked |
| `passed_count` | `int` | Files that passed |
| `failed_count` | `int` | Files that failed |
| `all_passed` | `bool` | `failed_count == 0` |
| `to_console()` | `str` | Console report |
| `to_markdown()` | `str` | GFM report |
| `to_json(indent=2)` | `str` | JSON report |
| `to_dict()` | `dict` | Raw dict |

---

## CI integration

### GitHub Actions

```yaml
name: ICS compliance

on: [push, pull_request]

jobs:
  ics-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install ICS toolchain
        run: pip install .

      - name: Validate and lint all ICS files
        run: ics-report prompts/**/*.ics --strict

      - name: Upload JSON report
        if: always()
        run: ics-report prompts/**/*.ics --format json > ics-report.json

      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: ics-report
          path: ics-report.json
```

### PR comment (Markdown report)

```yaml
      - name: Generate Markdown report
        id: ics
        run: |
          ics-report prompts/**/*.ics --format markdown > ics-report.md || true
          echo "body<<EOF" >> $GITHUB_OUTPUT
          cat ics-report.md >> $GITHUB_OUTPUT
          echo "EOF" >> $GITHUB_OUTPUT

      - name: Post report as PR comment
        uses: actions/github-script@v7
        with:
          script: |
            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: `${{ steps.ics.outputs.body }}`
            })
```

### Pre-commit hook

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: ics-report
        name: ICS validate + lint
        entry: ics-report
        language: system
        files: \.ics$
        pass_filenames: true
```

### Makefile

```makefile
.PHONY: ics-check ics-report

ics-check:
	ics-report prompts/*.ics

ics-report:
	ics-report prompts/*.ics --format markdown > docs/ics-report.md
```

---

## Programmatic API quick reference

```python
# Validate
from ics_validator import validate
result = validate(text)
result.compliant          # bool
result.violations         # list[Violation] — .step, .rule, .message

# Lint
from ics_linter import lint
result = lint(text)
result.has_errors         # bool
result.issues             # list[LintIssue] — .rule_id, .severity, .layer, .message, .hint

# Report (single file)
from ics_report import report_text
fr = report_text(text, path="doc.ics")
fr.passed()               # bool
fr.passed(strict=True)    # bool

# Report (multi-file)
from ics_report import report
suite = report(["a.ics", "b.ics", "glob/*.ics"], strict=False)
suite.all_passed          # bool
suite.to_json()           # str
suite.to_markdown()       # str
suite.to_console()        # str

# Parse capability block
from ics_constraint_parser import parse_capability_block
directives = parse_capability_block(capability_text)

# SDK — document assembly
from ics_sdk import ICSDocument
doc = ICSDocument()
doc.set_immutable("domain: payments")
doc.set_capability("ALLOW action:read_files from:user_request")
...
print(doc.render())
```
