"""Latency metrics collection with percentile reporting.

Rule 43: track per-stage latencies and compute P50/P90/P95/P99.
Rule 44: the headline metric is MEDIA_AVAILABLE -> INFORMATION_COPIABLE,
tracked under the key ``total_latency_ms`` (media_to_result) and
``clipboard_latency_ms`` (result_to_clipboard).
"""
from __future__ import annotations

import statistics
import threading
from dataclasses import dataclass, field
from typing import Dict, List

# Canonical stage keys (rule 43)
STAGES = (
    "detection_latency_ms",
    "download_latency_ms",
    "decode_latency_ms",
    "card_detection_latency_ms",
    "alignment_latency_ms",
    "diff_latency_ms",
    "ocr_latency_ms",
    "vision_latency_ms",
    "fusion_latency_ms",
    "state_latency_ms",
    "notification_latency_ms",
    "clipboard_latency_ms",
    "total_latency_ms",
    "media_to_result_ms",
)


@dataclass
class MetricSample:
    count: int = 0
    values: List[float] = field(default_factory=list)

    def record(self, value: float) -> None:
        self.count += 1
        self.values.append(value)
        # cap memory
        if len(self.values) > 5000:
            self.values = self.values[-5000:]

    def percentiles(self) -> Dict[str, float]:
        if not self.values:
            return {"count": 0, "p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0, "min": 0.0, "max": 0.0}
        vals = sorted(self.values)
        n = len(vals)

        def pct(p: float) -> float:
            if n == 1:
                return vals[0]
            k = (n - 1) * p
            f = int(k)
            c = min(f + 1, n - 1)
            return vals[f] + (vals[c] - vals[f]) * (k - f)

        return {
            "count": n,
            "p50": round(pct(0.50), 2),
            "p90": round(pct(0.90), 2),
            "p95": round(pct(0.95), 2),
            "p99": round(pct(0.99), 2),
            "mean": round(statistics.fmean(vals), 2),
            "min": round(vals[0], 2),
            "max": round(vals[-1], 2),
        }


class MetricsCollector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples: Dict[str, MetricSample] = {}

    def record(self, stage: str, value_ms: float) -> None:
        with self._lock:
            sample = self._samples.setdefault(stage, MetricSample())
            sample.record(value_ms)

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        with self._lock:
            return {stage: s.percentiles() for stage, s in self._samples.items()}

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()


# Process-wide singleton
_metrics: MetricsCollector | None = None
_metrics_lock = threading.Lock()


def get_metrics() -> MetricsCollector:
    global _metrics
    if _metrics is None:
        with _metrics_lock:
            if _metrics is None:
                _metrics = MetricsCollector()
    return _metrics
