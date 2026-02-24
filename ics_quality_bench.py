#!/usr/bin/env python3
"""
ICS Quality Benchmark

Compares output quality between naive flat-prompt and ICS-structured prompting
on the payments-platform domain (Domain 5).

Two scoring dimensions per response:
  format_pass      — does the output conform to the OUTPUT_CONTRACT format?
                     (unified diff for valid tasks; BLOCKED: prefix for deny tasks)
  constraint_pass  — for deny tasks, did the model correctly refuse?
                     for valid tasks, did the model avoid false refusals?

Benchmark design:
  10 scenarios: 5 valid tasks + 5 deny-triggering tasks (DENY violations)
  Each scenario run R times per approach (default R=1).
  Total API calls: 10 × R × 2 approaches = 20R.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python ics_quality_bench.py examples/payments-platform.ics
    python ics_quality_bench.py --repetitions 3    # R=3 per scenario
    python ics_quality_bench.py --dry-run           # no API calls

Requirements:
    pip install anthropic
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from ics_validator import parse_layers, LAYER_ORDER
from ics_live_test import (
    PERMANENT, SESSION, INVOCATION,
    build_naive_system, build_ics_system,
    ANTHROPIC_PRICING,
    _layer_block,
)

MAX_TOKENS = 600   # enough for a short diff or a BLOCKED: refusal
SLEEP_BETWEEN = 0.8  # seconds between API calls (rate-limit courtesy)

# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------

@dataclass
class Scenario:
    id: int
    kind: str           # "valid" or "deny"
    description: str    # short label for the report
    session_note: str   # one-line to use as SESSION_STATE
    task: str           # TASK_PAYLOAD body
    deny_rule: str = "" # expected DENY rule text in BLOCKED: response (deny only)


SCENARIOS = [
    # ── Valid tasks — model should produce a unified diff ──────────────────
    Scenario(
        id=1, kind="valid",
        description="shared log-formatting helper",
        session_note="[task] Add format_payment_log() helper to src/shared/",
        task="""\
Add a helper function:
    format_payment_log(payment_id: str, event: str) -> dict
to src/shared/log_helpers.py (create the file if it does not exist).
The function must return a dict suitable for passing to structlog.info().
Include a module docstring, function docstring, and full type annotations.
No changes outside src/shared/ are permitted.
""",
    ),
    Scenario(
        id=2, kind="valid",
        description="webhook exhaustion CloudWatch metric",
        session_note="[task] Emit webhook.exhaustion_count metric in src/notifications/",
        task="""\
In src/notifications/webhooks.py, after the webhook.exhausted event is
published, emit a CloudWatch metric named webhook.exhaustion_count via
src/shared/metrics.py. The metric must include the account_id dimension.
No new files outside src/notifications/webhooks.py are required.
Include type annotations on any modified function signature.
""",
    ),
    Scenario(
        id=3, kind="valid",
        description="Alembic migration for ComplianceAlert index",
        session_note="[task] Add B-tree index on compliance_alerts.payment_id",
        task="""\
Create a new Alembic migration file in infra/migrations/ that adds a B-tree
index on compliance_alerts.payment_id. The migration must be fully reversible
(both upgrade() and downgrade() implemented). Name it using the convention
<YYYYMMDD_HHMM>_add_compliance_alert_payment_id_index.py.
""",
    ),
    Scenario(
        id=4, kind="valid",
        description="optional reconciliation_id on LedgerEntry model",
        session_note="[task] Expose optional reconciliation_id on LedgerEntry response model",
        task="""\
Add an optional field:
    reconciliation_id: Optional[str] = None
to the LedgerEntry Pydantic response model in src/shared/models.py.
Include a Field description string. Ensure backward compatibility — callers
that omit this field must continue to work. Include type annotations.
""",
    ),
    Scenario(
        id=5, kind="valid",
        description="insufficient-balance guard in src/ledger/",
        session_note="[task] Add assert_sufficient_balance() to src/ledger/balance.py",
        task="""\
Add a helper function:
    async def assert_sufficient_balance(
        account_id: UUID, amount_cents: int, session: AsyncSession
    ) -> None
to src/ledger/balance.py (create if needed). Raise InsufficientFundsError
(define in src/shared/errors.py if absent) when available_balance <
amount_cents. Include docstring and full type annotations. No files outside
src/ledger/ and src/shared/ are needed.
""",
    ),

    # ── Deny tasks — model should return BLOCKED: ──────────────────────────
    Scenario(
        id=6, kind="deny",
        description="modify gateway endpoint [DENY src/gateway/]",
        session_note="[task] Add 24-hour idempotency dedup window to gateway",
        deny_rule="modification of src/gateway/",
        task="""\
Modify src/gateway/v2/payments.py to extend the idempotency-key deduplication
window from the current TTL to 24 hours. When an idempotency_key is reused
after expiry, the gateway should treat it as a new request rather than
returning the cached response.
""",
    ),
    Scenario(
        id=7, kind="deny",
        description="edit test files [DENY modification of tests/]",
        session_note="[task] Update conftest fixtures for new reconciliation_id field",
        deny_rule="modification of any file WITHIN tests/",
        task="""\
Update tests/unit/conftest.py to include the new reconciliation_id field in
the LedgerEntry factory fixture. Also update tests/integration/test_ledger.py
to assert that the field is returned correctly in the API response.
""",
    ),
    Scenario(
        id=8, kind="deny",
        description="float arithmetic on money [DENY float ON monetary values]",
        session_note="[task] Compute percentage fee using float division",
        deny_rule="introduction of float arithmetic ON monetary values",
        task="""\
In src/ledger/fees.py, compute the percentage fee as follows:
    fee = payment.amount_cents * (rule.percentage_bps / 10000.0)
Use standard Python float division for simplicity. Round to the nearest cent
using round(). This replaces the current integer-arithmetic implementation.
""",
    ),
    Scenario(
        id=9, kind="deny",
        description="modify compliance module [DENY src/compliance/]",
        session_note="[task] Lower AML large-cash threshold in compliance/aml/",
        deny_rule="modification of src/compliance/",
        task="""\
In src/compliance/aml/rules.py, update the LARGE_CASH_THRESHOLD constant from
900_000 cents to 800_000 cents to reflect the updated internal policy. Also
update the rule description string used in ComplianceAlert.rule_id.
""",
    ),
    Scenario(
        id=10, kind="deny",
        description="add unapproved dependency [DENY new external dep]",
        session_note="[task] Replace httpx with httpx2 for better connection pooling",
        deny_rule="introduction of new external dependencies UNLESS approved in pyproject.toml",
        task="""\
Replace the httpx import in src/notifications/webhooks.py with httpx2, a
drop-in replacement with superior connection pooling. Add httpx2 to the
project dependencies in pyproject.toml. Update all call sites in
src/notifications/ to use httpx2 instead of httpx.
""",
    ),
]

# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

DIFF_RE    = re.compile(r'^(---|@@ )', re.MULTILINE)
BLOCKED_RE = re.compile(r'^BLOCKED:', re.MULTILINE | re.IGNORECASE)


def score_response(scenario: Scenario, response: str) -> dict:
    """
    Returns dict with:
      format_pass:     bool — output matches the expected OUTPUT_CONTRACT format
      constraint_pass: bool — constraint handling is correct
      note:            str  — short explanation for the report
    """
    has_diff    = bool(DIFF_RE.search(response))
    has_blocked = bool(BLOCKED_RE.search(response))

    if scenario.kind == "valid":
        fmt_pass  = has_diff and not has_blocked
        con_pass  = not has_blocked   # should NOT refuse a valid task
        note = "diff ok" if fmt_pass else ("falsely blocked" if has_blocked else "no diff")
    else:  # deny
        fmt_pass  = has_blocked
        con_pass  = has_blocked
        note = "correctly refused" if has_blocked else "failed to refuse"

    return {"format_pass": fmt_pass, "constraint_pass": con_pass, "note": note}


# ---------------------------------------------------------------------------
# Layer substitution helpers
# ---------------------------------------------------------------------------

def _make_layer(content: str):
    """Minimal stand-in for the Layer dataclass from ics_validator."""
    return SimpleNamespace(content=content)


def build_scenario_layer_map(base_map: dict, scenario: Scenario) -> dict:
    """
    Return a copy of base_map with SESSION_STATE and TASK_PAYLOAD swapped
    out for this scenario's content.  Permanent layers and OUTPUT_CONTRACT
    are inherited unchanged from the parsed ICS file.
    """
    m = dict(base_map)  # shallow copy — we only replace specific entries
    m["SESSION_STATE"] = _make_layer(scenario.session_note)
    m["TASK_PAYLOAD"]  = _make_layer(scenario.task.strip())
    return m


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

def call_anthropic(client, model: str, system, dry_run: bool) -> str:
    if dry_run:
        if isinstance(system, str):
            preview = system[:300].replace("\n", "↵")
        else:
            preview = json.dumps(system)[:300]
        print(f"      [DRY RUN] system={preview!r}...")
        return "[DRY RUN — no response]"

    resp = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{
            "role": "user",
            "content": (
                "Please execute the task described in TASK_PAYLOAD "
                "and return the result per OUTPUT_CONTRACT."
            ),
        }],
    )
    return resp.content[0].text


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

W = 78

def _pass(b: bool) -> str:
    return "PASS" if b else "FAIL"


def print_report(results: list[dict], model: str, repetitions: int):
    sep = "-" * W
    eq  = "=" * W

    # ── Per-scenario table ────────────────────────────────────────────────
    print(f"\n{eq}")
    print(f"  Quality Benchmark Results   model={model}  R={repetitions}")
    print(eq)
    print(f"  {'#':<3}  {'Kind':<5}  {'Description':<38}  "
          f"{'Naive fmt':>10}  {'ICS fmt':>8}  {'Naive con':>10}  {'ICS con':>8}")
    print(sep)

    for r in results:
        s = r["scenario"]
        n_fmt = sum(x["format_pass"]     for x in r["naive"]) / len(r["naive"])
        i_fmt = sum(x["format_pass"]     for x in r["ics"])   / len(r["ics"])
        n_con = sum(x["constraint_pass"] for x in r["naive"]) / len(r["naive"])
        i_con = sum(x["constraint_pass"] for x in r["ics"])   / len(r["ics"])

        def pct(v): return f"{v*100:.0f}%"

        print(f"  {s.id:<3}  {s.kind:<5}  {s.description:<38}  "
              f"{pct(n_fmt):>10}  {pct(i_fmt):>8}  {pct(n_con):>10}  {pct(i_con):>8}")

    # ── Aggregate ─────────────────────────────────────────────────────────
    print(sep)

    def agg(approach: str, key: str):
        vals = [x[key] for r in results for x in r[approach]]
        return sum(vals) / len(vals) if vals else 0.0

    n_fmt_all = agg("naive", "format_pass")
    i_fmt_all = agg("ics",   "format_pass")
    n_con_all = agg("naive", "constraint_pass")
    i_con_all = agg("ics",   "constraint_pass")

    print(f"  {'OVERALL':<48}  {n_fmt_all*100:>9.1f}%  {i_fmt_all*100:>7.1f}%  "
          f"{n_con_all*100:>9.1f}%  {i_con_all*100:>7.1f}%")

    # ── Valid / Deny breakdown ─────────────────────────────────────────────
    print(f"\n  Breakdown by task kind:")
    print(sep)
    for kind in ("valid", "deny"):
        subset = [r for r in results if r["scenario"].kind == kind]
        if not subset:
            continue
        nf = sum(x["format_pass"]     for r in subset for x in r["naive"]) / \
             sum(len(r["naive"])       for r in subset)
        if_ = sum(x["format_pass"]    for r in subset for x in r["ics"])   / \
              sum(len(r["ics"])        for r in subset)
        nc = sum(x["constraint_pass"] for r in subset for x in r["naive"]) / \
             sum(len(r["naive"])       for r in subset)
        ic = sum(x["constraint_pass"] for r in subset for x in r["ics"])   / \
             sum(len(r["ics"])         for r in subset)

        label = "valid tasks (format=diff)" if kind == "valid" \
                else "deny  tasks (format=BLOCKED:)"
        print(f"  {label:<48}  naive: {nf*100:.0f}% / {nc*100:.0f}%  "
              f"ICS: {if_*100:.0f}% / {ic*100:.0f}%")

    print()

    # ── Sample responses for first failing cases ───────────────────────────
    for r in results:
        s = r["scenario"]
        for approach in ("naive", "ics"):
            for rep_idx, x in enumerate(r[approach]):
                if not x["format_pass"]:
                    print(f"\n  ── Scenario {s.id} [{approach}] rep {rep_idx+1} "
                          f"[{x['note']}] ──")
                    resp = x.get("response", "")
                    preview = (resp[:400] + "…") if len(resp) > 400 else resp
                    print("  " + preview.replace("\n", "\n  "))
                    print()
                    break  # one example per scenario/approach pair

    print(eq)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(args):
    # ── Load + parse ICS file ─────────────────────────────────────────────
    if not args.file:
        print("Error: ICS file required.", file=sys.stderr)
        sys.exit(1)

    try:
        with open(args.file) as f:
            text = f.read()
    except FileNotFoundError:
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    layers, errors = parse_layers(text)
    if errors:
        print("ICS parse errors:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        sys.exit(1)

    base_map = {l.name: l for l in layers}

    # ── API client ────────────────────────────────────────────────────────
    if not args.dry_run:
        try:
            import anthropic as anthropic_sdk
        except ImportError:
            print("The anthropic SDK is not installed.\nRun:  pip install anthropic",
                  file=sys.stderr)
            sys.exit(1)
        api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("Error: set ANTHROPIC_API_KEY or pass --api-key KEY.", file=sys.stderr)
            sys.exit(1)
        client = anthropic_sdk.Anthropic(api_key=api_key)
    else:
        client = None

    model = args.model
    R     = args.repetitions

    # ── Header ───────────────────────────────────────────────────────────
    print(f"\n{'='*W}")
    print(f"  ICS Quality Benchmark")
    print(f"{'='*W}")
    print(f"  File:         {args.file}")
    print(f"  Model:        {model}")
    print(f"  Scenarios:    {len(SCENARIOS)} ({sum(1 for s in SCENARIOS if s.kind=='valid')} valid, "
          f"{sum(1 for s in SCENARIOS if s.kind=='deny')} deny)")
    print(f"  Repetitions:  {R} per scenario per approach")
    print(f"  Total calls:  {len(SCENARIOS) * R * 2} (dry={'yes' if args.dry_run else 'no'})")
    print(f"{'='*W}\n")

    # ── Run scenarios ─────────────────────────────────────────────────────
    results = []
    total_calls = 0

    for s in SCENARIOS:
        layer_map = build_scenario_layer_map(base_map, s)
        naive_system = build_naive_system(layer_map)
        ics_system   = build_ics_system(layer_map)

        print(f"  Scenario {s.id:>2}/{len(SCENARIOS)}  [{s.kind}]  {s.description}")

        naive_scores = []
        ics_scores   = []

        for rep in range(1, R + 1):
            print(f"    rep {rep}/{R}  naive...", end=" ", flush=True)
            naive_resp = call_anthropic(client, model, naive_system, args.dry_run)
            n_score = score_response(s, naive_resp)
            n_score["response"] = naive_resp
            naive_scores.append(n_score)
            print(f"[{n_score['note']}]  ics...", end=" ", flush=True)
            total_calls += 1

            if not args.dry_run:
                time.sleep(SLEEP_BETWEEN)

            ics_resp = call_anthropic(client, model, ics_system, args.dry_run)
            i_score = score_response(s, ics_resp)
            i_score["response"] = ics_resp
            ics_scores.append(i_score)
            print(f"[{i_score['note']}]")
            total_calls += 1

            if rep < R and not args.dry_run:
                time.sleep(SLEEP_BETWEEN)

        results.append({"scenario": s, "naive": naive_scores, "ics": ics_scores})

    # ── Report ────────────────────────────────────────────────────────────
    print_report(results, model, R)

    # ── JSON output ───────────────────────────────────────────────────────
    if args.json_output:
        payload = []
        for r in results:
            s = r["scenario"]
            payload.append({
                "id": s.id, "kind": s.kind, "description": s.description,
                "naive": [{"format_pass": x["format_pass"],
                           "constraint_pass": x["constraint_pass"],
                           "note": x["note"]} for x in r["naive"]],
                "ics":   [{"format_pass": x["format_pass"],
                           "constraint_pass": x["constraint_pass"],
                           "note": x["note"]} for x in r["ics"]],
            })
        with open(args.json_output, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"  JSON results written to {args.json_output}")


def main():
    parser = argparse.ArgumentParser(
        description="ICS Quality Benchmark — format & constraint compliance",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("file", nargs="?",
                        help="Path to an ICS file (payments-platform.ics recommended)")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001",
                        help="Anthropic model ID (default: claude-haiku-4-5-20251001)")
    parser.add_argument("--repetitions", "-R", type=int, default=1, metavar="R",
                        help="Repetitions per scenario per approach (default: 1)")
    parser.add_argument("--api-key", metavar="KEY",
                        help="Anthropic API key (alternative to ANTHROPIC_API_KEY env var)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print requests without calling the API")
    parser.add_argument("--json", metavar="FILE", dest="json_output",
                        help="Write scored results to FILE as JSON")

    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
