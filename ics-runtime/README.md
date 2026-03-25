# ICS Runtime

Production execution layer for [ICS (Intelligent Context Structuring)](https://github.com/rahuljaiswal1808/ics-spec) prompts.

ICS Runtime sits **on top of** the ICS spec and handles everything the spec intentionally leaves out:
LLM calling, prompt caching, tool contracts, session persistence, capability enforcement, and observability.

## Quick Start

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

## Architecture

```
ics_runtime/
├── core/
│   ├── agent.py          # Agent — top-level entry point
│   ├── session.py        # Session — per-conversation state + run()
│   └── result.py         # RunResult — structured output
├── providers/
│   ├── base.py           # ProviderBase — abstract LLM interface
│   └── anthropic.py      # AnthropicProvider — cache_control, tool_use
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

## Build Phases

| Phase | Deliverable | Exit Criterion |
|-------|-------------|----------------|
| 1 | Agent + Session + AnthropicProvider | `cache_hit=True` on 2nd call |
| 2 | @tool + ToolRegistry + tool_use loop | Tool called, result used in reply |
| 3 | CapabilityEnforcer + OutputContract | DENY blocked, schema validated |
| 4 | OpenAIProvider | Same demo works with provider="openai" |
| 5 | BFSI demo app | End-to-end lead qualification with CRM/credit tools |
