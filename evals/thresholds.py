"""Plan §6.6 targets as data, and whether a report's measured values meet them."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from eval_report import RATIO_TOLERANCE
from golden_case import ScanKind
from metrics import metric_named


@dataclass(frozen=True)
class Threshold:
    """A §6.6 target on a global metric, or on one scan group when §6.6 splits it."""

    metric: str
    target: float
    scan: ScanKind | None = None

    @property
    def scan_qualifier(self) -> str:
        """What follows the metric name in every label: empty for a global target."""
        return "" if self.scan is None else f" ({self.scan} scans)"

    @property
    def label(self) -> str:
        return f"{self.metric}{self.scan_qualifier}"

    @property
    def lower_is_better(self) -> bool:
        return metric_named(self.metric).lower_is_better


# §6.6 rows the report has no metric for are left out rather than approximated: grounding_rate
# is a pipeline score in Langfuse, and the no-change and injection rows are single cases whose
# outcome shows in their per-case metrics, not in a global aggregate.
THRESHOLDS: tuple[Threshold, ...] = (
    Threshold("cer", 0.02, scan=ScanKind.CLEAN),
    Threshold("cer", 0.08, scan=ScanKind.DEGRADED),
    Threshold("amendment_kind_accuracy", 0.95),
    Threshold("change_precision", 0.90),
    Threshold("change_recall", 0.90),
    Threshold("change_type_accuracy", 0.90),
    Threshold("topic_jaccard", 0.80),
    Threshold("key_facts_coverage", 0.90),
    Threshold("judge_score", 7.0),
    Threshold("human_review", 1.0),
)


@dataclass(frozen=True)
class ThresholdCheck:
    """`met` is None when no case could evaluate the metric: N/A is not a pass or a miss."""

    threshold: Threshold
    value: float | None
    evaluable: int
    met: bool | None


def find_measured_aggregate(report: Mapping[str, Any], threshold: Threshold) -> Mapping[str, Any]:
    group = (
        report.get("global", {})
        if threshold.scan is None
        else report.get("by_scan", {}).get(threshold.scan, {})
    )
    aggregate: Mapping[str, Any] = group.get(threshold.metric, {})
    return aggregate


def meets_target(threshold: Threshold, value: float) -> bool:
    """Measured values are fractions of small integers; a value equal to its target meets it."""
    if threshold.lower_is_better:
        return value <= threshold.target + RATIO_TOLERANCE
    return value >= threshold.target - RATIO_TOLERANCE


def check_threshold(report: Mapping[str, Any], threshold: Threshold) -> ThresholdCheck:
    aggregate = find_measured_aggregate(report, threshold)
    value = aggregate.get("value")
    met = None if value is None else meets_target(threshold, value)
    return ThresholdCheck(threshold, value, aggregate.get("evaluable", 0), met)


def check_thresholds(report: Mapping[str, Any]) -> list[ThresholdCheck]:
    return [check_threshold(report, threshold) for threshold in THRESHOLDS]


def describe_thresholds(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The report's thresholds section, one JSON row per target."""
    return [
        {
            "label": check.threshold.label,
            "metric": check.threshold.metric,
            "scan": check.threshold.scan,
            "target": check.threshold.target,
            "lower_is_better": check.threshold.lower_is_better,
            "value": check.value,
            "evaluable": check.evaluable,
            "met": check.met,
        }
        for check in check_thresholds(report)
    ]
