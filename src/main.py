"""LegalMove CLI entry point and composition root."""

import argparse
import dataclasses
import json
import logging
import sys
import traceback
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, TextIO

from langchain_openai import ChatOpenAI

from agents.contextualization_agent import (
    CONTEXTUALIZATION_PROMPT_VERSION,
    ContextualizationInput,
    build_contextualization_agent,
    build_structured_context_models,
)
from agents.extraction_agent import (
    EXTRACTION_PROMPT_VERSION,
    ExtractionInput,
    build_extraction_agent,
    build_structured_extraction_models,
)
from budget import AttemptLimits, CallBudget
from budgeted_model import BudgetedModel, TimeoutPolicy, build_budgeted_structured_models
from config import APP_VERSION, Settings, load_settings
from errors import LegalMoveError, NeedsHumanReviewError, ObservabilityError, UsageError
from image_parser import (
    VISION_PROMPT_VERSION,
    ContractImageParser,
    ImageLimits,
    ParseCache,
    VisionConfig,
    build_contract_image_parser,
    build_openai_chat_model,
)
from models import ContractChangeOutput
from observability import (
    PipelineTracer,
    TraceAttributes,
    TraceLifecycle,
    build_langfuse_tracer,
)
from pipeline import AnalysisRequest, AnalysisResult, PipelineStages, run_analysis

UNEXPECTED_EXIT_CODE = 1
LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"

logger = logging.getLogger("legalmove")


@dataclass(frozen=True)
class Container:
    """Everything a run needs, wired once from Settings."""

    tracer: TraceLifecycle
    stages: PipelineStages
    budget: CallBudget
    environment: str
    root_metadata: Mapping[str, object]


@dataclass(frozen=True)
class Composition:
    """The two steps that build a Container; tests swap them for doubles."""

    load_settings: Callable[[], Settings]
    compose: Callable[[Settings], Container]


@dataclass(frozen=True)
class CliStreams:
    stdout: TextIO
    stderr: TextIO


@dataclass(frozen=True)
class CliArguments:
    original: Path
    amendment: Path
    session_id: str | None


@dataclass
class RunContext:
    """Mutable on purpose: the trace_id only exists once the root span is open."""

    run_id: str
    trace_id: str | None = None


class CliArgumentParser(argparse.ArgumentParser):
    """Raises instead of exiting, so a usage error still gets its run_id and envelope."""

    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


def main() -> int:
    streams = CliStreams(stdout=sys.stdout, stderr=sys.stderr)
    return run_cli(
        sys.argv[1:], streams, Composition(load_settings=load_settings, compose=build_container)
    )


def run_cli(argv: Sequence[str], streams: CliStreams, composition: Composition) -> int:
    """The only place that turns errors into exit codes and envelopes (plan §4.6)."""
    context = RunContext(run_id=uuid.uuid4().hex)
    logging.basicConfig(stream=streams.stderr, level=logging.INFO, format=LOG_FORMAT, force=True)
    try:
        output = execute(argv, composition, context)
    except Exception as error:
        return report_failure(error, context, streams)
    streams.stdout.write(output.model_dump_json(indent=2) + "\n")
    write_json_line(streams.stderr, {"run_id": context.run_id, "trace_id": context.trace_id})
    return 0


def execute(
    argv: Sequence[str], composition: Composition, context: RunContext
) -> ContractChangeOutput:
    arguments = parse_arguments(argv)
    settings = composition.load_settings()
    logging.getLogger().setLevel(settings.log_level)
    container = composition.compose(settings)
    return run_traced(container, arguments, context)


def parse_arguments(argv: Sequence[str]) -> CliArguments:
    parser = CliArgumentParser(
        prog="legalmove",
        description="Report the legal changes an amendment introduces into a contract.",
    )
    parser.add_argument("original", type=Path, help="scanned original contract (JPEG or PNG)")
    parser.add_argument("amendment", type=Path, help="scanned amendment (JPEG or PNG)")
    parser.add_argument("--session-id", default=None, help="groups several runs in Langfuse")
    namespace = parser.parse_args(argv)
    return CliArguments(namespace.original, namespace.amendment, namespace.session_id)


def run_traced(
    container: Container, arguments: CliArguments, context: RunContext
) -> ContractChangeOutput:
    """Fail closed: the output is only returned, and so printed, after a confirmed flush."""
    try:
        result = run_in_root(container, arguments, context)
    except Exception:
        flush_after_failure(container.tracer)
        raise
    container.tracer.flush()
    return result.output


def flush_after_failure(tracer: TraceLifecycle) -> None:
    """The original error wins; a failed flush on top of it is only logged."""
    try:
        tracer.flush()
    except ObservabilityError as error:
        logger.error("%s; reporting the original error", error)


def run_in_root(
    container: Container, arguments: CliArguments, context: RunContext
) -> AnalysisResult:
    request = AnalysisRequest(original_path=arguments.original, amendment_path=arguments.amendment)
    attributes = TraceAttributes(
        session_id=arguments.session_id, version=APP_VERSION, environment=container.environment
    )
    metadata = {"run_id": context.run_id, **container.root_metadata}
    tracer = container.tracer
    with tracer.open_root(describe_request(request), metadata, attributes) as root:
        context.trace_id = tracer.trace_id
        try:
            result = run_analysis(request, container.stages, tracer, container.budget)
        except Exception as error:
            root.set_output(build_envelope(error, context))
            raise
        root.set_metadata(describe_result(result))
        root.set_output(result.output.model_dump(mode="json"))
        return result


def describe_request(request: AnalysisRequest) -> dict[str, object]:
    """File names only; the images never reach the trace (decision 11)."""
    return {"original": request.original_path.name, "amendment": request.amendment_path.name}


def describe_result(result: AnalysisResult) -> dict[str, object]:
    return {
        "amendment_kind": result.context_map.amendment_kind.value,
        "original_image_sha256": result.original.image_sha256,
        "amendment_image_sha256": result.amendment.image_sha256,
    }


def report_failure(error: Exception, context: RunContext, streams: CliStreams) -> int:
    if not isinstance(error, LegalMoveError):
        # Foreign messages may quote documents, so only the type and the stack frames are logged.
        stack = "".join(traceback.format_tb(error.__traceback__))
        logger.error("unexpected failure: %s\n%s", type(error).__name__, stack)
    write_json_line(streams.stderr, build_envelope(error, context))
    return error.exit_code if isinstance(error, LegalMoveError) else UNEXPECTED_EXIT_CODE


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


def write_json_line(stream: TextIO, payload: Mapping[str, object]) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def build_container(settings: Settings) -> Container:
    tracer = build_langfuse_tracer(
        public_key=settings.langfuse_public_key.get_secret_value(),
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        host=settings.langfuse_host,
        environment=settings.app_env,
    )
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
    contextualize = build_contextualization_agent(
        build_budgeted_structured_models(
            lambda limits: build_structured_context_models(
                build_chat_model(settings, settings.contextualization_model, limits)
            ),
            timeout_policy(settings, budget, "contextualization"),
        ),
        tracer,
    )
    extract = build_extraction_agent(
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
    limits = ImageLimits(
        max_bytes=settings.max_image_bytes,
        min_side=settings.min_image_side_px,
        max_side=settings.max_image_side_px,
        max_pixels=settings.max_image_pixels,
    )
    return VisionConfig(
        model=settings.vision_model,
        detail=settings.vision_detail,
        illegible_max_marks=settings.illegible_max_marks,
        limits=limits,
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


if __name__ == "__main__":
    sys.exit(main())
