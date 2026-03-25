#!/usr/bin/env python3
"""
ICS Demo Web Application — Orion DevAssist

FastAPI backend for the ICS SDK visual demo.  Serves a single-page app that
lets users interactively explore all three SDK components.

Install:
    pip install fastapi uvicorn

Run:
    python web_app/app.py
    ANTHROPIC_API_KEY=sk-ant-... python web_app/app.py   # enables live chat
"""

import asyncio
import json
import os
import sys
import textwrap
import threading
import queue as Q
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import ics_prompt as ics
from ics_autoclassifier import ICSAutoClassifier, to_ics, to_report
from ics_validator import validate as ics_validate


# ── Helpers ───────────────────────────────────────────────────────────────────

def est_tokens(text: str) -> int:
    """Rough BPE approximation: ~4 chars per token."""
    return max(1, len(text) // 4)


# ── Shared ICS blocks (same domain as demo_app.py) ───────────────────────────

PLATFORM_CONTEXT = ics.immutable("""
    System: Orion B2B Payments Platform
    Language: Python 3.12
    Runtime: AWS Lambda + PostgreSQL 15 (RDS) + Redis 7 (ElastiCache)

    Repository layout:
      src/ledger/     — double-entry ledger, source of truth for all balances
      src/rails/      — payment rail adapters: ACH, SWIFT, SEPA, RTP
      src/gateway/    — external API (v2 stable, v3 in development)
      src/compliance/ — AML screening, OFAC sanctions, transaction monitoring
      src/settlement/ — end-of-day netting and reconciliation
      infra/cdk/      — AWS CDK infrastructure stacks
      infra/migrations/ — Alembic schema migrations
      tests/          — unit, integration, e2e

    Core data model:
      Payment.status  ENUM: PENDING → SUBMITTED → CLEARING → SETTLED | FAILED
      Payment.rail    ENUM: ACH | SWIFT | SEPA | RTP
      All monetary values stored as integer cents (BIGINT). Never use float.

    Architectural invariants:
      — All database queries MUST use parameterised statements; no SQL string interpolation.
      — Secrets MUST be retrieved via vault.Get(); os.Getenv() is prohibited for secrets.
      — All exported functions require docstrings and type annotations.
      — Async functions MUST NOT contain synchronous blocking I/O.
""")

CAPABILITIES = ics.capability("""
    ALLOW  answering questions about codebase architecture and data models
    ALLOW  explaining existing code within the repository
    ALLOW  suggesting code changes within src/ or tests/
    ALLOW  generating Alembic migrations within infra/migrations/
    DENY   suggesting changes to infra/cdk/ without explicit user confirmation
    DENY   providing advice that introduces SQL string interpolation
    DENY   providing advice that uses os.Getenv() for secrets
    REQUIRE citing the specific module path for every code reference
    REQUIRE flagging any suggestion that touches the payments state-machine transitions
""")

RESPONSE_FORMAT = ics.output_contract("""
    format:   structured markdown
    schema:   one-sentence headline summary, then detail paragraphs or fenced code blocks
    variance: code blocks MAY be omitted for conceptual questions
    on_failure: respond with "BLOCKED: <one-sentence reason>" and nothing else
""")


@ics.session
def build_session(topics: list[str], decisions: list[str]) -> str:
    if not topics and not decisions:
        return "No prior context this session."
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    lines = []
    for t in topics:
        lines.append(f"[{now}] Topic: {t}")
    for d in decisions:
        lines.append(f"[{now}] Decision: {d}")
    return "\n".join(lines)


@ics.dynamic
def build_task(msg: str) -> str:
    return f"The developer asks: {msg}"


# ── Large benchmark scenario (>1024 tokens in stable layers to trigger real caching) ──

LARGE_IMMUTABLE = ics.immutable("""
    System: Orion B2B Payments Platform
    Version: 4.2.1
    Language: Python 3.12
    Runtime: AWS Lambda (arm64) + PostgreSQL 15 (RDS Multi-AZ) + Redis 7 (ElastiCache cluster)
    Message queue: Amazon SQS (standard + FIFO) + SNS fan-out
    Observability: OpenTelemetry → Datadog (traces, metrics, logs)
    Secret management: HashiCorp Vault (AppRole auth)
    CI/CD: GitHub Actions → ECR → Lambda via AWS CDK

    ── Repository layout ────────────────────────────────────────────────────────
    src/
      ledger/
        __init__.py          — public API: post_entry(), get_balance(), get_ledger()
        models.py            — LedgerEntry, Account, JournalEntry SQLAlchemy models
        service.py           — double-entry posting logic, idempotency via entry_key
        queries.py           — parameterised read queries, cursor-based pagination
      rails/
        __init__.py          — RailAdapter protocol + registry
        ach/
          adapter.py         — ACH rail: NACHA file generation, R-code handling
          idempotency.py     — Redis-based idempotency keys (TTL 72 h)
          retry.py           — exponential backoff, NOC/return handling
        swift/
          adapter.py         — SWIFT MT103/MT202, UETR generation
          gpi.py             — g4C tracker integration
        sepa/
          adapter.py         — SEPA Credit Transfer (SCT) + Instant (SCT Inst)
          pain008.py         — ISO 20022 pain.008 XML generation
        rtp/
          adapter.py         — RTP (The Clearing House) connector
          connector.py       — TCH production + sandbox endpoints
      gateway/
        v2/
          routes.py          — stable v2 REST endpoints (frozen — no breaking changes)
          auth.py            — HMAC-SHA256 request signing, API key rotation
          rate_limit.py      — per-tenant token bucket (Redis INCR + EXPIRE)
        v3/
          routes.py          — v3 in development; feature-flagged, not GA
          openapi.py         — auto-generated OpenAPI 3.1 spec
      compliance/
        aml.py               — transaction monitoring rules, SAR flagging
        ofac.py              — OFAC SDN list screening (nightly refresh from OFAC API)
        kyc.py               — KYC status checks via Persona integration
        audit.py             — immutable audit log (append-only, S3 + Athena)
      settlement/
        netting.py           — end-of-day multilateral netting engine
        reconciliation.py    — nostro/vostro position reconciliation
        reports.py           — settlement reports (CSV + PDF) for operations team
    infra/
      cdk/                   — AWS CDK stacks (TypeScript); changes require infra review
      migrations/            — Alembic migration scripts; auto-generated via alembic revision
    tests/
      unit/                  — pytest, 100% coverage required for src/ledger and src/rails
      integration/           — LocalStack-based AWS service mocks
      e2e/                   — staging environment, real bank sandbox credentials

    ── Core data model ──────────────────────────────────────────────────────────
    Payment
      id            UUID primary key
      tenant_id     UUID foreign key → tenants.id
      rail          ENUM: ACH | SWIFT | SEPA | RTP
      status        ENUM: PENDING → SUBMITTED → CLEARING → SETTLED | FAILED | RETURNED
      amount        BIGINT (integer cents, always)
      currency      CHAR(3) ISO 4217
      entry_key     TEXT UNIQUE (idempotency — caller-supplied or auto-generated)
      created_at    TIMESTAMPTZ
      settled_at    TIMESTAMPTZ nullable
      metadata      JSONB (rail-specific fields, e.g. UETR for SWIFT)

    LedgerEntry
      id            UUID
      payment_id    UUID foreign key → payments.id
      account_id    UUID foreign key → accounts.id
      entry_type    ENUM: DEBIT | CREDIT
      amount        BIGINT
      posted_at     TIMESTAMPTZ
      entry_key     TEXT UNIQUE (must match parent Payment.entry_key)

    Account
      id            UUID
      tenant_id     UUID
      account_type  ENUM: OPERATING | SETTLEMENT | FEE | SUSPENSE
      currency      CHAR(3)
      balance       BIGINT (derived — never store directly; computed from ledger entries)

    ── Architectural invariants ─────────────────────────────────────────────────
      — All database queries MUST use parameterised statements (SQLAlchemy ORM or text() with bindparams).
        SQL string interpolation or f-strings in queries are a critical violation.
      — Secrets (DB passwords, API keys, Vault tokens) MUST be retrieved via vault.Get(path).
        os.Getenv() and os.environ are prohibited for secrets. Use config.py for non-secret env vars.
      — All exported functions and public methods MUST have docstrings and full type annotations.
      — Async functions MUST NOT contain synchronous blocking I/O (no requests, no psycopg2 directly).
        Use asyncpg or SQLAlchemy async session; use httpx.AsyncClient for HTTP.
      — Monetary values are ALWAYS integer cents (BIGINT). Float arithmetic on money is a critical violation.
      — All Payment status transitions must go through ledger.service.post_entry(); direct ORM updates
        to Payment.status are prohibited.
      — idempotency: every write operation must accept and honour an entry_key; duplicate entry_keys
        must return the original result without re-executing the operation.
      — Compliance screening (OFAC) must be invoked before any Payment leaves PENDING status.
        Skipping compliance checks is a critical violation.
""")

LARGE_CAPABILITIES = ics.capability("""
    ALLOW  answering questions about codebase architecture, data models, and design decisions
    ALLOW  explaining existing code within any src/ module
    ALLOW  suggesting code changes within src/ or tests/ with full type annotations and docstrings
    ALLOW  generating Alembic migration scripts within infra/migrations/
    ALLOW  explaining payment rail behaviour (ACH, SWIFT, SEPA, RTP) and their failure modes
    ALLOW  advising on idempotency patterns, retry logic, and distributed transaction safety
    ALLOW  explaining compliance requirements (OFAC, AML, KYC) in the context of this codebase
    DENY   suggesting changes to infra/cdk/ without explicit user confirmation and infra team review
    DENY   providing any advice that introduces SQL string interpolation or f-string queries
    DENY   providing any advice that uses os.Getenv() or os.environ for secret retrieval
    DENY   suggesting direct ORM updates to Payment.status (must go through ledger.service)
    DENY   suggesting float arithmetic for monetary amounts
    DENY   suggesting any code that bypasses compliance.ofac screening
    REQUIRE citing the specific module path (e.g. src/rails/ach/idempotency.py) for every code reference
    REQUIRE flagging any suggestion that modifies Payment state-machine transitions as HIGH RISK
    REQUIRE flagging any suggestion that touches compliance/ as requiring compliance team review
    REQUIRE all suggested code to include type annotations and a one-line docstring minimum
""")

LARGE_OUTPUT = ics.output_contract("""
    format:   structured markdown
    schema:
      1. One-sentence headline summarising the answer
      2. Context paragraph (why this matters in the Orion platform)
      3. Implementation section with fenced code blocks (Python 3.12, fully typed)
      4. Caveats / risks section (flag state-machine or compliance impacts if relevant)
      5. References: list of src/ module paths relevant to the answer
    variance:
      — Sections 3–4 MAY be omitted for purely conceptual questions
      — Code blocks MUST include import statements
    on_failure: "BLOCKED: <one-sentence reason>" and nothing else
""")

# ── Scenario data ─────────────────────────────────────────────────────────────

SCENARIOS: dict[str, dict] = {
    "payments": {
        "id":          "payments",
        "name":        "Orion DevAssist",
        "description": "Developer assistant for the B2B Payments Platform",
        "icon":        "💳",
        "immutable":   PLATFORM_CONTEXT.content,
        "capability":  CAPABILITIES.content,
        "session":     {"topics": ["ACH rail implementation"],
                        "decisions": ["add retry logic to NACHA file generator"]},
        "task":        "How should I implement idempotency for ACH payment submissions?",
        "output":      RESPONSE_FORMAT.content,
    },
    "code-review": {
        "id":          "code-review",
        "name":        "PR Review Agent",
        "description": "Automated pull-request review agent for the monorepo",
        "icon":        "🔍",
        "immutable":   textwrap.dedent("""\
            System: automated pull request review agent
            Owner: Platform Engineering — Developer Experience team
            Target: monorepo — Go 1.22 and Python 3.12

            Repository invariants:
              — All HTTP handlers MUST return structured JSON errors
              — All database queries MUST use parameterized statements
              — Secrets MUST be retrieved from vault.Get(); os.Getenv() is prohibited
              — All exported functions and public methods MUST have doc comments
              — Context propagation: Go functions accepting context.Context MUST pass it downstream
            """),
        "capability":  textwrap.dedent("""\
            ALLOW   read access to PR diff content
            ALLOW   flagging issues at severity: CRITICAL, HIGH, MEDIUM, LOW, INFO
            ALLOW   suggesting corrective code snippets within the reviewed file's language
            DENY    modification of files outside the PR diff
            DENY    approval of PRs containing CRITICAL findings
            REQUIRE flagging SQL string interpolation as CRITICAL
            REQUIRE flagging os.Getenv() for secrets as CRITICAL
            REQUIRE citing the specific invariant violated for every finding
            """),
        "session":     {"topics":    ["PR #1847 — Add user impersonation endpoint to admin service"],
                        "decisions": ["auth.go reviewed — 2 HIGH findings; test file reviewed — PASS"]},
        "task":        "Review internal/admin/impersonate.go from PR #1847.",
        "output":      textwrap.dedent("""\
            format:   JSON
            schema:   { "file": string, "findings": [{severity, line_range, description, suggested_fix}],
                        "finding_count": integer, "approved": boolean }
            variance: findings MAY be empty array if no issues found
            on_failure: { "error": "string" }
            """),
    },
    "rag": {
        "id":          "rag",
        "name":        "RAG Knowledge Base",
        "description": "Retrieval-augmented assistant over the engineering wiki",
        "icon":        "📚",
        "immutable":   textwrap.dedent("""\
            System: internal knowledge-base Q&A assistant
            Owner: Engineering Enablement team
            Corpus: Confluence wiki — 12,000 pages — standards, runbooks, ADRs
            Retrieval: text-embedding-3-large, k=5 chunks, cosine similarity ≥ 0.72
            Corpus last indexed: 2025-01-20

            Grounding rules:
              — Answers MUST be grounded exclusively in retrieved chunks
              — Conflicting sources: cite both and flag the conflict explicitly
              — Flag anything dated before 2024-01-01 as potentially outdated
            """),
        "capability":  textwrap.dedent("""\
            ALLOW   answering questions grounded in retrieved context
            ALLOW   citing specific Confluence pages by title
            ALLOW   flagging gaps where retrieved context is insufficient
            DENY    answering from parametric knowledge when context is absent
            DENY    generating new policies not present in the corpus
            REQUIRE citing at least one source for every factual claim
            REQUIRE flagging stale content (pre-2024) explicitly
            """),
        "session":     {"topics":    ["on-call rotation policy", "incident severity levels"],
                        "decisions": ["escalation runbook located in ENG-OPS Confluence space"]},
        "task":        "What is the process for escalating a P0 incident to the VP of Engineering?",
        "output":      textwrap.dedent("""\
            format:   structured markdown
            schema:   direct answer paragraph, then 'Sources:' section with cited pages
            variance: 'Sources:' MAY be omitted when answer is from a single inline citation
            on_failure: "Insufficient information in retrieved context to answer this."
            """),
    },
    "payments-large": {
        "id":          "payments-large",
        "name":        "Orion DevAssist (Full — benchmark)",
        "description": "Full production-scale prompt — stable layers exceed 1024 tokens to demonstrate real API caching",
        "icon":        "🏦",
        "immutable":   LARGE_IMMUTABLE.content,
        "capability":  LARGE_CAPABILITIES.content,
        "session":     {"topics":    ["ACH idempotency design", "NACHA file retry logic"],
                        "decisions": ["use Redis entry_key with 72h TTL", "R03/R04 returns trigger automatic retry"]},
        "task":        "How should I implement idempotency for ACH payment submissions to handle duplicate requests safely?",
        "output":      LARGE_OUTPUT.content,
    },
}

CLASSIFY_SAMPLES: dict[str, dict] = {
    "payments-flat": {
        "id":   "payments-flat",
        "name": "Payments Platform — Legacy Flat",
        "text": textwrap.dedent("""\
            You are a developer assistant for the Orion B2B Payments Platform.
            The system is written in Python 3.12 and runs on AWS Lambda with PostgreSQL 15 and Redis 7.

            The repository has these main areas: src/ledger for the double-entry ledger,
            src/rails for payment rail adapters (ACH, SWIFT, SEPA, RTP), src/gateway for
            the external API, src/compliance for AML and OFAC screening, and src/settlement
            for reconciliation. There is also infra/cdk for CDK stacks and
            infra/migrations for Alembic migrations.

            Payment status values are PENDING, SUBMITTED, CLEARING, SETTLED, and FAILED.
            All monetary values are stored as integers in cents.

            ALLOW answering questions about the codebase and suggesting code changes in src/ or tests/.
            ALLOW generating Alembic migrations in infra/migrations/.
            DENY suggesting changes to infra/cdk/ without user confirmation.
            DENY suggesting SQL string interpolation.
            DENY using os.Getenv() for secrets — use vault.Get() instead.

            The user has been discussing the ACH rail implementation.
            They decided to add retry logic to the NACHA file generator.

            The developer is now asking: How should I implement idempotency for ACH payment submissions?

            Respond in structured markdown with a one-sentence headline, then detail paragraphs or
            code blocks. If you cannot help, respond with BLOCKED followed by a brief reason.
        """),
    },
    "log-analysis-flat": {
        "id":   "log-analysis-flat",
        "name": "Log Analysis Agent — Legacy",
        "text": textwrap.dedent("""\
            You are a log analysis agent for a distributed microservices platform. The platform runs
            on Kubernetes 1.29 with services written in Go and Python. Logs are stored in
            OpenSearch 2.11 via Fluent Bit.

            You can query OpenSearch indexes for log entries. You can correlate events across
            services using trace IDs. You can identify error patterns and anomalies.
            You cannot modify any infrastructure or application code.
            You cannot access databases directly — only through log queries.

            The current investigation is for incident INC-2891. High error rates in the
            payment-processor service started at 14:32 UTC. The payment-gateway shows upstream
            timeout errors correlating with the payment-processor errors.

            Current task: Identify the root cause of the payment-processor errors by querying
            OpenSearch for the time window 14:30–14:45 UTC on 2025-01-15.

            Provide a root cause hypothesis with supporting log evidence. Include specific log
            entries as quotes. Rate your confidence LOW/MEDIUM/HIGH. List next investigation steps.
        """),
    },
    "content-mod-flat": {
        "id":   "content-mod-flat",
        "name": "Content Moderation — Mixed",
        "text": textwrap.dedent("""\
            You are a content moderation assistant for a global community platform.

            Policy categories: CRITICAL (immediate removal + law enforcement referral),
            HIGH (removal, no appeal), MEDIUM (removal with appeal), LOW (warning/label),
            CLEAR (no violation).

            You are allowed to flag content at any severity level and recommend removal actions.
            You cannot actually remove content. You cannot view user account history.
            You must not apply different standards based on user identity or account age.

            User report #4492: A user reported a post in the r/cooking community.
            The post contains a recipe the reporter claims is dangerous.
            The moderator previously reviewed and cleared three similar posts from the same author.

            Please review the reported content and provide a moderation decision using the
            policy categories above. Explain your reasoning clearly.
        """),
    },
}


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="ICS Demo — Orion DevAssist", version="1.0.0")


# ── Request models ────────────────────────────────────────────────────────────

class TextRequest(BaseModel):
    text: str


class BuildRequest(BaseModel):
    scenario_id: str


class CompareRequest(BaseModel):
    scenario_id: str
    naive_text: Optional[str] = None


class BenchmarkRequest(BaseModel):
    scenario_id: str
    n_calls: int = 3  # how many ICS calls to make (max 5)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/status")
async def api_status():
    try:
        import anthropic as _a  # noqa: F401
        installed = True
    except ImportError:
        installed = False
    return {
        "anthropic_installed": installed,
        "api_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


@app.get("/api/samples")
async def get_samples():
    examples_dir = ROOT / "examples"
    validate_samples = []
    for f in sorted(examples_dir.glob("*.ics")):
        validate_samples.append({
            "id":   f.stem,
            "name": f.stem.replace("-", " ").title(),
            "text": f.read_text(encoding="utf-8"),
        })
    return {
        "build_scenarios": [
            {"id": s["id"], "name": s["name"],
             "description": s["description"], "icon": s["icon"]}
            for s in SCENARIOS.values()
        ],
        "classify_samples": list(CLASSIFY_SAMPLES.values()),
        "validate_samples": validate_samples,
    }


@app.post("/api/build")
async def api_build(req: BuildRequest):
    scenario = SCENARIOS.get(req.scenario_id)
    if not scenario:
        raise HTTPException(404, f"Unknown scenario '{req.scenario_id}'")

    blocks = [
        ics.immutable(scenario["immutable"]),
        ics.capability(scenario["capability"]),
        build_session(scenario["session"]["topics"], scenario["session"]["decisions"]),
        build_task(scenario["task"]),
        ics.output_contract(scenario["output"]),
    ]

    compiled = ics.compile(*blocks, warn=False)
    total    = sum(est_tokens(b.content) for b in blocks)
    cached   = sum(est_tokens(b.content) for b in blocks if b.cache_eligible)

    return {
        "scenario_name": scenario["name"],
        "blocks": [
            {
                "layer":         b.layer.value,
                "content":       b.content,
                "cache_eligible": b.cache_eligible,
                "tokens":        est_tokens(b.content),
            }
            for b in blocks
        ],
        "compiled":      compiled,
        "total_tokens":  total,
        "cached_tokens": cached,
        "cache_pct":     round(cached / total * 100) if total else 0,
    }


@app.post("/api/classify")
async def api_classify(req: TextRequest):
    classifier = ICSAutoClassifier()
    result     = classifier.classify(req.text)
    report     = to_report(result)
    return {
        "summary":    report["summary"],
        "blocks":     report["blocks"],
        "warnings":   report["warnings"],
        "ics_output": to_ics(result),
    }


@app.post("/api/validate")
async def api_validate(req: TextRequest):
    result = ics_validate(req.text)
    return {
        "compliant":  result.compliant,
        "violations": [
            {"step": v.step, "rule": v.rule, "message": v.message}
            for v in result.violations
        ],
        "warnings": result.warnings,
    }


@app.post("/api/compare")
async def api_compare(req: CompareRequest):
    scenario = SCENARIOS.get(req.scenario_id)
    if not scenario:
        raise HTTPException(404, f"Unknown scenario '{req.scenario_id}'")

    blocks = [
        ics.immutable(scenario["immutable"]),
        ics.capability(scenario["capability"]),
        build_session(scenario["session"]["topics"], scenario["session"]["decisions"]),
        build_task(scenario["task"]),
        ics.output_contract(scenario["output"]),
    ]

    # Fallback naive text: join all layer content as plain text
    naive_text  = req.naive_text or "\n\n".join(b.content for b in blocks)
    naive_tok   = est_tokens(naive_text)
    ics_total   = sum(est_tokens(b.content) for b in blocks)
    dynamic_tok = sum(est_tokens(b.content) for b in blocks if not b.cache_eligible)

    savings = []
    for n in [10, 25, 50, 100]:
        naive    = naive_tok * n
        ics_cost = ics_total + dynamic_tok * (n - 1)
        saved    = max(0, naive - ics_cost)
        pct      = round(saved / naive * 100) if naive else 0
        savings.append({"calls": n, "naive": naive, "ics": ics_cost, "saved": saved, "pct": pct})

    return {
        "scenario_name": scenario["name"],
        "layers": [
            {"name": b.layer.value, "tokens": est_tokens(b.content),
             "cache_eligible": b.cache_eligible}
            for b in blocks
        ],
        "naive_tokens":   naive_tok,
        "ics_total":      ics_total,
        "dynamic_tokens": dynamic_tok,
        "savings":        savings,
    }


@app.post("/api/benchmark")
async def api_benchmark(req: BenchmarkRequest):
    """
    Make real Anthropic API calls and return actual token usage per call.
    Uses max_tokens=1 to keep output cost negligible — we only care about input usage.
    Requires ANTHROPIC_API_KEY to be set.
    """
    try:
        import anthropic
    except ImportError:
        raise HTTPException(400, "anthropic package not installed. Run: pip install anthropic")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(400, "ANTHROPIC_API_KEY is not set")

    scenario = SCENARIOS.get(req.scenario_id)
    if not scenario:
        raise HTTPException(404, f"Unknown scenario '{req.scenario_id}'")

    n_calls = max(1, min(req.n_calls, 5))  # clamp 1–5

    blocks = [
        ics.immutable(scenario["immutable"]),
        ics.capability(scenario["capability"]),
        build_session(scenario["session"]["topics"], scenario["session"]["decisions"]),
        build_task(scenario["task"]),
        ics.output_contract(scenario["output"]),
    ]

    # Build system parts with cache_control on eligible layers
    system_parts = []
    for block in blocks:
        part: dict = {"type": "text", "text": str(block)}
        if block.cache_eligible:
            part["cache_control"] = {"type": "ephemeral"}
        system_parts.append(part)

    client = anthropic.Anthropic(api_key=api_key)
    per_call = []

    for i in range(n_calls):
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",  # cheapest model — we only need usage data
                max_tokens=1,
                system=system_parts,
                messages=[{"role": "user", "content": scenario["task"]}],
            )
            u = resp.usage
            inp          = u.input_tokens
            cache_create = getattr(u, "cache_creation_input_tokens", 0) or 0
            cache_read   = getattr(u, "cache_read_input_tokens",    0) or 0
            out          = u.output_tokens
            per_call.append({
                "call":          i + 1,
                "input_tokens":        inp,
                "cache_creation":      cache_create,
                "cache_read":          cache_read,
                "output_tokens":       out,
                "total_billed":        inp + cache_create + cache_read,
                "cache_hit":           cache_read > 0,
            })
        except Exception as exc:
            per_call.append({
                "call":    i + 1,
                "error":   str(exc),
            })

    # Derive ground-truth summary from call 1
    first = per_call[0] if per_call and "error" not in per_call[0] else None
    summary = {}

    # Detect whether caching was actually triggered across any call
    any_cache_write = any(c.get("cache_creation", 0) > 0 for c in per_call if "error" not in c)
    any_cache_read  = any(c.get("cache_read",     0) > 0 for c in per_call if "error" not in c)
    cacheable_est   = sum(est_tokens(str(b)) for b in blocks if b.cache_eligible)
    cache_threshold = 1024  # Anthropic minimum tokens to cache a block

    if first:
        real_total   = first["input_tokens"] + first["cache_creation"]
        real_dynamic = first["input_tokens"]   # non-cached portion
        real_cached  = first["cache_creation"]

        est_total   = est_tokens("".join(str(b) for b in blocks))
        est_dynamic = sum(est_tokens(str(b)) for b in blocks if not b.cache_eligible)

        # cost model: input = $0.80/MTok, cache_write = $1.00/MTok, cache_read = $0.08/MTok (Haiku)
        PRICE_INPUT        = 0.80  / 1_000_000
        PRICE_CACHE_WRITE  = 1.00  / 1_000_000
        PRICE_CACHE_READ   = 0.08  / 1_000_000
        PRICE_OUTPUT       = 4.00  / 1_000_000

        naive_cost_per_call = real_total * PRICE_INPUT
        ics_call1_cost = (
            real_dynamic * PRICE_INPUT +
            real_cached  * PRICE_CACHE_WRITE
        )
        ics_repeat_cost = (
            real_dynamic * PRICE_INPUT +
            real_cached  * PRICE_CACHE_READ
        )

        calls_data = []
        for n in [1, 2, 3, 5, 10, 25, 50, 100]:
            naive_total_cost = naive_cost_per_call * n
            ics_total_cost   = ics_call1_cost + ics_repeat_cost * max(0, n - 1)
            saved_cost       = max(0.0, naive_total_cost - ics_total_cost)
            pct              = round(saved_cost / naive_total_cost * 100) if naive_total_cost else 0
            calls_data.append({
                "calls":      n,
                "naive_cost": round(naive_total_cost, 6),
                "ics_cost":   round(ics_total_cost,   6),
                "saved_cost": round(saved_cost,        6),
                "pct":        pct,
            })

        cache_warning = None
        if not any_cache_write:
            cache_warning = (
                f"Caching was NOT triggered. Anthropic requires a minimum of {cache_threshold} tokens "
                f"in a cacheable block. This scenario's stable layers are only ~{cacheable_est} tokens "
                f"(estimated). Use the 'Orion DevAssist (Full — benchmark)' scenario which has "
                f"1024+ tokens in stable layers to see real cache hits."
            )
        elif any_cache_write and not any_cache_read:
            cache_warning = (
                "Cache was written on call 1 but no cache reads were observed. "
                "Calls may have been too far apart (cache TTL is 5 minutes), or the scenario "
                "is at the edge of the minimum token threshold."
            )

        summary = {
            "real_total_tokens":   real_total,
            "real_dynamic_tokens": real_dynamic,
            "real_cached_tokens":  real_cached,
            "est_total_tokens":    est_total,
            "est_dynamic_tokens":  est_dynamic,
            "estimate_error_pct":  round(abs(est_total - real_total) / real_total * 100, 1) if real_total else None,
            "price_model":         "claude-haiku-4-5-20251001 — input $0.80/MTok, cache_write $1.00/MTok, cache_read $0.08/MTok",
            "cost_savings":        calls_data,
            "cache_warning":       cache_warning,
            "cache_threshold":     cache_threshold,
            "cacheable_est_tokens": cacheable_est,
        }

    return {"per_call": per_call, "summary": summary}


@app.get("/api/chat/stream")
async def api_chat_stream(
    message:   str,
    topics:    str = Query(default=""),
    decisions: str = Query(default=""),
):
    topics_list    = [t.strip() for t in topics.split(",")    if t.strip()]
    decisions_list = [d.strip() for d in decisions.split(",") if d.strip()]

    q: Q.Queue = Q.Queue()

    def _sync_stream() -> None:
        """Run synchronous Anthropic streaming in a background thread."""
        try:
            import anthropic
        except ImportError:
            q.put({"type": "error", "message": "anthropic not installed. Run: pip install anthropic"})
            q.put(None)
            return

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            q.put({"type": "error", "message": "ANTHROPIC_API_KEY is not set"})
            q.put(None)
            return

        client = anthropic.Anthropic(api_key=api_key)
        blocks = [
            PLATFORM_CONTEXT,
            CAPABILITIES,
            build_session(topics_list, decisions_list),
            build_task(message),
            RESPONSE_FORMAT,
        ]
        system_parts = []
        for block in blocks:
            part: dict = {"type": "text", "text": str(block)}
            if block.cache_eligible:
                part["cache_control"] = {"type": "ephemeral"}
            system_parts.append(part)

        try:
            with client.messages.stream(
                model="claude-opus-4-6",
                max_tokens=1024,
                system=system_parts,
                messages=[{"role": "user", "content": message}],
            ) as stream:
                for text in stream.text_stream:
                    q.put({"type": "text", "text": text})

                final = stream.get_final_message()
                usage = final.usage
                q.put({
                    "type":  "done",
                    "usage": {
                        "input":       usage.input_tokens,
                        "output":      usage.output_tokens,
                        "cache_read":  getattr(usage, "cache_read_input_tokens", 0) or 0,
                        "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
                    },
                })
        except Exception as exc:
            q.put({"type": "error", "message": str(exc)})
        finally:
            q.put(None)  # sentinel

    threading.Thread(target=_sync_stream, daemon=True).start()

    async def _sse_generator():
        while True:
            item = await asyncio.to_thread(q.get)
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    api_key_status = "✓  set" if os.environ.get("ANTHROPIC_API_KEY") else "✗  not set (chat tab disabled)"
    print(f"ICS Demo  →  http://localhost:{port}")
    print(f"API key   →  {api_key_status}")
    uvicorn.run(app, host=host, port=port, reload=False)
