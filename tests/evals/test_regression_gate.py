"""Regression gate: run_evals fails a full Split run that regressed against the baseline (T10)."""

import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import run_evals
from eval_case import CaseRun, GoldenCase
from eval_doubles import FakePipeline, RecordingPublisher, case_run, golden_case, review_case
from judge import JUDGE_PROMPT_VERSION
from run_evals import REGRESSION_EXIT_CODE, EvalArguments, EvalOptions, run_evaluation

CASES = [golden_case(), review_case()]


class MissingSectionPipeline(FakePipeline):
    """A run that loses one of the two expected Sections: recall falls from 1.0 to 0.5."""

    def __call__(self, case: GoldenCase) -> CaseRun:
        run = super().__call__(case)
        if case.expected_error:
            return run
        output = {
            "sections_changed": ["2"],
            "topics_touched": ["price_payment"],
            "summary_of_the_change": "2 — modified: Los honorarios suben a $ 2.900.000.",
        }
        return case_run(case_id=case.case_id, output=output, changes=run.changes[:1])


def build_synthetic_baseline(split: str = "calibration", **global_metrics: float) -> dict[str, Any]:
    """A frozen baseline where every listed metric was perfect on the same cases."""
    return {
        "split": split,
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
        "global": {
            name: {"value": value, "evaluable": 1} for name, value in global_metrics.items()
        },
    }


def write_baseline(path: Path, baseline: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(baseline), encoding="utf-8")


def stub_evaluate_split(
    tmp_path: Path, pipeline: FakePipeline, publisher: RecordingPublisher
) -> Callable[[EvalArguments], tuple[dict[str, Any], Path]]:
    """evaluate_split with the pipeline and Langfuse replaced; the CLI and the report are real."""

    def evaluate(arguments: EvalArguments) -> tuple[dict[str, Any], Path]:
        report_path = tmp_path / "outputs" / "eval_report.json"
        cases = run_evals.select_cases(CASES, arguments)
        options = EvalOptions(
            arguments.split, "calibration-test", report_path, arguments.baseline, arguments.case_id
        )
        return run_evaluation(cases, pipeline, publisher, options), report_path

    return evaluate


def run_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pipeline: FakePipeline,
    extra_argv: tuple[str, ...] = (),
) -> tuple[int, str, RecordingPublisher]:
    publisher = RecordingPublisher()
    monkeypatch.setattr(
        run_evals, "evaluate_split", stub_evaluate_split(tmp_path, pipeline, publisher)
    )
    baseline = tmp_path / "outputs" / "baseline.json"
    argv = ["--split", "calibration", "--baseline", str(baseline), *extra_argv]
    stderr = io.StringIO()
    exit_code = run_evals.run_cli(argv, stderr)
    return exit_code, stderr.getvalue(), publisher


def read_report(tmp_path: Path) -> dict[str, Any]:
    report: dict[str, Any] = json.loads(
        (tmp_path / "outputs" / "eval_report.json").read_text(encoding="utf-8")
    )
    return report


def test_a_regression_against_a_synthetic_baseline_is_detected(tmp_path: Path) -> None:
    baseline = tmp_path / "outputs" / "baseline.json"
    write_baseline(baseline, build_synthetic_baseline(change_recall=1.0))
    options = EvalOptions(
        run_evals.SplitName.CALIBRATION, "calibration-test", tmp_path / "report.json", baseline
    )

    report = run_evaluation(CASES, MissingSectionPipeline(), RecordingPublisher(), options)

    assert report["baseline"]["regressions"] == ["change_recall"]


def test_the_gate_fails_the_run_and_names_the_regressed_metrics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    write_baseline(
        tmp_path / "outputs" / "baseline.json",
        build_synthetic_baseline(change_recall=1.0, key_facts_coverage=1.0),
    )

    exit_code, stderr, _ = run_gate(monkeypatch, tmp_path, MissingSectionPipeline())

    assert exit_code == REGRESSION_EXIT_CODE
    assert "regressions against the baseline: change_recall, key_facts_coverage" in stderr


def test_a_failed_gate_still_writes_and_publishes_the_paid_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    write_baseline(
        tmp_path / "outputs" / "baseline.json", build_synthetic_baseline(change_recall=1.0)
    )

    _, _, publisher = run_gate(monkeypatch, tmp_path, MissingSectionPipeline())

    assert len(publisher.published) == 1
    assert read_report(tmp_path)["experiment_url"] == "https://langfuse.test/runs/1"


def test_the_regression_exit_code_is_not_a_pipeline_or_usage_exit_code() -> None:
    import errors  # noqa: PLC0415 - only this test needs the whole hierarchy

    pending: list[type[errors.LegalMoveError]] = [errors.LegalMoveError]
    codes = {run_evals.USAGE_EXIT_CODE, run_evals.UNEXPECTED_EXIT_CODE, 0}
    while pending:
        error = pending.pop()
        codes.add(error.exit_code)
        pending.extend(error.__subclasses__())

    assert REGRESSION_EXIT_CODE not in codes


def test_a_run_without_regressions_passes_the_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    write_baseline(
        tmp_path / "outputs" / "baseline.json", build_synthetic_baseline(change_recall=1.0)
    )

    exit_code, stderr, _ = run_gate(monkeypatch, tmp_path, FakePipeline())

    assert exit_code == 0
    assert "regressions against the baseline" not in stderr


def test_without_a_baseline_there_is_no_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    exit_code, _, _ = run_gate(monkeypatch, tmp_path, MissingSectionPipeline())

    assert exit_code == 0
    assert read_report(tmp_path)["baseline"] is None


def test_a_baseline_of_another_split_is_no_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    write_baseline(
        tmp_path / "outputs" / "baseline.json",
        build_synthetic_baseline("validation", change_recall=1.0),
    )

    exit_code, _, _ = run_gate(monkeypatch, tmp_path, MissingSectionPipeline())

    assert exit_code == 0
    assert read_report(tmp_path)["baseline"]["compared"] is False


def test_a_case_filter_is_no_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    write_baseline(
        tmp_path / "outputs" / "baseline.json", build_synthetic_baseline(change_recall=1.0)
    )

    exit_code, _, _ = run_gate(
        monkeypatch, tmp_path, MissingSectionPipeline(), ("--case", "standard_delta_demo")
    )

    assert exit_code == 0
    assert read_report(tmp_path)["baseline"] == {"compared": False, "reason": "case filter"}
