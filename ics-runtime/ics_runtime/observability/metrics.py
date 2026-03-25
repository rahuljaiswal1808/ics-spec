"""SessionMetrics — accumulated observability data for a session or agent lifetime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Per-provider pricing per 1M tokens (USD): input / output / cache_write / cache_read
_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-6":            {"in": 15.0,  "out": 75.0,  "cw": 18.75, "cr": 1.50},
    "claude-sonnet-4-6":          {"in": 3.0,   "out": 15.0,  "cw": 3.75,  "cr": 0.30},
    "claude-3-5-sonnet-20241022": {"in": 3.0,   "out": 15.0,  "cw": 3.75,  "cr": 0.30},
    "claude-3-5-haiku-20241022":  {"in": 0.80,  "out": 4.0,   "cw": 1.0,   "cr": 0.08},
    "gpt-4o":                     {"in": 2.50,  "out": 10.0,  "cw": 0.0,   "cr": 1.25},
    "gpt-4o-mini":                {"in": 0.15,  "out": 0.60,  "cw": 0.0,   "cr": 0.075},
    "o1":                         {"in": 15.0,  "out": 60.0,  "cw": 0.0,   "cr": 7.50},
}
_FALLBACK = {"in": 3.0, "out": 15.0, "cw": 3.75, "cr": 0.30}


def price_per_token(model: str) -> dict[str, float]:
    """Return the per-token price dict (values are per-token, not per-million)."""
    p = _PRICING.get(model, _FALLBACK)
    return {k: v / 1_000_000 for k, v in p.items()}


@dataclass
class RunMetrics:
    """Metrics for a single Session.run() call."""
    turn: int
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    violations: int
    tool_calls: int
    latency_ms: int
    cost_usd: float
    cache_hit: bool


@dataclass
class SessionMetrics:
    """Accumulated observability data for a single session.

    Updated after every ``Session.run()`` call by ``MetricsRecorder``.
    """
    session_id: str
    model: str
    provider: str
    runs: list[RunMetrics] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Aggregated counters
    # ------------------------------------------------------------------

    @property
    def total_runs(self) -> int:
        return len(self.runs)

    @property
    def total_input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.runs)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.runs)

    @property
    def total_cache_write_tokens(self) -> int:
        return sum(r.cache_write_tokens for r in self.runs)

    @property
    def total_cache_read_tokens(self) -> int:
        return sum(r.cache_read_tokens for r in self.runs)

    @property
    def total_violations(self) -> int:
        return sum(r.violations for r in self.runs)

    @property
    def total_tool_calls(self) -> int:
        return sum(r.tool_calls for r in self.runs)

    @property
    def total_cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.runs)

    @property
    def total_latency_ms(self) -> int:
        return sum(r.latency_ms for r in self.runs)

    @property
    def cache_hit_rate(self) -> float:
        if not self.runs:
            return 0.0
        hits = sum(1 for r in self.runs if r.cache_hit)
        return hits / len(self.runs)

    @property
    def tokens_saved_by_caching(self) -> int:
        """Total tokens served from cache across all runs."""
        return self.total_cache_read_tokens

    @property
    def cost_without_caching(self) -> float:
        """Hypothetical cost if cache reads had been billed as full input tokens."""
        p = price_per_token(self.model)
        extra = self.total_cache_read_tokens * (p["in"] - p["cr"])
        return self.total_cost_usd + max(extra, 0.0)

    @property
    def savings_usd(self) -> float:
        return self.cost_without_caching - self.total_cost_usd

    @property
    def savings_pct(self) -> float:
        full = self.cost_without_caching
        return (self.savings_usd / full * 100) if full > 0 else 0.0

    @property
    def avg_latency_ms(self) -> float:
        if not self.runs:
            return 0.0
        return self.total_latency_ms / len(self.runs)

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def summary(self) -> str:
        lines = [
            f"Session {self.session_id} | {self.provider}/{self.model}",
            f"  Runs:           {self.total_runs}",
            f"  Input tokens:   {self.total_input_tokens:,}",
            f"  Output tokens:  {self.total_output_tokens:,}",
            f"  Cache writes:   {self.total_cache_write_tokens:,}",
            f"  Cache reads:    {self.total_cache_read_tokens:,}",
            f"  Cache hit rate: {self.cache_hit_rate * 100:.1f}%",
            f"  Tokens saved:   {self.tokens_saved_by_caching:,}",
            f"  Total cost:     ${self.total_cost_usd:.5f}",
            f"  Cost w/o cache: ${self.cost_without_caching:.5f}",
            f"  Savings:        ${self.savings_usd:.5f} ({self.savings_pct:.1f}%)",
            f"  Violations:     {self.total_violations}",
            f"  Tool calls:     {self.total_tool_calls}",
            f"  Avg latency:    {self.avg_latency_ms:.0f}ms",
        ]
        return "\n".join(lines)
