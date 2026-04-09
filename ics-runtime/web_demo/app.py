#!/usr/bin/env python3
"""
ICS Runtime — BFSI Lead Qualification Web Demo

FastAPI backend that exposes the ics-runtime library through a clean REST/SSE
API consumed by the single-page frontend in index.html.

Run:
    cd ics-runtime
    pip install -e ".[dev]" fastapi uvicorn
    python web_demo/app.py

    # With API key in env:
    ANTHROPIC_API_KEY=sk-ant-... python web_demo/app.py
"""

from __future__ import annotations

import json
import os
import queue as Q
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent   # ics-runtime/
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))            # ics-spec/ (for demo. imports)

# ── .env loader ───────────────────────────────────────────────────────────────
def _load_dotenv() -> None:
    for candidate in [ROOT / ".env", ROOT.parent / ".env"]:
        if not candidate.exists():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip(); val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
        break

_load_dotenv()

# ── Log bus ───────────────────────────────────────────────────────────────────
_LOG_HISTORY: deque = deque(maxlen=300)
_LOG_SUBS: list[Q.Queue] = []
_LOG_LOCK = threading.Lock()


def _emit(level: str, msg: str) -> None:
    entry = {"ts": datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3],
             "level": level, "msg": msg}
    with _LOG_LOCK:
        _LOG_HISTORY.append(entry)
        for q in _LOG_SUBS:
            try: q.put_nowait(entry)
            except Q.Full: pass


def log_info(msg: str)  -> None: _emit("info", msg)
def log_ok(msg: str)    -> None: _emit("ok",   msg)
def log_warn(msg: str)  -> None: _emit("warn", msg)
def log_error(msg: str) -> None: _emit("error", msg)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="ICS Runtime Demo")

# ── Lead catalogue (mirrors tools.py mock data) ───────────────────────────────
LEADS = {
    "L-001": {
        "id": "L-001",
        "company": "Nexus Logistics Ltd",
        "industry": "Logistics",
        "revenue_usd": 1_500_000,
        "dscr": 1.42,
        "age_months": 84,
        "liens_usd": 0,
        "tier": "prime",
    },
    "L-002": {
        "id": "L-002",
        "company": "Beta Foods Inc",
        "industry": "Food Service",
        "revenue_usd": 890_000,
        "dscr": 1.18,
        "age_months": 36,
        "liens_usd": 55_000,
        "tier": "subprime",
    },
    "L-003": {
        "id": "L-003",
        "company": "Apex Consulting",
        "industry": "Consulting",
        "revenue_usd": 250_000,
        "dscr": 0.95,
        "age_months": 18,
        "liens_usd": 0,
        "tier": "decline",
    },
}

# ── Runtime singleton — lazily initialised ────────────────────────────────────
_agent_lock = threading.Lock()
_agent: Any = None          # ics_runtime Agent
_agent_provider = ""


def _get_agent(provider: str = "anthropic") -> Any:
    global _agent, _agent_provider
    with _agent_lock:
        if _agent is None or _agent_provider != provider:
            from demo.bfsi_agent.agent_definition import make_agent
            log_info(f"Initialising BFSI agent (provider={provider})")
            _agent = make_agent(provider=provider)
            _agent_provider = provider
            log_ok(f"Agent ready — model={_agent._model}")
        return _agent


# ── Cumulative session metrics store ──────────────────────────────────────────
_all_results: list[dict] = []
_results_lock = threading.Lock()

# ─────────────────────────────────────────────────────────────────────────────
# REST endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/status")
def api_status() -> dict:
    anthropic_key = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    openai_key    = bool(os.environ.get("OPENAI_API_KEY",    "").strip())
    try:
        import anthropic as _a
        anthropic_ver = getattr(_a, "__version__", "?")
    except ImportError:
        anthropic_ver = None
    try:
        import openai as _o
        openai_ver = getattr(_o, "__version__", "?")
    except ImportError:
        openai_ver = None

    return {
        "anthropic_key": anthropic_key,
        "openai_key":    openai_key,
        "anthropic_version": anthropic_ver,
        "openai_version":    openai_ver,
    }


@app.get("/api/leads")
def api_leads() -> dict:
    return {"leads": list(LEADS.values())}


class QualifyRequest(BaseModel):
    lead_id: str
    provider: str = "anthropic"
    custom_task: str = ""   # optional override


@app.post("/api/qualify")
def api_qualify(req: QualifyRequest) -> dict:
    """Run full qualification synchronously and return structured result."""
    lead = LEADS.get(req.lead_id)
    if not lead is None and req.lead_id not in LEADS:
        raise HTTPException(404, f"Lead '{req.lead_id}' not found")

    try:
        agent = _get_agent(req.provider)
    except Exception as exc:
        log_error(f"Agent init failed: {exc}")
        raise HTTPException(500, str(exc))

    task = req.custom_task or (
        f"Qualify lead {req.lead_id}: {lead['company'] if lead else req.lead_id}. "
        "Look up their CRM data, run the eligibility check, and provide a full "
        "qualification decision with all required fields."
    )

    log_info(f"[qualify] {req.lead_id} via {req.provider}")

    from ics_runtime.core.session import Session
    import uuid

    session_vars = {}
    if lead:
        session_vars["lead_id"] = req.lead_id

    try:
        session = Session(agent, str(uuid.uuid4()), session_vars)
        t0 = time.monotonic()
        result = session.run(task)
        elapsed = int((time.monotonic() - t0) * 1000)

        tool_summary = [
            {
                "name": tc.tool_name,
                "input": tc.input,
                "output": tc.output if not isinstance(tc.output, dict) or len(str(tc.output)) < 300
                          else {k: v for k, v in tc.output.items()},
                "duration_ms": tc.duration_ms,
                "blocked": tc.blocked,
            }
            for tc in result.tool_calls
        ]

        violation_summary = [
            {"rule": v.rule, "kind": v.kind, "severity": v.severity, "evidence": v.evidence[:120]}
            for v in result.violations
        ]

        parsed_dict = None
        if result.parsed:
            try:
                parsed_dict = result.parsed.model_dump()
            except Exception:
                parsed_dict = str(result.parsed)

        out = {
            "lead_id":        req.lead_id,
            "provider":       result.provider,
            "model":          result.model,
            "session_id":     result.session_id,
            "text":           result.text,
            "validated":      result.validated,
            "violations":     violation_summary,
            "parsed":         parsed_dict,
            "cache_hit":      result.cache_hit,
            "cache_write":    result.cache_write,
            "tokens_saved":   result.tokens_saved,
            "input_tokens":   result.input_tokens,
            "output_tokens":  result.output_tokens,
            "cache_write_tokens": result.cache_write_tokens,
            "cost_usd":       result.cost_usd,
            "latency_ms":     result.latency_ms,
            "tool_calls":     tool_summary,
        }

        log_ok(
            f"[qualify] {req.lead_id} done — "
            f"cache_hit={result.cache_hit} "
            f"saved={result.tokens_saved} "
            f"violations={len(result.violations)} "
            f"cost=${result.cost_usd:.4f}"
        )

        with _results_lock:
            _all_results.append(out)

        return out

    except Exception as exc:
        log_error(f"[qualify] {req.lead_id} failed: {exc}")
        raise HTTPException(500, str(exc))


@app.get("/api/qualify/stream")
def api_qualify_stream(
    lead_id: str = Query(...),
    provider: str = Query(default="anthropic"),
    custom_task: str = Query(default=""),
) -> StreamingResponse:
    """Server-sent events stream — yields log lines while qualification runs,
    then a final 'result' event with the full JSON payload."""

    lead = LEADS.get(lead_id)

    def generate():
        log_queue: Q.Queue = Q.Queue(maxsize=100)
        with _LOG_LOCK:
            _LOG_SUBS.append(log_queue)

        def drain_logs():
            while True:
                try:
                    entry = log_queue.get(timeout=0.05)
                    yield f"data: {json.dumps({'type': 'log', **entry})}\n\n"
                except Q.Empty:
                    break

        yield f"data: {json.dumps({'type': 'start', 'lead_id': lead_id})}\n\n"
        yield from drain_logs()

        result_payload = {}
        error_msg = ""

        def run_qualification():
            nonlocal result_payload, error_msg
            try:
                req = QualifyRequest(lead_id=lead_id, provider=provider, custom_task=custom_task)
                result_payload = api_qualify(req)
            except HTTPException as exc:
                error_msg = exc.detail
            except Exception as exc:
                error_msg = str(exc)

        t = threading.Thread(target=run_qualification, daemon=True)
        t.start()

        while t.is_alive():
            yield from drain_logs()
            time.sleep(0.05)
        t.join()
        yield from drain_logs()

        with _LOG_LOCK:
            try: _LOG_SUBS.remove(log_queue)
            except ValueError: pass

        if error_msg:
            yield f"data: {json.dumps({'type': 'error', 'message': error_msg})}\n\n"
        else:
            yield f"data: {json.dumps({'type': 'result', 'data': result_payload})}\n\n"
        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/metrics")
def api_metrics() -> dict:
    with _results_lock:
        results = list(_all_results)

    if not results:
        return {"total_runs": 0, "total_cost_usd": 0, "cache_hit_rate": 0,
                "total_tokens_saved": 0, "total_violations": 0, "runs": []}

    total_cost    = sum(r["cost_usd"] for r in results)
    cache_hits    = sum(1 for r in results if r["cache_hit"])
    tokens_saved  = sum(r["tokens_saved"] for r in results)
    violations    = sum(len(r["violations"]) for r in results)

    return {
        "total_runs":       len(results),
        "total_cost_usd":   round(total_cost, 6),
        "cache_hit_rate":   round(cache_hits / len(results) * 100, 1),
        "total_tokens_saved": tokens_saved,
        "total_violations": violations,
        "runs": results[-10:],   # last 10 for the summary table
    }


@app.get("/api/logs")
def api_logs() -> StreamingResponse:
    """Persistent SSE stream of all log events."""
    def generate():
        q: Q.Queue = Q.Queue(maxsize=200)
        # Replay history first
        with _LOG_LOCK:
            history = list(_LOG_HISTORY)
            _LOG_SUBS.append(q)
        for entry in history:
            yield f"data: {json.dumps(entry)}\n\n"
        try:
            while True:
                try:
                    entry = q.get(timeout=15)
                    yield f"data: {json.dumps(entry)}\n\n"
                except Q.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            with _LOG_LOCK:
                try: _LOG_SUBS.remove(q)
                except ValueError: pass

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    html_path = Path(__file__).parent / "index.html"
    return html_path.read_text(encoding="utf-8")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log_info("ICS Runtime Web Demo starting…")
    uvicorn.run("app:app", host="0.0.0.0", port=7861, reload=False,
                app_dir=str(Path(__file__).parent))
