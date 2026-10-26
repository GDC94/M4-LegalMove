"""PipelineTracer port: the recording double and the Langfuse adapter (plan §5.2, §5.4)."""

import base64
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from langfuse import Langfuse
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import observability
from errors import UpstreamError
from image_parser import ImageLimits, VisionConfig, build_contract_image_parser
from models import DocumentRole
from observability import LangfuseTracer, RecordingTracer, build_langfuse_tracer, mask_personal_data
from parser_doubles import RecordingChatModel, png_bytes


def test_recording_tracer_records_a_stage_with_input_output_and_metadata() -> None:
    tracer = RecordingTracer()

    with tracer.stage("parse_original_contract", {"image_sha256": "abc"}, {"a": 1}) as span:
        span.set_metadata({"cache_hit": True})
        span.set_output("texto")

    [stage] = tracer.stages
    assert stage.name == "parse_original_contract"
    assert stage.input == {"image_sha256": "abc"}
    assert stage.metadata == {"a": 1, "cache_hit": True}
    assert stage.output == "texto"
    assert stage.error is None


def test_recording_tracer_marks_the_stage_failed_and_reraises() -> None:
    tracer = RecordingTracer()

    with pytest.raises(UpstreamError), tracer.stage("parse_original_contract", {}):
        raise UpstreamError("vision call failed")

    assert isinstance(tracer.stages[0].error, UpstreamError)


def test_recording_tracer_records_scores_and_has_no_trace_id() -> None:
    tracer = RecordingTracer()

    tracer.score("consistency", 1.0)

    assert tracer.scores == [("consistency", 1.0)]
    assert tracer.trace_id is None
    assert list(tracer.callbacks) == []


class FakeObservation:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def update(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)


class FakeLangfuseClient:
    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.observations: list[tuple[dict[str, Any], FakeObservation]] = []
        self.scores: list[dict[str, Any]] = []

    @contextmanager
    def start_as_current_observation(self, **kwargs: Any) -> Iterator[FakeObservation]:
        observation = FakeObservation()
        self.observations.append((kwargs, observation))
        yield observation

    def score_current_trace(self, **kwargs: Any) -> None:
        self.scores.append(kwargs)

    def get_current_trace_id(self) -> str:
        return "trace-123"


def make_langfuse_tracer() -> tuple[LangfuseTracer, FakeLangfuseClient]:
    client = FakeLangfuseClient()
    return LangfuseTracer(client, callback_handler=object()), client


def test_langfuse_tracer_opens_a_span_with_input_and_metadata() -> None:
    tracer, client = make_langfuse_tracer()

    with tracer.stage("parse_original_contract", {"image_sha256": "abc"}, {"v": "1"}) as span:
        span.set_output("texto")
        span.set_metadata({"cache_hit": False})

    [(kwargs, observation)] = client.observations
    assert kwargs == {
        "name": "parse_original_contract",
        "as_type": "span",
        "input": {"image_sha256": "abc"},
        "metadata": {"v": "1"},
    }
    assert observation.updates == [{"output": "texto"}, {"metadata": {"cache_hit": False}}]


def test_langfuse_tracer_marks_failed_spans_as_error_without_foreign_messages() -> None:
    tracer, client = make_langfuse_tracer()

    with pytest.raises(KeyError), tracer.stage("parse_original_contract", {}):
        raise KeyError("Cláusula secreta de la página")

    [(_, observation)] = client.observations
    assert observation.updates == [{"level": "ERROR", "status_message": "KeyError"}]


def test_langfuse_tracer_keeps_sanitised_domain_error_messages() -> None:
    tracer, client = make_langfuse_tracer()

    with pytest.raises(UpstreamError), tracer.stage("parse_original_contract", {}):
        raise UpstreamError("vision call timed out")

    [(_, observation)] = client.observations
    assert observation.updates == [
        {"level": "ERROR", "status_message": "UpstreamError: vision call timed out"}
    ]


def test_langfuse_tracer_exposes_scores_trace_id_and_callbacks() -> None:
    handler = object()
    client = FakeLangfuseClient()
    tracer = LangfuseTracer(client, callback_handler=handler)

    tracer.score("consistency", 1.0)

    assert client.scores == [{"name": "consistency", "value": 1.0}]
    assert tracer.trace_id == "trace-123"
    assert list(tracer.callbacks) == [handler]


def test_build_langfuse_tracer_registers_the_mask_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[FakeLangfuseClient] = []
    handlers: list[dict[str, Any]] = []

    def fake_client(**kwargs: Any) -> FakeLangfuseClient:
        created.append(FakeLangfuseClient(**kwargs))
        return created[-1]

    def fake_handler(**kwargs: Any) -> object:
        handlers.append(kwargs)
        return object()

    monkeypatch.setattr(observability, "Langfuse", fake_client)
    monkeypatch.setattr(observability, "CallbackHandler", fake_handler)

    build_langfuse_tracer(
        public_key="pk-lf",
        secret_key="sk-lf",
        host="https://example.test",
        environment="development",
    )

    [client] = created
    assert client.init_kwargs["mask"] is mask_personal_data
    assert client.init_kwargs["public_key"] == "pk-lf"
    assert client.init_kwargs["secret_key"] == "sk-lf"
    assert client.init_kwargs["base_url"] == "https://example.test"
    assert client.init_kwargs["environment"] == "development"
    assert handlers == [{"public_key": "pk-lf"}]


def test_parse_span_input_describes_the_image_without_its_bytes(tmp_path: Path) -> None:
    content = png_bytes(40, 30)
    path = tmp_path / "original.png"
    path.write_bytes(content)
    tracer = RecordingTracer()
    limits = ImageLimits(max_bytes=1_000_000, min_side=10, max_side=200, max_pixels=40_000)
    config = VisionConfig(model="gpt-4o", detail="high", illegible_max_marks=10, limits=limits)
    parser = build_contract_image_parser(RecordingChatModel().runnable, tracer, config, None)

    parser.parse_contract_image(path, DocumentRole.ORIGINAL)

    [stage] = tracer.stages
    recorded = repr((stage.input, stage.metadata, stage.output))
    assert base64.b64encode(content).decode()[:40] not in recorded
    assert "data:image" not in recorded
    assert set(stage.input) == {"image_sha256", "bytes", "width", "height"}


MEDIA_SWITCH = "LANGFUSE_MEDIA_UPLOAD_ENABLED"


def test_build_langfuse_tracer_turns_media_upload_off_before_the_client_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MEDIA_SWITCH, "true")
    seen: list[str | None] = []

    def fake_client(**kwargs: Any) -> FakeLangfuseClient:
        seen.append(os.environ.get(MEDIA_SWITCH))
        return FakeLangfuseClient(**kwargs)

    monkeypatch.setattr(observability, "Langfuse", fake_client)
    monkeypatch.setattr(observability, "CallbackHandler", lambda **_: object())

    build_langfuse_tracer(
        public_key="pk-lf",
        secret_key="sk-lf",
        host="https://example.test",
        environment="development",
    )

    assert seen == ["false"]


def build_in_memory_tracer(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[LangfuseTracer, Langfuse, InMemorySpanExporter]:
    """Real SDK with spans kept in memory and a dead host, so nothing reaches a server."""
    exporter = InMemorySpanExporter()
    clients: list[Langfuse] = []

    def in_memory_client(**kwargs: Any) -> Langfuse:
        clients.append(Langfuse(**kwargs, span_exporter=exporter, flush_at=1))
        return clients[-1]

    monkeypatch.setattr(observability, "Langfuse", in_memory_client)
    tracer = build_langfuse_tracer(
        public_key=f"pk-lf-{uuid.uuid4()}",
        secret_key="sk-lf",
        host="http://127.0.0.1:9",
        environment="development",
    )
    [client] = clients
    return tracer, client, exporter


def test_inline_image_never_leaves_the_real_sdk_as_media_or_base64(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runs the installed SDK's media + mask path."""
    monkeypatch.setenv(MEDIA_SWITCH, "true")
    _, client, exporter = build_in_memory_tracer(monkeypatch)
    encoded = base64.b64encode(png_bytes(64, 48)).decode()
    try:
        with client.start_as_current_observation(
            name="vision",
            as_type="generation",
            input={"image_url": {"url": f"data:image/png;base64,{encoded}"}},
        ):
            pass
        client.flush()
    finally:
        client.shutdown()

    [span] = exporter.get_finished_spans()
    recorded = str(span.attributes)
    assert encoded[:40] not in recorded
    assert "base64," not in recorded
    assert "langfuseMedia" not in recorded
    assert "64x48" in recorded


def test_chain_input_with_an_image_never_leaves_the_real_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Runnable wrapping the model records its own chain observation with message objects."""
    tracer, client, exporter = build_in_memory_tracer(monkeypatch)
    encoded = base64.b64encode(png_bytes(64, 48)).decode()
    image = {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}
    messages = [SystemMessage("system"), HumanMessage(content=[image])]
    model = FakeListChatModel(responses=["ok"])
    chain = RunnableLambda(lambda value, config: model.invoke(value, config=config))
    try:
        chain.invoke(messages, config={"callbacks": list(tracer.callbacks)})
        client.flush()
    finally:
        client.shutdown()

    spans = exporter.get_finished_spans()
    recorded = str([span.attributes for span in spans])
    assert len(spans) == 2  # the chain and the generation; silence would pass the negatives
    assert recorded.count("64x48") == 2
    assert encoded[:40] not in recorded
    assert "base64," not in recorded
    assert "langfuseMedia" not in recorded
