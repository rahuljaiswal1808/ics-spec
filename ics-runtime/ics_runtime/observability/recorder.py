"""MetricsRecorder — accumulates per-run metrics into a SessionMetrics object."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ics_runtime.observability.metrics import RunMetrics, SessionMetrics

if TYPE_CHECKING:
    from ics_runtime.core.result import RunResult


class MetricsRecorder:
    """Accumulates ``RunResult`` data into a ``SessionMetrics`` instance.

    One recorder is held by each ``Session``.  After every ``run()`` call,
    ``record()`` is called with the ``RunResult`` to update the metrics.
    """

    def __init__(self, session_id: str, model: str, provider: str) -> None:
        self._metrics = SessionMetrics(
            session_id=session_id,
            model=model,
            provider=provider,
        )

    def record(self, result: "RunResult") -> None:
        """Add a completed run's data to the accumulated metrics."""
        run = RunMetrics(
            turn=result.turn_number,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_write_tokens=result.cache_write_tokens,
            cache_read_tokens=result.tokens_saved,
            violations=len(result.violations),
            tool_calls=len(result.tool_calls),
            latency_ms=result.latency_ms,
            cost_usd=result.cost_usd,
            cache_hit=result.cache_hit,
        )
        self._metrics.runs.append(run)

    @property
    def metrics(self) -> SessionMetrics:
        return self._metrics
