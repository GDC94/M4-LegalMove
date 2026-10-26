"""Application layer: run_analysis() runs the Stages in order through their ports (plan §1)."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from budget import CallBudget
from checks import compute_grounding_rate
from errors import NeedsHumanReviewError
from models import (
    ContextMap,
    ContractChangeOutput,
    DocumentRole,
    ExtractionResult,
    ParsedContract,
    render_summary,
)

if TYPE_CHECKING:
    # Type-only: the pipeline knows the port, never the Langfuse adapter behind it.
    from observability import PipelineTracer

ParseContractImage = Callable[[Path, DocumentRole], ParsedContract]
Contextualize = Callable[[ParsedContract, ParsedContract], ContextMap]
Extract = Callable[[ContextMap, ParsedContract, ParsedContract], ExtractionResult]


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
    scores: Mapping[str, float | None]


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
    context_map = stages.contextualize(original, amendment)
    budget.ensure_remaining(stage="extraction")
    extraction = stages.extract(context_map, original, amendment)
    return AnalysisResult(
        output=validate_output(extraction, tracer),
        original=original,
        amendment=amendment,
        context_map=context_map,
        extraction=extraction,
        scores=hard_validation_scores(extraction, original, amendment),
    )


def validate_output(extraction: ExtractionResult, tracer: PipelineTracer) -> ContractChangeOutput:
    """Keep the validated lists, publish the rendered Summary, then validate the public model."""
    with tracer.stage("output_validation", {}) as span:
        output = ContractChangeOutput.model_validate(
            {
                "sections_changed": extraction.sections_changed,
                "topics_touched": extraction.topics_touched,
                "summary_of_the_change": render_summary(extraction.changes),
            }
        )
        span.set_output(output.model_dump(mode="json"))
        return output


def hard_validation_scores(
    extraction: ExtractionResult, original: ParsedContract, amendment: ParsedContract
) -> dict[str, float | None]:
    """An accepted ExtractionResult passed schema and Consistency by construction."""
    return {
        "schema_valid": 1,
        "consistency": 1,
        "grounding_rate": compute_grounding_rate(extraction, original.text, amendment.text),
    }
