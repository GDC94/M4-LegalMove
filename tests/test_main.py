"""CLI contract: buffered stdout, fail-closed flush, exit codes and envelopes (§4.5-4.7, §5.7)."""

import dataclasses
import io
import json
from pathlib import Path
from typing import Any

import pytest
from langchain_openai import ChatOpenAI

import main
from agent_doubles import RecordingStructuredModel, structured_reply
from agents.extraction_agent import EXTRACTION_PROMPT_VERSION
from agents.structured_output import StructuredModels
from budget import CallBudget
from cli_doubles import RecordingLifecycleTracer, fake_settings
from config import APP_VERSION, Settings
from errors import (
    ConfigurationError,
    IllegibleDocumentError,
    InvalidContextMapError,
    InvalidExtractionError,
    InvalidImageError,
    LegalMoveError,
    ObservabilityError,
    UnclassifiableAmendmentError,
    UpstreamError,
    ValidationIssue,
)
from main import CliStreams, Composition, Container, run_cli
from models import ContractChangeOutput, DocumentRole
from pipeline_doubles import StubStages, context_map, parsed

ARGV = ["data/original.png", "data/adenda.png"]
ISSUE = ValidationIssue(code="ungrounded_quote", path="changes[0].original_quote", message="m")


@dataclasses.dataclass
class Harness:
    """One CLI run with recording doubles; `stdout_at_flush` proves the buffering order."""

    tracer: RecordingLifecycleTracer = dataclasses.field(default_factory=RecordingLifecycleTracer)
    failures: dict[str, BaseException] = dataclasses.field(default_factory=dict)
    compose_error: LegalMoveError | None = None
    settings_error: LegalMoveError | None = None
    settings_loads: int = 0
    stdout: io.StringIO = dataclasses.field(default_factory=io.StringIO)
    stderr: io.StringIO = dataclasses.field(default_factory=io.StringIO)
    stdout_at_flush: list[str] = dataclasses.field(default_factory=list)

    def load_settings(self) -> Settings:
        self.settings_loads += 1
        if self.settings_error:
            raise self.settings_error
        return fake_settings()

    def compose(self, settings: Settings) -> Container:
        if self.compose_error:
            raise self.compose_error
        self.tracer.on_flush = lambda: self.stdout_at_flush.append(self.stdout.getvalue())
        stages = StubStages(self.tracer, failures=self.failures)
        return Container(
            tracer=self.tracer,
            stages=stages.ports(),
            budget=CallBudget(settings.pipeline_timeout_seconds),
            environment=settings.app_env,
            root_metadata={"extraction_model": settings.extraction_model},
        )

    def run(self, argv: list[str] | None = None) -> int:
        composition = Composition(load_settings=self.load_settings, compose=self.compose)
        return run_cli(
            ARGV if argv is None else argv, CliStreams(self.stdout, self.stderr), composition
        )

    def envelope(self) -> dict[str, Any]:
        return dict(json.loads(self.stderr.getvalue().strip().splitlines()[-1]))


def test_success_prints_only_the_public_json_and_exits_zero() -> None:
    harness = Harness()

    assert harness.run() == 0

    output = ContractChangeOutput.model_validate_json(harness.stdout.getvalue())
    assert output.sections_changed == ["1"]
    assert output.summary_of_the_change.startswith("1 — modified:")


def test_success_ends_stderr_with_run_id_and_trace_id() -> None:
    harness = Harness()

    harness.run()

    final = harness.envelope()
    assert set(final) == {"run_id", "trace_id"}
    assert len(final["run_id"]) == 32
    assert final["trace_id"] == "trace-abc"


def test_stdout_is_written_only_after_a_successful_flush() -> None:
    harness = Harness()

    harness.run()

    assert harness.tracer.flushes == 1
    assert harness.stdout_at_flush == [""]
    assert harness.stdout.getvalue()


def test_settings_are_loaded_once() -> None:
    harness = Harness()

    harness.run()

    assert harness.settings_loads == 1


def test_container_is_a_frozen_dataclass() -> None:
    assert dataclasses.is_dataclass(Container)
    # dataclasses exposes the frozen flag only through this undeclared attribute.
    assert Container.__dataclass_params__.frozen  # type: ignore[attr-defined]


def test_a_failed_flush_after_success_returns_observability_error_and_empty_stdout() -> None:
    harness = Harness()
    harness.tracer.flush_error = ObservabilityError("trace flush failed: ConnectError")

    assert harness.run() == 5

    assert harness.stdout.getvalue() == ""
    assert harness.envelope()["error"] == "ObservabilityError"
    assert harness.envelope()["stage"] == "observability"


def test_a_failed_flush_after_a_business_error_preserves_the_original_error() -> None:
    harness = Harness(failures={"parse_amendment_contract": UpstreamError("vision call failed")})
    harness.tracer.flush_error = ObservabilityError("trace flush failed: ConnectError")

    assert harness.run() == 5

    envelope = harness.envelope()
    assert envelope["error"] == "UpstreamError"
    assert envelope["stage"] == "parse"
    assert harness.tracer.flushes == 1
    assert "trace flush failed" in harness.stderr.getvalue()


def test_a_business_error_still_flushes_the_trace() -> None:
    harness = Harness(failures={"extraction_agent": InvalidExtractionError([ISSUE])})

    harness.run()

    assert harness.tracer.flushes == 1


@pytest.mark.parametrize(
    ("stage", "error", "exit_code"),
    [
        ("parse_original_contract", InvalidImageError("image must be JPEG or PNG"), 4),
        ("parse_original_contract", UpstreamError("vision call failed"), 5),
        ("parse_amendment_contract", IllegibleDocumentError("12 illegible marks"), 6),
        ("contextualization_agent", UnclassifiableAmendmentError("kind is unknown"), 10),
        ("contextualization_agent", InvalidContextMapError([ISSUE]), 10),
        ("extraction_agent", InvalidExtractionError([ISSUE]), 10),
    ],
)
def test_each_domain_error_maps_to_its_exit_code_and_envelope(
    stage: str, error: LegalMoveError, exit_code: int
) -> None:
    harness = Harness(failures={stage: error})

    assert harness.run() == exit_code

    assert harness.stdout.getvalue() == ""
    envelope = harness.envelope()
    assert envelope["error"] == type(error).__name__
    assert envelope["stage"] == error.stage
    assert envelope["message"] == str(error)
    assert envelope["trace_id"] == "trace-abc"
    assert len(envelope["run_id"]) == 32


def test_human_review_envelope_carries_the_issues() -> None:
    harness = Harness(failures={"extraction_agent": InvalidExtractionError([ISSUE])})

    harness.run()

    assert harness.envelope()["issues"] == [
        {"code": "ungrounded_quote", "path": "changes[0].original_quote", "message": "m"}
    ]


def test_errors_without_issues_have_no_issues_field() -> None:
    harness = Harness(failures={"parse_original_contract": UpstreamError("down")})

    harness.run()

    assert "issues" not in harness.envelope()


def test_unexpected_errors_exit_one_without_their_message() -> None:
    harness = Harness(failures={"extraction_agent": KeyError("Cláusula secreta")})

    assert harness.run() == 1

    assert harness.stdout.getvalue() == ""
    envelope = harness.envelope()
    assert envelope["error"] == "UnexpectedError"
    assert "secreta" not in harness.stderr.getvalue()
    assert envelope["run_id"]


def test_usage_errors_exit_two_with_a_run_id_and_no_trace() -> None:
    harness = Harness()

    assert harness.run(["only-one.png"]) == 2

    envelope = harness.envelope()
    assert envelope["error"] == "UsageError"
    assert envelope["stage"] == "cli"
    assert envelope["run_id"]
    assert envelope["trace_id"] is None
    assert harness.settings_loads == 0


def test_configuration_errors_exit_three_with_a_null_trace_id() -> None:
    harness = Harness(settings_error=ConfigurationError("invalid settings: OPENAI_API_KEY"))

    assert harness.run() == 3

    envelope = harness.envelope()
    assert envelope["error"] == "ConfigurationError"
    assert envelope["trace_id"] is None
    assert harness.tracer.flushes == 0


def test_tracer_initialisation_failure_exits_five_with_a_null_trace_id() -> None:
    harness = Harness(compose_error=ObservabilityError("tracer initialisation failed: ValueError"))

    assert harness.run() == 5

    envelope = harness.envelope()
    assert envelope["error"] == "ObservabilityError"
    assert envelope["trace_id"] is None
    assert harness.stdout.getvalue() == ""


def test_session_id_and_trace_attributes_reach_the_root() -> None:
    harness = Harness()

    harness.run([*ARGV, "--session-id", "lote-7"])

    [(_, attributes)] = harness.tracer.roots
    assert attributes.session_id == "lote-7"
    assert attributes.version == APP_VERSION
    assert attributes.environment == "development"


def test_without_session_id_the_attribute_is_none() -> None:
    harness = Harness()

    harness.run()

    [(_, attributes)] = harness.tracer.roots
    assert attributes.session_id is None


def test_root_span_records_file_names_run_id_and_the_public_output() -> None:
    harness = Harness()

    harness.run()

    [(root, _)] = harness.tracer.roots
    final = harness.envelope()
    assert root.input == {"original": "original.png", "amendment": "adenda.png"}
    assert root.metadata["run_id"] == final["run_id"]
    assert root.metadata["amendment_kind"] == "restated"
    assert root.metadata["original_image_sha256"] == "a" * 64
    assert root.metadata["extraction_model"] == "gpt-4o"
    assert root.output == json.loads(harness.stdout.getvalue())


def test_root_span_records_the_error_envelope_and_the_error_level() -> None:
    harness = Harness(failures={"extraction_agent": InvalidExtractionError([ISSUE])})

    harness.run()

    [(root, _)] = harness.tracer.roots
    assert root.output == harness.envelope()
    assert isinstance(root.error, InvalidExtractionError)


def test_trace_id_is_null_when_the_tracer_has_none() -> None:
    harness = Harness()
    harness.tracer.current_trace_id = None

    harness.run()

    assert harness.envelope()["trace_id"] is None


def test_a_new_run_id_is_generated_for_every_run() -> None:
    first, second = Harness(), Harness()

    first.run()
    second.run()

    assert first.envelope()["run_id"] != second.envelope()["run_id"]


def offline_container(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Container, list[tuple[float, int | None]]]:
    """Real wiring with the tracer and the structured-output seam replaced: no network."""
    limits: list[tuple[float, int | None]] = []
    reply = structured_reply(context_map().model_dump(mode="json"))

    def build_context_models(chat_model: ChatOpenAI) -> StructuredModels:
        limits.append((chat_model.request_timeout, chat_model.max_retries))
        stub = RecordingStructuredModel(reply)
        return StructuredModels(attempt=stub.runnable, retry=stub.runnable)

    monkeypatch.setattr(main, "build_langfuse_tracer", lambda **_: RecordingLifecycleTracer())
    monkeypatch.setattr(main, "build_structured_context_models", build_context_models)
    return main.build_container(fake_settings()), limits


def test_build_container_wires_the_tracer_budget_and_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container, _ = offline_container(monkeypatch)

    assert isinstance(container.tracer, RecordingLifecycleTracer)
    assert container.environment == "development"
    assert container.root_metadata["extraction_prompt_version"] == EXTRACTION_PROMPT_VERSION
    assert container.budget.ensure_remaining(stage="parse") <= 300


def test_build_container_parse_port_validates_before_any_model_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    container, _ = offline_container(monkeypatch)

    with pytest.raises(InvalidImageError):
        container.stages.parse_contract_image(tmp_path / "missing.png", DocumentRole.ORIGINAL)


def test_build_container_agents_get_timeout_and_retries_planned_by_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container, limits = offline_container(monkeypatch)

    mapped = container.stages.contextualize(
        parsed(DocumentRole.ORIGINAL, Path("o.png")), parsed(DocumentRole.AMENDMENT, Path("a.png"))
    )

    assert mapped.result == context_map()
    assert mapped.retries == 0
    assert limits == [(60, 2)]


def test_unexpected_errors_log_their_stack_without_their_message() -> None:
    harness = Harness(failures={"extraction_agent": KeyError("Cláusula secreta")})

    harness.run()

    logged = harness.stderr.getvalue()
    assert 'File "' in logged
    assert "pipeline_doubles.py" in logged
    assert "secreta" not in logged
