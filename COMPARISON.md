# How does ICS compare to existing tools?

This document addresses the three most common objections: "LangChain already does this", "RAG solves this", and "ChatGPT memory handles this". The short answer to all three is: they solve different problems, and all three compose with ICS rather than replace it.

---

## ICS vs LangChain / prompt frameworks

**What LangChain gives you:** primitives. `PromptTemplate`, `ChatPromptTemplate`, `MessagesPlaceholder` let you build prompts programmatically. You *can* structure prompts using LangChain, but there is no enforced layer ordering, no linter, no validator, and no machine-verifiable constraint system. LangChain is a library; it imposes no discipline on what you build with it.

**What LangChain does not do:** by default, LangChain sends everything as a flat string to the API. There are no `cache_control` markers, no explicit cache boundary, and no concept of "this block is stable enough to cache". Prompt caching requires explicit structural decisions that LangChain does not make for you.

**What ICS adds:** mandatory layer ordering, machine-verifiable constraints (`ALLOW`/`DENY`/`REQUIRE`), declared output contracts, and a toolchain (validate, lint, diff, CI report) that treats your prompts like code rather than text.

**How they compose:** LangChain is the language. ICS is the type system, linter, and style guide. You can implement an ICS-compliant document using LangChain primitives — build the permanent block with `ChatPromptTemplate`, add `cache_control` to the stable block, use `ics-validate` in CI. They are not alternatives; they operate at different layers of the stack.

---

## ICS vs RAG

**What RAG solves:** *what the model knows*. Retrieval-Augmented Generation retrieves relevant content at query time and injects it into the prompt. If you need the model to answer questions about documents it was not trained on, RAG is the right tool.

**What RAG does not solve:** *recomputation cost*. Retrieved chunks are injected as text and fully reprocessed on every call, paying the full input token rate regardless of how recently they were seen. A RAG pipeline that retrieves the same foundational domain facts on every call is paying to reprocess stable knowledge on every invocation.

**What ICS solves:** *how stable knowledge is structured* so it can be cached at the KV layer. IMMUTABLE_CONTEXT and CAPABILITY_DECLARATION hold domain facts and constraints that never change call-to-call. Once written to the cache, they are served at roughly 0.10× the input token rate on every subsequent call.

**How they compose:** RAG output belongs in `SESSION_STATE` or `TASK_PAYLOAD` — it is dynamic, per-call content that belongs in the variable layers. Domain facts and constraints belong in `IMMUTABLE_CONTEXT` and `CAPABILITY_DECLARATION` — they are stable and cacheable. The combined architecture is:

```
IMMUTABLE_CONTEXT      ← stable domain model, cached (ICS)
CAPABILITY_DECLARATION ← rules and constraints, cached (ICS)
SESSION_STATE          ← RAG-retrieved facts for this session
TASK_PAYLOAD           ← RAG-retrieved context for this query
OUTPUT_CONTRACT        ← declared output shape
```

Result: stable context cached via ICS + dynamic context retrieved via RAG = lowest possible token cost per call.

---

## ICS vs ChatGPT memory / cross-session persistence

**What ChatGPT memory does:** stores recalled facts between conversations and injects them as text at the start of new sessions. This solves *recall* — the model remembers things about you across sessions.

**What it does not solve:** *recomputation cost*. Recalled facts are injected as plain text and fully reprocessed on every new conversation, paying the full input token rate. Persistence is free; recomputation is not.

**What ICS solves:** *compute cost* for stable context during an active session. The 5-minute KV cache TTL means stable layers are written once and read back at ~0.10× cost on every call within the cache window. For a 10-call session, this cuts the cost of the stable context by roughly 70% (empirically: 77.8% on the payments-platform benchmark, N=10).

**The distinction:** ChatGPT memory solves *what the model remembers*. ICS solves *how much it costs to use what it knows*. They address different parts of the problem:

- Cross-session recall → memory / persistence systems
- Intra-session compute cost → ICS + KV cache

---

## Comparison table

| Concern | LangChain | RAG | ChatGPT Memory | ICS |
|---|---|---|---|---|
| Structured prompt authoring | Partial | No | No | Yes |
| KV cache optimisation | No | No | No | Yes |
| Machine-verifiable constraints | No | No | No | Yes |
| Declared output contract | No | No | No | Yes |
| CI/lint toolchain | No | No | No | Yes |
| Dynamic knowledge retrieval | Via tools | Yes | Partial | No (by design) |
| Cross-session persistence | No | No | Yes | No (by design) |
| Composes with ICS | Yes | Yes | Partial | — |

**"No (by design)"** means the capability is deliberately out of scope — ICS is a prompt structure specification, not a memory system, retrieval engine, or agent framework. The absence is intentional.

---

*For empirical data on the token-savings claim, see [`experiments.md`](experiments.md). For the full specification, see [`ICS-v0.1.md`](ICS-v0.1.md).*
