"""run_analysis(): sequential Stages through ports, output_validation and the budget (§1, §3.3)."""

from pathlib import Path

import pytest

from budget import CallBudget
from errors import InvalidExtractionError, UpstreamError, ValidationIssue
from models import NO_CHANGES, AmendmentKind, ContextMap, ExtractionResult, SectionMapping
from observability import RecordingTracer
from pipeline import AnalysisRequest, AnalysisResult, run_analysis
from pipeline_doubles import ORIGINAL_TEXT, StubStages, context_map, extraction

REQUEST = AnalysisRequest(original_path=Path("original.png"), amendment_path=Path("adenda.png"))
STAGE_ORDER = [
    "parse_original_contract",
    "parse_amendment_contract",
    "contextualization_agent",
    "extraction_agent",
    "output_validation",
]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def run(
    stages: StubStages, tracer: RecordingTracer, budget: CallBudget | None = None
) -> AnalysisResult:
    return run_analysis(REQUEST, stages.ports(), tracer, budget or CallBudget(300))


def test_stages_run_in_order_and_hand_their_results_forward() -> None:
    tracer = RecordingTracer()
    stages = StubStages(tracer)

    result = run(stages, tracer)

    assert [stage.name for stage in tracer.stages] == STAGE_ORDER
    assert result.original.source_path == "original.png"
    assert result.amendment.source_path == "adenda.png"
    assert result.context_map == context_map()
    assert result.extraction == extraction()


def test_public_output_keeps_validated_lists_and_renders_the_summary() -> None:
    tracer = RecordingTracer()

    result = run(StubStages(tracer), tracer)

    assert result.output.sections_changed == ["1"]
    assert result.output.topics_touched == ["price_payment"]
    assert result.output.summary_of_the_change == (
        "1 — modified: El precio pasa de $ 1.500.000 a $ 1.850.000."
    )
    validation = tracer.stages[-1]
    assert validation.name == "output_validation"
    assert validation.output == result.output.model_dump(mode="json")


def test_result_carries_the_hard_and_soft_scores_with_n_a_kept_as_none() -> None:
    tracer = RecordingTracer()

    result = run(StubStages(tracer), tracer)

    assert result.scores == {
        "schema_valid": 1,
        "consistency": 1,
        "grounding_rate": 1.0,
        "diff_agreement": 1.0,
        "kind_agreement": None,
        "other_topic_rate": 0.0,
    }


def test_success_records_the_soft_scores_then_outcome_ok() -> None:
    tracer = RecordingTracer()

    run(StubStages(tracer), tracer)

    # The stub Amendment has no Delta marker, so kind_agreement is N/A and is not recorded.
    assert tracer.scores == [
        ("diff_agreement", 1.0),
        ("other_topic_rate", 0.0),
        ("outcome", "ok"),
    ]


def test_output_validation_records_diff_coverage_in_its_span() -> None:
    tracer = RecordingTracer()

    run(StubStages(tracer), tracer)

    validation = tracer.stages[-1]
    assert validation.name == "output_validation"
    assert validation.metadata == {
        "diff_agreement": 1.0,
        "sections_diffed": 1,
        "sections_total": 1,
        "kind_agreement": None,
        "other_topic_rate": 0.0,
    }


DELTA_AMENDMENT = "ADENDA\nModifícase la Cláusula Primera: $ 1.850.000."


def delta_map() -> ContextMap:
    return ContextMap(
        reasoning="Solo instrucciones.",
        amendment_kind=AmendmentKind.DELTA,
        sections=[
            SectionMapping(
                section_id="1",
                title="Precio",
                original_ref="Cláusula Primera",
                amendment_ref="Modifícase la Cláusula Primera",
                purpose="Fija el precio.",
            )
        ],
    )


def test_delta_records_kind_agreement_and_no_diff_agreement() -> None:
    tracer = RecordingTracer()
    stages = StubStages(tracer, context_map_result=delta_map, amendment_text=DELTA_AMENDMENT)

    result = run(stages, tracer)

    names = [name for name, _ in tracer.scores]
    assert "diff_agreement" not in names
    assert ("kind_agreement", 1) in tracer.scores
    assert result.scores["diff_agreement"] is None
    assert result.scores["kind_agreement"] == 1
    assert tracer.stages[-1].metadata["diff_agreement"] is None
    assert "sections_total" not in tracer.stages[-1].metadata


def no_changes() -> ExtractionResult:
    return ExtractionResult(
        reasoning="Sin cambios.",
        changes=[],
        sections_changed=[],
        topics_touched=[],
        summary_of_the_change=NO_CHANGES,
    )


def test_other_topic_rate_is_not_recorded_without_changes() -> None:
    tracer = RecordingTracer()
    stages = StubStages(tracer, extraction_result=no_changes, amendment_text=ORIGINAL_TEXT)

    result = run(stages, tracer)

    assert result.scores["other_topic_rate"] is None
    assert "other_topic_rate" not in [name for name, _ in tracer.scores]


def test_a_soft_disagreement_never_retries_or_blocks() -> None:
    tracer = RecordingTracer()
    # The agent reports a Change on an Amendment identical to the original: whatever the
    # threshold, difflib sees none, so the two signals disagree completely.
    stages = StubStages(tracer, amendment_text=ORIGINAL_TEXT)

    result = run(stages, tracer)

    assert result.scores["diff_agreement"] == 0.0
    assert stages.calls == STAGE_ORDER[:4]
    assert tracer.scores[-1] == ("outcome", "ok")
    assert result.output.sections_changed == ["1"]


def test_diff_agreement_is_one_when_neither_side_sees_a_change() -> None:
    tracer = RecordingTracer()
    stages = StubStages(tracer, extraction_result=no_changes, amendment_text=ORIGINAL_TEXT)

    result = run(stages, tracer)

    assert result.scores["diff_agreement"] == 1.0
    assert ("diff_agreement", 1.0) in tracer.scores


def test_result_carries_the_semantic_retries_of_each_agent() -> None:
    tracer = RecordingTracer()
    stages = StubStages(tracer, context_retries=1, extraction_retries=0)

    result = run(stages, tracer)

    assert (result.context_retries, result.extraction_retries) == (1, 0)


def test_human_review_stops_the_pipeline_and_scores_outcome_human_review() -> None:
    tracer = RecordingTracer()
    error = InvalidExtractionError([ValidationIssue("ungrounded_quote", "changes[0]", "m")])
    stages = StubStages(tracer, failures={"extraction_agent": error})

    with pytest.raises(InvalidExtractionError):
        run(stages, tracer)

    assert [stage.name for stage in tracer.stages] == STAGE_ORDER[:4]
    assert tracer.stages[-1].error is error
    assert tracer.scores == [("outcome", "human_review")]


def test_any_other_failure_scores_outcome_failed() -> None:
    tracer = RecordingTracer()
    stages = StubStages(tracer, failures={"parse_amendment_contract": UpstreamError("down")})

    with pytest.raises(UpstreamError):
        run(stages, tracer)

    assert stages.calls == ["parse_original_contract", "parse_amendment_contract"]
    assert tracer.scores == [("outcome", "failed")]


def test_output_validation_failure_marks_its_span_as_error() -> None:
    tracer = RecordingTracer()
    broken = {**dict(extraction()), "sections_changed": [], "topics_touched": []}
    # Bypasses validation on purpose: only a bug upstream could hand this to the pipeline.
    stages = StubStages(
        tracer, extraction_result=lambda: ExtractionResult.model_construct(**broken)
    )

    with pytest.raises(ValueError, match="validation error"):
        run(stages, tracer)

    assert tracer.stages[-1].name == "output_validation"
    assert tracer.stages[-1].error is not None
    # Soft scores are recorded before the final validation, so its failure keeps them.
    assert ("other_topic_rate", 0.0) in tracer.scores
    assert tracer.scores[-1] == ("outcome", "failed")


def test_a_spent_budget_starts_no_further_stage() -> None:
    clock = FakeClock()
    tracer = RecordingTracer()
    stages = StubStages(tracer, on_call=lambda name: setattr(clock, "now", 301.0))

    with pytest.raises(UpstreamError) as caught:
        run(stages, tracer, CallBudget(300, clock=clock))

    assert stages.calls == ["parse_original_contract"]
    assert caught.value.stage == "parse"
    assert tracer.scores == [("outcome", "failed")]


def test_budget_is_consulted_before_each_model_stage() -> None:
    clock = FakeClock()
    tracer = RecordingTracer()
    stages = StubStages(
        tracer,
        on_call=lambda name: setattr(
            clock, "now", 301.0 if name == "contextualization_agent" else 0
        ),
    )

    with pytest.raises(UpstreamError) as caught:
        run(stages, tracer, CallBudget(300, clock=clock))

    assert stages.calls == STAGE_ORDER[:3]
    assert caught.value.stage == "extraction"
