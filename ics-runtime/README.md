# ICS Runtime

Production execution layer for [ICS (Instruction Contract Specification)](https://github.com/rahuljaiswal1808/ics-spec) prompts.

ICS Runtime sits **on top of** the ICS spec and handles everything the spec intentionally leaves out:
LLM calling, prompt caching, tool contracts, session persistence, capability enforcement, and observability.

Available in **Python** and **Java**.

---

## Python Runtime

### Quick Start

```python
from ics_runtime import Agent, tool, OutputContract
from pydantic import BaseModel

@tool(name="crm.lookup")
def lookup_lead(lead_id: str) -> dict:
    return {"name": "Acme Corp", "status": "prospect"}

class LeadSummary(BaseModel):
    company: str
    recommendation: str

agent = Agent(
    provider="anthropic",
    system="You are a BFSI lead qualification assistant.",
    tools=[lookup_lead],
    output_contract=OutputContract(schema=LeadSummary),
)

async def main():
    session = agent.session(session_id="demo-001")
    result = await session.run("Qualify lead L-42")
    print(result.text)
    print(result.cache_hit)        # True on second call
    print(result.tokens_saved)     # Input tokens avoided via cache
```

### Architecture

```
ics_runtime/
├── core/
│   ├── agent.py          # Agent — top-level entry point
│   ├── session.py        # Session — per-conversation state + run()
│   └── result.py         # RunResult — structured output
├── providers/
│   ├── base.py           # ProviderBase — abstract LLM interface
│   ├── anthropic.py      # AnthropicProvider — cache_control, tool_use
│   └── openai.py         # OpenAIProvider — prefix caching, function_calling
├── prompt/
│   └── builder.py        # ICSPromptBuilder — assembles ICS layers
├── tools/
│   ├── decorator.py      # @tool decorator + ToolSchema
│   └── registry.py       # ToolRegistry — lookup, enforcement
├── contracts/
│   ├── output.py         # OutputContract — Pydantic schema validation
│   └── capability.py     # CapabilityEnforcer — DENY/REQUIRE scanning
├── session_backends/
│   ├── base.py           # SessionBackend ABC
│   ├── memory.py         # MemoryBackend (default)
│   └── redis.py          # RedisBackend
└── observability/
    └── metrics.py        # SessionMetrics — token savings, cache hits, violations
```

### Web Demo (Python)

```bash
cd web_demo
pip install -r requirements.txt
ANTHROPIC_API_KEY=sk-ant-... python app.py
# Open http://localhost:7860
```

---

## Java Runtime

Full Java port of the ICS Runtime — same architecture, same ICS contract layers, compatible with both Anthropic and OpenAI providers.

**Requirements:** Java 17+, Maven 3.8+

### Quick Start

```java
import io.ics.runtime.*;
import io.ics.runtime.tools.*;
import io.ics.runtime.contracts.*;

// Define a tool
ToolDefinition lookup = ToolDefinition.builder()
    .name("crm_lookup")
    .description("Look up a lead by ID")
    .param("lead_id", ToolDefinition.ParamType.STRING, "Lead identifier", true)
    .build();

ToolRegistry registry = new ToolRegistry();
registry.register(lookup, args -> {
    String id = (String) args.get("lead_id");
    return Map.of("name", "Acme Corp", "status", "prospect");
});

// Build agent
Agent agent = Agent.builder()
    .provider("anthropic")          // or "openai"
    .model("claude-sonnet-4-6")
    .immutable("You are a BFSI lead qualification assistant.")
    .tools(registry)
    .build();

// Run a session
Session session = new Session(agent);
RunResult result = session.run("Qualify lead L-42");
System.out.println(result.getText());
System.out.println(result.isCacheHit());      // true on second call
System.out.println(result.getTokensSaved());  // tokens avoided via cache
```

### Architecture

```
java/
└── src/main/java/io/ics/runtime/
    ├── Agent.java                    # Agent — top-level entry point
    ├── Session.java                  # Session — per-conversation state + run()
    ├── RunResult.java                # RunResult — structured output
    ├── providers/
    │   ├── ProviderBase.java         # Abstract LLM interface
    │   ├── AnthropicProvider.java    # Anthropic — cache_control, tool_use
    │   └── OpenAIProvider.java       # OpenAI — prefix caching, function_calling
    ├── prompt/
    │   └── PromptBuilder.java        # Assembles ICS layers into system blocks
    ├── tools/
    │   ├── ToolDefinition.java       # Tool schema (name, params, description)
    │   ├── ToolRegistry.java         # Tool lookup + handler dispatch
    │   └── ToolDeniedException.java
    ├── contracts/
    │   ├── OutputContract.java       # Response schema validation
    │   └── CapabilityEnforcer.java   # DENY/REQUIRE scanning
    ├── backends/
    │   ├── SessionBackend.java       # Interface
    │   ├── MemoryBackend.java        # Default in-memory store
    │   └── SQLiteBackend.java        # SQLite persistence
    └── observability/
        └── SessionMetrics.java       # Token savings, cache hits, cost tracking
```

### Build

```bash
# 1. Build and install the library
cd ics-runtime/java
mvn install -DskipTests

# 2. Build the web demo fat-jar
cd ../java_web_demo
mvn package -DskipTests
```

### Web Demo (Java)

Javalin 6 web app on port **7862** — parallel to the Python web demo.

```bash
cd java_web_demo

# Anthropic (Claude)
ANTHROPIC_API_KEY=sk-ant-... java -jar target/ics-runtime-web-demo.jar

# OpenAI
OPENAI_API_KEY=sk-... java -jar target/ics-runtime-web-demo.jar openai
```

Open `http://localhost:7862`

> The pre-built fat-jar (`target/ics-runtime-web-demo.jar`) is included in the repo — only **Java 17** is required to run it, no Maven needed.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Web UI |
| `GET` | `/api/status` | Provider + key status |
| `GET` | `/api/leads` | List mock CRM leads |
| `POST` | `/api/qualify` | Run qualification (blocking) |
| `GET` | `/api/qualify/stream` | Run qualification (SSE stream) |
| `GET` | `/api/metrics` | Session metrics |
| `GET` | `/api/logs` | Live log stream (SSE) |

---

## Feature Comparison

| Feature | Python | Java |
|---------|--------|------|
| Anthropic provider | ✅ | ✅ |
| OpenAI provider | ✅ | ✅ |
| Prompt caching | ✅ | ✅ |
| Tool calling loop | ✅ | ✅ |
| CapabilityEnforcer | ✅ | ✅ |
| OutputContract | ✅ | ✅ |
| Memory backend | ✅ | ✅ |
| Redis / SQLite backend | Redis ✅ | SQLite ✅ |
| SessionMetrics | ✅ | ✅ |
| Web demo | ✅ port 7860 | ✅ port 7862 |

---

## Build Phases

| Phase | Deliverable | Exit Criterion |
|-------|-------------|----------------|
| 1 | Agent + Session + AnthropicProvider | `cache_hit=True` on 2nd call |
| 2 | @tool + ToolRegistry + tool_use loop | Tool called, result used in reply |
| 3 | CapabilityEnforcer + OutputContract | DENY blocked, schema validated |
| 4 | OpenAIProvider | Same demo works with provider="openai" |
| 5 | BFSI demo app | End-to-end lead qualification with CRM/credit tools |
| 6 | Java runtime port | Full Java library + Javalin web demo on port 7862 |
