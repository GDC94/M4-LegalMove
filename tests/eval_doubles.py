"""Synthetic Golden Cases, Case Runs and a recording Experiment publisher for the eval tests."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eval_case import CaseRun, GoldenCase
from experiment_publisher import ExperimentRun

SUMMARY = (
    "2 — modified: Los honorarios suben a $ 2.900.000 más IVA. "
    "new:8 — added: Se agregan las notificaciones."
)
EXPECTED_OUTPUT = {
    "sections_changed": ["2", "new:8"],
    "topics_touched": ["price_payment", "notices"],
    "summary_of_the_change": "Suben los honorarios y se agregan las notificaciones.",
}
EXPECTED_CHANGES = [
    {"section_id": "2", "change_type": "modified", "topic": "price_payment"},
    {"section_id": "new:8", "change_type": "added", "topic": "notices"},
]


def golden_case(**overrides: Any) -> GoldenCase:
    fields: dict[str, Any] = {
        "case_id": "standard_delta_demo",
        "split": "calibration",
        "category": "standard",
        "amendment_kind": "delta",
        "expected_error": None,
        "output": EXPECTED_OUTPUT,
        "changes": EXPECTED_CHANGES,
        "key_facts": ["$ 2.900.000", "notificaciones"],
        "reference_summary": "Suben los honorarios y se agregan las notificaciones.",
        "source_original": "CONTRATO\nCláusula Segunda. $ 2.400.000.",
        "source_amendment": "ADENDA\nModifícase la Cláusula Segunda: $ 2.900.000.",
        "original_image": Path("original.png"),
        "amendment_image": Path("amendment.png"),
    }
    return GoldenCase.model_validate({**fields, **overrides})


def review_case(**overrides: Any) -> GoldenCase:
    """A case whose expected result is a Human Review: no output, no changes."""
    return golden_case(
        **{
            "case_id": "boundary_unknown_demo",
            "category": "boundary",
            "expected_error": "UnclassifiableAmendmentError",
            "output": None,
            "changes": [],
            "key_facts": [],
            **overrides,
        }
    )


def case_run(**overrides: Any) -> CaseRun:
    """A perfect run of `golden_case()` unless overridden."""
    fields: dict[str, Any] = {
        "case_id": "standard_delta_demo",
        "exit_code": 0,
        "trace_id": "trace-1",
        "output": {
            "sections_changed": ["2", "new:8"],
            "topics_touched": ["price_payment", "notices"],
            "summary_of_the_change": SUMMARY,
        },
        "amendment_kind": "delta",
        "changes": EXPECTED_CHANGES,
        "parsed_original": "CONTRATO\nCláusula Segunda. $ 2.400.000.",
        "parsed_amendment": "ADENDA\nModifícase la Cláusula Segunda: $ 2.900.000.",
        "judge_score": 8.0,
    }
    return CaseRun.model_validate({**fields, **overrides})


def failed_run(**overrides: Any) -> CaseRun:
    """A run that ended in a Human Review before any output existed."""
    return CaseRun.model_validate(
        {
            "case_id": "boundary_unknown_demo",
            "exit_code": 10,
            "error": "UnclassifiableAmendmentError",
            "amendment_kind": "unknown",
            **overrides,
        }
    )


@dataclass
class RecordingPublisher:
    """ExperimentPublisher double: records what would reach Langfuse and returns a fixed URL."""

    url: str | None = "https://langfuse.test/runs/1"
    published: list[ExperimentRun] = field(default_factory=list)

    def publish(self, experiment: ExperimentRun) -> str | None:
        self.published.append(experiment)
        return self.url
