"""CaseRunner: one Golden Case through main.py's composition root, recorded as a CaseRun."""

from dataclasses import dataclass, field

import pytest

from budget import CallBudget
from case_runner import CaseRunner
from cli_doubles import RecordingLifecycleTracer
from composition import Container
from errors import (
    InvalidExtractionError,
    ObservabilityError,
    UnclassifiableAmendmentError,
    UnresolvedReferenceError,
    ValidationIssue,
)
from eval_case import CaseRun, GoldenCase, SecondJudgeResult
from eval_doubles import golden_case, review_case
from judge import JudgeVerdict
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
    second_judged: list[tuple[str, str]] = field(default_factory=list)
    second_scores: list[int] = field(default_factory=lambda: [4, 6])
    call_log: list[str] = field(default_factory=list)

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

    def judge(self, summary: str, case: GoldenCase) -> JudgeVerdict:
        self.judged.append((summary, case.case_id))
        self.call_log.append("judge")
        if self.judge_error:
            raise self.judge_error
        return JudgeVerdict(reasoning="Cubre todos los hechos.", score=9)

    def second_judge(self, summary: str, case: GoldenCase) -> SecondJudgeResult:
        score = self.second_scores[len(self.second_judged)]
        self.second_judged.append((summary, case.case_id))
        self.call_log.append("second_judge")
        return SecondJudgeResult(
            status="scored",
            score=score,
            reasoning="Falta un hecho.",
            model="claude-opus-5-5",
            latency_s=0.1,
        )

    def run(self, case: GoldenCase, *, with_second: bool = False, reps: int = 1) -> CaseRun:
        runner = CaseRunner(
            compose=self.compose,
            judge=self.judge,
            session_id=SESSION,
            second_judge=self.second_judge if with_second else None,
            second_judge_reps=reps,
        )
        return runner(case)


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
    assert run.judge_reasoning == "Cubre todos los hechos."


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
    assert run.judge_reasoning is None


def test_without_a_second_judge_nothing_is_recorded_for_it() -> None:
    harness = Harness()

    run = harness.run(golden_case())

    assert run.second_judge is None
    assert run.second_judge_repeats == ()


def test_the_second_judge_rates_the_same_summary_after_the_first() -> None:
    harness = Harness()

    run = harness.run(golden_case(), with_second=True)

    assert run.output is not None
    assert harness.second_judged == [(run.output.summary_of_the_change, "standard_delta_demo")]
    assert harness.call_log == ["judge", "second_judge"]
    assert run.second_judge is not None and run.second_judge.score == 4
    assert run.second_judge_repeats == ()


def test_the_second_judge_never_rates_a_case_expecting_human_review() -> None:
    harness = Harness()

    run = harness.run(review_case(), with_second=True)

    assert harness.second_judged == []
    assert run.second_judge is None


def test_repetitions_keep_the_first_as_the_score_and_the_rest_as_repeats() -> None:
    harness = Harness()

    run = harness.run(golden_case(), with_second=True, reps=2)

    assert harness.call_log == ["judge", "second_judge", "second_judge"]
    assert run.second_judge is not None and run.second_judge.score == 4
    assert [repeat.score for repeat in run.second_judge_repeats] == [6]


def test_a_failed_first_judge_does_not_hide_the_second_judge() -> None:
    harness = Harness(judge_error=RuntimeError("judge timed out"))

    run = harness.run(golden_case(), with_second=True)

    assert run.judge_score is None
    assert run.second_judge is not None and run.second_judge.score == 4


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


def test_an_unresolved_reference_is_recorded_as_a_delta_with_its_issue_code() -> None:
    # Only a delta map can raise it (checks.find_unresolved_references), so the kind is known.
    issue = ValidationIssue("unresolved_reference", "sections[1]", "section new:11 ...")
    harness = Harness(failures={"contextualization_agent": UnresolvedReferenceError([issue])})

    run = harness.run(review_case())

    assert run.exit_code == 10
    assert run.error == "UnresolvedReferenceError"
    assert run.issue_codes == ("unresolved_reference",)
    assert run.amendment_kind is AmendmentKind.DELTA


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


def test_fewer_than_one_second_judge_repetition_is_refused_before_any_case_runs() -> None:
    harness = Harness()

    with pytest.raises(ValueError, match="second_judge_reps must be at least 1"):
        harness.run(golden_case(), with_second=True, reps=0)

    assert harness.call_log == []
