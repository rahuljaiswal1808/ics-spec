package io.ics.runtime.observability;

import io.ics.runtime.RunResult;

import java.util.ArrayList;
import java.util.List;

/**
 * Accumulates observability metrics across all runs in a session.
 *
 * <p>Mirrors {@code ics_runtime.observability.metrics.SessionMetrics} from the
 * Python library.
 */
public final class SessionMetrics {

    private final List<RunResult> runs = new ArrayList<>();

    public void record(RunResult result) { runs.add(result); }

    public int getTotalRuns()    { return runs.size(); }
    public int getCacheHits()    { return (int) runs.stream().filter(RunResult::isCacheHit).count(); }
    public int getCacheMisses()  { return getTotalRuns() - getCacheHits(); }

    /** Cache hit rate 0.0–1.0 */
    public double getCacheHitRate() {
        return runs.isEmpty() ? 0.0 : (double) getCacheHits() / runs.size();
    }

    public int getTotalTokensSaved() {
        return runs.stream().mapToInt(RunResult::getTokensSaved).sum();
    }

    public double getTotalCostUsd() {
        return runs.stream().mapToDouble(RunResult::getCostUsd).sum();
    }

    /** Estimated cost if there were no caching (all cache-read tokens billed at input rate). */
    public double getCostWithoutCaching() {
        // Pricing fallback: use 3.0 per 1M tokens (claude-sonnet-4-6 input rate)
        return getTotalCostUsd() + (getTotalTokensSaved() * 3.0 / 1_000_000.0);
    }

    public double getSavingsUsd()  { return getCostWithoutCaching() - getTotalCostUsd(); }
    public double getSavingsPct()  {
        double base = getCostWithoutCaching();
        return base == 0 ? 0 : getSavingsUsd() / base * 100.0;
    }

    public double getAvgLatencyMs() {
        return runs.isEmpty() ? 0.0 :
               runs.stream().mapToInt(RunResult::getLatencyMs).average().orElse(0.0);
    }

    @Override
    public String toString() {
        return String.format(
            "SessionMetrics{runs=%d, hitRate=%.1f%%, saved=%d tokens, cost=$%.5f}",
            getTotalRuns(), getCacheHitRate() * 100, getTotalTokensSaved(), getTotalCostUsd()
        );
    }
}
