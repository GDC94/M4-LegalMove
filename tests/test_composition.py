"""Shared composition root: wiring, error envelope and the split Langfuse connection (plan §1)."""

import dataclasses
from pathlib import Path
from typing import Any

import pytest
from langchain_openai import ChatOpenAI

import composition
from agent_doubles import RecordingStructuredModel, structured_reply
from agents.extraction_agent import EXTRACTION_PROMPT_VERSION
from agents.structured_output import StructuredModels
from cli_doubles import RecordingLifecycleTracer, fake_settings
from composition import Container, RunContext, build_envelope
from errors import (
    InvalidExtractionError,
    InvalidImageError,
    UpstreamError,
    ValidationIssue,
)
from models import DocumentRole
from observability import RecordingTracer
from pipeline_doubles import context_map, parsed

ISSUE = ValidationIssue(code="ungrounded_quote", path="changes[0].original_quote", message="m")


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

    monkeypatch.setattr(composition, "connect_langfuse", lambda _: object())
    monkeypatch.setattr(composition, "build_tracer", lambda *_: RecordingLifecycleTracer())
    monkeypatch.setattr(composition, "build_structured_context_models", build_context_models)
    return composition.build_container(fake_settings()), limits


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


def test_container_is_a_frozen_dataclass() -> None:
    assert dataclasses.is_dataclass(Container)
    # dataclasses exposes the frozen flag only through this undeclared attribute.
    assert Container.__dataclass_params__.frozen  # type: ignore[attr-defined]


def test_build_stages_traces_through_the_tracer_and_budget_it_receives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    offline_container(monkeypatch)
    tracer = RecordingTracer()
    budget = composition.CallBudget(300)

    stages = composition.build_stages(fake_settings(), tracer, budget)
    stages.contextualize(
        parsed(DocumentRole.ORIGINAL, Path("o.png")), parsed(DocumentRole.AMENDMENT, Path("a.png"))
    )

    assert [stage.name for stage in tracer.stages] == ["contextualization_agent"]


def test_assemble_container_gives_every_call_its_own_tracer_and_a_fresh_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    offline_container(monkeypatch)
    first_tracer, second_tracer = RecordingLifecycleTracer(), RecordingLifecycleTracer()

    first = composition.assemble_container(fake_settings(), first_tracer)
    second = composition.assemble_container(fake_settings(), second_tracer)

    assert (first.tracer, second.tracer) == (first_tracer, second_tracer)
    assert first.budget is not second.budget


def test_envelope_of_a_domain_error_carries_its_stage_and_message() -> None:
    context = RunContext(run_id="r" * 32, trace_id="trace-1")

    envelope = build_envelope(UpstreamError("vision call failed", stage="extraction"), context)

    assert envelope == {
        "error": "UpstreamError",
        "stage": "extraction",
        "message": "vision call failed",
        "run_id": "r" * 32,
        "trace_id": "trace-1",
    }


def test_envelope_of_a_human_review_error_adds_the_issues() -> None:
    envelope = build_envelope(InvalidExtractionError([ISSUE]), RunContext(run_id="r1"))

    assert envelope["error"] == "InvalidExtractionError"
    assert envelope["trace_id"] is None
    assert envelope["issues"] == [
        {"code": "ungrounded_quote", "path": "changes[0].original_quote", "message": "m"}
    ]


def test_envelope_of_a_generic_exception_is_sanitised() -> None:
    envelope = build_envelope(KeyError("Cláusula secreta"), RunContext(run_id="r1"))

    assert envelope == {
        "error": "UnexpectedError",
        "stage": "unknown",
        "message": "unexpected failure; see the logs",
        "run_id": "r1",
        "trace_id": None,
    }


def test_connect_langfuse_builds_the_client_from_the_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, Any] = {}
    client = object()

    def open_client(**kwargs: Any) -> object:
        received.update(kwargs)
        return client

    monkeypatch.setattr(composition, "open_client", open_client)

    assert composition.connect_langfuse(fake_settings()) is client
    assert received == {
        "public_key": "pk-lf-test",
        "secret_key": "sk-lf-test",
        "host": "https://cloud.langfuse.com",
        "environment": "development",
    }


def test_build_tracer_wraps_an_already_connected_client(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[tuple[object, str]] = []
    tracer = RecordingLifecycleTracer()
    client = object()

    def create_tracer(connected: object, *, public_key: str) -> RecordingLifecycleTracer:
        received.append((connected, public_key))
        return tracer

    monkeypatch.setattr(composition, "create_langfuse_tracer", create_tracer)

    assert composition.build_tracer(client, fake_settings()) is tracer  # type: ignore[arg-type]
    assert received == [(client, "pk-lf-test")]
