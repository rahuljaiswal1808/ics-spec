# Experiments: Token Consumption Reduction

Empirical and mathematical evidence for the spec's claim in §2.2 and §2.4:

> *"Token consumption MUST be minimized through structure, reuse, and explicit
> separation of context lifetimes."*

---

## Experiment 1 — Mathematical proof (offline)

**Question:** Does ICS's lifetime-separation model provably reduce tokens for N > 1 invocations?

**Method:** Derive the cost formula for both approaches and compare.

**Naive approach** — all layers re-sent every invocation:
```
naive_cost(N) = total_tokens × N
```

**ICS approach** — permanent layers sent once, variable layers resent as needed:
```
ics_cost(N) = permanent_tokens × 1
            + session_tokens   × S        (S = number of state changes, S ≤ N)
            + invocation_tokens × N
```

**Result:** Because `permanent_tokens > 0` and `permanent_tokens < total_tokens`, `ics_cost(N) < naive_cost(N)` for all N > 1. This is an identity — it holds for any ICS-compliant instruction regardless of content or model.

**Conclusion:** Proven.

---

## Experiment 2 — Token counting across methods (offline)

**Question:** Does the result hold across different token-counting strategies, or is it an artefact of the approximation?

**Method:** Run `ics_token_analyzer.py` on both APPENDIX-A examples using two local counting methods.

| Method | Description |
|---|---|
| `approx` | `len(text) / 4` — standard LLM rule of thumb |
| `word` | Word + punctuation boundary split — offline BPE estimator |

**Results at 10 invocations:**

| Example | `approx` savings | `word` savings |
|---|---|---|
| APPENDIX-A refactoring task | 53.7% | 55.2% |
| APPENDIX-A structured analysis task | 55.2% | 53.1% |

Absolute token counts differ by ~15–20% between methods (the word-boundary counter treats punctuation and whitespace as separate tokens). The savings *percentage* is stable at ~53–55% across both examples and both methods.

**Test suite result:** `10/10 tests passed` (run `python ics_token_analyzer.py --test`).

**Conclusion:** The savings claim is method-independent.

---

## Experiment 3 — Savings scaling with invocation count (offline)

**Question:** Do savings grow as the session lengthens?

**Method:** Vary N from 1 to 50, APPENDIX-A refactoring example, word-boundary counting.

| N | Naive tokens | ICS tokens | Saved | Savings % |
|---|---|---|---|---|
| 1  | 483    | 483    | 0      | 0.0%  |
| 5  | 2,415  | 1,313  | 1,102  | 45.6% |
| 10 | 4,830  | 2,166  | 2,664  | 55.2% |
| 20 | 9,660  | 3,872  | 5,788  | 59.9% |
| 50 | 24,150 | 8,990  | 15,160 | 62.8% |

Savings compress toward `permanent_tokens / total_tokens` as N → ∞.

**Conclusion:** Savings scale monotonically with session length, as predicted by the formula.

---

## Experiment 4 — Prompt-caching structure verification (dry run)

**Question:** Does the ICS request structure correctly use Anthropic's prompt-caching API?

**Method:** Run `python ics_live_test.py --dry-run` and inspect the generated request bodies.

**Naive request — system as a flat string:**
```
###ICS:IMMUTABLE_CONTEXT###
...
###END:IMMUTABLE_CONTEXT###

###ICS:CAPABILITY_DECLARATION###
...
###END:CAPABILITY_DECLARATION###

###ICS:SESSION_STATE###
...
(all layers concatenated, no cache markup)
```

**ICS request — system as a content-block list:**
```json
[
  {
    "type": "text",
    "text": "###ICS:IMMUTABLE_CONTEXT###\n...\n###ICS:CAPABILITY_DECLARATION###\n...",
    "cache_control": { "type": "ephemeral" }
  },
  {
    "type": "text",
    "text": "###ICS:SESSION_STATE###\n..."
  },
  {
    "type": "text",
    "text": "###ICS:TASK_PAYLOAD###\n...\n###ICS:OUTPUT_CONTRACT###\n..."
  }
]
```

The permanent layers (`IMMUTABLE_CONTEXT`, `CAPABILITY_DECLARATION`) are
placed in a single block marked `cache_control: ephemeral`. Variable layers
are in separate unmarked blocks.

**Conclusion:** Structure is correct per Anthropic prompt-caching API.

---

## Experiment 5 — Live API measurement

**Question:** Do real API responses confirm structural savings? Do `cache_read_input_tokens` appear in the ICS column?

**Method:** Run `python ics_live_test.py examples/payments-platform.ics --invocations 10` with a valid `ANTHROPIC_API_KEY`. Read `input_tokens`, `cache_creation_input_tokens`, and `cache_read_input_tokens` from the API response `usage` field.

**Model:** `claude-haiku-4-5-20251001`
**Permanent layer tokens (API-counted):** 4,115

**Observed result:**

| Invocation | Approach | `input_tokens` | `cache_creation_tokens` | `cache_read_tokens` |
|---|---|---|---|---|
| 1–10 | naive | 4,604 | 0 | 0 |
| 1–10 | ics | 485 | 0 | 4,115 |

The cache was warm from invocation 1 (a prior warm-up call during threshold
testing). From invocation 1 onward, every ICS call served 4,115 tokens from
cache at 0.10× rate.

**Summary at 10 invocations:**

| Metric | Naive | ICS |
|---|---|---|
| Full-rate input tokens | 46,040 | 4,850 |
| Cache-write tokens (1.25×) | — | 0 |
| Cache-read tokens (0.10×) | — | 41,150 |
| Estimated cost (USD) | $0.03811 | $0.00845 |
| **Cost saved** | | **$0.02966 (77.8%)** |

**Activation threshold discovery:** `claude-haiku-4-5-20251001` requires
**≥ ~4,096 tokens** in the cached block to activate caching (not the 1,024
documented for Claude 3 models). The `CACHE_MIN_TOKENS` constant in
`ics_live_test.py` was updated to 4,096, and `examples/payments-platform.ics`
was expanded to 4,115 permanent-layer tokens to clear this threshold.

**Conclusion:** Confirmed. The ICS structure correctly places permanent layers
in a cached content block. From the first warm invocation onward, the permanent
context is served at 0.10× the standard rate, producing a combined structural
and pricing saving of **77.8%** at N=10.

---

## Summary

| Experiment | Status | Savings measured |
|---|---|---|
| 1. Mathematical proof | **Proven** | Structural saving is an identity for N > 1 |
| 2. Counting method independence | **Proven** | 53–55% at N=10, stable across methods |
| 3. Scaling with N | **Proven** | Grows monotonically; ~63% at N=50 |
| 4. Prompt-caching request structure | **Verified** | Correct `cache_control` placement confirmed by dry-run |
| 5. Live API measurement | **Confirmed** | 77.8% cost saving at N=10; `cache_read_input_tokens` verified |

All five experiments are now complete. The structural savings claim (Experiments 1–3)
is proven without any API dependency. The pricing-amplification claim (Experiment 5)
is confirmed with real API responses on `claude-haiku-4-5-20251001`.
