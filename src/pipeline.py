"""Application layer: run_analysis() runs the Stages in order through their ports (plan §1)."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from budget import CallBudget
from checks import (
    DiffAgreement,
    compute_diff_agreement,
    compute_grounding_rate,
    compute_kind_agreement,
    compute_other_topic_rate,
    detect_kind_heuristic,
)
from errors import NeedsHumanReviewError
from models import (
    AgentOutcome,
    ContextMap,
    ContractChangeOutput,
    DocumentRole,
    ExtractionResult,
    ParsedContract,
    SemanticRetries,
    render_summary,
)

if TYPE_CHECKING:
    # Type-only: the pipeline knows the port, never the Langfuse adapter behind it.
    from observability import PipelineTracer, StageSpan

ParseContractImage = Callable[[Path, DocumentRole], ParsedContract]
Contextualize = Callable[[ParsedContract, ParsedContract], AgentOutcome[ContextMap]]
Extract = Callable[[ContextMap, ParsedContract, ParsedContract], AgentOutcome[ExtractionResult]]
Scores = Mapping[str, float | None]


@dataclass(frozen=True)
class PipelineStages:
    """Ports for the model-backed Stages; main.py wires the adapters behind them."""

    parse_contract_image: ParseContractImage
    contextualize: Contextualize
    extract: Extract


@dataclass(frozen=True)
class AnalysisRequest:
    """The two contract images of one run."""

    original_path: Path
    amendment_path: Path


@dataclass(frozen=True)
class AnalysisResult:
    """Internal result of a run; only `output` is published (plan §3.3)."""

    output: ContractChangeOutput
    original: ParsedContract
    amendment: ParsedContract
    context_map: ContextMap
    extraction: ExtractionResult
    scores: Scores
    context_retries: SemanticRetries
    extraction_retries: SemanticRetries


def run_analysis(
    request: AnalysisRequest, stages: PipelineStages, tracer: PipelineTracer, budget: CallBudget
) -> AnalysisResult:
    """Score the outcome and re-raise: only main.py turns errors into exit codes."""
    try:
        result = analyze(request, stages, tracer, budget)
    except NeedsHumanReviewError:
        tracer.score("outcome", "human_review")
        raise
    except Exception:
        tracer.score("outcome", "failed")
        raise
    tracer.score("outcome", "ok")
    return result


def analyze(
    request: AnalysisRequest, stages: PipelineStages, tracer: PipelineTracer, budget: CallBudget
) -> AnalysisResult:
    """Sequential on purpose (decision 57); no Stage starts once the budget is spent."""
    budget.ensure_remaining(stage="parse")
    original = stages.parse_contract_image(request.original_path, DocumentRole.ORIGINAL)
    budget.ensure_remaining(stage="parse")
    amendment = stages.parse_contract_image(request.amendment_path, DocumentRole.AMENDMENT)
    budget.ensure_remaining(stage="contextualization")
    mapped = stages.contextualize(original, amendment)
    budget.ensure_remaining(stage="extraction")
    extracted = stages.extract(mapped.result, original, amendment)
    validated = ValidatedRun(original, amendment, mapped.result, extracted.result)
    output, soft_scores = validate_output(validated, tracer)
    return AnalysisResult(
        output=output,
        original=original,
        amendment=amendment,
        context_map=mapped.result,
        extraction=extracted.result,
        scores={**hard_validation_scores(validated), **soft_scores},
        context_retries=mapped.retries,
        extraction_retries=extracted.retries,
    )


@dataclass(frozen=True)
class ValidatedRun:
    """What output_validation reads: both Parsed Contracts and both accepted agent results."""

    original: ParsedContract
    amendment: ParsedContract
    context_map: ContextMap
    extraction: ExtractionResult


def validate_output(
    run: ValidatedRun, tracer: PipelineTracer
) -> tuple[ContractChangeOutput, Scores]:
    """Soft scores first, then the rendered Summary and the public model; nothing here retries."""
    with tracer.stage("output_validation", {}) as span:
        soft_scores = record_soft_scores(run, span, tracer)
        output = publish_output(run.extraction)
        span.set_output(output.model_dump(mode="json"))
        return output, soft_scores


def publish_output(extraction: ExtractionResult) -> ContractChangeOutput:
    """Keep the validated lists, publish the rendered Summary, then validate the public model."""
    return ContractChangeOutput.model_validate(
        {
            "sections_changed": extraction.sections_changed,
            "topics_touched": extraction.topics_touched,
            "summary_of_the_change": render_summary(extraction.changes),
        }
    )


def record_soft_scores(run: ValidatedRun, span: StageSpan, tracer: PipelineTracer) -> Scores:
    """Telemetry only (decision 45): N/A stays None in metadata and is never scored as 0."""
    diff = compute_diff_agreement(
        run.context_map, run.extraction.sections_changed, run.original.text, run.amendment.text
    )
    heuristic = detect_kind_heuristic(run.amendment.text)
    scores: Scores = {
        "diff_agreement": None if diff is None else diff.value,
        "kind_agreement": compute_kind_agreement(run.context_map.amendment_kind, heuristic),
        "other_topic_rate": compute_other_topic_rate(run.extraction.changes),
    }
    span.set_metadata({**scores, **describe_coverage(diff)})
    for name, value in scores.items():
        if value is not None:
            tracer.score(name, value)
    return scores


def describe_coverage(diff: DiffAgreement | None) -> dict[str, int]:
    """sections_diffed / sections_total; absent in Delta, where nothing is segmented."""
    if diff is None:
        return {}
    return {"sections_diffed": diff.sections_diffed, "sections_total": diff.sections_total}


def hard_validation_scores(run: ValidatedRun) -> Scores:
    """An accepted ExtractionResult passed schema and Consistency by construction."""
    grounding_rate = compute_grounding_rate(run.extraction, run.original.text, run.amendment.text)
    return {"schema_valid": 1, "consistency": 1, "grounding_rate": grounding_rate}
