"""Langfuse adapter of ExperimentPublisher: the Split as a Dataset, the run as an Experiment."""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from langfuse import Evaluation
from langfuse.api import DatasetItem
from langfuse.experiment import ExperimentItemResult, ExperimentResult

from errors import ObservabilityError
from eval_doubles import case_run, failed_run, golden_case, review_case
from experiment_publisher import ExperimentRun
from langfuse_publisher import LangfusePublisher
from metrics import METRICS

NOW = datetime(2026, 10, 27, tzinfo=UTC)


def dataset_item(dataset_name: str, item_id: str, payload: dict[str, Any]) -> DatasetItem:
    return DatasetItem(
        id=item_id,
        status="ACTIVE",
        input=payload.get("input"),
        expected_output=payload.get("expected_output"),
        metadata=payload.get("metadata"),
        source_trace_id=None,
        source_observation_id=None,
        dataset_id="dataset-1",
        dataset_name=dataset_name,
        created_at=NOW,
        updated_at=NOW,
        media_references=[],
    )


@dataclass
class FakeDataset:
    items: list[DatasetItem]


@dataclass
class RecordingLangfuse:
    """Stores datasets in memory and runs experiments the way the SDK does, without a server."""

    stale_items: list[DatasetItem] = field(default_factory=list)
    drop_items: int = 0
    failing_evaluators: set[str] = field(default_factory=set)
    failing_call: str | None = None
    run_url: str | None = "https://langfuse.test/runs/1"
    replayed_output: dict[str, Any] | None = None
    datasets: list[dict[str, Any]] = field(default_factory=list)
    items: dict[str, DatasetItem] = field(default_factory=dict)
    experiments: list[dict[str, Any]] = field(default_factory=list)
    flushes: int = 0

    def fail_if_asked(self, call: str) -> None:
        if self.failing_call == call:
            raise RuntimeError("HTTP 500 body: Cláusula Segunda sk-lf-secret")

    def create_dataset(self, *, name: str, **kwargs: Any) -> None:
        self.fail_if_asked("create_dataset")
        self.datasets.append({"name": name, **kwargs})

    def create_dataset_item(self, *, dataset_name: str, id: str, **kwargs: Any) -> None:
        self.fail_if_asked("create_dataset_item")
        self.items[id] = dataset_item(dataset_name, id, kwargs)

    def get_dataset(self, name: str) -> FakeDataset:
        self.fail_if_asked("get_dataset")
        return FakeDataset([*self.stale_items, *self.items.values()])

    def run_experiment(self, **kwargs: Any) -> ExperimentResult:
        self.fail_if_asked("run_experiment")
        self.experiments.append(kwargs)
        results = [self.run_item(item, kwargs) for item in kwargs["data"]]
        return ExperimentResult(
            name=kwargs["name"],
            run_name=kwargs["run_name"],
            description=kwargs.get("description"),
            item_results=results[self.drop_items :],
            run_evaluations=[],
            experiment_id="experiment-1",
            dataset_run_id="run-1",
            dataset_run_url=self.run_url,
        )

    def run_item(self, item: DatasetItem, kwargs: dict[str, Any]) -> ExperimentItemResult:
        output = self.replayed_output or kwargs["task"](item=item)
        evaluations: list[Evaluation] = []
        for evaluator in kwargs["evaluators"]:
            # The SDK logs a raising evaluator and carries on, so its score silently vanishes.
            if evaluator.__name__ in self.failing_evaluators:
                continue
            evaluations.extend(
                evaluator(
                    input=item.input,
                    output=output,
                    expected_output=item.expected_output,
                    metadata=item.metadata,
                )
            )
        return ExperimentItemResult(
            item=item, output=output, evaluations=evaluations, trace_id="t", dataset_run_id="r"
        )

    def flush(self) -> None:
        self.flushes += 1


def experiment() -> ExperimentRun:
    answered, review = golden_case(), review_case()
    return ExperimentRun(
        dataset_name="legalmove-calibration",
        run_name="calibration-20261027T120000Z",
        cases=(answered, review),
        runs={answered.case_id: case_run(), review.case_id: failed_run()},
        metrics=METRICS,
    )


def test_the_split_is_upserted_as_a_dataset_with_one_item_per_case() -> None:
    client = RecordingLangfuse()

    LangfusePublisher(client).publish(experiment())

    assert [dataset["name"] for dataset in client.datasets] == ["legalmove-calibration"]
    assert list(client.items) == [
        "legalmove-calibration:standard_delta_demo",
        "legalmove-calibration:boundary_unknown_demo",
    ]


def test_an_item_holds_file_names_and_truth_never_the_images() -> None:
    client = RecordingLangfuse()

    LangfusePublisher(client).publish(experiment())

    item = client.items["legalmove-calibration:boundary_unknown_demo"]
    assert item.input == {
        "case_id": "boundary_unknown_demo",
        "original_image": "original.png",
        "amendment_image": "amendment.png",
    }
    assert item.expected_output["expected_error"] == "UnclassifiableAmendmentError"
    assert item.expected_output["output"] is None
    assert item.metadata == {
        "case_id": "boundary_unknown_demo",
        "split": "calibration",
        "category": "boundary",
        "amendment_kind": "delta",
    }
    published = json.dumps([item.input, item.expected_output, item.metadata], ensure_ascii=False)
    assert review_case().source_original not in published
    assert review_case().source_amendment not in published
    assert "source_original" not in published and "reference_summary" not in published


def test_the_experiment_runs_only_this_splits_items() -> None:
    stale = dataset_item("legalmove-calibration", "legalmove-calibration:retired_case", {})
    client = RecordingLangfuse(stale_items=[stale])

    url = LangfusePublisher(client).publish(experiment())

    (call,) = client.experiments
    assert [item.id for item in call["data"]] == list(client.items)
    assert call["name"] == "legalmove-calibration"
    assert call["run_name"] == "calibration-20261027T120000Z"
    assert url == "https://langfuse.test/runs/1"
    assert client.flushes == 1


def test_the_task_replays_the_recorded_run_without_calling_the_pipeline() -> None:
    client = RecordingLangfuse()

    LangfusePublisher(client).publish(experiment())

    call = client.experiments[0]
    output = call["task"](item=call["data"][1])
    assert output["exit_code"] == 10
    assert output["error"] == "UnclassifiableAmendmentError"


def test_the_checks_are_the_evaluators_and_not_applicable_scores_are_skipped() -> None:
    client = RecordingLangfuse()

    LangfusePublisher(client).publish(experiment())

    call = client.experiments[0]
    assert [evaluator.__name__ for evaluator in call["evaluators"]] == [m.name for m in METRICS]
    answered, review = (client.run_item(item, call) for item in call["data"])
    assert {e.name: e.value for e in review.evaluations} == {
        "amendment_kind_accuracy": 1.0,
        "human_review": 1.0,
    }
    assert {e.name for e in answered.evaluations} == {m.name for m in METRICS} - {"human_review"}


def test_a_case_langfuse_dropped_from_the_experiment_is_an_error() -> None:
    client = RecordingLangfuse(drop_items=1)

    with pytest.raises(ObservabilityError, match="1 of 2 cases"):
        LangfusePublisher(client).publish(experiment())

    assert client.flushes == 1


def test_a_missing_evaluation_is_an_error_naming_only_the_metrics() -> None:
    client = RecordingLangfuse(failing_evaluators={"cer", "judge_score"})

    with pytest.raises(ObservabilityError) as caught:
        LangfusePublisher(client).publish(experiment())

    message = str(caught.value)
    assert "standard_delta_demo" in message
    assert "cer" in message and "judge_score" in message
    assert "8.0" not in message and "Cláusula" not in message


def test_an_unexpected_evaluation_is_an_error_too() -> None:
    # Langfuse grades a run carrying a Judge score while the local run has none.
    client = RecordingLangfuse(replayed_output=case_run().model_dump(mode="json"))
    run = experiment()
    unjudged = {**run.runs, "standard_delta_demo": case_run(judge_score=None)}
    replay = ExperimentRun(run.dataset_name, run.run_name, run.cases, unjudged, run.metrics)

    with pytest.raises(ObservabilityError, match="judge_score"):
        LangfusePublisher(client).publish(replay)


def test_a_run_without_dataset_run_url_is_published_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = RecordingLangfuse(run_url=None)

    with caplog.at_level(logging.WARNING):
        url = LangfusePublisher(client).publish(experiment())

    assert url is None
    assert "dataset-run-items" in caplog.text


@pytest.mark.parametrize(
    "call", ["create_dataset", "create_dataset_item", "get_dataset", "run_experiment"]
)
def test_sdk_failures_become_observability_errors_with_the_type_only(call: str) -> None:
    client = RecordingLangfuse(failing_call=call)

    with pytest.raises(ObservabilityError) as caught:
        LangfusePublisher(client).publish(experiment())

    assert str(caught.value).endswith(": RuntimeError")
    assert "secret" not in str(caught.value) and "Cláusula" not in str(caught.value)
    assert caught.value.__cause__ is None
