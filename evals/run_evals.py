"""Eval runner: a Split through the pipeline, eval_report.json and a Langfuse Experiment (§6.7).

Run from the repository root, with src on the path like generate_golden.py:
    PYTHONPATH=src uv run python evals/run_evals.py --split calibration
    PYTHONPATH=src uv run python evals/run_evals.py --split calibration --case <case_id>
"""

import argparse
import json
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from case_runner import CaseRunner
from errors import LegalMoveError
from eval_case import CaseRun, GoldenCase, load_split
from eval_report import ReportHeader, build_report, compare_with_baseline, read_baseline
from experiment_publisher import ExperimentPublisher, ExperimentRun
from golden_case import GoldenCaseError, SplitName
from image_parser import build_openai_chat_model
from judge import JUDGE_PROMPT_VERSION, SummaryJudge, build_judge_model
from langfuse_publisher import LangfusePublisher
from main import LOG_FORMAT, UNEXPECTED_EXIT_CODE, build_container
from metrics import METRICS
from observability import connect_langfuse
from settings import EvalSettings, load_eval_settings

GOLDEN_ROOT = Path(__file__).resolve().parent.parent / "data" / "golden"
DEFAULT_REPORT = Path("outputs/eval_report.json")
DEFAULT_BASELINE = Path("outputs/baseline.json")
DATASET_PREFIX = "legalmove"
USAGE_EXIT_CODE = 2

RunCase = Callable[[GoldenCase], CaseRun]

logger = logging.getLogger("legalmove.evals")


@dataclass(frozen=True)
class EvalOptions:
    split: SplitName
    run_name: str
    report_path: Path
    baseline_path: Path
    case_id: str | None = None


@dataclass(frozen=True)
class EvalArguments:
    split: SplitName
    report: Path | None
    baseline: Path
    run_name: str | None
    case_id: str | None = None


def parse_arguments(argv: Sequence[str]) -> EvalArguments:
    parser = argparse.ArgumentParser(
        prog="run_evals", description="Run a golden Split and publish it as an Experiment."
    )
    parser.add_argument("--split", required=True, type=SplitName, choices=list(SplitName))
    parser.add_argument(
        "--report", type=Path, default=None, help="defaults to outputs/eval_report.json"
    )
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--run-name", default=None, help="defaults to <split>-<UTC timestamp>")
    parser.add_argument("--case", dest="case_id", default=None, help="one case_id of the split")
    namespace = parser.parse_args(argv)
    return EvalArguments(
        namespace.split, namespace.report, namespace.baseline, namespace.run_name, namespace.case_id
    )


def select_cases(cases: Sequence[GoldenCase], arguments: EvalArguments) -> list[GoldenCase]:
    """Every case of the Split, or the one --case names; never a case from another Split."""
    if arguments.case_id is None:
        return list(cases)
    selected = [case for case in cases if case.case_id == arguments.case_id]
    if not selected:
        raise GoldenCaseError(
            f"{arguments.case_id} is not a reviewed case of the {arguments.split.value} split"
        )
    return selected


def build_run_name(split: SplitName, now: datetime, case_id: str | None = None) -> str:
    name = f"{split.value}-{now.astimezone(UTC):%Y%m%dT%H%M%SZ}"
    return name if case_id is None else f"{name}-{case_id}"


def resolve_report_path(arguments: EvalArguments, run_name: str) -> Path:
    """A --case repetition gets its own default report, so the next one cannot overwrite it."""
    if arguments.report is not None:
        return arguments.report
    if arguments.case_id is None:
        return DEFAULT_REPORT
    return DEFAULT_REPORT.with_name(f"{DEFAULT_REPORT.stem}-{run_name}{DEFAULT_REPORT.suffix}")


def dataset_name(split: SplitName) -> str:
    return f"{DATASET_PREFIX}-{split.value}"


def run_evaluation(
    cases: Sequence[GoldenCase],
    run_case: RunCase,
    publisher: ExperimentPublisher,
    options: EvalOptions,
) -> dict[str, Any]:
    """The report is written before publishing, so a Langfuse failure never loses a paid run."""
    if not cases:
        raise GoldenCaseError(f"no reviewed cases in the {options.split.value} split")
    runs = {case.case_id: run_case(case) for case in cases}
    header = ReportHeader(
        options.split.value,
        options.run_name,
        dataset_name(options.split),
        JUDGE_PROMPT_VERSION,
        options.case_id,
    )
    report = build_report(cases, runs, header)
    report["baseline"] = describe_baseline(report, options)
    write_report(report, options.report_path)
    metadata = {"judge_prompt_version": JUDGE_PROMPT_VERSION}
    experiment = ExperimentRun(
        header.dataset_name, options.run_name, tuple(cases), runs, METRICS, metadata
    )
    report["experiment_url"] = publisher.publish(experiment)
    write_report(report, options.report_path)
    return report


def describe_baseline(report: dict[str, Any], options: EvalOptions) -> dict[str, Any] | None:
    """One case against a whole-split baseline would read as a regression of every metric."""
    if options.case_id is not None:
        return {"compared": False, "reason": "case filter"}
    baseline = read_baseline(options.baseline_path)
    if baseline is None:
        return None
    return {"path": str(options.baseline_path), **compare_with_baseline(report, baseline)}


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_summary_judge(settings: EvalSettings) -> SummaryJudge:
    """Temperature 0 regardless of LLM_TEMPERATURE: a grader should not be creative."""
    chat_model = build_openai_chat_model(
        model=settings.judge_model,
        temperature=0.0,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        api_key=settings.openai_api_key,
    )
    return SummaryJudge(build_judge_model(chat_model))


def build_publisher(settings: EvalSettings) -> LangfusePublisher:
    """Built before any case runs: its auth check fails the eval before it spends anything."""
    client = connect_langfuse(
        public_key=settings.langfuse_public_key.get_secret_value(),
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        host=settings.langfuse_host,
        environment=settings.app_env,
    )
    return LangfusePublisher(client)


def evaluate_split(arguments: EvalArguments) -> tuple[dict[str, Any], Path]:
    """Composition root of the evals: the pipeline itself is main.py's build_container.

    Returns the report and where it was written.
    """
    settings = load_eval_settings()
    logging.getLogger().setLevel(settings.log_level)
    cases = select_cases(load_split(GOLDEN_ROOT, arguments.split), arguments)
    now = datetime.now(UTC)
    run_name = arguments.run_name or build_run_name(arguments.split, now, arguments.case_id)
    publisher = build_publisher(settings)
    runner = CaseRunner(
        compose=lambda: build_container(settings),
        judge=build_summary_judge(settings),
        session_id=run_name,
    )
    report_path = resolve_report_path(arguments, run_name)
    options = EvalOptions(
        arguments.split, run_name, report_path, arguments.baseline, arguments.case_id
    )
    return run_evaluation(cases, runner, publisher, options), report_path


def run_cli(argv: Sequence[str], stderr: TextIO) -> int:
    logging.basicConfig(stream=stderr, level=logging.INFO, format=LOG_FORMAT, force=True)
    arguments = parse_arguments(argv)
    try:
        report, report_path = evaluate_split(arguments)
    except LegalMoveError as error:
        stderr.write(f"{type(error).__name__}: {error}\n")
        return error.exit_code
    except GoldenCaseError as error:
        stderr.write(f"{type(error).__name__}: {error}\n")
        return USAGE_EXIT_CODE
    # Last resort, as main.report_failure: foreign messages may quote documents or keys.
    except Exception as error:
        logger.error("unexpected failure: %s", type(error).__name__)
        return UNEXPECTED_EXIT_CODE
    stderr.write(f"report: {report_path}\nexperiment: {report['experiment_url']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(run_cli(sys.argv[1:], sys.stderr))
