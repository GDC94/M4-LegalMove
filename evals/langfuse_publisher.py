"""Langfuse adapter of ExperimentPublisher: the Split as a Dataset, the run as an Experiment.

Checked against the installed langfuse 4.17.0 source: `Langfuse.create_dataset` (POST
/api/public/v2/datasets), `create_dataset_item` (upsert by id), `get_dataset` (v2 dataset plus
paged /api/public/dataset-items) and `Langfuse.run_experiment` with `Evaluation` results.
"""

import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Protocol

from langfuse import Evaluation
from langfuse.api import DatasetItem
from langfuse.experiment import (
    EvaluatorFunction,
    ExperimentData,
    ExperimentItem,
    ExperimentResult,
    TaskFunction,
)

from errors import ObservabilityError
from eval_case import CaseRun, GoldenCase
from experiment_publisher import ExperimentRun
from metrics import Metric

logger = logging.getLogger("legalmove.evals")

EXPERIMENT_DESCRIPTION = "LegalMove golden set: pipeline runs graded by the plan §6.6 checks"


class DatasetWithItems(Protocol):
    @property
    def items(self) -> list[DatasetItem]: ...


# Any: these mirror the SDK's own untyped JSON payload parameters.
class ExperimentClient(Protocol):
    """The slice of the Langfuse client this adapter uses; tests pass a recording double."""

    def create_dataset(
        self, *, name: str, description: str | None = ..., metadata: Any = ...
    ) -> object: ...

    def create_dataset_item(
        self,
        *,
        dataset_name: str,
        input: Any = ...,
        expected_output: Any = ...,
        metadata: Any = ...,
        id: str | None = ...,
    ) -> object: ...

    def get_dataset(self, name: str) -> DatasetWithItems: ...

    def run_experiment(
        self,
        *,
        name: str,
        run_name: str | None = ...,
        data: ExperimentData,
        task: TaskFunction,
        evaluators: list[EvaluatorFunction] = ...,
    ) -> ExperimentResult: ...

    def flush(self) -> None: ...


class LangfusePublisher:
    """Upserts the Dataset, then replays the recorded runs as one Experiment run.

    The task and the evaluators are pure replays of finished runs, so the SDK's default item
    concurrency is harmless: no model is called from inside the Experiment.
    """

    def __init__(self, client: ExperimentClient) -> None:
        self._client = client

    def publish(self, experiment: ExperimentRun) -> str | None:
        self.upload_cases(experiment)
        items = self.select_items(experiment)
        result = self.run_replay(experiment, items)
        ensure_no_dropped_items(result, expected=len(items))
        ensure_evaluations_match(result, experiment)
        if result.dataset_run_url is None:
            logger.warning(
                "Langfuse returned no dataset run URL: the deprecated dataset-run-items endpoint "
                "may have refused the link (HTTP 410); the Experiment items exist without it"
            )
        return result.dataset_run_url

    def upload_cases(self, experiment: ExperimentRun) -> None:
        """Item ids are global in Langfuse, hence the Dataset prefix; uploading again updates."""
        with translate_sdk_errors("create_dataset"):
            self._client.create_dataset(
                name=experiment.dataset_name, description=EXPERIMENT_DESCRIPTION
            )
        for case in experiment.cases:
            with translate_sdk_errors("create_dataset_item"):
                self._client.create_dataset_item(
                    dataset_name=experiment.dataset_name,
                    id=item_id(experiment.dataset_name, case.case_id),
                    input=build_item_input(case),
                    expected_output=build_item_expected_output(case),
                    metadata=build_item_metadata(case),
                )

    def select_items(self, experiment: ExperimentRun) -> list[DatasetItem]:
        """Only this run's cases: a Dataset may keep items of cases retired since."""
        wanted = {item_id(experiment.dataset_name, case.case_id) for case in experiment.cases}
        with translate_sdk_errors("get_dataset"):
            items = self._client.get_dataset(experiment.dataset_name).items
        selected = [item for item in items if item.id in wanted]
        if len(selected) != len(wanted):
            raise ObservabilityError("Langfuse did not return every uploaded dataset item")
        return selected

    def run_replay(self, experiment: ExperimentRun, items: list[DatasetItem]) -> ExperimentResult:
        try:
            with translate_sdk_errors("run_experiment"):
                return self._client.run_experiment(
                    name=experiment.dataset_name,
                    run_name=experiment.run_name,
                    data=items,
                    task=build_replay_task(experiment.runs),
                    evaluators=build_evaluators(experiment.metrics, experiment.cases),
                )
        finally:
            with translate_sdk_errors("flush"):
                self._client.flush()


@contextmanager
def translate_sdk_errors(action: str) -> Iterator[None]:
    """The SDK documents no error types and its messages may echo keys or bodies: type only."""
    try:
        yield
    except Exception as error:
        raise ObservabilityError(f"langfuse {action} failed: {type(error).__name__}") from None


def item_id(dataset_name: str, case_id: str) -> str:
    return f"{dataset_name}:{case_id}"


def build_item_input(case: GoldenCase) -> dict[str, str]:
    """File names only: the images never leave the process (decision 11)."""
    return {
        "case_id": case.case_id,
        "original_image": case.original_image.name,
        "amendment_image": case.amendment_image.name,
    }


def build_item_expected_output(case: GoldenCase) -> dict[str, Any]:
    return case.model_dump(
        mode="json",
        include={"output", "expected_error", "amendment_kind", "changes", "key_facts"},
    )


def build_item_metadata(case: GoldenCase) -> dict[str, str]:
    return {
        "case_id": case.case_id,
        "split": case.split.value,
        "category": case.category,
        "amendment_kind": case.amendment_kind.value,
    }


def read_case_id(item: ExperimentItem) -> str:
    payload = item["input"] if isinstance(item, dict) else item.input
    return str(payload["case_id"])


# Any: the task signature is the SDK's TaskFunction, whose keyword extras are untyped.
def build_replay_task(runs: Mapping[str, CaseRun]) -> Callable[..., dict[str, Any]]:
    """The pipeline already ran through main.py; the task hands Langfuse what it produced."""

    def replay_case_run(*, item: ExperimentItem, **_: Any) -> dict[str, Any]:
        return runs[read_case_id(item)].model_dump(mode="json")

    return replay_case_run


def build_evaluators(
    metrics: Sequence[Metric], cases: Sequence[GoldenCase]
) -> list[EvaluatorFunction]:
    by_id = {case.case_id: case for case in cases}
    return [adapt_metric(metric, by_id) for metric in metrics]


# Any: evaluator arguments are whatever the SDK passes through from the Dataset item.
def adapt_metric(metric: Metric, cases: Mapping[str, GoldenCase]) -> EvaluatorFunction:
    """The pure check as a Langfuse evaluator; N/A yields no Evaluation instead of a zero."""

    def evaluate(
        *, input: Any, output: Any, expected_output: Any, metadata: Any, **_: Any
    ) -> list[Evaluation]:
        ratio = metric.check(CaseRun.model_validate(output), cases[str(input["case_id"])])
        return [] if ratio is None else [Evaluation(name=metric.name, value=ratio.value)]

    evaluate.__name__ = metric.name
    return evaluate


def ensure_evaluations_match(result: ExperimentResult, experiment: ExperimentRun) -> None:
    """The SDK logs a raising evaluator and drops its score, which would read as N/A.

    Each item must hold exactly the scores the local checks produced; names only, no values.
    """
    cases = {case.case_id: case for case in experiment.cases}
    mismatches: list[str] = []
    for item_result in result.item_results:
        case_id = read_case_id(item_result.item)
        expected = expected_metric_names(experiment, cases[case_id])
        actual = {evaluation.name for evaluation in item_result.evaluations}
        if expected != actual:
            mismatches.append(describe_mismatch(case_id, expected, actual))
    if mismatches:
        raise ObservabilityError(f"Langfuse evaluations differ from the local checks: {mismatches}")


def expected_metric_names(experiment: ExperimentRun, case: GoldenCase) -> set[str]:
    run = experiment.runs[case.case_id]
    return {metric.name for metric in experiment.metrics if metric.check(run, case) is not None}


def describe_mismatch(case_id: str, expected: set[str], actual: set[str]) -> str:
    return f"{case_id} missing {sorted(expected - actual)} unexpected {sorted(actual - expected)}"


def ensure_no_dropped_items(result: ExperimentResult, expected: int) -> None:
    """Langfuse logs and drops an item whose task or evaluator raised; that is an error here."""
    dropped = expected - len(result.item_results)
    if dropped > 0:
        raise ObservabilityError(
            f"{dropped} of {expected} cases were dropped from the Langfuse Experiment"
        )
