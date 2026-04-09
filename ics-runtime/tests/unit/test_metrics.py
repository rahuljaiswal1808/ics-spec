"""Unit tests for SessionMetrics and MetricsRecorder."""

import pytest
from ics_runtime.observability.metrics import SessionMetrics, RunMetrics
from ics_runtime.observability.recorder import MetricsRecorder
from ics_runtime.core.result import RunResult


def _make_run_result(**overrides) -> RunResult:
    defaults = dict(
        text="Lead L-1 is QUALIFIED.",
        validated=True,
        violations=[],
        parsed=None,
        cache_hit=False,
        cache_write=True,
        tokens_saved=0,
        input_tokens=4200,
        output_tokens=300,
        cache_write_tokens=4200,
        tool_calls=[],
        session_id="sess-1",
        turn_number=1,
        latency_ms=1100,
        model="claude-3-5-sonnet-20241022",
        provider="anthropic",
        cost_usd=0.0150,
    )
    defaults.update(overrides)
    return RunResult(**defaults)


def test_recorder_accumulates_runs():
    rec = MetricsRecorder("sess-1", "claude-3-5-sonnet-20241022", "anthropic")
    r1 = _make_run_result(turn_number=1)
    r2 = _make_run_result(turn_number=2, cache_hit=True, tokens_saved=4200, input_tokens=200, cost_usd=0.003)
    rec.record(r1)
    rec.record(r2)

    m = rec.metrics
    assert m.total_runs == 2
    assert m.total_input_tokens == 4400
    assert m.tokens_saved_by_caching == 4200


def test_cache_hit_rate():
    rec = MetricsRecorder("sess-2", "claude-3-5-sonnet-20241022", "anthropic")
    for i in range(4):
        hit = i >= 1  # first call is a miss, rest are hits
        rec.record(_make_run_result(turn_number=i + 1, cache_hit=hit, tokens_saved=4200 if hit else 0))

    m = rec.metrics
    assert m.cache_hit_rate == pytest.approx(0.75)


def test_cost_without_caching_exceeds_actual():
    rec = MetricsRecorder("sess-3", "claude-3-5-sonnet-20241022", "anthropic")
    # Run 2 has a large cache read
    rec.record(_make_run_result(turn_number=1, cache_hit=False, cost_usd=0.015))
    rec.record(_make_run_result(turn_number=2, cache_hit=True, tokens_saved=4000, cost_usd=0.003))

    m = rec.metrics
    assert m.cost_without_caching > m.total_cost_usd
    assert m.savings_usd > 0


def test_zero_runs_safe():
    m = SessionMetrics(session_id="s", model="gpt-4o", provider="openai")
    assert m.total_runs == 0
    assert m.cache_hit_rate == 0.0
    assert m.total_cost_usd == 0.0
    assert m.savings_pct == 0.0


def test_summary_is_string():
    rec = MetricsRecorder("sess-4", "gpt-4o", "openai")
    rec.record(_make_run_result(model="gpt-4o", provider="openai"))
    summary = rec.metrics.summary()
    assert "sess-4" in summary
    assert "gpt-4o" in summary
