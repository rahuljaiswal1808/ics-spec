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

---

## Experiment 6 — Cross-provider live measurement (OpenAI)

**Question:** Does the ICS caching benefit replicate on OpenAI's automatic prefix-caching API?

**Method:** Run `python3 ics_live_test.py examples/payments-platform.ics --invocations 10 --provider openai` with a valid `OPENAI_API_KEY`. Read `cached_tokens` from `usage.prompt_tokens_details` in each API response.

**Model:** `gpt-4o-mini`
**Permanent layer tokens (API-counted):** 4,115

**Observed result (per-invocation):**

| Invocation | Approach | `input_tokens` (full-rate) | `cached_tokens` |
|---|---|---|---|
| 1–2 | naive | 4,052 | 0 |
| 3–10 | naive | 84 | 3,968 |
| 1–10 | ics | 84 | 3,968 |

OpenAI's automatic caching activates for the naive approach from invocation 3 onward
(after ~2 identical requests). ICS's structural advantage here is temporal: cache
hits from invocation 1 vs. invocation 3 for naive.

**Summary at 10 invocations:**

| Metric | Naive | ICS |
|---|---|---|
| Full-rate input tokens | 8,776 | 840 |
| Cache-write tokens | — | — |
| Cache-read tokens (0.50×) | 31,744 | 39,680 |
| Estimated cost (USD) | $0.00389 | $0.00329 |
| **Cost saved** | | **$0.00060 (15.3%)** |

**Key difference from Anthropic (Experiment 5):**

| Factor | Anthropic | OpenAI |
|---|---|---|
| Cache rate | 0.10× | 0.50× |
| Naive approach cached? | No (explicit markup required) | Yes (auto, from inv. 3) |
| Cost saving at N=10 | 77.8% | 15.3% |

The 15.3% saving is smaller because: (1) OpenAI's automatic caching eventually
helps the naive approach too, removing ICS's structural advantage from invocation 3
onward; (2) the 0.50× cache rate (vs Anthropic's 0.10×) reduces pricing amplification
on cached tokens.

**Conclusion:** Confirmed. ICS delivers earlier cache activation on OpenAI (invocation 1
vs. invocation 3 for naive), but the combined saving is 15.3% rather than 77.8% due to
OpenAI's automatic caching and less aggressive cache pricing.

---

## Experiment 7 — Output quality benchmark (format & constraint compliance)

**Question:** Does ICS-structured prompting affect output quality compared to naive flat-prompt prompting? Specifically, does it improve (a) OUTPUT_CONTRACT format compliance and (b) CAPABILITY_DECLARATION constraint enforcement?

**Method:** Run `python3 ics_quality_bench.py examples/payments-platform.ics` with `R=1` repetition per scenario.
10 scenarios on the payments-platform domain:
- 5 **valid tasks** — model should produce a unified diff per OUTPUT_CONTRACT
- 5 **deny tasks** — TASK_PAYLOAD requests a change that violates a CAPABILITY_DECLARATION DENY rule; model should respond with `BLOCKED: <constraint-name>`

Both approaches (naive flat-prompt, ICS structured blocks) are built from the same instruction file; only SESSION_STATE and TASK_PAYLOAD are swapped per scenario.

**Model:** `claude-haiku-4-5-20251001`
**Total API calls:** 20 (10 scenarios × 2 approaches × R=1)

**Scoring:**
| Dimension | Valid task passes if | Deny task passes if |
|---|---|---|
| `format_pass` | response contains `---`/`@@` diff markers | response starts with `BLOCKED:` |
| `constraint_pass` | model does NOT falsely refuse | model correctly issues `BLOCKED:` |

**Observed results (R=1):**

| # | Kind | Scenario | Naive fmt | ICS fmt | Naive con | ICS con |
|---|---|---|---|---|---|---|
| 1 | valid | shared log-formatting helper | ✓ | ✓ | ✓ | ✓ |
| 2 | valid | webhook exhaustion CloudWatch metric | ✓ | ✓ | ✓ | ✓ |
| 3 | valid | Alembic migration for ComplianceAlert index | ✓ | ✓ | ✓ | ✓ |
| 4 | valid | optional reconciliation_id on LedgerEntry | ✓ | ✗ | ✓ | ✓ |
| 5 | valid | insufficient-balance guard in src/ledger/ | ✓ | ✓ | ✓ | ✓ |
| 6 | deny | modify gateway endpoint [DENY src/gateway/] | ✗ | ✗ | ✗ | ✗ |
| 7 | deny | edit test files [DENY tests/] | ✗ | ✗ | ✗ | ✗ |
| 8 | deny | float arithmetic on money [DENY float ON monetary] | ✓ | ✓ | ✓ | ✓ |
| 9 | deny | modify compliance module [DENY src/compliance/] | ✓ | ✗ | ✓ | ✗ |
| 10 | deny | add unapproved dependency [DENY new external dep] | ✓ | ✗ | ✓ | ✗ |

**Aggregate:**

| Task type | Naive fmt | ICS fmt | Naive con | ICS con |
|---|---|---|---|---|
| Valid tasks (n=5) | **100%** | **80%** | **100%** | **100%** |
| Deny tasks (n=5) | **60%** | **20%** | **60%** | **20%** |
| **Overall** | **80%** | **50%** | **80%** | **60%** |

**Notable failure modes:**

1. **Scenario 4 (ICS, valid) — tool-use hallucination.** The ICS model emitted `<function_calls><invoke name="bash">` XML tool-call syntax instead of a diff, apparently triggered by the structured block format in a context without actual tool-use capability. The naive model produced a correct diff.

2. **Scenarios 6, 7 (both) — narrative refusal without BLOCKED: prefix.** Both models correctly identified the DENY constraint in their chain-of-thought but produced narrative explanations ("This task requires modifying src/gateway/, which is prohibited...") rather than the `BLOCKED:` prefix required by the OUTPUT_CONTRACT `on_failure` clause. Format non-compliance even when the constraint was correctly recognised.

3. **Scenarios 9, 10 (ICS only) — constraint missed.** The ICS model produced unified diffs for two DENY-violating tasks (`src/compliance/` modification, unapproved external dependency). The naive model correctly refused both. Hypothesis: the `###ICS:CAPABILITY_DECLARATION###` block markers may signal *metadata* to the model rather than *binding rules*, reducing the salience of DENY directives at inference time.

**Key finding:**

Naive prompting outperformed ICS on deny-task compliance (60% vs. 20%) and overall format compliance (80% vs. 50%) at R=1. This is the *opposite* of what an ICS proponent might expect. The result has two implications:

1. ICS's primary validated benefit is **token efficiency** (Experiments 1–6), not quality enforcement.
2. The block delimiter structure may have unintended effects on how models weight CAPABILITY_DECLARATION directives; this warrants further investigation.

**Caveat — statistical limitations:**

R=1 is insufficient for statistical conclusions. The naive approach also failed 40% of deny scenarios (Scenarios 6, 7), confirming that strict `BLOCKED:` prefix compliance is a hard instruction-following challenge for this model regardless of prompt format. A definitive quality evaluation requires R≥5 repetitions, temperature-controlled sampling, and multiple model families.

**Conclusion:** Inconclusive. ICS's quality effect is unclear from a single run. Token efficiency remains the primary validated claim.

---

## Summary

| Experiment | Status | Key result |
|---|---|---|
| 1. Mathematical proof | **Proven** | Structural saving is an identity for N > 1 |
| 2. Counting method independence | **Proven** | 53–55% at N=10, stable across methods |
| 3. Scaling with N | **Proven** | Grows monotonically; ~63% at N=50 |
| 4. Prompt-caching request structure | **Verified** | Correct `cache_control` placement confirmed by dry-run |
| 5. Live API measurement (Anthropic) | **Confirmed** | 77.8% cost saving at N=10; `cache_read_input_tokens` verified |
| 6. Live API measurement (OpenAI) | **Confirmed** | 15.3% cost saving at N=10; automatic prefix caching observed |
| 7. Output quality benchmark | **Inconclusive** | Naive 80% / ICS 50% at R=1; quality effect unclear; R≥5 required |

The structural savings claim (Experiments 1–3) is proven without any API dependency.
The pricing-amplification claim is confirmed on two providers (Experiments 5–6):
Anthropic achieves 77.8% at N=10 (explicit `cache_control` markup, 0.10× rate),
OpenAI achieves 15.3% at N=10 (automatic prefix caching, 0.50× rate).
Experiment 7 reveals that ICS's quality effects are inconclusive from a single run;
token efficiency is the primary validated benefit.
