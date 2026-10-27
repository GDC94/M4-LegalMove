"""CaseRunner: one Golden Case through main.py's composition root, recorded as a CaseRun."""

from dataclasses import dataclass, field

import pytest

from budget import CallBudget
from case_runner import CaseRunner
from cli_doubles import RecordingLifecycleTracer
from errors import (
    InvalidExtractionError,
    ObservabilityError,
    UnclassifiableAmendmentError,
    ValidationIssue,
)
from eval_case import CaseRun, GoldenCase
from eval_doubles import golden_case, review_case
from main import Container
from models import AmendmentKind, ChangeType, Topic
from pipeline_doubles import AMENDMENT_TEXT, ORIGINAL_TEXT, StubStages

SESSION = "calibration-20261027T120000Z"


@dataclass
class Harness:
    """A fake pipeline behind the real Container, plus a recording Judge."""

    failures: dict[str, BaseException] = field(default_factory=dict)
    judge_error: Exception | None = None
    tracers: list[RecordingLifecycleTracer] = field(default_factory=list)
    stages: list[StubStages] = field(default_factory=list)
    judged: list[tuple[str, str]] = field(default_factory=list)

    def compose(self) -> Container:
        tracer = RecordingLifecycleTracer()
        stages = StubStages(tracer, failures=self.failures)
        self.tracers.append(tracer)
        self.stages.append(stages)
        return Container(
            tracer=tracer,
            stages=stages.ports(),
            budget=CallBudget(300),
            environment="development",
            root_metadata={"extraction_model": "gpt-4o"},
        )

    def judge(self, summary: str, case: GoldenCase) -> float:
        self.judged.append((summary, case.case_id))
        if self.judge_error:
            raise self.judge_error
        return 9.0

    def run(self, case: GoldenCase) -> CaseRun:
        return CaseRunner(compose=self.compose, judge=self.judge, session_id=SESSION)(case)


def test_a_successful_run_keeps_what_every_check_reads() -> None:
    harness = Harness()

    run = harness.run(golden_case())

    assert run.case_id == "standard_delta_demo"
    assert run.exit_code == 0
    assert run.error is None
    assert run.trace_id == "trace-abc"
    assert run.output is not None and run.output.sections_changed == ["1"]
    assert run.amendment_kind is AmendmentKind.RESTATED
    assert [(c.section_id, c.change_type, c.topic) for c in run.changes] == [
        ("1", ChangeType.MODIFIED, Topic.PRICE_PAYMENT)
    ]
    assert (run.parsed_original, run.parsed_amendment) == (ORIGINAL_TEXT, AMENDMENT_TEXT)


def test_the_case_images_go_through_the_root_span_and_are_flushed() -> None:
    harness = Harness()

    harness.run(golden_case())

    tracer = harness.tracers[0]
    root, attributes = tracer.roots[0]
    assert root.input == {"original": "original.png", "amendment": "amendment.png"}
    assert attributes.session_id == SESSION
    assert tracer.flushes == 1
    assert harness.stages[0].calls[0] == "parse_original_contract"


def test_every_case_gets_a_fresh_container_and_time_budget() -> None:
    harness = Harness()

    harness.run(golden_case())
    harness.run(golden_case())

    assert len(harness.tracers) == 2


def test_the_judge_rates_the_published_summary_once() -> None:
    harness = Harness()

    run = harness.run(golden_case())

    assert run.output is not None
    assert harness.judged == [(run.output.summary_of_the_change, "standard_delta_demo")]
    assert run.judge_score == 9.0


def test_a_case_expecting_human_review_is_never_judged() -> None:
    harness = Harness()

    run = harness.run(review_case())

    assert run.exit_code == 0
    assert harness.judged == []
    assert run.judge_score is None


def test_a_failed_judge_leaves_the_score_not_applicable() -> None:
    harness = Harness(judge_error=RuntimeError("judge timed out"))

    run = harness.run(golden_case())

    assert run.exit_code == 0
    assert run.judge_score is None


def test_a_human_review_is_recorded_with_exit_10_and_its_issue_codes() -> None:
    issue = ValidationIssue("unresolved_section_reference", "changes[0]", "unknown section")
    harness = Harness(failures={"extraction_agent": InvalidExtractionError([issue])})

    run = harness.run(golden_case())

    assert run.exit_code == 10
    assert run.error == "InvalidExtractionError"
    assert run.issue_codes == ("unresolved_section_reference",)
    assert run.output is None
    assert run.trace_id == "trace-abc"
    assert harness.judged == []
    assert harness.tracers[0].flushes == 1


def test_an_unknown_amendment_is_recorded_as_an_unknown_kind() -> None:
    error = UnclassifiableAmendmentError("the amendment kind is unknown")
    harness = Harness(failures={"contextualization_agent": error})

    run = harness.run(review_case())

    assert run.exit_code == 10
    assert run.amendment_kind is AmendmentKind.UNKNOWN


def test_an_unexpected_failure_is_exit_1_and_does_not_stop_the_eval() -> None:
    harness = Harness(failures={"parse_original_contract": RuntimeError("boom Cláusula")})

    run = harness.run(golden_case())

    assert run.exit_code == 1
    assert run.error == "UnexpectedError"
    assert run.amendment_kind is None


def test_a_container_that_cannot_be_composed_stops_the_eval() -> None:
    def broken_compose() -> Container:
        raise ObservabilityError("tracer initialisation failed: auth check rejected")

    runner = CaseRunner(compose=broken_compose, judge=Harness().judge, session_id=SESSION)

    with pytest.raises(ObservabilityError):
        runner(golden_case())
