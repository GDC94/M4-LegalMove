"""run_evals.py: a Split through the pipeline, then eval_report.json and the Experiment."""

import dataclasses
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from langchain_core.runnables import RunnableLambda

import main
import run_evals
from cli_doubles import RecordingLifecycleTracer
from errors import ConfigurationError, ObservabilityError
from eval_case import CaseRun, GoldenCase
from eval_doubles import RecordingPublisher, case_run, failed_run, golden_case, review_case
from golden_case import SplitName
from judge import JUDGE_PROMPT_VERSION, JudgeVerdict
from metrics import METRICS
from pipeline_doubles import StubStages
from run_evals import (
    EvalArguments,
    EvalOptions,
    build_run_name,
    build_summary_judge,
    parse_arguments,
    resolve_report_path,
    run_evaluation,
    select_cases,
)
from settings import EvalSettings


def options(tmp_path: Path) -> EvalOptions:
    return EvalOptions(
        split=SplitName.CALIBRATION,
        run_name="calibration-20261027T120000Z",
        report_path=tmp_path / "outputs" / "eval_report.json",
        baseline_path=tmp_path / "outputs" / "baseline.json",
    )


class FakePipeline:
    """Stands in for CaseRunner: canned runs, and a log of which cases ran."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, case: GoldenCase) -> CaseRun:
        self.calls.append(case.case_id)
        if case.expected_error:
            return failed_run(case_id=case.case_id)
        return case_run(case_id=case.case_id)


CASES = [golden_case(), review_case()]


def read_report(path: Path) -> dict[str, Any]:
    report: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return report


def test_every_case_runs_once_in_order(tmp_path: Path) -> None:
    pipeline = FakePipeline()

    run_evaluation(CASES, pipeline, RecordingPublisher(), options(tmp_path))

    assert pipeline.calls == ["standard_delta_demo", "boundary_unknown_demo"]


def test_the_split_and_runs_are_published_with_every_check(tmp_path: Path) -> None:
    publisher = RecordingPublisher()

    run_evaluation(CASES, FakePipeline(), publisher, options(tmp_path))

    (published,) = publisher.published
    assert published.dataset_name == "legalmove-calibration"
    assert published.run_name == "calibration-20261027T120000Z"
    assert published.cases == tuple(CASES)
    assert published.runs["boundary_unknown_demo"].exit_code == 10
    assert published.metrics == METRICS
    assert published.metadata == {"judge_prompt_version": JUDGE_PROMPT_VERSION}


def test_the_report_is_written_with_the_experiment_url(tmp_path: Path) -> None:
    run_evaluation(CASES, FakePipeline(), RecordingPublisher(), options(tmp_path))

    report = read_report(tmp_path / "outputs" / "eval_report.json")
    assert report["experiment_url"] == "https://langfuse.test/runs/1"
    assert report["judge_prompt_version"] == JUDGE_PROMPT_VERSION
    assert report["cases_total"] == 2
    assert report["global"]["human_review"] == {"value": 1.0, "evaluable": 1}
    assert report["baseline"] is None


def test_the_report_survives_a_failed_publication(tmp_path: Path) -> None:
    class BrokenPublisher:
        def publish(self, experiment: object) -> str | None:
            raise ObservabilityError("2 of 2 cases were dropped from the Langfuse Experiment")

    with pytest.raises(ObservabilityError):
        run_evaluation(CASES, FakePipeline(), BrokenPublisher(), options(tmp_path))

    report = read_report(tmp_path / "outputs" / "eval_report.json")
    assert report["experiment_url"] is None
    assert report["cases_total"] == 2


def test_an_existing_baseline_is_compared(tmp_path: Path) -> None:
    baseline = tmp_path / "outputs" / "baseline.json"
    baseline.parent.mkdir()
    baseline.write_text(
        json.dumps(
            {
                "split": "calibration",
                "judge_prompt_version": JUDGE_PROMPT_VERSION,
                "global": {"judge_score": {"value": 9.0}},
            }
        ),
        "utf-8",
    )

    run_evaluation(CASES, FakePipeline(), RecordingPublisher(), options(tmp_path))

    report = read_report(tmp_path / "outputs" / "eval_report.json")
    assert report["baseline"] == {
        "path": str(baseline),
        "compared": True,
        "judge_prompt_version_changed": False,
        "deltas": {"judge_score": -1.0},
        "evaluable_deltas": {},
        "regressions": ["judge_score"],
    }


def test_an_empty_split_is_refused_before_anything_runs(tmp_path: Path) -> None:
    pipeline = FakePipeline()

    with pytest.raises(ValueError, match="no reviewed cases"):
        run_evaluation([], pipeline, RecordingPublisher(), options(tmp_path))

    assert pipeline.calls == []


def test_the_run_name_is_the_split_and_a_utc_timestamp() -> None:
    now = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)

    assert build_run_name(SplitName.CALIBRATION, now) == "calibration-20261027T120000Z"


def test_a_case_filter_suffixes_the_run_name_with_the_case_id() -> None:
    now = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)

    assert build_run_name(SplitName.CALIBRATION, now, "boundary_x") == (
        "calibration-20261027T120000Z-boundary_x"
    )


def test_a_case_filter_writes_each_repetition_to_its_own_default_report() -> None:
    arguments = parse_arguments(["--split", "calibration", "--case", "boundary_x"])

    assert resolve_report_path(arguments, "calibration-20261027T120000Z-boundary_x") == Path(
        "outputs/eval_report-calibration-20261027T120000Z-boundary_x.json"
    )


def test_an_explicit_report_path_is_kept_with_a_case_filter() -> None:
    argv = ["--split", "calibration", "--case", "boundary_x", "--report", "mine.json"]

    assert resolve_report_path(parse_arguments(argv), "run") == Path("mine.json")


def test_a_case_filter_is_recorded_and_never_compared_with_the_baseline(tmp_path: Path) -> None:
    baseline = tmp_path / "outputs" / "baseline.json"
    baseline.parent.mkdir()
    baseline.write_text(json.dumps({"split": "calibration", "global": {}}), "utf-8")
    filtered = dataclasses.replace(options(tmp_path), case_id="boundary_unknown_demo")

    run_evaluation([review_case()], FakePipeline(), RecordingPublisher(), filtered)

    report = read_report(tmp_path / "outputs" / "eval_report.json")
    assert report["case_filter"] == "boundary_unknown_demo"
    assert report["baseline"] == {"compared": False, "reason": "case filter"}


def test_a_full_split_run_records_no_case_filter(tmp_path: Path) -> None:
    run_evaluation(CASES, FakePipeline(), RecordingPublisher(), options(tmp_path))

    assert read_report(tmp_path / "outputs" / "eval_report.json")["case_filter"] is None


def test_arguments_default_to_the_plan_paths() -> None:
    arguments = parse_arguments(["--split", "calibration"])

    assert arguments.split is SplitName.CALIBRATION
    assert arguments.report is None
    assert resolve_report_path(arguments, "calibration-x") == Path("outputs/eval_report.json")
    assert arguments.baseline == Path("outputs/baseline.json")
    assert arguments.run_name is None
    assert arguments.case_id is None


def test_case_narrows_the_run_to_one_case_of_the_split() -> None:
    arguments = parse_arguments(["--split", "calibration", "--case", "boundary_unknown_demo"])

    assert arguments.case_id == "boundary_unknown_demo"
    assert select_cases(CASES, arguments) == [review_case()]


def test_without_case_every_case_of_the_split_runs() -> None:
    assert select_cases(CASES, parse_arguments(["--split", "calibration"])) == CASES


def test_a_case_outside_the_split_is_refused() -> None:
    arguments = parse_arguments(["--split", "calibration", "--case", "validation_only_case"])

    with pytest.raises(
        ValueError, match="validation_only_case is not a reviewed case of the calibration split"
    ):
        select_cases(CASES, arguments)


def test_split_is_required_and_closed() -> None:
    with pytest.raises(SystemExit):
        parse_arguments([])
    with pytest.raises(SystemExit):
        parse_arguments(["--split", "real"])


def test_a_configuration_error_exits_with_its_code_before_any_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse() -> None:
        raise ConfigurationError("invalid eval settings: JUDGE_MODEL must differ")

    monkeypatch.setattr(run_evals, "load_eval_settings", refuse)
    stderr = io.StringIO()

    exit_code = run_evals.run_cli(["--split", "calibration"], stderr)

    assert exit_code == 3
    assert "JUDGE_MODEL must differ" in stderr.getvalue()


def eval_settings(**overrides: Any) -> EvalSettings:
    keys = {"openai_api_key": "sk-test", "langfuse_public_key": "pk", "langfuse_secret_key": "sk"}
    return EvalSettings(_env_file=None, parse_cache_enabled=False, **{**keys, **overrides})


def test_the_judge_runs_on_judge_model_at_temperature_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, Any] = {}
    wrapped: list[object] = []

    def chat_model(**kwargs: Any) -> str:
        received.update(kwargs)
        return "chat-model"

    def judge_model(chat: object) -> RunnableLambda[Any, Any]:
        wrapped.append(chat)
        return RunnableLambda(lambda _: None)

    monkeypatch.setattr(run_evals, "build_openai_chat_model", chat_model)
    monkeypatch.setattr(run_evals, "build_judge_model", judge_model)
    settings = eval_settings(judge_model="gpt-4.1-mini", llm_temperature=0.7)

    build_summary_judge(settings)

    assert received["model"] == "gpt-4.1-mini"
    assert received["temperature"] == 0.0
    assert received["timeout_seconds"] == settings.llm_timeout_seconds
    assert received["max_retries"] == settings.llm_max_retries
    assert received["api_key"] == settings.openai_api_key
    assert wrapped == ["chat-model"]


def test_the_publisher_connects_before_the_first_case_and_every_case_gets_a_fresh_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[str] = []
    budgets: list[object] = []
    settings = eval_settings()

    def build_publisher(_: EvalSettings) -> RecordingPublisher:
        events.append("publisher")
        return RecordingPublisher()

    def compose(composed: EvalSettings) -> main.Container:
        container = main.build_container(composed)  # the real root, offline with a fake tracer
        events.append("compose")
        budgets.append(container.budget)
        return dataclasses.replace(container, stages=StubStages(container.tracer).ports())

    def judge(summary: str, case: GoldenCase) -> JudgeVerdict:
        return JudgeVerdict(reasoning="Cubre los hechos.", score=8)

    monkeypatch.setattr(run_evals, "load_eval_settings", lambda: settings)
    monkeypatch.setattr(run_evals, "build_publisher", build_publisher)
    monkeypatch.setattr(run_evals, "build_summary_judge", lambda _: judge)
    monkeypatch.setattr(run_evals, "build_container", compose)
    monkeypatch.setattr(main, "build_langfuse_tracer", lambda **_: RecordingLifecycleTracer())
    arguments = EvalArguments(
        SplitName.CALIBRATION, tmp_path / "report.json", tmp_path / "baseline.json", "cal-test"
    )

    report, _ = run_evals.evaluate_split(arguments)

    calibration_cases = 10
    assert events == ["publisher", *["compose"] * calibration_cases]
    assert len({id(budget) for budget in budgets}) == calibration_cases
    assert report["cases_total"] == calibration_cases


def test_the_cli_refuses_a_case_outside_the_split_before_connecting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connected: list[EvalSettings] = []
    monkeypatch.setattr(run_evals, "load_eval_settings", eval_settings)
    monkeypatch.setattr(run_evals, "build_publisher", connected.append)
    stderr = io.StringIO()
    argv = ["--split", "calibration", "--case", "nonexistent_case", "--report", str(tmp_path)]

    exit_code = run_evals.run_cli(argv, stderr)

    assert exit_code == 2
    assert "nonexistent_case is not a reviewed case of the calibration split" in stderr.getvalue()
    assert connected == []


def test_an_unexpected_failure_exits_1_with_the_type_only(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(_: EvalArguments) -> tuple[dict[str, Any], Path]:
        raise RuntimeError("Cláusula Segunda sk-secret")

    monkeypatch.setattr(run_evals, "evaluate_split", explode)
    stderr = io.StringIO()

    exit_code = run_evals.run_cli(["--split", "calibration"], stderr)

    assert exit_code == 1
    assert "RuntimeError" in stderr.getvalue()
    assert "secret" not in stderr.getvalue() and "Cláusula" not in stderr.getvalue()
