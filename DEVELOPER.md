# ICS SDK — Developer Guide

This guide covers everything you need to build with the ICS tools: the prompt
library (Python and TypeScript), the auto-classifier, the validator, and the
Java runtime.

---

## Contents

1. [Installation](#1-installation)
2. [Core concepts](#2-core-concepts)
3. [Python prompt library](#3-python-prompt-library)
4. [TypeScript prompt library](#4-typescript-prompt-library)
5. [Auto-classifier](#5-auto-classifier)
6. [Validator](#6-validator)
7. [CLI reference](#7-cli-reference)
8. [Integration patterns](#8-integration-patterns)
9. [Java runtime](#9-java-runtime)

---

## 1. Installation

```bash
# Validator + token analyzer only (no external deps)
pip install .

# Add live API testing (Anthropic)
pip install ".[anthropic]"

# Add OpenAI / Ollama support
pip install ".[openai]"

# Add Google Gemini support
pip install ".[gemini]"

# Exact BPE token counting via tiktoken
pip install ".[exact]"

# Everything
pip install ".[all]"
```

The TypeScript library (`ics_prompt.ts`) has no npm dependencies. Copy it
directly into your project or reference it via a path import.

---

## 2. Core concepts

An ICS instruction is a string divided into exactly five **layers**, always in
this order:

| Layer | Lifetime | Cache-eligible |
|---|---|---|
| `IMMUTABLE_CONTEXT` | Forever — stable domain facts | Yes |
| `CAPABILITY_DECLARATION` | Deployment — permissions and constraints | Yes |
| `SESSION_STATE` | Session — current decisions and preferences | No |
| `TASK_PAYLOAD` | Per-call — the specific user request | No |
| `OUTPUT_CONTRACT` | Deployment — output format and schema | Yes |

Each layer is delimited in the compiled string:

```
###ICS:IMMUTABLE_CONTEXT###
You are a senior financial analyst assistant.
###END:IMMUTABLE_CONTEXT###

###ICS:CAPABILITY_DECLARATION###
ALLOW  read-only market-data queries
DENY   trading actions or account mutations
###END:CAPABILITY_DECLARATION###
...
```

Cache-eligible layers are identical across calls and can be sent with
`cache_control` headers to reduce cost and latency (see
[Integration patterns](#8-integration-patterns)).

---

## 3. Python prompt library

**File:** `ics_prompt.py`

### 3.1 Importing

```python
import ics_prompt as ics
```

### 3.2 Tagging content

Each layer has a factory function. Pass a string to get an `ICSBlock`:

```python
PERSONA = ics.immutable("You are a senior financial analyst assistant.")

RULES = ics.capability("""
    ALLOW  read-only market-data queries
    DENY   trading actions or account mutations
""")

FORMAT = ics.output_contract("""
    format:     structured markdown
    schema:     { "analysis": "string", "risks": ["string"] }
    variance:   "risks" MAY be omitted for informational queries
    on_failure: plain-text apology with brief reason
""")
```

The same functions work as **decorators** on factory functions for
per-call layers:

```python
@ics.session
def session_ctx(user_name: str, portfolio: str) -> str:
    return f"User: {user_name}.  Portfolio focus: {portfolio}."

@ics.dynamic
def task(user_message: str) -> str:
    return f"The user asked: {user_message}"
```

Calling the decorated function returns an `ICSBlock`:

```python
block = session_ctx("Alice", "tech equities")   # ICSBlock
block = task("What is the P/E ratio for NVDA?") # ICSBlock
```

### 3.3 Compiling a prompt

```python
prompt = ics.compile(
    PERSONA,
    RULES,
    session_ctx(name, portfolio),
    task(msg),
    FORMAT,
)
# Returns a single ICS-delimited string ready to send as a system prompt.
```

`compile` emits `warnings.warn` for:
- layers appearing out of canonical order
- template variables inside cache-eligible blocks

Suppress warnings with `warn=False`:

```python
prompt = ics.compile(PERSONA, RULES, ..., warn=False)
```

### 3.4 Validating without compiling

```python
issues = ics.validate(PERSONA, RULES, session_ctx(name, portfolio), task(msg), FORMAT)
# Returns a list[str] of warning messages. Empty list = clean.
```

### 3.5 Parsing a compiled prompt

```python
blocks = ics.parse(prompt)
# Returns list[ICSBlock] — useful for tests and inspection.
```

### 3.6 ICSBlock properties

```python
block.layer          # ICSLayer enum value
block.content        # str — raw text content
block.cache_eligible # bool — True for IMMUTABLE_CONTEXT, CAPABILITY_DECLARATION, OUTPUT_CONTRACT
str(block)           # same as block.content
```

### 3.7 Complete example

```python
import ics_prompt as ics
import anthropic

# --- Static blocks (defined once at module level) ---

PERSONA = ics.immutable("""
    You are a senior financial analyst assistant.
    Domain: public equity markets, Q1-Q4 earnings analysis.
""")

RULES = ics.capability("""
    ALLOW  read-only market-data queries
    ALLOW  earnings report analysis
    DENY   trading actions or account mutations
    DENY   disclosure of model or system internals
    REQUIRE all monetary figures quoted in USD
""")

FORMAT = ics.output_contract("""
    format:     structured markdown
    schema:     { "analysis": "string", "risks": ["string"], "verdict": "BUY|HOLD|SELL" }
    variance:   "risks" MAY be omitted for informational queries
    on_failure: plain-text apology with brief reason
""")

# --- Per-call factories ---

@ics.session
def session_ctx(user_name: str, portfolio: str) -> str:
    return f"User: {user_name}.  Portfolio focus: {portfolio}."

@ics.dynamic
def task(user_message: str) -> str:
    return f"The user asked: {user_message}"

# --- Build and send ---

def ask(name: str, portfolio: str, message: str) -> str:
    prompt = ics.compile(
        PERSONA,
        RULES,
        session_ctx(name, portfolio),
        task(message),
        FORMAT,
    )
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=prompt,
        messages=[{"role": "user", "content": message}],
    )
    return response.content[0].text
```

---

## 4. TypeScript prompt library

**File:** `ics_prompt.ts`

### 4.1 Importing

```ts
import * as ics from "./ics_prompt"
```

### 4.2 Tagging content

Each layer has a **tagger** that works as both a tagged template literal and a
plain function call:

```ts
// Tagged template literal
const PERSONA = ics.immutable`You are a senior financial analyst assistant.`

// Plain function call
const PERSONA = ics.immutable("You are a senior financial analyst assistant.")
```

Tagged template literals support interpolation in any layer:

```ts
function sessionCtx(userName: string, portfolio: string) {
  return ics.session`User: ${userName}.  Portfolio focus: ${portfolio}.`
}

function task(userMessage: string) {
  return ics.dynamic`The user asked: ${userMessage}`
}
```

### 4.3 Compiling a prompt

```ts
const prompt = ics.compile(
  PERSONA,
  RULES,
  sessionCtx(name, portfolio),
  task(msg),
  FORMAT,
)
```

Suppress validation warnings:

```ts
const prompt = ics.compile(PERSONA, RULES, ..., { warn: false })
```

### 4.4 Validating without compiling

```ts
const issues = ics.validate(PERSONA, RULES, sessionCtx(name, portfolio), task(msg), FORMAT)
// string[] — empty array means no issues
```

### 4.5 Parsing a compiled prompt

```ts
const blocks = ics.parse(prompt)
// ICSBlock[] — useful for tests
```

### 4.6 ICSBlock interface

```ts
block.layer          // ICSLayer string literal union
block.content        // string
block.cacheEligible  // boolean
block.toString()     // same as block.content
```

### 4.7 Complete example

```ts
import Anthropic from "@anthropic-ai/sdk"
import * as ics from "./ics_prompt"

const PERSONA = ics.immutable`
  You are a senior financial analyst assistant.
  Domain: public equity markets, Q1-Q4 earnings analysis.
`

const RULES = ics.capability`
  ALLOW  read-only market-data queries
  ALLOW  earnings report analysis
  DENY   trading actions or account mutations
  REQUIRE all monetary figures quoted in USD
`

const FORMAT = ics.output_contract`
  format:     structured markdown
  schema:     { "analysis": "string", "risks": ["string"], "verdict": "BUY|HOLD|SELL" }
  variance:   "risks" MAY be omitted for informational queries
  on_failure: plain-text apology with brief reason
`

function sessionCtx(userName: string, portfolio: string) {
  return ics.session`User: ${userName}.  Portfolio focus: ${portfolio}.`
}

function task(userMessage: string) {
  return ics.dynamic`The user asked: ${userMessage}`
}

async function ask(name: string, portfolio: string, message: string): Promise<string> {
  const prompt = ics.compile(
    PERSONA,
    RULES,
    sessionCtx(name, portfolio),
    task(message),
    FORMAT,
  )

  const client = new Anthropic()
  const response = await client.messages.create({
    model: "claude-sonnet-4-6",
    max_tokens: 1024,
    system: prompt,
    messages: [{ role: "user", content: message }],
  })
  return (response.content[0] as { text: string }).text
}
```

---

## 5. Auto-classifier

**File:** `ics_autoclassifier.py`

Takes an unstructured system prompt and classifies its sections into ICS layers.
Useful when migrating an existing prompt to ICS.

### 5.1 Classification modes (precedence order)

1. **Delimiter fast-path** — if the prompt already uses `###ICS:LAYER###` delimiters, those are parsed directly.
2. **Annotation-driven** — developer wraps sections in `<ics:tag>` tags; always wins over heuristics.
3. **Heuristic scoring** — signal-based inference for unlabelled text.
4. **Conservative fallback** — ambiguous segments → `UNCLASSIFIED` (never cached).

### 5.2 Annotation syntax

Wrap any section in an annotation tag to assign it a layer explicitly:

```
<ics:immutable>
You are a senior financial analyst assistant.
</ics:immutable>

<ics:capability>
ALLOW read-only market-data queries
DENY  trading actions
</ics:capability>

<ics:output-contract>
format:     JSON
schema:     { "result": "string" }
variance:   none
on_failure: error string
</ics:output-contract>
```

**Accepted aliases:**

| Tag | Layer |
|---|---|
| `immutable`, `stable`, `permanent` | `IMMUTABLE_CONTEXT` |
| `capability`, `capabilities`, `constraints` | `CAPABILITY_DECLARATION` |
| `session`, `semi-static` | `SESSION_STATE` |
| `dynamic`, `task`, `per-call` | `TASK_PAYLOAD` |
| `output-contract`, `output_contract`, `format-contract` | `OUTPUT_CONTRACT` |

Annotation tags are stripped before the prompt reaches the LLM.

### 5.3 Python API

```python
from ics_autoclassifier import ICSAutoClassifier, to_ics, to_report

classifier = ICSAutoClassifier()
result = classifier.classify(prompt_text)

# Inspect blocks
for block in result.blocks:
    print(block.layer, block.confidence, block.cache_eligible)
    print(block.content[:80])

# Check for problems
print(result.has_conflicts)
print(result.warnings)

# Cache-eligible blocks only
for block in result.cache_eligible_blocks:
    ...

# Unclassified blocks (need manual review)
for block in result.unclassified_blocks:
    ...

# Render as ICS-delimited string
ics_output = to_ics(result)

# JSON report
report = to_report(result)
```

### 5.4 ClassificationResult reference

| Property | Type | Description |
|---|---|---|
| `blocks` | `list[ClassifiedBlock]` | All classified segments |
| `warnings` | `list[str]` | Classifier warnings |
| `has_conflicts` | `bool` | True if any warning contains "conflict" |
| `cache_eligible_blocks` | `list[ClassifiedBlock]` | Blocks safe to cache |
| `unclassified_blocks` | `list[ClassifiedBlock]` | Blocks needing manual review |

### 5.5 ClassifiedBlock reference

| Property | Type | Description |
|---|---|---|
| `content` | `str` | Text of the segment |
| `layer` | `ICSLayer` | Assigned layer |
| `confidence` | `float` | 0.0–1.0; 1.0 for annotation/delimiter-driven |
| `source` | `str` | `"annotation"`, `"delimiter"`, `"heuristic"`, or `"conservative"` |
| `warnings` | `list[str]` | Per-block warnings |
| `cache_eligible` | `bool` | Derived from layer |

### 5.6 Typical migration workflow

```python
from ics_autoclassifier import ICSAutoClassifier, to_ics, to_report
import json

with open("my_old_prompt.txt") as f:
    old_prompt = f.read()

classifier = ICSAutoClassifier()
result = classifier.classify(old_prompt)

# Step 1: review the JSON report to understand how sections were classified
print(json.dumps(to_report(result), indent=2))

# Step 2: add <ics:...> annotations to fix any UNCLASSIFIED or wrong sections
# Step 3: re-run until all blocks have confidence 1.0 and source "annotation"
# Step 4: emit the clean ICS-delimited output
print(to_ics(result))
```

---

## 6. Validator

**File:** `ics_validator.py`

Validates an ICS-formatted string against the full spec (all seven steps).

### 6.1 Python API

```python
from ics_validator import validate

with open("my_instruction.ics") as f:
    text = f.read()

result = validate(text)

print(result.compliant)      # bool
print(result.report())       # human-readable summary
print(result.to_dict())      # JSON-serialisable dict

for v in result.violations:
    print(v.step, v.rule, v.message)

for w in result.warnings:
    print(w)
```

### 6.2 Validation steps

| Step | Spec ref | What is checked |
|---|---|---|
| 1 | §5.2 / §3.6 | All five layers are present with correct delimiter syntax |
| 2 | §4.1 | Layers appear in canonical order |
| 3 | §3.3 | `SESSION_STATE` with `CLEAR` contains nothing else |
| 4 | §3.4 / §4.2 | No layer re-states or overrides a preceding layer |
| 5 | §3.2 | `CAPABILITY_DECLARATION` uses only `ALLOW`/`DENY`/`REQUIRE` directives with valid scope grammar |
| 6 | §3.5 | `OUTPUT_CONTRACT` contains all four required fields: `format`, `schema`, `variance`, `on_failure` |
| 7 | §3.2 | `ALLOW`/`DENY` specificity overlaps (warning, not violation) |

### 6.3 Exit codes (CLI)

| Code | Meaning |
|---|---|
| `0` | Compliant |
| `1` | Non-compliant |
| `2` | Usage error |

### 6.4 Example: CI gate

```python
import sys
from ics_validator import validate

def assert_compliant(path: str):
    with open(path) as f:
        result = validate(f.read())
    if not result.compliant:
        print(result.report(), file=sys.stderr)
        sys.exit(1)
```

---

## 7. CLI reference

After `pip install .` the following commands are available:

### ics-validate

```
ics-validate <file>
ics-validate --stdin
ics-validate --test        # run built-in test suite
ics-validate --json <file> # machine-readable output
```

### ics-analyze

```
ics-analyze <file>
ics-analyze <file> --invocations 10
ics-analyze <file> --exact   # requires pip install ".[exact]"
```

Proves the token-savings claim (§2.2 / §2.4) offline — no API key needed.

### ics-live-test

```
export ANTHROPIC_API_KEY=sk-ant-...
ics-live-test                          # built-in APPENDIX-A example
ics-live-test <file> --invocations 5
ics-live-test --dry-run                # preview requests without spending tokens
```

Sends paired naive vs ICS requests and reports real token counts including
`cache_creation_input_tokens` and `cache_read_input_tokens`.

### ics-quality-bench

Runs the 20-scenario quality benchmark catalogue from `APPENDIX-C.md`.

```
export ANTHROPIC_API_KEY=sk-ant-...
ics-quality-bench
ics-quality-bench --model claude-opus-4-6
```

### ics_autoclassifier (direct invocation)

```
python ics_autoclassifier.py <file>            # print ICS-delimited output
python ics_autoclassifier.py --stdin           # read from stdin
python ics_autoclassifier.py --report <file>   # JSON classification report
python ics_autoclassifier.py --to-ics <file>   # explicit ICS render mode
```

---

## 8. Integration patterns

### 8.1 Prompt caching with Anthropic

Cache-eligible layers (`IMMUTABLE_CONTEXT`, `CAPABILITY_DECLARATION`,
`OUTPUT_CONTRACT`) are identical across calls. Mark them with
`cache_control` to activate Anthropic prompt caching:

```python
import ics_prompt as ics
import anthropic

PERSONA  = ics.immutable("...")
RULES    = ics.capability("...")
FORMAT   = ics.output_contract("...")

@ics.session
def session_ctx(name, portfolio): ...

@ics.dynamic
def task(msg): ...

def build_messages(name, portfolio, msg):
    blocks = [
        PERSONA,
        RULES,
        session_ctx(name, portfolio),
        task(msg),
        FORMAT,
    ]

    system_parts = []
    for block in blocks:
        part = {"type": "text", "text": str(block)}
        if block.cache_eligible:
            part["cache_control"] = {"type": "ephemeral"}
        system_parts.append(part)

    return system_parts

client = anthropic.Anthropic()
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system=build_messages(name, portfolio, msg),
    messages=[{"role": "user", "content": msg}],
    betas=["prompt-caching-2024-07-31"],
)
```

> **Cache activation threshold:** Anthropic requires the cached block to
> reach approximately 4,096 tokens (Claude 4-series). Small demo prompts
> will not trigger cache hits. Use `examples/payments-platform.ics`
> (4,115 permanent-layer tokens) to observe real savings.

### 8.2 Resetting session state

Send `CLEAR` as the sole content of `SESSION_STATE` to signal a fresh
session context without changing any other layer:

```python
@ics.session
def reset_session() -> str:
    return "CLEAR"

prompt = ics.compile(PERSONA, RULES, reset_session(), task(msg), FORMAT)
```

### 8.3 Validating before sending

```python
issues = ics.validate(PERSONA, RULES, session_ctx(name, portfolio), task(msg), FORMAT)
if issues:
    raise ValueError("\n".join(issues))
prompt = ics.compile(...)
```

### 8.4 Migrating an existing prompt

```bash
# 1. Get a classification report for your current prompt
python ics_autoclassifier.py --report my_prompt.txt | python -m json.tool

# 2. Add <ics:...> annotations to your prompt file for any UNCLASSIFIED sections
# 3. Re-run until the report shows no UNCLASSIFIED blocks
# 4. Render the clean ICS output
python ics_autoclassifier.py --to-ics my_prompt.txt > my_prompt.ics

# 5. Validate the result
ics-validate my_prompt.ics
```

### 8.5 CI validation gate

```yaml
# .github/workflows/validate.yml
- name: Validate ICS instructions
  run: |
    pip install .
    for f in prompts/*.ics; do
      ics-validate "$f" || exit 1
    done
```

---

## 9. Java runtime

**Location:** `ics-runtime/java/`

The Java runtime is a full port of the Python ICS Runtime — same five-layer
architecture, same ICS contract model, compatible with both Anthropic and
OpenAI providers.

**Requirements:** Java 17+, Maven 3.8+

---

### 9.1 Build

```bash
# Build and install the library JAR into your local Maven cache
cd ics-runtime/java
mvn install -DskipTests

# Build the Javalin web demo fat-jar
cd ../java_web_demo
mvn package -DskipTests
```

To use the library in another Maven project, add the dependency after
`mvn install`:

```xml
<dependency>
  <groupId>io.ics</groupId>
  <artifactId>ics-runtime-java</artifactId>
  <version>0.1.0</version>
</dependency>
```

---

### 9.2 Quick start

```java
import io.ics.runtime.*;
import io.ics.runtime.tools.*;
import io.ics.runtime.contracts.*;
import java.util.Map;

// 1. Define a tool
ToolDefinition lookup = ToolDefinition.builder()
    .name("crm_lookup")
    .description("Look up a CRM lead by ID")
    .param("lead_id", ToolDefinition.ParamType.STRING, "Lead identifier", true)
    .build();

ToolRegistry registry = new ToolRegistry();
registry.register(lookup, args -> {
    String id = (String) args.get("lead_id");
    return Map.of("name", "Acme Corp", "status", "prospect");
});

// 2. Build the agent
Agent agent = Agent.builder()
    .provider("anthropic")               // or "openai"
    .model("claude-sonnet-4-6")
    .immutable("You are a BFSI lead qualification assistant.")
    .capability("DENY: logging PII\nREQUIRE: risk category in every result")
    .tool(lookup)
    .build();

// 3. One-shot run
RunResult result = agent.run("Qualify lead L-42");
System.out.println(result.getText());
System.out.println(result.isCacheHit());      // true on second call
System.out.println(result.getTokensSaved());  // input tokens avoided via cache
System.out.printf("Cost: $%.6f%n", result.getCostUsd());
```

---

### 9.3 Agent builder

`Agent.builder()` accepts the following options:

| Method | Type | Default | Description |
|--------|------|---------|-------------|
| `.provider(String)` | `"anthropic"` \| `"openai"` | required | LLM provider |
| `.model(String)` | string | `claude-sonnet-4-6` / `gpt-4o` | Model ID |
| `.apiKey(String)` | string | env var | Override `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` |
| `.immutable(String)` | string | `""` | `IMMUTABLE_CONTEXT` layer text |
| `.capability(String)` | string | `""` | `CAPABILITY_DECLARATION` layer text |
| `.tool(ToolDefinition)` | — | none | Register a tool (repeatable) |
| `.outputContract(OutputContract)` | — | none | Attach an output contract |
| `.backend(SessionBackend)` | — | `MemoryBackend` | Session persistence backend |

---

### 9.4 Session API

Use sessions for multi-turn conversations. `Session` implements `AutoCloseable`.

```java
// Multi-turn session with initial session variables
try (Session session = agent.session(Map.of("user_id", "U-99"))) {
    RunResult r1 = session.run("Qualify lead L-001");
    RunResult r2 = session.run("Explain the risk score");

    // Access metrics after the session
    SessionMetrics m = session.getMetrics();
    System.out.println(m.getTotalTokensSaved());
    System.out.println(m.getCacheHits());
    System.out.println(m.getTotalCostUsd());
}
```

`session.run(String userMessage)` returns a `RunResult` for every turn.

---

### 9.5 RunResult reference

| Method | Type | Description |
|--------|------|-------------|
| `getText()` | `String` | LLM response text |
| `isValidated()` | `boolean` | OutputContract check passed |
| `getViolations()` | `List<Violation>` | Contract violations (empty = clean) |
| `getParsed()` | `Object` | Deserialized JSON object (Map or custom class) |
| `isCacheHit()` | `boolean` | Permanent layers were served from cache |
| `isCacheWrite()` | `boolean` | This call wrote to the prompt cache |
| `getTokensSaved()` | `int` | `cache_read_tokens` avoided on this turn |
| `getInputTokens()` | `int` | Billed input tokens |
| `getOutputTokens()` | `int` | Billed output tokens |
| `getCacheWriteTokens()` | `int` | Tokens written to cache |
| `getToolCalls()` | `List<ToolCallRecord>` | All tool invocations this turn |
| `getSessionId()` | `String` | Session identifier |
| `getTurnNumber()` | `int` | 1-based turn index |
| `getLatencyMs()` | `int` | Wall-clock latency for this turn |
| `getCostUsd()` | `double` | Estimated USD cost for this turn |

---

### 9.6 Tools

#### Defining a tool

```java
ToolDefinition calc = ToolDefinition.builder()
    .name("credit_score")
    .description("Calculate credit eligibility score for a lead")
    .param("lead_id",      ToolDefinition.ParamType.STRING,  "Lead ID",       true)
    .param("loan_amount",  ToolDefinition.ParamType.NUMBER,  "Loan amount",   true)
    .param("include_risk", ToolDefinition.ParamType.BOOLEAN, "Include risk",  false)
    .build();
```

Supported `ParamType` values: `STRING`, `NUMBER`, `BOOLEAN`, `OBJECT`, `ARRAY`.

#### Registering a handler

```java
ToolRegistry registry = new ToolRegistry();
registry.register(calc, args -> {
    String id     = (String)  args.get("lead_id");
    double amount = (Double)  args.get("loan_amount");
    // ... compute score
    return Map.of("score", 720, "eligible", true);
});

// Pass to Agent builder
Agent agent = Agent.builder()
    .tool(calc)
    // ...
    .build();
```

The runtime calls the handler automatically when the model issues a tool call
and injects the result back into the conversation.

---

### 9.7 OutputContract

Declares the expected response structure and enforces it at runtime.

```java
OutputContract contract = OutputContract.builder()
    .requiredFields("decision", "score", "risk_category", "lead_id", "rationale")
    .failureMode("BLOCKED:")
    .failureMode("insufficient_data")
    .formatHint("JSON with keys: decision, score, risk_category, lead_id, rationale")
    .validator(json -> {
        String decision = (String) json.get("decision");
        var allowed = Set.of("QUALIFIED", "NOT_QUALIFIED", "REVIEW_REQUIRED");
        if (!allowed.contains(decision)) {
            return List.of(new Violation(
                "OUTPUT_CONTRACT: invalid decision value: " + decision,
                "OUTPUT_CONTRACT", Violation.Severity.ERROR
            ));
        }
        return List.of();
    })
    .build();

Agent agent = Agent.builder()
    // ...
    .outputContract(contract)
    .build();
```

When the response fails validation, `result.isValidated()` is `false` and
`result.getViolations()` contains the details.

---

### 9.8 CapabilityEnforcer

Scans model output for DENY/REQUIRE violations after each turn. It is
constructed automatically from the `.capability(...)` text you pass to the
Agent builder. You can also use it standalone:

```java
CapabilityEnforcer enforcer = new CapabilityEnforcer(
    "DENY: logging PII\nREQUIRE: risk category in every result"
);

List<Violation> violations = enforcer.scanOutput(responseText);
for (Violation v : violations) {
    System.out.println(v.getSeverity() + ": " + v.getMessage());
}
```

Built-in heuristics detect PII patterns (SSN, credit card numbers, email
addresses), bulk-export keywords, and unsafe float arithmetic on monetary
values.

---

### 9.9 Session backends

| Backend | Class | Use case |
|---------|-------|----------|
| In-memory (default) | `MemoryBackend` | Development, single-JVM |
| SQLite | `SQLiteBackend` | Single-server persistence across restarts |

```java
// SQLite backend — persists sessions to a local file
import io.ics.runtime.backends.SQLiteBackend;

Agent agent = Agent.builder()
    .provider("anthropic")
    .immutable("...")
    .backend(new SQLiteBackend("sessions.db"))
    .build();
```

---

### 9.10 SessionMetrics

Available from `session.getMetrics()` after one or more `run()` calls:

```java
SessionMetrics m = session.getMetrics();

m.getTurns()            // int   — number of completed turns
m.getCacheHits()        // int   — turns where cache was read
m.getCacheWrites()      // int   — turns that wrote to cache
m.getTotalTokensSaved() // int   — cumulative cache_read_tokens
m.getTotalInputTokens() // int   — cumulative billed input tokens
m.getTotalOutputTokens()// int   — cumulative billed output tokens
m.getTotalCostUsd()     // double — cumulative estimated cost
m.getViolationCount()   // int   — total contract violations across turns
```

---

### 9.11 Complete example

```java
import io.ics.runtime.*;
import io.ics.runtime.contracts.*;
import io.ics.runtime.tools.*;
import java.util.*;

public class BFSIExample {

    public static void main(String[] args) throws Exception {

        // Tool definitions
        ToolDefinition crmLookup = ToolDefinition.builder()
            .name("crm_lookup")
            .description("Look up a lead by ID")
            .param("lead_id", ToolDefinition.ParamType.STRING, "Lead ID", true)
            .build();

        ToolDefinition creditScore = ToolDefinition.builder()
            .name("credit_score")
            .description("Calculate credit eligibility score")
            .param("lead_id",     ToolDefinition.ParamType.STRING, "Lead ID",     true)
            .param("loan_amount", ToolDefinition.ParamType.NUMBER, "Loan amount", true)
            .build();

        // Tool handlers
        ToolRegistry registry = new ToolRegistry();
        registry.register(crmLookup,   a -> Map.of("name", "Acme Corp", "status", "prospect"));
        registry.register(creditScore, a -> Map.of("score", 720, "eligible", true));

        // Output contract
        OutputContract contract = OutputContract.builder()
            .requiredFields("decision", "score", "risk_category", "lead_id", "rationale")
            .failureMode("BLOCKED:")
            .formatHint("JSON")
            .build();

        // Agent
        Agent agent = Agent.builder()
            .provider("anthropic")
            .model("claude-sonnet-4-6")
            .immutable("""
                You are a BFSI lead qualification assistant.
                Domain: SME lending, risk scoring, CRM integration.
                """)
            .capability("""
                DENY: logging PII
                DENY: bulk export of customer records
                REQUIRE: risk category in every qualification result
                """)
            .tool(crmLookup)
            .tool(creditScore)
            .outputContract(contract)
            .build();

        // Multi-turn session
        try (Session session = agent.session(Map.of("region", "APAC"))) {
            RunResult r1 = session.run("Qualify lead L-001 for a $50,000 loan");
            System.out.println(r1.getText());
            System.out.printf("Cache hit: %b | Tokens saved: %d | Cost: $%.6f%n",
                r1.isCacheHit(), r1.getTokensSaved(), r1.getCostUsd());

            RunResult r2 = session.run("What is the main risk factor?");
            System.out.println(r2.getText());

            // Session summary
            SessionMetrics m = session.getMetrics();
            System.out.printf("Session total — turns: %d, saved: %d tokens, cost: $%.4f%n",
                m.getTurns(), m.getTotalTokensSaved(), m.getTotalCostUsd());
        }
    }
}
```

---

### 9.12 Web demo (Java)

A Javalin 6 web app that runs the BFSI demo on port **7862**, parallel to the
Python web demo on port 7860.

```bash
# Run with a pre-built fat-jar (Java 17 only, no Maven needed)
cd ics-runtime/java_web_demo

# Anthropic
ANTHROPIC_API_KEY=sk-ant-... java -jar target/ics-runtime-web-demo.jar

# OpenAI
OPENAI_API_KEY=sk-... java -jar target/ics-runtime-web-demo.jar openai
```

Open `http://localhost:7862`

**API endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Web UI |
| `GET` | `/api/status` | Provider + API key status |
| `GET` | `/api/leads` | List mock CRM leads |
| `POST` | `/api/qualify` | Run lead qualification (blocking) |
| `GET` | `/api/qualify/stream` | Run qualification (SSE stream) |
| `GET` | `/api/metrics` | Session metrics |
| `GET` | `/api/logs` | Live log stream (SSE) |

---

## Further reading

| Document | Purpose |
|---|---|
| `ICS-v0.1.md` | Full specification |
| `RATIONALE.md` | Design decisions and interpretation guide |
| `APPENDIX-A.md` | Annotated worked examples |
| `APPENDIX-B.md` | Infrastructure and schema migration examples |
| `APPENDIX-C.md` | Quality benchmark scenario catalogue |
| `experiments.md` | Empirical token-savings evidence |
| `ics-runtime/README.md` | Runtime architecture and feature comparison |
