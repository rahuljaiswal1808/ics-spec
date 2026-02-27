#!/usr/bin/env python3
"""
ICS Live Tester

Validates the ICS token-savings claim (§2.2, §2.4) against the real API
using your own key. Supports both Anthropic and OpenAI.

For each invocation it runs two requests:

  Naive:  all five layers concatenated into a single flat system prompt,
          no prompt-caching markup.

  ICS:    permanent layers (IMMUTABLE_CONTEXT + CAPABILITY_DECLARATION)
          are cached; variable layers are sent normally.

          Anthropic — explicit: cache_control=ephemeral on permanent block.
          OpenAI    — automatic: permanent layers placed first in the prompt
                      to maximise prefix-cache hit rate.

How caching is reported:

  Anthropic:
    input_tokens                — charged at full rate
    cache_creation_input_tokens — written to cache (1.25× on first call)
    cache_read_input_tokens     — served from cache (0.10× on later calls)

  OpenAI:
    prompt_tokens               — total tokens sent
    cached_tokens               — served from prefix cache (0.50× rate)

NOTE — cache activation thresholds:
  Anthropic: ≥ 1024 tokens in the cached block (some models require 2048).
  OpenAI:    ≥ 1024 tokens in the prompt prefix (automatic, no markup).
  The built-in APPENDIX-A examples are too small to trigger cache hits.
  Use examples/payments-platform.ics (~1,920 permanent-layer tokens).

Usage:
    # Anthropic (default)
    export ANTHROPIC_API_KEY=sk-ant-...
    python ics_live_test.py examples/payments-platform.ics

    # OpenAI
    export OPENAI_API_KEY=sk-...
    python ics_live_test.py examples/payments-platform.ics --provider openai

    # Google Gemini
    export GEMINI_API_KEY=AI...
    python ics_live_test.py examples/payments-platform.ics --provider gemini

    # Ollama (local, no API key required)
    python ics_live_test.py examples/payments-platform.ics --provider ollama --model llama3.2

    python ics_live_test.py --invocations 5
    python ics_live_test.py --dry-run      # preview requests, no API calls

Requirements:
    pip install anthropic          # for Anthropic provider
    pip install openai             # for OpenAI or Ollama provider
    pip install google-genai       # for Gemini provider
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
# Verified empirically: claude-haiku-4-5-20251001 requires ≥ ~4096 tokens.
# (Claude 3 models required 1024; Claude 3.5 models required 2048.)
CACHE_MIN_TOKENS = 4096

# ---------------------------------------------------------------------------
# Approximate per-model pricing (USD per million tokens).
# Sources: https://www.anthropic.com/pricing  https://openai.com/pricing
# ---------------------------------------------------------------------------

ANTHROPIC_PRICING = {
    "claude-haiku-4-5-20251001": {
        "input": 0.80, "cache_write": 1.00, "cache_read": 0.08, "output": 4.00,
    },
    "claude-sonnet-4-6": {
        "input": 3.00, "cache_write": 3.75, "cache_read": 0.30, "output": 15.00,
    },
    "claude-opus-4-6": {
        "input": 15.00, "cache_write": 18.75, "cache_read": 1.50, "output": 75.00,
    },
    "default": {
        "input": 3.00, "cache_write": 3.75, "cache_read": 0.30, "output": 15.00,
    },
}

# OpenAI: no explicit cache_write cost; cached_tokens billed at 0.50× input rate.
OPENAI_PRICING = {
    "gpt-4o":               {"input": 2.50, "cache_read": 1.25, "output": 10.00},
    "gpt-4o-mini":          {"input": 0.15, "cache_read": 0.075, "output": 0.60},
    "gpt-4.1":              {"input": 2.00, "cache_read": 0.50,  "output": 8.00},
    "gpt-4.1-mini":         {"input": 0.40, "cache_read": 0.10,  "output": 1.60},
    "o1":                   {"input": 15.00, "cache_read": 7.50, "output": 60.00},
    "o3-mini":              {"input": 1.10, "cache_read": 0.55,  "output": 4.40},
    "default":              {"input": 2.50, "cache_read": 1.25,  "output": 10.00},
}

# Keep PRICING as an alias for backward compatibility
PRICING = ANTHROPIC_PRICING

# Google Gemini: implicit context caching on Gemini 2.0+ (no explicit markup needed).
# Cached tokens billed at ~0.25× input rate.  Sources: https://ai.google.dev/pricing
GEMINI_PRICING = {
    "gemini-2.0-flash":      {"input": 0.10,  "cache_read": 0.025, "output": 0.40},
    "gemini-2.0-flash-lite": {"input": 0.075, "cache_read": 0.019, "output": 0.30},
    "gemini-1.5-flash":      {"input": 0.075, "cache_read": 0.019, "output": 0.30},
    "gemini-1.5-pro":        {"input": 3.50,  "cache_read": 0.875, "output": 10.50},
    "default":               {"input": 0.10,  "cache_read": 0.025, "output": 0.40},
}

# Ollama: local inference — no API cost; no prompt caching.
OLLAMA_PRICING = {
    "default": {"input": 0.00, "cache_read": 0.00, "output": 0.00},
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

    def cost(self, model: str, provider: str = "anthropic") -> float:
        """Estimated USD cost for this invocation."""
        if provider == "openai":
            table = OPENAI_PRICING
        elif provider == "gemini":
            table = GEMINI_PRICING
        elif provider == "ollama":
            table = OLLAMA_PRICING
        else:
            table = ANTHROPIC_PRICING
        p = table.get(model, table["default"])
        return (
            self.input_tokens                  * p["input"]
            + self.cache_creation_input_tokens * p.get("cache_write", 0)
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


def build_ics_system_flat(layer_map: dict) -> str:
    """
    ICS for OpenAI: flat string with permanent layers grouped first.

    OpenAI caches prompt prefixes automatically (no explicit cache_control).
    Placing permanent layers at the top of the prompt ensures the largest
    stable prefix, maximising cache hit rate across invocations.

    Structure:
      [permanent layers]   ← stable prefix; auto-cached by OpenAI
      [session layer]      ← changes per session
      [invocation layers]  ← changes every call
    """
    parts = []
    for group in (PERMANENT, SESSION, INVOCATION):
        group_parts = [
            _layer_block(name, layer_map[name].content)
            for name in LAYER_ORDER
            if name in layer_map and name in group
        ]
        parts.extend(group_parts)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# API call helpers
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


def call_api_openai(
    client,
    model: str,
    system: str,
    invocation: int,
    approach: str,
    dry_run: bool,
) -> InvocationUsage:
    usage = InvocationUsage(invocation=invocation, approach=approach)

    if dry_run:
        preview = system[:400].replace("\n", "\n      ")
        print(f"\n  [DRY RUN] {approach.upper()} — invocation {invocation}")
        print(f"  System prompt (OpenAI flat string, {len(system)} chars):")
        print(f"    {preview}")
        if len(system) > 400:
            print(f"    ... ({len(system) - 400} more chars)")
        return usage

    resp = client.chat.completions.create(
        model=model,
        max_tokens=32,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": USER_MESSAGE},
        ],
    )
    u = resp.usage
    details = getattr(u, "prompt_tokens_details", None)
    cached  = getattr(details, "cached_tokens", 0) or 0

    # Map to InvocationUsage fields:
    #   input_tokens           = prompt_tokens minus cached portion
    #   cache_read_input_tokens = cached tokens (billed at 0.50× for OpenAI)
    #   cache_creation_input_tokens = 0 (OpenAI doesn't bill separately for writes)
    usage.input_tokens            = (u.prompt_tokens or 0) - cached
    usage.cache_read_input_tokens = cached
    usage.output_tokens           = u.completion_tokens or 0
    return usage


def call_api_gemini(
    client,
    model: str,
    system: str,
    invocation: int,
    approach: str,
    dry_run: bool,
) -> "InvocationUsage":
    """
    Gemini API call via google-genai SDK.

    Gemini 2.0+ supports implicit context caching (no explicit markup).
    Token usage is reported in response.usage_metadata:
      prompt_token_count          — total tokens in the prompt
      cached_content_token_count  — tokens served from cache (billed at ~0.25×)
      candidates_token_count      — output tokens
    """
    usage = InvocationUsage(invocation=invocation, approach=approach)

    if dry_run:
        preview = system[:400].replace("\n", "\n      ")
        print(f"\n  [DRY RUN] {approach.upper()} — invocation {invocation}")
        print(f"  System prompt (Gemini flat string, {len(system)} chars):")
        print(f"    {preview}")
        if len(system) > 400:
            print(f"    ... ({len(system) - 400} more chars)")
        return usage

    from google import genai as google_genai  # noqa: PLC0415

    resp = client.models.generate_content(
        model=model,
        contents=USER_MESSAGE,
        config=google_genai.types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=32,
        ),
    )
    u = resp.usage_metadata
    cached = getattr(u, "cached_content_token_count", 0) or 0
    prompt = getattr(u, "prompt_token_count", 0) or 0
    usage.input_tokens            = prompt - cached
    usage.cache_read_input_tokens = cached
    usage.output_tokens           = getattr(u, "candidates_token_count", 0) or 0
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
    provider: str,
    cache_warning: bool,
    perm_tokens: int,
):
    sep = "-" * W
    eq  = "=" * W
    is_anthropic = provider == "anthropic"
    is_ollama    = provider == "ollama"

    # Per-provider display labels
    _cached_col = {
        "openai": "Cached(0.5×)",
        "gemini": "Cached(0.25×)",
        "ollama": "Cached(—)",
    }
    _cache_rate = {"openai": "0.50×", "gemini": "0.25×"}
    _pricing_url = {
        "anthropic": "https://www.anthropic.com/pricing",
        "openai":    "https://openai.com/pricing",
        "gemini":    "https://ai.google.dev/pricing",
    }
    cached_col  = _cached_col.get(provider, "Cached")
    cache_rate  = _cache_rate.get(provider, "n/a")
    pricing_url = _pricing_url.get(provider, "")

    # ── Per-invocation table ──────────────────────────────────────────────
    print(f"\n{eq}")
    print(f"  Per-invocation token usage  [{provider}]")
    print(eq)

    if is_anthropic:
        print(f"  {'Inv':<4}  {'Approach':<8}  {'Input':>8}  "
              f"{'CacheWrite(1.25×)':>18}  {'CacheRead(0.10×)':>17}  {'Output':>7}")
    else:
        print(f"  {'Inv':<4}  {'Approach':<8}  {'Input':>8}  {cached_col:>13}  {'Output':>7}")
    print(sep)

    for u in naive:
        if is_anthropic:
            print(f"  {u.invocation:<4}  {'naive':<8}  {u.input_tokens:>8,}  "
                  f"{u.cache_creation_input_tokens:>18,}  "
                  f"{u.cache_read_input_tokens:>17,}  {u.output_tokens:>7,}")
        else:
            print(f"  {u.invocation:<4}  {'naive':<8}  {u.input_tokens:>8,}  "
                  f"{u.cache_read_input_tokens:>13,}  {u.output_tokens:>7,}")

    print()

    for u in ics:
        if is_anthropic:
            print(f"  {u.invocation:<4}  {'ics':<8}  {u.input_tokens:>8,}  "
                  f"{u.cache_creation_input_tokens:>18,}  "
                  f"{u.cache_read_input_tokens:>17,}  {u.output_tokens:>7,}")
        else:
            print(f"  {u.invocation:<4}  {'ics':<8}  {u.input_tokens:>8,}  "
                  f"{u.cache_read_input_tokens:>13,}  {u.output_tokens:>7,}")

    # ── Summary ───────────────────────────────────────────────────────────
    n_input  = sum(u.input_tokens for u in naive)
    n_cached = sum(u.cache_read_input_tokens for u in naive)
    i_input  = sum(u.input_tokens for u in ics)
    i_write  = sum(u.cache_creation_input_tokens for u in ics)
    i_read   = sum(u.cache_read_input_tokens for u in ics)

    n_cost = sum(u.cost(model, provider) for u in naive)
    i_cost = sum(u.cost(model, provider) for u in ics)

    print(f"\n{eq}")
    print(f"  Summary — {len(naive)} invocation(s)")
    print(eq)
    print(f"  {'Metric':<42}  {'Naive':>10}  {'ICS':>10}")
    print(sep)
    print(f"  {'Full-rate input tokens':<42}  {n_input:>10,}  {i_input:>10,}")

    if is_anthropic:
        print(f"  {'Cache-write tokens (billed at 1.25×)':<42}  {'—':>10}  {i_write:>10,}")
        print(f"  {'Cache-read tokens (billed at 0.10×)':<42}  {'—':>10}  {i_read:>10,}")
    elif not is_ollama:
        cache_label = f"Cached tokens (billed at {cache_rate})"
        print(f"  {cache_label:<42}  {n_cached:>10,}  {i_read:>10,}")

    print(sep)
    if is_ollama:
        print(f"  {'Token counts (local — no API cost)':<52}")
    else:
        print(f"  {'Estimated cost (USD)*':<42}  ${n_cost:>9.5f}  ${i_cost:>9.5f}")

    if n_cost > 0 and not is_ollama:
        savings_pct = (n_cost - i_cost) / n_cost * 100
        savings_usd = n_cost - i_cost
        print(f"  {'Cost saved':<42}  {'':>10}  "
              f"${savings_usd:>8.5f}  ({savings_pct:.1f}%)")

    if not is_ollama:
        print(f"\n  * Pricing approximate; verify at {pricing_url}")
    print(f"    Model: {model}  Provider: {provider}")

    total_cached = i_read
    if is_ollama:
        n_total = n_input
        i_total = i_input
        if n_total > 0:
            struct_pct = (n_total - i_total) / n_total * 100
            print(f"\n  ✓  ICS structural reduction: {i_total:,} vs {n_total:,} tokens "
                  f"({struct_pct:.1f}% smaller prompts).")
            print(f"     (Ollama is local — no caching, but ICS reduces prompt size.)")
    elif cache_warning:
        print(f"\n  ⚠  Prompt cache was NOT activated.")
        print(f"     Permanent layers = ~{perm_tokens} tokens "
              f"(need ≥ {CACHE_MIN_TOKENS} for caching).")
        if is_anthropic:
            print(f"     cache_creation_input_tokens and cache_read_input_tokens")
            print(f"     will both be 0. Use a larger instruction file.")
        else:
            print(f"     cached_tokens will be 0. Use a larger instruction file.")
    else:
        if total_cached > 0:
            print(f"\n  ✓  Prompt cache activated — "
                  f"{total_cached:,} tokens served from cache at {cache_rate} rate.")
        else:
            print(f"\n  ℹ  No cached tokens yet. The cache may need one warm-up")
            print(f"     call before serving reads; try more invocations.")

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

    provider = getattr(args, "provider", "anthropic")

    # ── Build system prompts ──────────────────────────────────────────────
    naive_system = build_naive_system(layer_map)
    if provider == "anthropic":
        ics_system = build_ics_system(layer_map)        # content blocks with cache_control
    else:
        ics_system = build_ics_system_flat(layer_map)   # flat string, stable prefix first

    # ── Set up API client ─────────────────────────────────────────────────
    if not args.dry_run:
        if provider == "openai":
            try:
                import openai as openai_sdk
            except ImportError:
                print("The openai SDK is not installed.\nRun:  pip install openai",
                      file=sys.stderr)
                sys.exit(1)
            api_key = args.api_key or os.environ.get("OPENAI_API_KEY")
            if not api_key:
                print("Error: set OPENAI_API_KEY or pass --api-key KEY.",
                      file=sys.stderr)
                sys.exit(1)
            client = openai_sdk.OpenAI(api_key=api_key)
        elif provider == "gemini":
            try:
                from google import genai as google_genai
            except ImportError:
                print("The google-genai SDK is not installed.\nRun:  pip install google-genai",
                      file=sys.stderr)
                sys.exit(1)
            api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
            if not api_key:
                print("Error: set GEMINI_API_KEY or pass --api-key KEY.",
                      file=sys.stderr)
                sys.exit(1)
            client = google_genai.Client(api_key=api_key)
        elif provider == "ollama":
            try:
                import openai as openai_sdk
            except ImportError:
                print("The openai SDK is not installed.\nRun:  pip install openai",
                      file=sys.stderr)
                sys.exit(1)
            base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
            client = openai_sdk.OpenAI(base_url=base_url, api_key="ollama")
        else:  # anthropic
            try:
                import anthropic
            except ImportError:
                print("The anthropic SDK is not installed.\nRun:  pip install anthropic",
                      file=sys.stderr)
                sys.exit(1)
            api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                print("Error: set ANTHROPIC_API_KEY or pass --api-key KEY.",
                      file=sys.stderr)
                sys.exit(1)
            client = anthropic.Anthropic(api_key=api_key)
    else:
        client = None

    model = args.model
    n     = args.invocations

    # Default model per provider if user didn't override
    if model == "claude-haiku-4-5-20251001":
        if provider == "openai":
            model = "gpt-4o-mini"
        elif provider == "gemini":
            model = "gemini-2.0-flash"
        elif provider == "ollama":
            model = "llama3.2"

    # ── Print header ──────────────────────────────────────────────────────
    print(f"\n{'=' * W}")
    print(f"  ICS Live Tester")
    print(f"{'=' * W}")
    print(f"  Instruction:     {label}")
    print(f"  Provider:        {provider}")
    print(f"  Model:           {model}")
    print(f"  Invocations:     {n}")
    print(f"  Perm. layers:    ~{perm_tokens} tokens (word-boundary estimate)")
    print(f"  Cache threshold: {CACHE_MIN_TOKENS} tokens")
    print(f"  Dry run:         {'yes' if args.dry_run else 'no'}")
    if provider == "openai":
        print(f"\n  Cache model: OpenAI automatic prefix caching (no explicit markup).")
        print(f"  ICS benefit: permanent layers grouped first → largest stable prefix.")
    elif provider == "gemini":
        print(f"\n  Cache model: Gemini implicit context caching (≥ 32K tokens typical).")
        print(f"  ICS benefit: permanent layers grouped first → largest stable prefix.")
    elif provider == "ollama":
        print(f"\n  Cache model: Ollama (local) — no prompt caching available.")
        print(f"  ICS benefit: structural reduction decreases prompt token count.")
    if cache_warning:
        print(f"\n  ⚠  Permanent layers are below the cache threshold.")
        print(f"     No cache hits expected. Use a larger instruction file.")
    print(f"{'=' * W}\n")

    # ── Run invocations ───────────────────────────────────────────────────
    naive_usages: list[InvocationUsage] = []
    ics_usages:   list[InvocationUsage] = []

    for i in range(1, n + 1):
        print(f"  Invocation {i}/{n}...", end=" ", flush=True)

        if provider == "openai":
            u_naive = call_api_openai(client, model, naive_system, i, "naive", args.dry_run)
            u_ics   = call_api_openai(client, model, ics_system,   i, "ics",   args.dry_run)
        elif provider == "gemini":
            u_naive = call_api_gemini(client, model, naive_system, i, "naive", args.dry_run)
            u_ics   = call_api_gemini(client, model, ics_system,   i, "ics",   args.dry_run)
        elif provider == "ollama":
            u_naive = call_api_openai(client, model, naive_system, i, "naive", args.dry_run)
            u_ics   = call_api_openai(client, model, ics_system,   i, "ics",   args.dry_run)
        else:
            u_naive = call_api(client, model, naive_system, i, "naive", args.dry_run)
            u_ics   = call_api(client, model, ics_system,   i, "ics",   args.dry_run)

        naive_usages.append(u_naive)
        ics_usages.append(u_ics)

        if not args.dry_run:
            if provider in ("openai", "gemini", "ollama"):
                print(
                    f"naive: {u_naive.input_tokens:,} input "
                    f"+{u_naive.cache_read_input_tokens:,} cached  |  "
                    f"ics: {u_ics.input_tokens:,} input "
                    f"+{u_ics.cache_read_input_tokens:,} cached"
                )
            else:
                print(
                    f"naive: {u_naive.input_tokens:,} input  |  "
                    f"ics: {u_ics.input_tokens:,} input  "
                    f"+{u_ics.cache_creation_input_tokens:,} cache_write  "
                    f"+{u_ics.cache_read_input_tokens:,} cache_read"
                )
        else:
            print("(dry run)")

        if i < n and not args.dry_run:
            time.sleep(0.5)

    # ── Output ────────────────────────────────────────────────────────────
    if args.dry_run:
        print("\n  (dry run complete — no API calls made)")
        return

    if args.json_output:
        pricing_table = {
            "openai": OPENAI_PRICING,
            "gemini": GEMINI_PRICING,
            "ollama": OLLAMA_PRICING,
        }.get(provider, ANTHROPIC_PRICING)
        payload = {
            "provider": provider,
            "model":    model,
            "label":    label,
            "naive":    [vars(u) for u in naive_usages],
            "ics":      [vars(u) for u in ics_usages],
            "pricing":  pricing_table.get(model, pricing_table["default"]),
        }
        print(json.dumps(payload, indent=2))
        return

    print_summary(naive_usages, ics_usages, model, provider, cache_warning, perm_tokens)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "ICS Live Tester — Validate the ICS token-savings claim "
            "against the real API (Anthropic or OpenAI)."
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
        "--provider", default="anthropic",
        choices=["anthropic", "openai", "gemini", "ollama"],
        help="API provider (default: anthropic)",
    )
    parser.add_argument(
        "--invocations", "-n", type=int, default=3, metavar="N",
        help="Number of invocations to simulate per approach (default: 3)",
    )
    parser.add_argument(
        "--model", default="claude-haiku-4-5-20251001",
        help=(
            "Model ID. Provider defaults: anthropic=claude-haiku-4-5-20251001, "
            "openai=gpt-4o-mini, gemini=gemini-2.0-flash, ollama=llama3.2 "
            "(auto-selected when --provider is set)"
        ),
    )
    parser.add_argument(
        "--api-key", metavar="KEY",
        help="API key (alternative to ANTHROPIC_API_KEY / OPENAI_API_KEY env vars)",
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
