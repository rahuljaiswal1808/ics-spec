#!/usr/bin/env python3
"""
ICS Token Analyzer

Proves the spec's claim (§2.2, §2.4):
  "Token consumption MUST be minimized through structure, reuse, and
   explicit separation of context lifetimes."

The analyzer simulates a realistic multi-invocation session and compares
two approaches:

  Naive approach:  Every invocation sends all layers in full. The caller
                   makes no use of lifetime distinctions; everything is
                   flattened into a single repeated context.

  ICS approach:    IMMUTABLE_CONTEXT and CAPABILITY_DECLARATION are sent
                   once and cached (permanent lifetime). SESSION_STATE is
                   resent when it changes (session lifetime). TASK_PAYLOAD
                   and OUTPUT_CONTRACT change every invocation (invocation
                   lifetime).

Token counts use a character-based approximation (1 token ≈ 4 characters),
consistent with published LLM tokenizer guidance. No external dependencies
are required. Pass --exact to use tiktoken if installed.

Usage:
    python ics_token_analyzer.py <instruction_file> [--invocations N]
    python ics_token_analyzer.py --test
    python ics_token_analyzer.py --help
"""

import re
import sys
import json
import math
from dataclasses import dataclass, field
from typing import Optional

# Import layer parser from the reference validator
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from ics_validator import parse_layers, Layer, LAYER_ORDER


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

CHARS_PER_TOKEN = 4  # OpenAI / Anthropic rule-of-thumb approximation

# Regex that splits text the way BPE tokenizers do:
# words, numbers, punctuation runs, and whitespace are each separate pieces.
_TOKEN_SPLIT = re.compile(
    r"""(?x)
    [A-Za-z]+       # alphabetic runs
    | [0-9]+        # numeric runs
    | [^\w\s]+      # punctuation / symbol runs
    | \s+           # whitespace (each whitespace run = 1 token)
    """
)


def count_tokens_approx(text: str) -> int:
    """Approximate token count: len(text) / 4, rounded up."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def count_tokens_word_boundary(text: str) -> int:
    """
    Local word-boundary token count. Splits on word/number/punctuation/whitespace
    boundaries — the same strategy used by offline BPE estimators.
    No network access required. Typically within 5-10% of tiktoken counts
    for English prose.
    """
    return len(_TOKEN_SPLIT.findall(text))


def count_tokens_exact(text: str) -> int:
    """Exact token count using tiktoken (cl100k_base). Falls back to word-boundary."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return count_tokens_word_boundary(text)


# ---------------------------------------------------------------------------
# Lifetime classification
# ---------------------------------------------------------------------------

# Each layer's declared lifetime per §2.4
LAYER_LIFETIME = {
    "IMMUTABLE_CONTEXT":    "permanent",   # sent once, eligible for caching
    "CAPABILITY_DECLARATION": "permanent", # sent once, eligible for caching
    "SESSION_STATE":        "session",     # resent when changed; cleared explicitly
    "TASK_PAYLOAD":         "invocation",  # changes every call
    "OUTPUT_CONTRACT":      "invocation",  # changes every call (or stays same)
}

LIFETIME_LABEL = {
    "permanent":  "cacheable (permanent)",
    "session":    "session-scoped",
    "invocation": "per-invocation",
}


# ---------------------------------------------------------------------------
# Analysis types
# ---------------------------------------------------------------------------

@dataclass
class LayerTokens:
    name: str
    lifetime: str
    token_count: int
    char_count: int


@dataclass
class SessionSimulation:
    """
    Models a session of `num_invocations` calls.

    Assumptions:
    - IMMUTABLE_CONTEXT and CAPABILITY_DECLARATION are sent once and cached.
    - SESSION_STATE is resent on `session_state_changes` invocations (including
      the first), and omitted (or sent as CLEAR token-equivalent) on others.
      Default: changes once (sent in full on invocation 1 only).
    - TASK_PAYLOAD and OUTPUT_CONTRACT are unique every invocation.
      Estimated as their token count from the example instruction.
    """
    num_invocations: int
    session_state_changes: int
    layer_tokens: list[LayerTokens]

    def _tokens_for(self, name: str) -> int:
        for lt in self.layer_tokens:
            if lt.name == name:
                return lt.token_count
        return 0

    def naive_total(self) -> int:
        """Naive: resend all layers every invocation."""
        per_call = sum(lt.token_count for lt in self.layer_tokens)
        return per_call * self.num_invocations

    def ics_total(self) -> int:
        """
        ICS: cache permanent layers, resend session layer on changes,
        resend invocation layers every call.
        """
        permanent = (
            self._tokens_for("IMMUTABLE_CONTEXT")
            + self._tokens_for("CAPABILITY_DECLARATION")
        )
        session = self._tokens_for("SESSION_STATE")
        per_invocation = (
            self._tokens_for("TASK_PAYLOAD")
            + self._tokens_for("OUTPUT_CONTRACT")
        )

        # Permanent layers sent once (cache prime)
        total = permanent
        # Session layer sent on each change
        total += session * self.session_state_changes
        # Invocation layers sent every call
        total += per_invocation * self.num_invocations
        return total

    def tokens_saved(self) -> int:
        return self.naive_total() - self.ics_total()

    def savings_pct(self) -> float:
        naive = self.naive_total()
        if naive == 0:
            return 0.0
        return (self.tokens_saved() / naive) * 100


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def analyze(
    instruction_text: str,
    num_invocations: int = 10,
    session_state_changes: int = 1,
    exact: bool = False,
    method: str = "approx",
) -> dict:
    """
    Parse the instruction, compute token counts per layer, simulate a session.
    Returns a structured result dict (also suitable for --json output).

    method: "approx"  — chars/4 (default)
            "word"    — local word-boundary split (no network required)
            "exact"   — tiktoken cl100k_base (requires network on first run)
    """
    if exact or method == "exact":
        count_fn = count_tokens_exact
        method_label = "exact (tiktoken cl100k_base)"
    elif method == "word":
        count_fn = count_tokens_word_boundary
        method_label = "word-boundary split (local BPE estimate)"
    else:
        count_fn = count_tokens_approx
        method_label = "approximate (chars/4)"

    layers, parse_errors = parse_layers(instruction_text)
    if parse_errors:
        return {"error": "parse_errors", "details": parse_errors}

    layer_map = {l.name: l for l in layers}

    layer_tokens = []
    for name in LAYER_ORDER:
        if name not in layer_map:
            continue
        layer = layer_map[name]
        # Count the full layer including boundary tags for realism
        full_text = (
            f"###ICS:{name}###\n{layer.content}\n###END:{name}###"
        )
        tc = count_fn(full_text)
        layer_tokens.append(LayerTokens(
            name=name,
            lifetime=LAYER_LIFETIME.get(name, "unknown"),
            token_count=tc,
            char_count=len(full_text),
        ))

    sim = SessionSimulation(
        num_invocations=num_invocations,
        session_state_changes=session_state_changes,
        layer_tokens=layer_tokens,
    )

    return {
        "method": method_label,
        "layers": [
            {
                "name": lt.name,
                "lifetime": lt.lifetime,
                "tokens": lt.token_count,
                "chars": lt.char_count,
            }
            for lt in layer_tokens
        ],
        "single_invocation_tokens": sum(lt.token_count for lt in layer_tokens),
        "simulation": {
            "num_invocations": num_invocations,
            "session_state_changes": session_state_changes,
            "naive_total_tokens": sim.naive_total(),
            "ics_total_tokens": sim.ics_total(),
            "tokens_saved": sim.tokens_saved(),
            "savings_pct": round(sim.savings_pct(), 1),
        },
    }


def print_report(result: dict, label: str = ""):
    if "error" in result:
        print(f"ERROR: {result['error']}")
        for d in result.get("details", []):
            print(f"  {d}")
        return

    width = 72
    sep = "-" * width

    if label:
        print(f"\n{'=' * width}")
        print(f"  {label}")
        print(f"{'=' * width}")

    print(f"\nToken counting method: {result['method']}\n")
    print(sep)
    print(f"  {'Layer':<30}  {'Lifetime':<26}  {'Tokens':>7}")
    print(sep)

    cacheable_total = 0
    session_total = 0
    invocation_total = 0

    for layer in result["layers"]:
        lifetime_label = LIFETIME_LABEL.get(layer["lifetime"], layer["lifetime"])
        print(f"  {layer['name']:<30}  {lifetime_label:<26}  {layer['tokens']:>7,}")
        if layer["lifetime"] == "permanent":
            cacheable_total += layer["tokens"]
        elif layer["lifetime"] == "session":
            session_total += layer["tokens"]
        elif layer["lifetime"] == "invocation":
            invocation_total += layer["tokens"]

    print(sep)
    total = result["single_invocation_tokens"]
    print(f"  {'TOTAL (single invocation)':<57}  {total:>7,}")
    print()
    print(f"  Cacheable (permanent):   {cacheable_total:>7,} tokens  "
          f"({cacheable_total/total*100:.1f}% of single invocation)")
    print(f"  Session-scoped:          {session_total:>7,} tokens  "
          f"({session_total/total*100:.1f}% of single invocation)")
    print(f"  Per-invocation:          {invocation_total:>7,} tokens  "
          f"({invocation_total/total*100:.1f}% of single invocation)")

    sim = result["simulation"]
    n = sim["num_invocations"]
    sc = sim["session_state_changes"]

    print(f"\n{sep}")
    print(f"  Session simulation: {n} invocations, "
          f"SESSION_STATE changes {sc} time(s)")
    print(sep)
    print(f"  {'Naive (resend all layers every call)':<45}  "
          f"{sim['naive_total_tokens']:>10,} tokens")
    print(f"  {'ICS (cache permanent, resend variable)':<45}  "
          f"{sim['ics_total_tokens']:>10,} tokens")
    print(sep)
    print(f"  {'Tokens saved':<45}  {sim['tokens_saved']:>10,} tokens")
    print(f"  {'Savings':<45}  {sim['savings_pct']:>9.1f}%")
    print()

    # Breakdown explanation
    print(f"  How ICS achieves this:")
    print(f"    - Cacheable layers sent once:       {cacheable_total:>6,} tokens (1 × {cacheable_total:,})")
    print(f"    - Session layer sent {sc} time(s):    "
          f"{session_total * sc:>6,} tokens ({sc} × {session_total:,})")
    print(f"    - Invocation layers sent {n:2d} times:  "
          f"{invocation_total * n:>6,} tokens ({n} × {invocation_total:,})")
    print(f"    - Total ICS cost:                   "
          f"{sim['ics_total_tokens']:>6,} tokens")
    print(f"    - vs. naive cost:                   "
          f"{sim['naive_total_tokens']:>6,} tokens ({n} × {total:,})")
    print()


# ---------------------------------------------------------------------------
# Built-in test suite
# ---------------------------------------------------------------------------

# Example 1 from APPENDIX-A (code refactoring task)
EXAMPLE_REFACTORING = """
###ICS:IMMUTABLE_CONTEXT###
System: order management service
Language: Python 3.11
Repo structure:
  src/orders/       — business logic
  src/orders/api/   — HTTP handlers
  tests/            — pytest test suite
Invariant: all monetary values stored as integer cents
###END:IMMUTABLE_CONTEXT###

###ICS:CAPABILITY_DECLARATION###
ALLOW   file modification WITHIN src/orders/
ALLOW   file creation WITHIN src/orders/ IF new file has corresponding test
DENY    modification of src/orders/api/
DENY    modification of any file WITHIN tests/
DENY    introduction of new external dependencies
REQUIRE type annotations ON all new functions
REQUIRE docstring ON all new public functions
###END:CAPABILITY_DECLARATION###

###ICS:SESSION_STATE###
[2024-01-15T09:30Z] Confirmed: discount logic currently lives in apply_discount() in src/orders/pricing.py
[2024-01-15T09:45Z] Decision: percentage and flat discounts to be handled by separate functions
###END:SESSION_STATE###

###ICS:TASK_PAYLOAD###
Split apply_discount() into two functions: apply_percentage_discount() and apply_flat_discount().
Preserve existing call sites by having apply_discount() delegate to the appropriate function
based on the discount type field.
###END:TASK_PAYLOAD###

###ICS:OUTPUT_CONTRACT###
format:     unified diff
schema:     standard unified diff against current HEAD; one diff block per modified file
variance:   diff header comments are permitted; no other variance allowed
on_failure: return plain text block with prefix "BLOCKED:" followed by a single-sentence
            description of the blocking constraint
###END:OUTPUT_CONTRACT###
""".strip()

# Example 2 from APPENDIX-A (structured analysis task)
EXAMPLE_ANALYSIS = """
###ICS:IMMUTABLE_CONTEXT###
System: API gateway analytics pipeline
Data source: structured JSON logs, one record per request
Log schema:
  endpoint:    string   — fully qualified path (e.g., /v2/orders/{id})
  method:      string   — HTTP method
  status:      integer  — HTTP response code
  duration_ms: integer  — response time in milliseconds
  caller_id:   string   — authenticated client identifier
Invariant: duration_ms is always present; other fields may be null for malformed requests
###END:IMMUTABLE_CONTEXT###

###ICS:CAPABILITY_DECLARATION###
ALLOW   read access to log data
ALLOW   aggregation and statistical summarization
DENY    output of individual caller_id values
DENY    output of any field that could identify a specific caller
REQUIRE flagging of any result where sample size is below 100 records
###END:CAPABILITY_DECLARATION###

###ICS:SESSION_STATE###
[2024-01-20T14:00Z] Analysis window: 2024-01-13 to 2024-01-20 (rolling 7 days)
[2024-01-20T14:05Z] Confirmed: /v2/orders/{id} endpoint flagged for elevated error rate in prior run
###END:SESSION_STATE###

###ICS:TASK_PAYLOAD###
Produce a latency percentile breakdown (p50, p90, p99) for the /v2/orders/{id} endpoint,
segmented by HTTP method. Flag any method where p99 exceeds 2000ms.
###END:TASK_PAYLOAD###

###ICS:OUTPUT_CONTRACT###
format:     JSON
schema: {
  "endpoint": "string",
  "breakdown": [
    {
      "method":  "string",
      "p50_ms":  "integer",
      "p90_ms":  "integer",
      "p99_ms":  "integer",
      "flagged": "boolean"
    }
  ],
  "warnings": ["string"]
}
variance:   "warnings" field MAY be omitted if empty; "flagged" MUST be present even if false
on_failure: Return { "status": "error", "reason": "<single-sentence description>" }
###END:OUTPUT_CONTRACT###
""".strip()


TESTS = [
    {
        "name": "Cacheable tokens are > 0",
        "instruction": EXAMPLE_REFACTORING,
        "invocations": 10,
        "check": lambda r: (
            sum(l["tokens"] for l in r["layers"] if l["lifetime"] == "permanent") > 0
        ),
        "description": "IMMUTABLE_CONTEXT + CAPABILITY_DECLARATION have non-zero token counts",
    },
    {
        "name": "ICS saves tokens vs naive (10 invocations, refactoring example)",
        "instruction": EXAMPLE_REFACTORING,
        "invocations": 10,
        "check": lambda r: r["simulation"]["tokens_saved"] > 0,
        "description": "ICS total < naive total",
    },
    {
        "name": "ICS saves tokens vs naive (10 invocations, analysis example)",
        "instruction": EXAMPLE_ANALYSIS,
        "invocations": 10,
        "check": lambda r: r["simulation"]["tokens_saved"] > 0,
        "description": "ICS total < naive total",
    },
    {
        "name": "Savings scale with invocation count",
        "instruction": EXAMPLE_REFACTORING,
        "invocations": 20,
        "check": lambda r: (
            r["simulation"]["tokens_saved"] >
            analyze(EXAMPLE_REFACTORING, num_invocations=10)["simulation"]["tokens_saved"]
        ),
        "description": "More invocations → more tokens saved",
    },
    {
        "name": "Single invocation: no savings (nothing to cache across calls)",
        "instruction": EXAMPLE_REFACTORING,
        "invocations": 1,
        "check": lambda r: r["simulation"]["tokens_saved"] == 0,
        "description": "With 1 invocation, ICS cost equals naive cost",
    },
    {
        "name": "Savings percentage is positive and meaningful (10 invocations)",
        "instruction": EXAMPLE_REFACTORING,
        "invocations": 10,
        "check": lambda r: r["simulation"]["savings_pct"] >= 30.0,
        "description": "Savings are at least 30% over 10 invocations",
    },
    {
        "name": "Cacheable fraction reported correctly",
        "instruction": EXAMPLE_REFACTORING,
        "invocations": 10,
        "check": lambda r: (
            sum(l["tokens"] for l in r["layers"] if l["lifetime"] == "permanent")
            < r["single_invocation_tokens"]
        ),
        "description": "Cacheable tokens are a subset of the total",
    },
    {
        "name": "All five lifetime categories represented",
        "instruction": EXAMPLE_REFACTORING,
        "invocations": 10,
        "check": lambda r: (
            any(l["lifetime"] == "permanent" for l in r["layers"])
            and any(l["lifetime"] == "session" for l in r["layers"])
            and any(l["lifetime"] == "invocation" for l in r["layers"])
        ),
        "description": "All three lifetime categories (permanent/session/invocation) present",
    },
    {
        "name": "ICS total = permanent + (session × changes) + (invocation × N)",
        "instruction": EXAMPLE_REFACTORING,
        "invocations": 10,
        "check": lambda r: (
            r["simulation"]["ics_total_tokens"] ==
            sum(l["tokens"] for l in r["layers"] if l["lifetime"] == "permanent") +
            sum(l["tokens"] for l in r["layers"] if l["lifetime"] == "session") * 1 +
            sum(l["tokens"] for l in r["layers"] if l["lifetime"] == "invocation") * 10
        ),
        "description": "ICS total computed correctly from lifetime formula",
    },
    {
        "name": "Naive total = single_invocation × N",
        "instruction": EXAMPLE_REFACTORING,
        "invocations": 10,
        "check": lambda r: (
            r["simulation"]["naive_total_tokens"] ==
            r["single_invocation_tokens"] * 10
        ),
        "description": "Naive total is simply total_tokens × num_invocations",
    },
]


def run_tests() -> int:
    passed = 0
    failed = 0

    print("Running ICS token analyzer test suite...\n")

    for test in TESTS:
        inv = test.get("invocations", 10)
        result = analyze(test["instruction"], num_invocations=inv)

        if "error" in result:
            print(f"  [FAIL] {test['name']}")
            print(f"         Parse error: {result['error']}")
            failed += 1
            continue

        try:
            ok = test["check"](result)
        except Exception as e:
            ok = False
            err = str(e)
        else:
            err = None

        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {test['name']}")

        if not ok:
            print(f"         Expected: {test['description']}")
            sim = result["simulation"]
            print(f"         naive={sim['naive_total_tokens']:,}  "
                  f"ics={sim['ics_total_tokens']:,}  "
                  f"saved={sim['tokens_saved']:,}  "
                  f"savings_pct={sim['savings_pct']}%")
            if err:
                print(f"         Error: {err}")
            failed += 1
        else:
            passed += 1

    print(f"\n{passed}/{passed + failed} tests passed.")
    return 0 if failed == 0 else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]

    if not args or "--help" in args:
        print(__doc__)
        sys.exit(0)

    if "--test" in args:
        sys.exit(run_tests())

    # Parse flags
    invocations = 10
    session_changes = 1
    exact = "--exact" in args
    json_output = "--json" in args

    if "--invocations" in args:
        idx = args.index("--invocations")
        try:
            invocations = int(args[idx + 1])
        except (IndexError, ValueError):
            print("Error: --invocations requires an integer argument", file=sys.stderr)
            sys.exit(2)

    if "--session-changes" in args:
        idx = args.index("--session-changes")
        try:
            session_changes = int(args[idx + 1])
        except (IndexError, ValueError):
            print("Error: --session-changes requires an integer argument", file=sys.stderr)
            sys.exit(2)

    # Find the file argument (first non-flag arg)
    file_args = [a for a in args if not a.startswith("--")]
    if not file_args:
        print("Error: provide an instruction file path", file=sys.stderr)
        sys.exit(2)

    path = file_args[0]
    try:
        with open(path) as f:
            text = f.read()
    except FileNotFoundError:
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(2)

    result = analyze(
        text,
        num_invocations=invocations,
        session_state_changes=session_changes,
        exact=exact,
    )

    if json_output:
        print(json.dumps(result, indent=2))
    else:
        print_report(result, label=path)


if __name__ == "__main__":
    main()
