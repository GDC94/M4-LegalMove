"""Plan §6.6 targets as data, and whether a report's measured values meet them (T10)."""

from typing import Any

from eval_doubles import build_aggregate, build_perfect_report
from golden_case import ScanKind
from metrics import METRICS
from thresholds import THRESHOLDS, check_thresholds, describe_thresholds


def map_met_by_label(report: dict[str, Any]) -> dict[str, bool | None]:
    return {check.threshold.label: check.met for check in check_thresholds(report)}


def test_every_target_names_a_real_metric() -> None:
    names = {metric.name for metric in METRICS}

    assert {threshold.metric for threshold in THRESHOLDS} <= names


def test_the_cer_targets_cover_every_scan_kind() -> None:
    scans = {threshold.scan for threshold in THRESHOLDS if threshold.metric == "cer"}

    assert scans == set(ScanKind)


def test_the_targets_are_the_plan_values() -> None:
    targets = {threshold.label: threshold.target for threshold in THRESHOLDS}

    assert targets == {
        "cer (clean scans)": 0.02,
        "cer (degraded scans)": 0.08,
        "amendment_kind_accuracy": 0.95,
        "change_precision": 0.90,
        "change_recall": 0.90,
        "change_type_accuracy": 0.90,
        "topic_jaccard": 0.80,
        "key_facts_coverage": 0.90,
        "judge_score": 7.0,
        "human_review": 1.0,
    }


def test_a_report_that_meets_every_target_passes_every_check() -> None:
    assert set(map_met_by_label(build_perfect_report()).values()) == {True}


def test_a_higher_is_better_metric_below_its_target_is_missed() -> None:
    report = build_perfect_report(change_recall=build_aggregate(0.85))

    assert map_met_by_label(report)["change_recall"] is False


def test_exactly_the_target_meets_it() -> None:
    report = build_perfect_report(
        judge_score=build_aggregate(7.0), human_review=build_aggregate(1.0)
    )

    assert map_met_by_label(report)["judge_score"] is True
    assert map_met_by_label(report)["human_review"] is True


def test_cer_is_checked_per_scan_and_lower_is_better() -> None:
    report = build_perfect_report()
    report["by_scan"]["clean"]["cer"] = build_aggregate(0.021)

    met = map_met_by_label(report)

    assert met["cer (clean scans)"] is False
    assert met["cer (degraded scans)"] is True


def test_a_metric_without_evaluable_cases_is_neither_met_nor_missed() -> None:
    report = build_perfect_report(change_type_accuracy=build_aggregate(None))
    del report["by_scan"]["degraded"]

    met = map_met_by_label(report)

    assert met["change_type_accuracy"] is None
    assert met["cer (degraded scans)"] is None


def test_the_report_section_lists_target_value_and_outcome() -> None:
    described = describe_thresholds(build_perfect_report(change_recall=build_aggregate(0.5, 2)))

    recall = next(row for row in described if row["label"] == "change_recall")
    assert recall == {
        "label": "change_recall",
        "metric": "change_recall",
        "scan": None,
        "target": 0.90,
        "lower_is_better": False,
        "value": 0.5,
        "evaluable": 2,
        "met": False,
    }
