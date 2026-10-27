"""Eval runner: a Split through the pipeline, eval_report.json and a Langfuse Experiment (§6.7).

Run from the repository root, with src on the path like generate_golden.py:
    PYTHONPATH=src uv run python evals/run_evals.py --split calibration
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
from judge import SummaryJudge, build_judge_model
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


@dataclass(frozen=True)
class EvalArguments:
    split: SplitName
    report: Path
    baseline: Path
    run_name: str | None


def parse_arguments(argv: Sequence[str]) -> EvalArguments:
    parser = argparse.ArgumentParser(
        prog="run_evals", description="Run a golden Split and publish it as an Experiment."
    )
    parser.add_argument("--split", required=True, type=SplitName, choices=list(SplitName))
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--run-name", default=None, help="defaults to <split>-<UTC timestamp>")
    namespace = parser.parse_args(argv)
    return EvalArguments(namespace.split, namespace.report, namespace.baseline, namespace.run_name)


def build_run_name(split: SplitName, now: datetime) -> str:
    return f"{split.value}-{now.astimezone(UTC):%Y%m%dT%H%M%SZ}"


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
    header = ReportHeader(options.split.value, options.run_name, dataset_name(options.split))
    report = build_report(cases, runs, header)
    report["baseline"] = describe_baseline(report, options.baseline_path)
    write_report(report, options.report_path)
    experiment = ExperimentRun(header.dataset_name, options.run_name, tuple(cases), runs, METRICS)
    report["experiment_url"] = publisher.publish(experiment)
    write_report(report, options.report_path)
    return report


def describe_baseline(report: dict[str, Any], path: Path) -> dict[str, Any] | None:
    baseline = read_baseline(path)
    if baseline is None:
        return None
    return {"path": str(path), **compare_with_baseline(report, baseline)}


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


def evaluate_split(arguments: EvalArguments) -> dict[str, Any]:
    """Composition root of the evals: the pipeline itself is main.py's build_container."""
    settings = load_eval_settings()
    logging.getLogger().setLevel(settings.log_level)
    cases = load_split(GOLDEN_ROOT, arguments.split)
    run_name = arguments.run_name or build_run_name(arguments.split, datetime.now(UTC))
    publisher = build_publisher(settings)
    runner = CaseRunner(
        compose=lambda: build_container(settings),
        judge=build_summary_judge(settings),
        session_id=run_name,
    )
    options = EvalOptions(arguments.split, run_name, arguments.report, arguments.baseline)
    return run_evaluation(cases, runner, publisher, options)


def run_cli(argv: Sequence[str], stderr: TextIO) -> int:
    logging.basicConfig(stream=stderr, level=logging.INFO, format=LOG_FORMAT, force=True)
    arguments = parse_arguments(argv)
    try:
        report = evaluate_split(arguments)
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
    stderr.write(f"report: {arguments.report}\nexperiment: {report['experiment_url']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(run_cli(sys.argv[1:], sys.stderr))
