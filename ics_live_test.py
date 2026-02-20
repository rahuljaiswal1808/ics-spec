#!/usr/bin/env python3
"""
ICS Live Tester

Validates the ICS token-savings claim (§2.2, §2.4) against the real
Anthropic API using your own API key.

For each invocation it runs two requests:

  Naive:  all five layers concatenated into a single flat system prompt,
          no prompt-caching markup.

  ICS:    IMMUTABLE_CONTEXT + CAPABILITY_DECLARATION placed in a
          cache_control=ephemeral block (permanent lifetime, cached once).
          SESSION_STATE, TASK_PAYLOAD, and OUTPUT_CONTRACT are sent as
          plain text blocks (variable lifetime, never cached).

Real token counts are read from the API response usage field:

  input_tokens                 — tokens charged at the full input rate
  cache_creation_input_tokens  — tokens written to cache (1.25× rate on first call)
  cache_read_input_tokens      — tokens served from cache (0.1× rate on subsequent calls)

After N invocations the tool prints a per-call table and a cost summary
showing actual measured savings.

NOTE — cache activation threshold:
  Anthropic prompt caching requires the cached block to be at least
  1024 tokens for most models (check current docs; some models require
  2048). The built-in APPENDIX-A examples are intentionally small
  demonstration snippets and will NOT trigger cache hits. The tool will
  warn you and still run — the request structure is correct. Supply a
  real, production-sized instruction file to observe cache_read hits.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python ics_live_test.py                           # built-in example
    python ics_live_test.py path/to/instruction.txt   # your own ICS file
    python ics_live_test.py --invocations 5           # default: 3
    python ics_live_test.py --model claude-haiku-4-5-20251001
    python ics_live_test.py --dry-run                 # print requests, no API calls

Requirements:
    pip install anthropic
"""

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from ics_validator import parse_layers, LAYER_ORDER
from ics_token_analyzer import EXAMPLE_REFACTORING, count_tokens_word_boundary

# ---------------------------------------------------------------------------
# Layer lifetime groups (from §2.4)
# ---------------------------------------------------------------------------

PERMANENT  = {"IMMUTABLE_CONTEXT", "CAPABILITY_DECLARATION"}
SESSION    = {"SESSION_STATE"}
INVOCATION = {"TASK_PAYLOAD", "OUTPUT_CONTRACT"}

# Minimum tokens Anthropic requires in the cached block for caching to activate.
# Conservative lower bound — some models require 2048; check current docs.
CACHE_MIN_TOKENS = 1024

# ---------------------------------------------------------------------------
# Approximate per-model pricing (USD per million tokens).
# Source: https://www.anthropic.com/pricing  (check for latest values)
# ---------------------------------------------------------------------------

PRICING = {
    "claude-haiku-4-5-20251001": {
        "input": 0.80, "cache_write": 1.00, "cache_read": 0.08, "output": 4.00,
    },
    "claude-sonnet-4-6": {
        "input": 3.00, "cache_write": 3.75, "cache_read": 0.30, "output": 15.00,
    },
    "claude-opus-4-6": {
        "input": 15.00, "cache_write": 18.75, "cache_read": 1.50, "output": 75.00,
    },
    # Fallback for any model not listed above
    "default": {
        "input": 3.00, "cache_write": 3.75, "cache_read": 0.30, "output": 15.00,
    },
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class InvocationUsage:
    invocation: int
    approach: str  # "naive" or "ics"
    input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    output_tokens: int = 0

    def billed_input(self) -> int:
        """Input tokens billed at the full (non-cached) rate."""
        return self.input_tokens

    def total_tokens_sent(self) -> int:
        """Sum of all token types reported in usage."""
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def cost(self, model: str) -> float:
        """Estimated USD cost for this invocation."""
        p = PRICING.get(model, PRICING["default"])
        return (
            self.input_tokens                  * p["input"]
            + self.cache_creation_input_tokens * p["cache_write"]
            + self.cache_read_input_tokens     * p["cache_read"]
            + self.output_tokens               * p["output"]
        ) / 1_000_000


# ---------------------------------------------------------------------------
# System-prompt builders
# ---------------------------------------------------------------------------

def _layer_block(name: str, content: str) -> str:
    return f"###ICS:{name}###\n{content}\n###END:{name}###"


def build_naive_system(layer_map: dict) -> str:
    """
    Naive: all layers joined into one flat string.
    No cache markup — the full context is charged at the input token rate
    on every invocation.
    """
    parts = [
        _layer_block(name, layer_map[name].content)
        for name in LAYER_ORDER
        if name in layer_map
    ]
    return "\n\n".join(parts)


def build_ics_system(layer_map: dict) -> list:
    """
    ICS: system prompt as a list of content blocks.

    Block 1 (permanent layers) — marked cache_control=ephemeral.
        Charged at cache_write rate on the first call;
        at cache_read rate (≈ 10% of full price) on all subsequent calls.

    Block 2 (session layer) — plain text, no caching.

    Block 3 (invocation layers) — plain text, no caching.
    """
    blocks = []

    # Block 1: permanent layers (cacheable)
    perm_parts = [
        _layer_block(name, layer_map[name].content)
        for name in LAYER_ORDER
        if name in layer_map and name in PERMANENT
    ]
    if perm_parts:
        blocks.append({
            "type": "text",
            "text": "\n\n".join(perm_parts),
            "cache_control": {"type": "ephemeral"},
        })

    # Block 2: session layer
    for name in LAYER_ORDER:
        if name in layer_map and name in SESSION:
            blocks.append({
                "type": "text",
                "text": _layer_block(name, layer_map[name].content),
            })

    # Block 3: invocation layers
    inv_parts = [
        _layer_block(name, layer_map[name].content)
        for name in LAYER_ORDER
        if name in layer_map and name in INVOCATION
    ]
    if inv_parts:
        blocks.append({
            "type": "text",
            "text": "\n\n".join(inv_parts),
        })

    return blocks


# ---------------------------------------------------------------------------
# API call helper
# ---------------------------------------------------------------------------

USER_MESSAGE = (
    "Please execute the task described in TASK_PAYLOAD "
    "and return the result per OUTPUT_CONTRACT."
)


def call_api(
    client,
    model: str,
    system,
    invocation: int,
    approach: str,
    dry_run: bool,
) -> InvocationUsage:
    usage = InvocationUsage(invocation=invocation, approach=approach)

    if dry_run:
        if isinstance(system, str):
            preview = system[:400].replace("\n", "\n      ")
        else:
            preview = json.dumps(system, indent=2)[:400]
        print(f"\n  [DRY RUN] {approach.upper()} — invocation {invocation}")
        print(f"  System prompt ({type(system).__name__}):")
        print(f"    {preview}")
        if isinstance(system, str) and len(system) > 400:
            print(f"    ... ({len(system) - 400} more chars)")
        return usage

    resp = client.messages.create(
        model=model,
        max_tokens=32,
        system=system,
        messages=[{"role": "user", "content": USER_MESSAGE}],
    )
    u = resp.usage
    usage.input_tokens                  = getattr(u, "input_tokens", 0) or 0
    usage.cache_creation_input_tokens   = getattr(u, "cache_creation_input_tokens", 0) or 0
    usage.cache_read_input_tokens       = getattr(u, "cache_read_input_tokens", 0) or 0
    usage.output_tokens                 = getattr(u, "output_tokens", 0) or 0
    return usage


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

W = 76

def _bar(n: int, total: int, width: int = 20) -> str:
    if total == 0:
        return " " * width
    filled = round(n / total * width)
    return "█" * filled + "░" * (width - filled)


def print_summary(
    naive: list[InvocationUsage],
    ics: list[InvocationUsage],
    model: str,
    cache_warning: bool,
    perm_tokens: int,
):
    sep = "-" * W
    eq  = "=" * W

    print(f"\n{eq}")
    print(f"  Per-invocation token usage")
    print(eq)
    print(f"  {'Inv':<4}  {'Approach':<8}  {'Input':>8}  "
          f"{'CacheWrite':>10}  {'CacheRead':>10}  {'Output':>7}")
    print(sep)

    for u in naive:
        print(f"  {u.invocation:<4}  {'naive':<8}  {u.input_tokens:>8,}  "
              f"{u.cache_creation_input_tokens:>10,}  "
              f"{u.cache_read_input_tokens:>10,}  "
              f"{u.output_tokens:>7,}")

    print()

    for u in ics:
        print(f"  {u.invocation:<4}  {'ics':<8}  {u.input_tokens:>8,}  "
              f"{u.cache_creation_input_tokens:>10,}  "
              f"{u.cache_read_input_tokens:>10,}  "
              f"{u.output_tokens:>7,}")

    # Totals
    n_input  = sum(u.input_tokens for u in naive)
    i_input  = sum(u.input_tokens for u in ics)
    i_write  = sum(u.cache_creation_input_tokens for u in ics)
    i_read   = sum(u.cache_read_input_tokens for u in ics)

    n_cost = sum(u.cost(model) for u in naive)
    i_cost = sum(u.cost(model) for u in ics)

    print(f"\n{eq}")
    print(f"  Summary — {len(naive)} invocation(s)")
    print(eq)

    print(f"  {'Metric':<42}  {'Naive':>10}  {'ICS':>10}")
    print(sep)
    print(f"  {'Full-rate input tokens':<42}  {n_input:>10,}  {i_input:>10,}")
    print(f"  {'Cache-write tokens (billed at 1.25×)':<42}  {'—':>10}  {i_write:>10,}")
    print(f"  {'Cache-read tokens (billed at 0.10×)':<42}  {'—':>10}  {i_read:>10,}")
    print(sep)
    print(f"  {'Estimated cost (USD)*':<42}  ${n_cost:>9.5f}  ${i_cost:>9.5f}")

    if n_cost > 0:
        savings_pct = (n_cost - i_cost) / n_cost * 100
        savings_usd = n_cost - i_cost
        print(f"  {'Cost saved':<42}  {'':>10}  "
              f"${savings_usd:>8.5f}  ({savings_pct:.1f}%)")

    print(f"\n  * Pricing approximate; verify at https://www.anthropic.com/pricing")
    print(f"    Model used: {model}")

    if cache_warning:
        print(f"\n  ⚠  Prompt cache was NOT activated.")
        print(f"     Permanent layers = ~{perm_tokens} tokens "
              f"(need ≥ {CACHE_MIN_TOKENS} for caching).")
        print(f"     cache_creation_input_tokens and cache_read_input_tokens")
        print(f"     will both be 0 above. Supply a larger instruction file")
        print(f"     (or duplicate your IMMUTABLE_CONTEXT content) to observe")
        print(f"     real cache hits in the ICS column.")
    else:
        if i_read > 0:
            print(f"\n  ✓  Prompt cache activated — "
                  f"{i_read:,} tokens served from cache at 0.10× rate.")
        else:
            print(f"\n  ℹ  cache_read_input_tokens = 0. The cache may need one")
            print(f"     'warm-up' call before serving reads; try more invocations.")

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(args):
    # ── Load instruction ──────────────────────────────────────────────────
    if args.file:
        try:
            with open(args.file) as f:
                text = f.read()
        except FileNotFoundError:
            print(f"Error: file not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        label = args.file
    else:
        text  = EXAMPLE_REFACTORING
        label = "built-in example (APPENDIX-A refactoring task)"

    # ── Parse layers ──────────────────────────────────────────────────────
    layers, errors = parse_layers(text)
    if errors:
        print("ICS parse errors:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        sys.exit(1)

    layer_map = {l.name: l for l in layers}

    # ── Check cache threshold ─────────────────────────────────────────────
    perm_text = "\n\n".join(
        _layer_block(name, layer_map[name].content)
        for name in LAYER_ORDER
        if name in layer_map and name in PERMANENT
    )
    perm_tokens   = count_tokens_word_boundary(perm_text)
    cache_warning = perm_tokens < CACHE_MIN_TOKENS

    # ── Build system prompts ──────────────────────────────────────────────
    naive_system = build_naive_system(layer_map)
    ics_system   = build_ics_system(layer_map)

    # ── Set up API client ─────────────────────────────────────────────────
    if not args.dry_run:
        try:
            import anthropic
        except ImportError:
            print(
                "The anthropic SDK is not installed.\n"
                "Run:  pip install anthropic",
                file=sys.stderr,
            )
            sys.exit(1)

        api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print(
                "Error: no API key found.\n"
                "Set the ANTHROPIC_API_KEY environment variable "
                "or pass --api-key KEY.",
                file=sys.stderr,
            )
            sys.exit(1)

        client = anthropic.Anthropic(api_key=api_key)
    else:
        client = None

    model = args.model
    n     = args.invocations

    # ── Print header ──────────────────────────────────────────────────────
    print(f"\n{'=' * W}")
    print(f"  ICS Live Tester")
    print(f"{'=' * W}")
    print(f"  Instruction:     {label}")
    print(f"  Model:           {model}")
    print(f"  Invocations:     {n}")
    print(f"  Perm. layers:    ~{perm_tokens} tokens (word-boundary estimate)")
    print(f"  Cache threshold: {CACHE_MIN_TOKENS} tokens")
    print(f"  Dry run:         {'yes' if args.dry_run else 'no'}")
    if cache_warning:
        print(f"\n  ⚠  Permanent layers are below the cache threshold.")
        print(f"     Real cache savings require a larger instruction file.")
        print(f"     The request structure is still correct — no cache hits expected.")
    print(f"{'=' * W}\n")

    # ── Run invocations ───────────────────────────────────────────────────
    naive_usages: list[InvocationUsage] = []
    ics_usages:   list[InvocationUsage] = []

    for i in range(1, n + 1):
        print(f"  Invocation {i}/{n}...", end=" ", flush=True)

        u_naive = call_api(client, model, naive_system, i, "naive", args.dry_run)
        naive_usages.append(u_naive)

        u_ics = call_api(client, model, ics_system, i, "ics", args.dry_run)
        ics_usages.append(u_ics)

        if not args.dry_run:
            print(
                f"naive: {u_naive.input_tokens:,} input  |  "
                f"ics: {u_ics.input_tokens:,} input  "
                f"+{u_ics.cache_creation_input_tokens:,} cache_write  "
                f"+{u_ics.cache_read_input_tokens:,} cache_read"
            )
        else:
            print("(dry run)")

        # Brief pause to stay within rate limits
        if i < n and not args.dry_run:
            time.sleep(0.5)

    # ── Output ────────────────────────────────────────────────────────────
    if args.dry_run:
        print("\n  (dry run complete — no API calls made)")
        return

    if args.json_output:
        payload = {
            "model":   model,
            "label":   label,
            "naive":   [vars(u) for u in naive_usages],
            "ics":     [vars(u) for u in ics_usages],
            "pricing": PRICING.get(model, PRICING["default"]),
        }
        print(json.dumps(payload, indent=2))
        return

    print_summary(naive_usages, ics_usages, model, cache_warning, perm_tokens)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "ICS Live Tester — Validate the ICS token-savings claim "
            "against the real Anthropic API."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="See module docstring for full details.",
    )
    parser.add_argument(
        "file", nargs="?",
        help="Path to an ICS-compliant instruction file "
             "(default: built-in APPENDIX-A refactoring example)",
    )
    parser.add_argument(
        "--invocations", "-n", type=int, default=3, metavar="N",
        help="Number of invocations to simulate per approach (default: 3)",
    )
    parser.add_argument(
        "--model", default="claude-haiku-4-5-20251001",
        help="Anthropic model ID (default: claude-haiku-4-5-20251001)",
    )
    parser.add_argument(
        "--api-key", metavar="KEY",
        help="Anthropic API key (alternative to ANTHROPIC_API_KEY env var)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the requests that would be sent without calling the API",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output raw usage data as JSON instead of formatted report",
    )

    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
