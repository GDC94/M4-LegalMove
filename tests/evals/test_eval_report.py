"""eval_report.json: global, per-category, per-kind and per-case metrics, and the baseline check."""

from pathlib import Path
from typing import Any

import pytest

from eval_case import CaseRun
from eval_doubles import case_run, failed_run, golden_case, review_case
from eval_report import ReportHeader, build_report, compare_with_baseline, read_baseline

HEADER = ReportHeader(
    split="calibration",
    run_name="calibration-20261027T120000Z",
    dataset_name="legalmove-calibration",
    judge_prompt_version="judge-v9",
)


def two_case_report() -> dict[str, Any]:
    perfect = golden_case()
    restated = golden_case(case_id="standard_restated_demo", amendment_kind="restated")
    review = review_case()
    runs = {
        perfect.case_id: case_run(),
        restated.case_id: case_run(case_id=restated.case_id, amendment_kind="delta"),
        review.case_id: failed_run(),
    }
    return build_report([perfect, restated, review], runs, HEADER)


def test_report_header_names_the_split_run_and_dataset() -> None:
    report = two_case_report()

    assert report["split"] == "calibration"
    assert report["run_name"] == "calibration-20261027T120000Z"
    assert report["dataset_name"] == "legalmove-calibration"
    assert report["judge_prompt_version"] == "judge-v9"
    assert report["cases_total"] == 3
    assert report["experiment_url"] is None
    assert report["baseline"] is None


def test_global_metrics_carry_their_evaluable_case_count() -> None:
    report = two_case_report()

    assert report["global"]["amendment_kind_accuracy"] == {"value": 2 / 3, "evaluable": 3}
    assert report["global"]["change_recall"] == {"value": 1.0, "evaluable": 2}
    assert report["global"]["human_review"] == {"value": 1.0, "evaluable": 1}
    assert report["global"]["judge_score"] == {"value": 8.0, "evaluable": 2}


def test_a_metric_no_case_could_evaluate_is_null_with_zero_evaluable() -> None:
    case = golden_case()
    report = build_report([case], {case.case_id: case_run(judge_score=None)}, HEADER)

    assert report["global"]["judge_score"] == {"value": None, "evaluable": 0}


def test_metrics_are_grouped_by_category_and_by_declared_kind() -> None:
    report = two_case_report()

    assert set(report["by_category"]) == {"standard", "boundary"}
    assert report["by_category"]["standard"]["amendment_kind_accuracy"] == {
        "value": 0.5,
        "evaluable": 2,
    }
    assert set(report["by_kind"]) == {"delta", "restated"}
    assert report["by_kind"]["restated"]["amendment_kind_accuracy"] == {
        "value": 0.0,
        "evaluable": 1,
    }


def test_metrics_are_grouped_by_clean_and_degraded_scans() -> None:
    clean = golden_case()
    degraded = golden_case(case_id="variant_delta_demo", degraded=True)
    runs = {
        clean.case_id: case_run(),
        degraded.case_id: case_run(case_id=degraded.case_id, parsed_amendment="ADENDA"),
    }

    report = build_report([clean, degraded], runs, HEADER)

    assert set(report["by_scan"]) == {"clean", "degraded"}
    assert report["by_scan"]["clean"]["cer"] == {"value": 0.0, "evaluable": 1}
    assert report["by_scan"]["degraded"]["cer"]["value"] > 0


def test_each_case_reports_its_outcome_and_metric_values() -> None:
    report = two_case_report()
    review = report["cases"][2]

    assert [case["case_id"] for case in report["cases"]] == [
        "standard_delta_demo",
        "standard_restated_demo",
        "boundary_unknown_demo",
    ]
    assert review["category"] == "boundary"
    assert review["amendment_kind"] == "delta"
    assert review["exit_code"] == 10
    assert review["error"] == "UnclassifiableAmendmentError"
    assert review["metrics"]["human_review"] == 1.0
    assert review["metrics"]["change_recall"] is None
    assert report["cases"][0]["trace_id"] == "trace-1"
    assert report["cases"][0]["judge_reasoning"] == "Cubre los hechos."
    assert review["judge_reasoning"] is None


def test_a_case_without_a_run_is_refused() -> None:
    with pytest.raises(KeyError):
        build_report([golden_case()], {}, HEADER)


def baseline_report(evaluable: int = 1, **global_values: float | None) -> dict[str, Any]:
    return {
        "split": "calibration",
        "judge_prompt_version": "judge-v2",
        "global": {
            name: {"value": value, "evaluable": evaluable} for name, value in global_values.items()
        },
    }


def test_baseline_comparison_marks_regressions_by_metric_direction() -> None:
    current = baseline_report(cer=0.05, change_recall=0.9, judge_score=8.0, topic_jaccard=None)
    baseline = baseline_report(cer=0.02, change_recall=0.8, judge_score=8.5, topic_jaccard=0.7)

    comparison = compare_with_baseline(current, baseline)

    assert comparison["compared"] is True
    assert comparison["deltas"]["cer"] == pytest.approx(0.03)
    assert comparison["deltas"]["change_recall"] == pytest.approx(0.1)
    assert "topic_jaccard" not in comparison["deltas"]
    assert comparison["regressions"] == ["cer", "judge_score"]
    assert comparison["evaluable_deltas"] == {
        "cer": 0,
        "change_recall": 0,
        "topic_jaccard": 0,
        "judge_score": 0,
    }


def with_scan_cer(
    report: dict[str, Any], evaluable: int = 1, **cer_by_scan: float
) -> dict[str, Any]:
    report["by_scan"] = {
        scan: {"cer": {"value": value, "evaluable": evaluable}}
        for scan, value in cer_by_scan.items()
    }
    return report


def test_a_degraded_cer_rise_is_a_regression_while_global_cer_holds() -> None:
    """Decision 90: CER has a target per scan, so a per-scan rise is no longer hidden."""
    current = with_scan_cer(baseline_report(cer=0.03), clean=0.01, degraded=0.07)
    baseline = with_scan_cer(baseline_report(cer=0.03), clean=0.01, degraded=0.05)

    comparison = compare_with_baseline(current, baseline)

    assert comparison["deltas"]["cer"] == 0
    assert comparison["deltas"]["cer[clean]"] == 0
    assert comparison["deltas"]["cer[degraded]"] == pytest.approx(0.02)
    assert comparison["regressions"] == ["cer[degraded]"]


def test_float_noise_on_a_scan_cer_is_not_a_regression() -> None:
    current = with_scan_cer(baseline_report(cer=0.03), degraded=0.05 + 1e-12)
    baseline = with_scan_cer(baseline_report(cer=0.03), degraded=0.05)

    assert compare_with_baseline(current, baseline)["regressions"] == []


def test_fewer_evaluable_cases_in_a_scan_group_is_a_regression() -> None:
    current = with_scan_cer(baseline_report(cer=0.03), evaluable=2, degraded=0.05)
    baseline = with_scan_cer(baseline_report(cer=0.03), evaluable=3, degraded=0.05)

    comparison = compare_with_baseline(current, baseline)

    assert comparison["regressions"] == ["cer[degraded].evaluable"]


def test_float_noise_on_a_ratio_is_not_a_regression() -> None:
    current = baseline_report(change_recall=0.9 - 1e-12, cer=0.02 + 1e-12)
    baseline = baseline_report(change_recall=0.9, cer=0.02)

    assert compare_with_baseline(current, baseline)["regressions"] == []


def test_a_real_ratio_drop_is_a_regression() -> None:
    current = baseline_report(change_recall=0.9 - 1e-6)
    baseline = baseline_report(change_recall=0.9)

    assert compare_with_baseline(current, baseline)["regressions"] == ["change_recall"]


@pytest.mark.parametrize(("judge", "regressed"), [(7.8, False), (7.7, True)])
def test_the_judge_tolerates_a_quarter_point(judge: float, regressed: bool) -> None:
    comparison = compare_with_baseline(
        baseline_report(judge_score=judge), baseline_report(judge_score=8.0)
    )

    assert (comparison["regressions"] == ["judge_score"]) is regressed


def test_fewer_evaluable_cases_is_a_regression_even_with_a_better_value() -> None:
    current = baseline_report(evaluable=2, change_recall=1.0)
    baseline = baseline_report(evaluable=3, change_recall=0.9)

    comparison = compare_with_baseline(current, baseline)

    assert comparison["evaluable_deltas"] == {"change_recall": -1}
    assert comparison["regressions"] == ["change_recall.evaluable"]


def test_more_evaluable_cases_is_not_a_regression() -> None:
    current = baseline_report(evaluable=4, change_recall=0.9)

    comparison = compare_with_baseline(current, baseline_report(evaluable=3, change_recall=0.9))

    assert comparison["evaluable_deltas"] == {"change_recall": 1}
    assert comparison["regressions"] == []


def test_a_baseline_of_another_split_is_not_compared() -> None:
    baseline = {**baseline_report(cer=0.02), "split": "validation"}

    comparison = compare_with_baseline(baseline_report(cer=0.05), baseline)

    assert comparison == {
        "compared": False,
        "reason": "baseline split validation differs from calibration",
    }


def test_a_missing_baseline_file_means_no_comparison(tmp_path: Path) -> None:
    assert read_baseline(tmp_path / "baseline.json") is None


def test_an_existing_baseline_file_is_read(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text('{"split": "validation", "global": {}}', encoding="utf-8")

    assert read_baseline(path) == {"split": "validation", "global": {}}


@pytest.mark.parametrize("baseline_version", ["judge-v1", None])
def test_a_judge_prompt_change_leaves_judge_score_out_of_the_comparison(
    baseline_version: str | None,
) -> None:
    baseline = baseline_report(judge_score=9.0, change_recall=0.9)
    if baseline_version is None:
        del baseline["judge_prompt_version"]  # reports from before the version was recorded
    else:
        baseline["judge_prompt_version"] = baseline_version
    current = baseline_report(judge_score=7.0, change_recall=0.9)

    comparison = compare_with_baseline(current, baseline)

    assert comparison["judge_prompt_version_changed"] is True
    assert "judge_score" not in comparison["deltas"]
    assert "judge_score" not in comparison["evaluable_deltas"]
    assert comparison["regressions"] == []
    assert comparison["deltas"]["change_recall"] == 0


def test_the_same_judge_prompt_keeps_judge_score_in_the_comparison() -> None:
    comparison = compare_with_baseline(
        baseline_report(judge_score=7.0), baseline_report(judge_score=9.0)
    )

    assert comparison["judge_prompt_version_changed"] is False
    assert comparison["regressions"] == ["judge_score"]


def test_a_case_run_recorded_before_judge_reasoning_still_loads() -> None:
    old = case_run().model_dump(mode="json")
    del old["judge_reasoning"]

    assert CaseRun.model_validate(old).judge_reasoning is None
