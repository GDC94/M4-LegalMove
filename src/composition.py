"""Shared composition root: wires Settings into Stages, tracers and the public error envelope."""

import dataclasses
import logging
import traceback
from collections.abc import Mapping
from dataclasses import dataclass

from langchain_openai import ChatOpenAI
from langfuse import Langfuse

from agents.contextualization_agent import (
    CONTEXTUALIZATION_PROMPT_VERSION,
    ContextualizationInput,
    build_contextualization_stage,
    build_structured_context_models,
)
from agents.extraction_agent import (
    EXTRACTION_PROMPT_VERSION,
    ExtractionInput,
    build_extraction_stage,
    build_structured_extraction_models,
)
from budget import AttemptLimits, CallBudget
from budgeted_model import BudgetedModel, TimeoutPolicy, build_budgeted_structured_models
from config import Settings
from errors import LegalMoveError, NeedsHumanReviewError
from image_parser import (
    VISION_PROMPT_VERSION,
    ContractImageParser,
    ImageLimits,
    ParseCache,
    VisionConfig,
    build_contract_image_parser,
    build_openai_chat_model,
)
from observability import (
    PipelineTracer,
    TraceLifecycle,
    create_langfuse_tracer,
)
from observability import connect_langfuse as open_client
from pipeline import AnalysisRequest, AnalysisResult, PipelineStages

logger = logging.getLogger("legalmove")


@dataclass(frozen=True)
class Container:
    """Everything a run needs, wired once from Settings."""

    tracer: TraceLifecycle
    stages: PipelineStages
    budget: CallBudget
    environment: str
    root_metadata: Mapping[str, object]


@dataclass
class RunContext:
    """Mutable on purpose: the trace_id only exists once the root span is open."""

    run_id: str
    trace_id: str | None = None


def connect_langfuse(settings: Settings) -> Langfuse:
    """Client plus `auth_check()`; the CLI connects per run, the API once per process."""
    return open_client(
        public_key=settings.langfuse_public_key.get_secret_value(),
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        host=settings.langfuse_host,
        environment=settings.app_env,
    )


def build_tracer(client: Langfuse, settings: Settings) -> TraceLifecycle:
    """A tracer with its own callback handler over an already connected client."""
    return create_langfuse_tracer(
        client, public_key=settings.langfuse_public_key.get_secret_value()
    )


def build_container(settings: Settings) -> Container:
    """The CLI's composition: connect, then one tracer for the run."""
    return assemble_container(settings, build_tracer(connect_langfuse(settings), settings))


def assemble_container(settings: Settings, tracer: TraceLifecycle) -> Container:
    """A fresh budget and Stages around the given tracer; the API calls this once per job."""
    budget = CallBudget(settings.pipeline_timeout_seconds)
    return Container(
        tracer=tracer,
        stages=build_stages(settings, tracer, budget),
        budget=budget,
        environment=settings.app_env,
        root_metadata=describe_versions(settings),
    )


def describe_versions(settings: Settings) -> dict[str, object]:
    return {
        "vision_prompt_version": VISION_PROMPT_VERSION,
        "contextualization_prompt_version": CONTEXTUALIZATION_PROMPT_VERSION,
        "extraction_prompt_version": EXTRACTION_PROMPT_VERSION,
        "vision_model": settings.vision_model,
        "contextualization_model": settings.contextualization_model,
        "extraction_model": settings.extraction_model,
    }


def build_stages(settings: Settings, tracer: PipelineTracer, budget: CallBudget) -> PipelineStages:
    parser = build_parser(settings, tracer, budget)
    contextualize = build_contextualization_stage(
        build_budgeted_structured_models(
            lambda limits: build_structured_context_models(
                build_chat_model(settings, settings.contextualization_model, limits)
            ),
            timeout_policy(settings, budget, "contextualization"),
        ),
        tracer,
    )
    extract = build_extraction_stage(
        build_budgeted_structured_models(
            lambda limits: build_structured_extraction_models(
                build_chat_model(settings, settings.extraction_model, limits)
            ),
            timeout_policy(settings, budget, "extraction"),
        ),
        tracer,
    )
    return PipelineStages(
        parse_contract_image=parser.parse_contract_image,
        contextualize=lambda original, amendment: contextualize.invoke(
            ContextualizationInput(original=original, amendment=amendment)
        ),
        extract=lambda context_map, original, amendment: extract.invoke(
            ExtractionInput(context_map=context_map, original=original, amendment=amendment)
        ),
    )


def build_parser(
    settings: Settings, tracer: PipelineTracer, budget: CallBudget
) -> ContractImageParser:
    vision_model = BudgetedModel(
        lambda limits: build_chat_model(settings, settings.vision_model, limits),
        timeout_policy(settings, budget, "parse"),
    )
    cache = (
        ParseCache(settings.parse_cache_dir, settings.parse_cache_ttl_hours)
        if settings.is_parse_cache_enabled
        else None
    )
    return build_contract_image_parser(vision_model, tracer, build_vision_config(settings), cache)


def build_vision_config(settings: Settings) -> VisionConfig:
    return VisionConfig(
        model=settings.vision_model,
        detail=settings.vision_detail,
        illegible_max_marks=settings.illegible_max_marks,
        limits=build_image_limits(settings),
    )


def build_image_limits(settings: Settings) -> ImageLimits:
    return ImageLimits(
        max_bytes=settings.max_image_bytes,
        min_side=settings.min_image_side_px,
        max_side=settings.max_image_side_px,
        max_pixels=settings.max_image_pixels,
    )


def build_chat_model(settings: Settings, model: str, limits: AttemptLimits) -> ChatOpenAI:
    return build_openai_chat_model(
        model=model,
        temperature=settings.llm_temperature,
        timeout_seconds=limits.timeout,
        max_retries=limits.retries,
        api_key=settings.openai_api_key,
    )


def timeout_policy(settings: Settings, budget: CallBudget, stage: str) -> TimeoutPolicy:
    return TimeoutPolicy(
        budget=budget,
        per_call_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        stage=stage,
    )


def describe_request(request: AnalysisRequest) -> dict[str, object]:
    """File names only; the images never reach the trace (decision 11)."""
    return {"original": request.original_path.name, "amendment": request.amendment_path.name}


def describe_result(result: AnalysisResult) -> dict[str, object]:
    return {
        "amendment_kind": result.context_map.amendment_kind.value,
        "original_image_sha256": result.original.image_sha256,
        "amendment_image_sha256": result.amendment.image_sha256,
    }


def flush_client(client: Langfuse) -> None:
    """Hand the last traces of the process over; a failure is logged, never raised."""
    try:
        client.flush()
    # The SDK documents no error type for flush; shutdown must not hide the real stop reason.
    except Exception as error:
        logger.error("final trace flush failed: %s", type(error).__name__)


def log_unexpected_failure(error: Exception) -> None:
    """Foreign messages may quote documents, so only the type and the stack frames are logged."""
    if isinstance(error, LegalMoveError):
        return
    stack = "".join(traceback.format_tb(error.__traceback__))
    logger.error("unexpected failure: %s\n%s", type(error).__name__, stack)


def build_envelope(error: Exception, context: RunContext) -> dict[str, object]:
    """Public error contract of plan §4.7; domain messages are already sanitised."""
    known = isinstance(error, LegalMoveError)
    envelope: dict[str, object] = {
        "error": type(error).__name__ if known else "UnexpectedError",
        "stage": error.stage if isinstance(error, LegalMoveError) else "unknown",
        "message": str(error) if known else "unexpected failure; see the logs",
        "run_id": context.run_id,
        "trace_id": context.trace_id,
    }
    if isinstance(error, NeedsHumanReviewError):
        envelope["issues"] = [dataclasses.asdict(issue) for issue in error.issues]
    return envelope
