"""BFSI Lead Qualification demo — runs four scenarios showcasing ICS Runtime.

Usage:
    cd ics-runtime
    pip install -e ".[dev]"
    python -m demo.bfsi_agent.main

    # Override provider:
    python -m demo.bfsi_agent.main --provider openai
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap


# ---------------------------------------------------------------------------
# Terminal colours (no external deps)
# ---------------------------------------------------------------------------
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_CYAN   = "\033[36m"
_DIM    = "\033[2m"


def _c(colour: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{colour}{text}{_RESET}"


def _header(text: str) -> None:
    print(f"\n{_c(_BOLD, '='*60)}")
    print(_c(_BOLD + _CYAN, f"  {text}"))
    print(_c(_BOLD, '='*60))


def _result_line(label: str, value: str, ok: bool | None = None) -> None:
    icon = ""
    if ok is True:
        icon = _c(_GREEN, "✓ ")
    elif ok is False:
        icon = _c(_RED, "✗ ")
    print(f"  {icon}{_c(_DIM, label + ':')} {value}")


# ---------------------------------------------------------------------------
# Scenario helpers
# ---------------------------------------------------------------------------

def _run_scenario(
    title: str,
    task: str,
    agent,
    session_vars: dict | None = None,
    expected_cache_hit: bool = False,
) -> None:
    """Execute one scenario turn and print a formatted result block."""
    from ics_runtime.core.session import Session
    import uuid

    print(f"\n{_c(_BOLD, title)}")
    print(_c(_DIM, f"  Task: {task[:90]}"))

    session_vars = session_vars or {}
    session = Session(agent, str(uuid.uuid4()), session_vars)

    result = session.run(task)

    _result_line("Provider / Model", f"{result.provider} / {result.model}")
    _result_line("Cache hit",        str(result.cache_hit),    ok=result.cache_hit if expected_cache_hit else None)
    _result_line("Cache write",      str(result.cache_write))
    _result_line(
        "Tokens",
        f"in={result.input_tokens:,}  out={result.output_tokens:,}  "
        f"cached={result.tokens_saved:,}  write={result.cache_write_tokens:,}"
    )
    _result_line("Cost",             f"${result.cost_usd:.5f}")
    _result_line("Validated",        str(result.validated), ok=result.validated)
    _result_line("Violations",       str(len(result.violations)), ok=len(result.violations) == 0)
    _result_line("Tool calls",       ", ".join(tc.tool_name for tc in result.tool_calls) or "none")

    if result.violations:
        for v in result.violations:
            print(_c(_RED, f"    ⚠  {v}"))

    if result.parsed:
        q = result.parsed
        print(f"  {_c(_GREEN, 'Decision:')} {q.decision}  "
              f"{_c(_CYAN, 'Risk:')} {q.risk_category}  "
              f"{_c(_DIM, 'Score:')} {q.score}/100")
        if q.compliance_flags:
            for flag in q.compliance_flags:
                print(_c(_YELLOW, f"    🚩 {flag}"))

    return result


# ---------------------------------------------------------------------------
# Main demo
# ---------------------------------------------------------------------------

def run_demo(provider: str = "anthropic", model: str | None = None) -> None:
    from demo.bfsi_agent.agent_definition import make_agent

    agent = make_agent(provider=provider, model=model)

    _header(f"ICS Runtime — BFSI Lead Qualification Demo ({provider})")
    print(_c(_DIM, "  Demonstrates: caching · tool contracts · capability enforcement · schema validation\n"))

    results = []

    # ------------------------------------------------------------------
    # Scenario 1a: Happy path — first call (cache write)
    # ------------------------------------------------------------------
    r1a = _run_scenario(
        "Scenario 1a — Happy path (first call, cache write)",
        "Qualify lead L-001: Nexus Logistics Ltd. Look up their CRM data first, "
        "then run the eligibility check, and provide a full qualification decision.",
        agent,
        session_vars={"lead_id": "L-001"},
        expected_cache_hit=False,
    )
    results.append(("1a: Happy path (write)", r1a))

    # ------------------------------------------------------------------
    # Scenario 1b: Same agent, new session — should cache hit
    # ------------------------------------------------------------------
    r1b = _run_scenario(
        "Scenario 1b — Cache hit (same static layers, new session)",
        "Qualify lead L-002: Beta Foods Inc. Retrieve their data, run eligibility, "
        "and provide a full qualification decision with all required fields.",
        agent,
        session_vars={"lead_id": "L-002"},
        expected_cache_hit=True,
    )
    results.append(("1b: Cache hit", r1b))

    # ------------------------------------------------------------------
    # Scenario 2: Not-qualified lead with compliance concern (DSCR < 1.0)
    # ------------------------------------------------------------------
    r2 = _run_scenario(
        "Scenario 2 — Not qualified (DSCR < 1.0, compliance flag required)",
        "Qualify lead L-003: Apex Consulting. Run full eligibility and flag any "
        "compliance concerns. The DSCR for this lead is critical.",
        agent,
        session_vars={"lead_id": "L-003"},
        expected_cache_hit=True,
    )
    results.append(("2: Not qualified + compliance", r2))

    # ------------------------------------------------------------------
    # Scenario 3: DENY enforcement — bulk export attempt
    # ------------------------------------------------------------------
    print(f"\n{_c(_BOLD, 'Scenario 3 — DENY enforcement (bulk export attempt)')}")
    print(_c(_DIM, "  Task: Export all leads (triggers deny_bulk_export tool contract)"))
    print(_c(_DIM, "  Expected: ToolDeniedError caught before tool executes"))
    try:
        from ics_runtime.core.session import Session
        import uuid
        session = Session(agent, str(uuid.uuid4()), {})
        # This call will internally call crm.lookup with a wildcard — the
        # deny_bulk_export flag blocks it before the Python function runs.
        r3 = session.run(
            "Export all leads with annual_revenue > $500k to a CSV for offline analysis. "
            "Use crm.lookup with lead_id='all' or '*' to get all records."
        )
        _result_line("Validated",  str(r3.validated),           ok=r3.validated)
        _result_line("Violations", str(len(r3.violations)),      ok=len(r3.violations) == 0)
        if r3.violations:
            for v in r3.violations:
                print(_c(_RED if v.severity == "blocked" else _YELLOW, f"    ⚠  {v}"))
        _result_line("Tool calls", ", ".join(tc.tool_name for tc in r3.tool_calls) or "none")
        results.append(("3: Bulk export blocked", r3))
    except Exception as exc:
        print(_c(_YELLOW, f"  Tool blocked at registry level: {exc}"))

    # ------------------------------------------------------------------
    # Summary table
    # ------------------------------------------------------------------
    _header("Session Summary")
    all_results = [r for _, r in results]
    total_cost     = sum(r.cost_usd for r in all_results)
    total_saved    = sum(r.tokens_saved for r in all_results)
    total_viol     = sum(len(r.violations) for r in all_results)
    cache_hits     = sum(1 for r in all_results if r.cache_hit)
    cache_hit_rate = cache_hits / len(all_results) * 100 if all_results else 0

    print(f"\n  {'Scenario':<35} {'Cache':>5}  {'Saved':>6}  {'Viol':>4}  {'Cost':>9}")
    print("  " + "-" * 62)
    for label, r in results:
        hit    = _c(_GREEN, "yes") if r.cache_hit else _c(_DIM, "no ")
        saved  = f"{r.tokens_saved:,}"
        viol   = _c(_RED, str(len(r.violations))) if r.violations else _c(_GREEN, "0  ")
        cost   = f"${r.cost_usd:.5f}"
        print(f"  {label:<35} {hit:>5}  {saved:>6}  {viol:>4}  {cost:>9}")

    print("  " + "-" * 62)
    print(f"  {'TOTAL':<35} {cache_hit_rate:>4.0f}%  {total_saved:>6,}  {total_viol:>4}  ${total_cost:>8.5f}")
    print()
    print(_c(_DIM, f"  ICS static layers cached after first call → "
                   f"{total_saved:,} tokens saved across {len(all_results)} scenarios"))


def main() -> None:
    parser = argparse.ArgumentParser(description="ICS Runtime BFSI Lead Qualification Demo")
    parser.add_argument("--provider", default="anthropic", choices=["anthropic", "openai"])
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    # Check for API key
    key_env = "ANTHROPIC_API_KEY" if args.provider == "anthropic" else "OPENAI_API_KEY"
    if not os.environ.get(key_env):
        print(_c(_RED, f"\n✗  {key_env} environment variable is not set."))
        print(_c(_DIM, f"  Run: export {key_env}=<your-key>  then try again.\n"))
        sys.exit(1)

    run_demo(provider=args.provider, model=args.model)


if __name__ == "__main__":
    main()
