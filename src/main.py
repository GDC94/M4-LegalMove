"""LegalMove CLI entry point and composition root."""

import argparse
import json
import logging
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, TextIO, override

from composition import (
    Container,
    RunContext,
    build_container,
    build_envelope,
    describe_request,
    describe_result,
    log_unexpected_failure,
)
from config import APP_VERSION, Settings, load_settings
from errors import LegalMoveError, ObservabilityError, UsageError
from models import ContractChangeOutput
from observability import TraceAttributes, TraceLifecycle
from pipeline import AnalysisRequest, AnalysisResult, run_analysis

UNEXPECTED_EXIT_CODE = 1
LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"

logger = logging.getLogger("legalmove")


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


class CliArgumentParser(argparse.ArgumentParser):
    """Raises instead of exiting, so a usage error still gets its run_id and envelope."""

    @override
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
    return run_traced(container, arguments, context).output


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
) -> AnalysisResult:
    """Fail closed: the result is only returned, and so printed, after a confirmed flush.

    The CLI prints `result.output`; the eval runner (evals/case_runner.py) reads the rest.
    """
    try:
        result = run_in_root(container, arguments, context)
    except Exception:
        flush_after_failure(container.tracer)
        raise
    container.tracer.flush()
    return result


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


def report_failure(error: Exception, context: RunContext, streams: CliStreams) -> int:
    log_unexpected_failure(error)
    write_json_line(streams.stderr, build_envelope(error, context))
    return error.exit_code if isinstance(error, LegalMoveError) else UNEXPECTED_EXIT_CODE


def write_json_line(stream: TextIO, payload: Mapping[str, object]) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    sys.exit(main())
