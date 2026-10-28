"""Port that publishes a Split as a Langfuse Dataset and one eval run as its Experiment."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from eval_case import CaseRun, GoldenCase
from metrics import Metric


@dataclass(frozen=True)
class ExperimentRun:
    """One eval run: the Split's cases, what the pipeline did with each, and the checks."""

    dataset_name: str
    run_name: str
    cases: tuple[GoldenCase, ...]
    runs: Mapping[str, CaseRun]
    metrics: tuple[Metric, ...]
    # Prompt versions and the like, attached to the Experiment run as plain strings.
    metadata: Mapping[str, str] = field(default_factory=dict)


class ExperimentPublisher(Protocol):
    """Publishes an ExperimentRun and returns where to see it, when the backend knows."""

    def publish(self, experiment: ExperimentRun) -> str | None: ...
