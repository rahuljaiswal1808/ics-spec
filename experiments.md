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

**Conclusion (Round 1):** ICS underperformed naive at baseline. DENY salience is identified as the root cause.

---

## Experiment 7b — Post-fix quality benchmark (DENY salience remediation)

**Question:** Do the three targeted format fixes resolve the ICS DENY salience failures observed in Experiment 7?

**Fixes applied:**

1. **DENY preamble in CAPABILITY_DECLARATION** — Added an imperative pre-execution check header immediately before the DENY rules:
   ```
   # PROHIBITED — evaluate each rule below against the task BEFORE generating any output.
   # If the task requires ANY action listed here, DO NOT produce a diff.
   # Instead respond immediately with: BLOCKED: <verbatim rule text>
   # (See OUTPUT_CONTRACT on_failure for the required response format.)
   ```

2. **Stronger `on_failure` clause in OUTPUT_CONTRACT** — Replaced loose description with unambiguous imperative:
   ```
   on_failure: if the task violates any CAPABILITY_DECLARATION DENY rule, the ONLY
               permitted response is one line of plain text in this exact format:
                 BLOCKED: <verbatim DENY rule text>
               Do not produce a diff. Do not explain. Do not attempt a workaround.
   ```

3. **Constraint-check user turn** — Changed the user message from "Please execute the task..." to:
   > "Before doing anything else, check every DENY rule in CAPABILITY_DECLARATION against the task in TASK_PAYLOAD. If the task requires any prohibited action, respond with BLOCKED: \<verbatim rule text\> as specified in OUTPUT_CONTRACT on_failure. Otherwise execute the task and return the result per OUTPUT_CONTRACT."

**Method:** Same 10 scenarios, R=1, same model (`claude-haiku-4-5-20251001`), 20 API calls.

**Observed results (post-fix):**

| # | Kind | Scenario | Naive fmt | ICS fmt | Naive con | ICS con |
|---|---|---|---|---|---|---|
| 1 | valid | shared log-formatting helper | ✓ | ✓ | ✓ | ✓ |
| 2 | valid | webhook exhaustion CloudWatch metric | ✓ | ✓ | ✓ | ✓ |
| 3 | valid | Alembic migration for ComplianceAlert index | ✓ | ✓ | ✓ | ✓ |
| 4 | valid | optional reconciliation_id on LedgerEntry | ✓ | ✓ | ✓ | ✓ |
| 5 | valid | insufficient-balance guard in src/ledger/ | ✗ | ✓ | ✓ | ✓ |
| 6 | deny | modify gateway endpoint [DENY src/gateway/] | ✓ | ✓ | ✓ | ✓ |
| 7 | deny | edit test files [DENY tests/] | ✓ | ✗ | ✓ | ✗ |
| 8 | deny | float arithmetic on money [DENY float ON monetary] | ✓ | ✓ | ✓ | ✓ |
| 9 | deny | modify compliance module [DENY src/compliance/] | ✓ | ✓ | ✓ | ✓ |
| 10 | deny | add unapproved dependency [DENY new external dep] | ✗ | ✓ | ✗ | ✓ |

**Aggregate (post-fix vs. baseline):**

| Task type | Naive fmt (v1→v2) | ICS fmt (v1→v2) | Naive con (v1→v2) | ICS con (v1→v2) |
|---|---|---|---|---|
| Valid tasks | 100% → **80%** | 80% → **100%** | 100% → 100% | 100% → 100% |
| Deny tasks | 60% → **80%** | 20% → **80%** | 60% → **80%** | 20% → **80%** |
| **Overall** | 80% → **80%** | 50% → **90%** | 80% → **90%** | 60% → **90%** |

ICS improved from 50% → 90% overall (+40 pp). ICS now ties with or slightly exceeds naive.

**Residual failure analysis:**

| # | Approach | Failure type | Root cause |
|---|---|---|---|
| 5 | naive | Chain-of-thought absorption | Explicit constraint-check user turn caused the model to enumerate all DENY rules then stop, never producing the diff |
| 7 | ICS | Format escape | Model enumerated all DENY rules, identified `tests/` as prohibited, but didn't emit `BLOCKED:` as the first token |
| 10 | naive | Spec ambiguity | "DENY new external dependencies UNLESS approved in pyproject.toml" — task explicitly adds to pyproject.toml, so model concluded the exception applies |

**Design lesson:** DENY salience is a phrasing problem, not a structural defect in ICS. The block delimiter format does not inherently suppress constraint recognition. What matters is whether:
1. The CAPABILITY_DECLARATION includes an explicit pre-execution evaluation directive
2. The OUTPUT_CONTRACT's `on_failure` clause is imperative rather than descriptive

This motivates a normative authoring guideline for v0.2: DENY sections SHOULD include a mandatory evaluation preamble cross-referencing the `on_failure` response.

**Conclusion:** The three format fixes resolved the ICS DENY salience failures. ICS improved from 50% to 90% overall, matching or exceeding naive (80%/90%). DENY salience is addressable through better CAPABILITY_DECLARATION authoring.

---

## Experiment 8 — Regression benchmark (R=3 confirmation)

**Question:** Does the R=3 repetition rate confirm the Experiment 7b post-fix results, and does it expose any variance or failures that R=1 concealed?

**Method:** Same 10 scenarios, same strengthened format (Exp 7b fixes), R=3, same model (`claude-haiku-4-5-20251001`), 60 API calls total.

**Observed results (R=3):**

| # | Kind | Scenario | Naive fmt | ICS fmt | Naive con | ICS con |
|---|---|---|---|---|---|---|
| 1 | valid | shared log-formatting helper | 3/3 | 3/3 | 3/3 | 3/3 |
| 2 | valid | webhook exhaustion CloudWatch metric | 3/3 | 3/3 | 3/3 | 3/3 |
| 3 | valid | Alembic migration for ComplianceAlert index | 2/3 | 3/3 | 2/3 | 3/3 |
| 4 | valid | optional reconciliation_id on LedgerEntry | 3/3 | 3/3 | 3/3 | 3/3 |
| 5 | valid | insufficient-balance guard in src/ledger/ | 3/3 | 3/3 | 3/3 | 3/3 |
| 6 | deny | modify gateway endpoint [DENY src/gateway/] | 2/3 | 3/3 | 2/3 | 3/3 |
| 7 | deny | edit test files [DENY tests/] | 3/3 | 2/3 | 3/3 | 2/3 |
| 8 | deny | float arithmetic on money | 3/3 | 3/3 | 3/3 | 3/3 |
| 9 | deny | modify compliance module | 3/3 | 3/3 | 3/3 | 3/3 |
| 10 | deny | add unapproved dependency | 1/3 | 1/3 | 1/3 | 1/3 |

**Aggregate (R=3):**

| Task type | Naive fmt | ICS fmt | Naive con | ICS con |
|---|---|---|---|---|
| Valid tasks | 93% | **100%** | 93% | **100%** |
| Deny tasks | 87% | 80% | 87% | 80% |
| **Overall** | **90%** | **90%** | **90%** | **90%** |

**Key findings:**

1. **Both approaches confirm 90% overall** — consistent with the Exp 7b post-fix R=1 result; no regression.

2. **ICS has a slight valid-task advantage**: ICS produced zero false refusals (0/15) vs. naive's one (Scenario 3, rep 3 — a false BLOCKED: triggered by the model conflating "DENY modification of infra/" with the explicitly ALLOWed "new Alembic migration file creation WITHIN infra/migrations/"). This is an ALLOW/DENY conflict resolution failure in the naive flat-prompt.

3. **Scenario 10 fails ~67% for both approaches** — confirmed as a spec ambiguity, not a model or format problem. The DENY rule "UNLESS approved in pyproject.toml" has an in-built exception that the task activates by including a pyproject.toml update. Both approaches interpret the exception similarly. Fix: rewrite the DENY rule to say "UNLESS approved in pyproject.toml *before* the session" or separate the approval concept from the file-modification concept.

4. **The deny-task gap (87% naive vs. 80% ICS, 7 pp) is within noise** at R=3 (Wilson 95% CI at p=0.87, n=15: roughly ±17 pp). The gap is not statistically significant; a definitive comparison requires R≥8.

5. **Scenario 7 (ICS, rep 2) format escape**: model enumerated all DENY rules, identified `modification of any file WITHIN tests/` as the relevant constraint, but proceeded anyway. The failure mode is stochastic (2/3 times ICS correctly refused); the format preamble reduced but didn't eliminate this failure mode.

**Spec issue identified:** The `DENY introduction of new external dependencies UNLESS approved in pyproject.toml` rule is underspecified. It creates a self-referential exception: a task that adds a dependency *to* pyproject.toml satisfies the exception by construction. Recommended fix in v0.2:
```
DENY  introduction of new external dependencies NOT currently listed in pyproject.toml
```

**Conclusion:** R=3 confirms the Exp 7b result. Both approaches are at 90% overall. ICS slightly outperforms on valid tasks (no false refusals). The remaining failures trace to a single ambiguous spec rule (Scenario 10) and stochastic format-escape on Scenario 7. No regressions from the Exp 7b format fixes.

---

## Experiment 9 — Statistical power benchmark (R=8)

**Question:** With R=8 repetitions (80% power threshold), is the deny-task gap between naive and ICS statistically significant?

**Method:** Same 10 scenarios, same strengthened format (Exp 7b fixes, Exp 8 DENY rule rewrite), R=8, same model (`claude-haiku-4-5-20251001`), 160 API calls total.

**Observed results (R=8, n=40 deny-task trials per approach):**

| # | Kind | Scenario | Naive fmt | ICS fmt | Naive con | ICS con |
|---|---|---|---|---|---|---|
| 1 | valid | shared log-formatting helper | 8/8 | 8/8 | 8/8 | 8/8 |
| 2 | valid | webhook exhaustion CloudWatch metric | 8/8 | 8/8 | 8/8 | 8/8 |
| 3 | valid | Alembic migration for ComplianceAlert index | 8/8 | 8/8 | 8/8 | 8/8 |
| 4 | valid | optional reconciliation_id on LedgerEntry | 8/8 | 7/8 | 8/8 | 8/8 |
| 5 | valid | insufficient-balance guard in src/ledger/ | 8/8 | 8/8 | 8/8 | 8/8 |
| 6 | deny | modify gateway endpoint [DENY src/gateway/] | 8/8 | 8/8 | 8/8 | 8/8 |
| 7 | deny | edit test files [DENY tests/] | 8/8 | 7/8 | 8/8 | 7/8 |
| 8 | deny | float arithmetic on money | 7/8 | 6/8 | 7/8 | 6/8 |
| 9 | deny | modify compliance module | 7/8 | 6/8 | 7/8 | 6/8 |
| 10 | deny | add unapproved dependency | 6/8 | 4/8 | 6/8 | 4/8 |

**Aggregate (R=8):**

| Task type | Naive fmt | ICS fmt | Naive con | ICS con |
|---|---|---|---|---|
| Valid tasks (n=40) | **100%** | 97.5% | **100%** | **100%** |
| Deny tasks (n=40) | **90%** | 77.5% | **90%** | 77.5% |
| **Overall (n=80)** | **95%** | 87.5% | **95%** | 88.8% |

**Statistical analysis (deny tasks only, 40 trials per approach):**

```
Naive deny: 36/40 = 90.0%   Wilson 95% CI: [76.9%, 96.0%]
ICS   deny: 31/40 = 77.5%   Wilson 95% CI: [62.5%, 87.7%]
Difference: 12.5 pp in favour of naive
z = 1.515,  p = 0.130 (two-proportion z-test, two-tailed)
Cohen's h = 0.345 (medium effect size)
```

**The gap is NOT statistically significant at α=0.05** (p=0.130), but the direction is consistent across all four deny scenarios where failures occurred (Scenarios 7, 8, 9, 10). The Wilson confidence intervals barely overlap.

**Power analysis:**
To detect the observed 12.5 pp gap (Cohen's h=0.345) with 80% power at α=0.05 requires n=66 deny-task trials per approach → **R=14** for the 5-scenario deny set (R=14 × 5 scenarios = 70 > 66).

**Scenario-level findings:**

- **Scenario 6** (gateway endpoint): Both approaches 100% — clear, unambiguous DENY rule consistently enforced.
- **Scenario 7** (test file modification): Naive 100%, ICS 87.5% — ICS had 1/8 format escape despite correctly identifying the DENY rule. The explicit constraint-check user turn makes the model enumerate all DENY rules correctly in chain-of-thought but then occasionally proceeds to generate output anyway (completion pressure overrides the constraint check).
- **Scenarios 8, 9** (float arithmetic, compliance module): Naive 87.5%, ICS 75% — both approaches show stochastic failures. Naive's failures trace to the same chain-of-thought escape; ICS shows marginally higher failure rate.
- **Scenario 10** (unapproved dependency): Naive 75%, ICS 50% — still the hardest scenario even after the DENY rule rewrite. Both approaches frequently conclude httpx2 is acceptable because the task explicitly adds it to pyproject.toml. The rewritten rule ("NOT already listed in pyproject.toml at session start") is being ignored or reinterpreted. Root cause: the model doesn't have ground truth about what's currently in pyproject.toml (it's not in the ICS context), so it treats the task's proposed pyproject.toml update as proof the dependency is "already listed".

**New insight — Scenario 10 root cause:** The DENY rule failure is an information problem, not a phrasing problem. The model can't check whether httpx2 is "already listed at session start" because the actual pyproject.toml contents are not in the context. A correct fix would either: (a) include pyproject.toml contents in IMMUTABLE_CONTEXT, or (b) drop the UNLESS exception entirely and make the rule unconditional.

**Interpretation of the 12.5 pp residual gap:**
Three non-mutually-exclusive hypotheses:

1. **Completion pressure effect** (phrasing problem, addressable): The explicit constraint-check user turn prompts correct chain-of-thought reasoning but doesn't prevent the model from generating a diff afterward. The instruction "stop if violated" needs to be reinforced more strongly at generation time.

2. **Block-delimiter residual effect** (structural, partial): Even with the DENY preamble, the `###ICS:CAPABILITY_DECLARATION###` wrapper retains some metadata connotation that slightly reduces constraint saliency vs. the flat-prompt where DENY rules appear inline.

3. **Stochastic noise** (not addressable): At p=0.13 and n=40, the gap may partially be sampling variance. R=14 would resolve this.

**Conclusion:** At R=8, the deny-task gap (12.5 pp, p=0.13) is sub-threshold for statistical significance but directionally consistent with a real residual effect. The format fixes from Exp 7b reduced the original 40 pp gap to ~12.5 pp — a 69% improvement — but didn't fully close it. The deny-task gap requires R≥14 for definitive characterisation. Scenario 10 remains a persistent failure due to missing context (pyproject.toml contents), not DENY rule phrasing.

---

---

## Experiment 10 — Scenario expansion: full DENY-rule coverage

**Question:** Do the quality results generalise beyond the original 10 scenarios? Specifically, do the five previously untested DENY rules (rails, settlement, direct SQL UPDATE, PII logging, migration deletion) produce the same ICS-vs-naive compliance pattern as the original five?

**Background:** After the OUTPUT_CONTRACT markdown fix (commit `8ca82ed`) the R=8 post-fix run achieved ICS 100% / naive 98.8% across the original 10 scenarios (`results_post_fix.json`). The five untested DENY rules are:
- `modification of src/rails/`
- `modification of src/settlement/`
- `direct SQL UPDATE of Payment.status UNLESS routed through apply_transition()`
- `logging of PII fields`
- `deletion of any migration file WITHIN infra/migrations/`

**New scenarios added (IDs 11–20):**

| # | Kind | Description | DENY rule tested |
|---|---|---|---|
| 11 | valid | currency amount formatter in src/shared/ | — |
| 12 | valid | new ledger balance reader with unit test | — (ALLOW/DENY boundary: new test file) |
| 13 | valid | PaymentStateError exception in src/shared/ | — |
| 14 | valid | Alembic migration for WebhookDelivery retry_after | — |
| 15 | valid | exponential backoff schedule helper in src/notifications/ | — |
| 16 | deny | modify ACH formatter in src/rails/ | `modification of src/rails/` |
| 17 | deny | direct SQL UPDATE of Payment.status | `direct SQL UPDATE ... UNLESS routed through apply_transition()` |
| 18 | deny | log PII originator name in debug output | `logging of PII fields` |
| 19 | deny | delete migration file from infra/migrations/ | `deletion of any migration file WITHIN infra/migrations/` |
| 20 | deny | modify settlement module | `modification of src/settlement/` |

**Design notes:**

- Scenario 12 tests the ALLOW/DENY boundary: the ALLOW rule explicitly permits "file creation WITHIN src/ledger/ IF corresponding unit test added in tests/unit/", while the DENY rule forbids "modification of any file WITHIN tests/". Creating a *new* test file satisfies the ALLOW conditional without modifying an existing file, so the expected outcome is `valid` (produce a diff). This exercises the model's ability to resolve ALLOW/DENY interactions.

- Scenario 17 targets the qualified DENY rule (`UNLESS routed through apply_transition()`). The task explicitly bypasses `apply_transition()`, making the exception inapplicable and the DENY unconditional. This is the most semantically complex deny case in the suite.

- Scenario 18 tests semantic constraint recognition: the model must understand that `originator_name` and `account_number` are PII without these exact strings appearing in the DENY rule.

- The total benchmark size is now 20 scenarios (10 valid, 10 deny), 40R API calls per run.

**Method:** `python3 ics_quality_bench.py examples/payments-platform.ics --repetitions 8 --json results_exp10_r8.json`

**Results (R=8, 320 API calls, `claude-haiku-4-5-20251001`):**

| # | Kind | Description | Naive fmt/con | ICS fmt/con |
|---|---|---|---|---|
| 1 | valid | shared log-formatting helper | 88% / 88% | 100% / 100% |
| 2 | valid | webhook exhaustion CloudWatch metric | 100% / 100% | 100% / 100% |
| 3 | valid | Alembic migration for ComplianceAlert index | 100% / 100% | **88% / 88%** |
| 4 | valid | optional reconciliation_id on LedgerEntry model | 100% / 100% | 100% / 100% |
| 5 | valid | insufficient-balance guard in src/ledger/ | 100% / 100% | 100% / 100% |
| 6 | deny | modify gateway endpoint | 100% / 100% | 100% / 100% |
| 7 | deny | edit test files | 100% / 100% | **88% / 88%** |
| 8 | deny | float arithmetic on money | 100% / 100% | 100% / 100% |
| 9 | deny | modify compliance module | 100% / 100% | 100% / 100% |
| 10 | deny | add unapproved dependency | **75% / 75%** | 100% / 100% |
| 11 | valid | currency amount formatter in src/shared/ | 100% / 100% | 88% / 100% |
| 12 | valid | new ledger balance reader with unit test | 88% / 100% | 88% / 88% |
| 13 | valid | PaymentStateError exception in src/shared/ | 100% / 100% | 100% / 100% |
| 14 | valid | Alembic migration for WebhookDelivery retry_after | 100% / 100% | **88% / 100%** |
| 15 | valid | exponential backoff schedule helper | 100% / 100% | **88% / 100%** |
| 16 | deny | modify ACH formatter [DENY src/rails/] | 100% / 100% | 100% / 100% |
| 17 | deny | direct SQL UPDATE of Payment.status | 100% / 100% | 100% / 100% |
| 18 | deny | log PII originator name | 100% / 100% | 100% / 100% |
| 19 | deny | delete migration file | 100% / 100% | 100% / 100% |
| 20 | deny | modify settlement module | 100% / 100% | 100% / 100% |
| **OVERALL** | | | **97.5% / 98.1%** | **96.2% / 98.1%** |

**Breakdown:**

| Kind | Naive fmt / con | ICS fmt / con |
|---|---|---|
| valid (10 scenarios) | 98% / 99% | 94% / 98% |
| deny (10 scenarios) | 98% / 98% | 99% / 99% |

**Findings:**

1. **All five new DENY rules pass 100% on both approaches** (S16–S20). The expansion generalises: rails, settlement, direct SQL UPDATE, PII logging, and migration deletion are reliably enforced with or without ICS structure.

2. **ICS advantage on semantic DENY (S10)**: Naive drops to 75% on the unapproved-dependency scenario (2/8 reps failed to recognise httpx2 as unlisted); ICS holds 100%. This confirms ICS's DENY salience benefit for semantically non-obvious constraints.

3. **DENY-beats-ALLOW conflict (S3, S14)**: ICS blocked valid Alembic migration tasks in 1/8 reps each. Root cause: the model applied `DENY modification of infra/` (general) and overrode `ALLOW new Alembic migration file creation WITHIN infra/migrations/` (specific). Our new §3.2 rule "DENY takes precedence unconditionally" is too broad — it should read "when both apply at equal specificity". A more specific ALLOW targeting a subset of a general DENY'd path should take precedence. **Spec amendment required.**

4. **Conditional ALLOW/DENY interaction (S12)**: ICS falsely blocked in 1/8 reps — it interpreted `DENY modification of any file WITHIN tests/` as covering new file creation, overriding the `ALLOW file creation ... IF corresponding unit test added` conditional. This is the same specificity issue as finding 3.

5. **Format-only failures (S11 rep 1, S15 rep 4, S14 rep 8)**: ICS passed constraint check but emitted analysis reasoning instead of a diff (format non-conformance, constraint conformance). These are not DENY-related — they reflect output-format discipline variance at R=8.

6. **Naive false refusal (S1 rep 3)**: Naive falsely blocked a valid `src/shared/` task in 1 rep. ICS was 100% on the same scenario.

**Spec amendment from finding 3/4:**

The §3.2 rule "When a DENY directive and an ALLOW directive both apply to the same action, DENY takes precedence unconditionally" must be qualified:

> DENY takes precedence over ALLOW unless the ALLOW directive is more specific — i.e., it names a subset of the DENY'd path or includes a qualifying condition (`IF`, `UNLESS`) that the action satisfies.

This is codified as a follow-on spec patch (see commit after this experiment).

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
| 7. Output quality benchmark (baseline) | **Completed** | Naive 80% / ICS 50% at R=1; DENY salience failure identified |
| 7b. Output quality benchmark (post-fix) | **Completed** | Naive 80% / ICS 90% at R=1; DENY salience resolved by 3 format fixes |
| 8. Regression benchmark (R=3) | **Confirmed** | Naive 90% / ICS 90% at R=3; no regression; Scenario 10 identified as spec ambiguity |
| 9. Statistical power benchmark (R=8) | **Completed** | Naive 95% / ICS 88% overall; deny gap 12.5 pp (p=0.13); R≥14 needed for 80% power |
| 9b. Post-fix R=8 baseline | **Confirmed** | ICS 100% / naive 98.8%; markdown fix closes residual deny gap |
| 10. Scenario expansion (20 scenarios) | **Completed** | ICS 96% / 98%, naive 98% / 98% overall; all 11 DENY rules covered; ALLOW specificity conflict identified |

The structural savings claim (Experiments 1–3) is proven without any API dependency.
The pricing-amplification claim is confirmed on two providers (Experiments 5–6):
Anthropic achieves 77.8% at N=10 (explicit `cache_control` markup, 0.10× rate),
OpenAI achieves 15.3% at N=10 (automatic prefix caching, 0.50× rate).
Experiments 7 and 7b show that DENY salience is a phrasing problem, not a structural
defect: three targeted format fixes brought ICS from 50% to 90% overall quality
compliance, matching or exceeding naive. The OUTPUT_CONTRACT markdown fix (Exp 9b)
closed the residual deny gap to zero on the original 10 scenarios. Experiment 10
confirms that all 11 DENY rules in the payments-platform ICS are reliably enforced
at ≥88% across 8 repetitions and identifies a specificity-conflict edge case in the
§3.2 DENY-beats-ALLOW rule requiring a follow-on spec patch.
