#!/usr/bin/env python3
"""
ICS Demo — zero-config token-savings demonstration

Dry-run mode (default, no API key needed):
    python ics_demo.py

    Shows the naive vs ICS request structure, approximate token counts,
    projected costs at N=1/5/10 invocations, and the break-even call number.

Live mode:
    python ics_demo.py --live        # requires ANTHROPIC_API_KEY
    python ics_demo.py --live --invocations 5

    Runs real Anthropic API calls and reports actual
    cache_creation_input_tokens and cache_read_input_tokens.
"""

import argparse
import math
import os
import sys

# ---------------------------------------------------------------------------
# Optional colour support — plain-text fallback, no required dependencies
# ---------------------------------------------------------------------------

_IS_TTY = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    if not _IS_TTY:
        return text
    return f"\033[{code}m{text}\033[0m"


def bold(t: str) -> str:   return _c("1",  t)
def green(t: str) -> str:  return _c("32", t)
def cyan(t: str) -> str:   return _c("36", t)
def yellow(t: str) -> str: return _c("33", t)
def red(t: str) -> str:    return _c("31", t)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEMO_FILE  = os.path.join(SCRIPT_DIR, "examples", "payments-platform.ics")

# ---------------------------------------------------------------------------
# Pricing  (Anthropic Sonnet — source: anthropic.com/pricing)
# ---------------------------------------------------------------------------

MODEL         = "claude-sonnet-4-6"
INPUT_PER_M   = 3.00   # $3.00 / million input tokens        (1.00×)
CACHE_WRITE_M = 3.75   # $3.75 / million cache-write tokens  (1.25×)
CACHE_READ_M  = 0.30   # $0.30 / million cache-read tokens   (0.10×)
OUTPUT_PER_M  = 15.00  # $15.00 / million output tokens

WRITE_MULT = CACHE_WRITE_M / INPUT_PER_M   # 1.25
READ_MULT  = CACHE_READ_M  / INPUT_PER_M   # 0.10

# ---------------------------------------------------------------------------
# Layer groups  (§2.4 of ICS-v0.1.md)
# ---------------------------------------------------------------------------

PERMANENT   = {"IMMUTABLE_CONTEXT", "CAPABILITY_DECLARATION"}
VARIABLE    = {"SESSION_STATE", "TASK_PAYLOAD", "OUTPUT_CONTRACT"}
LAYER_ORDER = [
    "IMMUTABLE_CONTEXT",
    "CAPABILITY_DECLARATION",
    "SESSION_STATE",
    "TASK_PAYLOAD",
    "OUTPUT_CONTRACT",
]

# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

def approx_tokens(text: str) -> int:
    """Approximate token count: ceiling of len(text) / 4."""
    return max(1, math.ceil(len(text) / 4))

# ---------------------------------------------------------------------------
# ICS file loading
# ---------------------------------------------------------------------------

def load_ics_file(path: str) -> dict:
    """
    Parse an ICS file into {layer_name: content_text}.
    Uses the reference validator's parser when available; falls back to a
    minimal built-in regex parser so the demo works without installation.
    """
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()

    try:
        sys.path.insert(0, SCRIPT_DIR)
        from ics_validator import parse_layers  # type: ignore
        layers, _ = parse_layers(text)
        return {la.name: la.content for la in layers}
    except ImportError:
        import re
        result: dict = {}
        pattern = re.compile(r"###ICS:(\w+)###\n(.*?)###END:\1###", re.DOTALL)
        for m in pattern.finditer(text):
            result[m.group(1)] = m.group(2).strip()
        return result

# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _block(name: str, content: str) -> str:
    return f"###ICS:{name}###\n{content}\n###END:{name}###"


def build_naive(layers: dict) -> str:
    """All five layers joined into one flat string — no cache markup."""
    return "\n\n".join(
        _block(n, layers[n]) for n in LAYER_ORDER if n in layers
    )


def build_perm(layers: dict) -> str:
    """Permanent layers (sit above the cache boundary)."""
    return "\n\n".join(
        _block(n, layers[n]) for n in LAYER_ORDER
        if n in layers and n in PERMANENT
    )


def build_var(layers: dict) -> str:
    """Variable layers (sent on every call, not cached)."""
    return "\n\n".join(
        _block(n, layers[n]) for n in LAYER_ORDER
        if n in layers and n in VARIABLE
    )

# ---------------------------------------------------------------------------
# Cost projection
# ---------------------------------------------------------------------------

def projected_costs(perm_tok: int, var_tok: int, n_list: list) -> list:
    """
    Returns [(n, naive_cost, ics_cost), ...] for each n in n_list.

    Naive: entire prompt at input rate on every call.
    ICS:   permanent block at cache-write rate on call 1,
           at cache-read rate on calls 2+;
           variable block at input rate on every call.
    """
    total_tok = perm_tok + var_tok
    rows = []
    for n in n_list:
        naive = total_tok * n * INPUT_PER_M / 1_000_000
        ics = (
            perm_tok * CACHE_WRITE_M / 1_000_000
            + max(0, n - 1) * perm_tok * CACHE_READ_M / 1_000_000
            + n * var_tok * INPUT_PER_M / 1_000_000
        )
        rows.append((n, naive, ics))
    return rows


def break_even_call() -> int:
    """
    First N where cumulative ICS cost < cumulative naive cost.
    Derived: N = (write_mult - read_mult) / (1 - read_mult) ≈ 1.28 → 2.
    """
    return math.ceil((WRITE_MULT - READ_MULT) / (1.0 - READ_MULT))

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

DIV_HEAVY = "═" * 62
DIV_LIGHT = "─" * 62


def _fmt_cost(c: float) -> str:
    return f"${c:.5f}"


def _fmt_savings(naive: float, ics: float) -> str:
    if naive == 0:
        return "n/a"
    pct = (naive - ics) / naive * 100
    label = f"{pct:+.1f}%"
    return green(label) if pct > 0 else red(label)

# ---------------------------------------------------------------------------
# Diagrams
# ---------------------------------------------------------------------------

NAIVE_DIAGRAM = (
    "  ┌─────────────────────────────────────┐\n"
    "  │  flat string — all 5 layers         │  ← sent in full on every call\n"
    "  │    IMMUTABLE_CONTEXT                │\n"
    "  │    CAPABILITY_DECLARATION           │  charged at full input rate\n"
    "  │    SESSION_STATE                    │  even if nothing changed\n"
    "  │    TASK_PAYLOAD                     │\n"
    "  │    OUTPUT_CONTRACT                  │\n"
    "  └─────────────────────────────────────┘"
)

ICS_DIAGRAM = (
    "  ┌─────────────────────────────────────┐\n"
    "  │  Block 1: permanent  ← cache_control│  written once (1.25× rate)\n"
    "  │    IMMUTABLE_CONTEXT                │  read back on calls 2+\n"
    "  │    CAPABILITY_DECLARATION           │  at 0.10× rate\n"
    "  ├ ─ ─ ─ ─ cache boundary ─ ─ ─ ─ ─  ┤\n"
    "  │  Block 2: SESSION_STATE             │  sent each call\n"
    "  │  Block 3: TASK_PAYLOAD +            │\n"
    "  │           OUTPUT_CONTRACT           │\n"
    "  └─────────────────────────────────────┘"
)

# ---------------------------------------------------------------------------
# Dry-run demo
# ---------------------------------------------------------------------------

def run_dry(file_path: str) -> None:
    print(bold(DIV_HEAVY))
    print(bold("  ICS Token Efficiency Demo"))
    rel = os.path.relpath(file_path) if os.path.isabs(file_path) else file_path
    print(f"  File: {rel}")
    print(bold(DIV_HEAVY))

    if not os.path.exists(file_path):
        print(f"\n{red('ERROR')}: demo file not found: {file_path}")
        print("       Run from the repo root or pass --file <path>.")
        sys.exit(1)

    layers   = load_ics_file(file_path)
    present  = [n for n in LAYER_ORDER if n in layers]
    naive_t  = approx_tokens(build_naive(layers))
    perm_t   = approx_tokens(build_perm(layers))
    var_t    = approx_tokens(build_var(layers))

    # ── Structure ──────────────────────────────────────────────────────────
    print()
    print(bold("STRUCTURE"))
    print(DIV_LIGHT)
    print()
    print("  Naive — one flat string, every call:")
    print(NAIVE_DIAGRAM)
    print()
    print("  ICS — three content blocks with explicit cache boundary:")
    print(ICS_DIAGRAM)

    # ── Token counts ───────────────────────────────────────────────────────
    print()
    print(bold("TOKEN COUNTS") + "  (approx: 1 token ≈ 4 chars)")
    print(DIV_LIGHT)
    print(f"  Layers found:      {', '.join(present)}")
    print(f"  Total tokens:      {cyan(f'{naive_t:,}')}")
    print(f"  Permanent layer:   {cyan(f'{perm_t:,}')}  "
          f"(IMMUTABLE_CONTEXT + CAPABILITY_DECLARATION)")
    print(f"  Variable layer:    {cyan(f'{var_t:,}')}  "
          f"(SESSION_STATE + TASK_PAYLOAD + OUTPUT_CONTRACT)")
    print()
    print(f"  Naive:  {naive_t:,} tokens at full rate — every single call")
    print(f"  ICS:    call 1  →  {perm_t:,} cache-write  +  {var_t:,} normal")
    print(f"          call 2+ →  {perm_t:,} cache-read   +  {var_t:,} normal")
    print(f"          (cache-read = {READ_MULT:.0%} of full input rate)")

    # ── Projected cost ─────────────────────────────────────────────────────
    print()
    print(bold(f"PROJECTED COST") + f"  (model: {MODEL}  |  "
          f"input: ${INPUT_PER_M}/M  |  "
          f"cache-write: ${CACHE_WRITE_M}/M  |  "
          f"cache-read: ${CACHE_READ_M}/M)")
    print(DIV_LIGHT)
    cw = 13
    header = (f"  {'Calls':<7} "
              f"{'Naive':>{cw}} "
              f"{'ICS':>{cw}} "
              f"{'Savings':>{cw}}")
    print(bold(header))
    print(f"  {'─'*7} {'─'*cw} {'─'*cw} {'─'*cw}")

    rows = projected_costs(perm_t, var_t, [1, 5, 10])
    for n, naive_c, ics_c in rows:
        label = f"N={n}"
        print(f"  {label:<7} "
              f"{_fmt_cost(naive_c):>{cw}} "
              f"{_fmt_cost(ics_c):>{cw}} "
              f"{_fmt_savings(naive_c, ics_c):>{cw}}")

    be = break_even_call()
    print()
    print(f"  Break-even: ICS becomes cheaper from {bold(f'call {be}')} onwards.")
    print(f"  The cache-write premium ({WRITE_MULT:.2f}×) is recovered by the")
    print(f"  first cache-read (tokens served at {READ_MULT:.2f}× = 10% of full rate).")

    # ── Summary ────────────────────────────────────────────────────────────
    _, naive_10, ics_10 = rows[-1]
    pct_10 = (naive_10 - ics_10) / naive_10 * 100

    print()
    print(bold(DIV_HEAVY))
    print(bold(f"  Summary: ICS saves ~{perm_t:,} tokens per call after warmup. "
               f"Break-even at call {be}."))
    print(f"           At N=10: {green(f'{pct_10:.1f}% cheaper')} than naive "
          f"(${naive_10 - ics_10:.5f} saved per 10-call session).")
    print(bold(DIV_HEAVY))
    print()
    print("  To see real API cache numbers:  python ics_demo.py --live")
    print()

# ---------------------------------------------------------------------------
# Live demo
# ---------------------------------------------------------------------------

def run_live(file_path: str, invocations: int = 3) -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(f"{red('ERROR')}: ANTHROPIC_API_KEY is not set.")
        print("       export ANTHROPIC_API_KEY=sk-ant-...  then retry.")
        sys.exit(1)

    try:
        import anthropic  # type: ignore
    except ImportError:
        print(f"{red('ERROR')}: anthropic SDK not installed.")
        print("       pip install anthropic")
        sys.exit(1)

    if not os.path.exists(file_path):
        print(f"{red('ERROR')}: file not found: {file_path}")
        sys.exit(1)

    layers   = load_ics_file(file_path)
    perm_t   = approx_tokens(build_perm(layers))
    var_t    = approx_tokens(build_var(layers))
    naive_prompt = build_naive(layers)
    perm_prompt  = build_perm(layers)
    var_prompt   = build_var(layers)

    client = anthropic.Anthropic(api_key=api_key)
    task_msg = "List all payment status transitions defined in this system. Be concise."

    print(bold(DIV_HEAVY))
    print(bold("  ICS Live Demo — real Anthropic API calls"))
    rel = os.path.relpath(file_path) if os.path.isabs(file_path) else file_path
    print(f"  File: {rel}  |  Model: {MODEL}  |  Invocations: {invocations}")
    print(bold(DIV_HEAVY))

    naive_costs: list[float] = []
    ics_costs:   list[float] = []

    for i in range(1, invocations + 1):
        print()
        print(bold(f"  Invocation {i}/{invocations}"))
        print(f"  {'─'*44}")

        # Naive call — flat string, no cache markup
        nr = client.messages.create(
            model=MODEL,
            max_tokens=128,
            system=naive_prompt,
            messages=[{"role": "user", "content": task_msg}],
        )
        nu = nr.usage
        n_in    = getattr(nu, "input_tokens", 0)
        n_cw    = getattr(nu, "cache_creation_input_tokens", 0)
        n_cr    = getattr(nu, "cache_read_input_tokens", 0)
        n_out   = getattr(nu, "output_tokens", 0)
        n_cost  = (n_in * INPUT_PER_M + n_cw * CACHE_WRITE_M
                   + n_cr * CACHE_READ_M + n_out * OUTPUT_PER_M) / 1_000_000
        naive_costs.append(n_cost)

        print(f"  Naive:  input={n_in:,}  cache_write={n_cw}  "
              f"cache_read={n_cr}  cost≈{_fmt_cost(n_cost)}")

        # ICS call — permanent block with cache_control, variable block plain
        ir = client.messages.create(
            model=MODEL,
            max_tokens=128,
            system=[
                {"type": "text", "text": perm_prompt,
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": var_prompt},
            ],
            messages=[{"role": "user", "content": task_msg}],
        )
        iu = ir.usage
        i_in   = getattr(iu, "input_tokens", 0)
        i_cw   = getattr(iu, "cache_creation_input_tokens", 0)
        i_cr   = getattr(iu, "cache_read_input_tokens", 0)
        i_out  = getattr(iu, "output_tokens", 0)
        i_cost = (i_in * INPUT_PER_M + i_cw * CACHE_WRITE_M
                  + i_cr * CACHE_READ_M + i_out * OUTPUT_PER_M) / 1_000_000
        ics_costs.append(i_cost)

        savings_pct = (n_cost - i_cost) / n_cost * 100 if n_cost > 0 else 0.0
        print(f"  ICS:    input={i_in:,}  "
              f"cache_write={cyan(str(i_cw))}  "
              f"cache_read={green(str(i_cr))}  "
              f"cost≈{_fmt_cost(i_cost)}")
        print(f"  Saving: {_fmt_savings(n_cost, i_cost)}")

    total_n   = sum(naive_costs)
    total_i   = sum(ics_costs)
    total_pct = (total_n - total_i) / total_n * 100 if total_n > 0 else 0.0

    print()
    print(bold(DIV_HEAVY))
    print(bold(f"  Summary: ICS saves ~{perm_t:,} tokens per call after warmup. "
               f"Break-even at call {break_even_call()}."))
    print(f"           {invocations} calls — ICS saved {green(f'{total_pct:.1f}%')} "
          f"vs naive (${total_n - total_i:.5f} total).")
    print(bold(DIV_HEAVY))
    print()

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ICS token-savings demo. No API key required by default.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python ics_demo.py                     # dry-run, no key needed\n"
            "  python ics_demo.py --live              # real API calls\n"
            "  python ics_demo.py --file my.ics       # custom ICS file\n"
        ),
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Run real Anthropic API calls (requires ANTHROPIC_API_KEY)",
    )
    parser.add_argument(
        "--file", default=DEMO_FILE, metavar="PATH",
        help="ICS file to analyse (default: examples/payments-platform.ics)",
    )
    parser.add_argument(
        "--invocations", type=int, default=3, metavar="N",
        help="Number of API calls in --live mode (default: 3)",
    )
    args = parser.parse_args()

    if args.live:
        run_live(args.file, args.invocations)
    else:
        run_dry(args.file)


if __name__ == "__main__":
    main()
