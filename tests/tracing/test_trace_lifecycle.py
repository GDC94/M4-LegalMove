"""Trace lifecycle owned by main.py: root span, propagated attributes and flush (§5.3, §5.7)."""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from langfuse import Langfuse
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import observability
from errors import ObservabilityError, UpstreamError
from observability import (
    LANGFUSE_MEDIA_UPLOAD_ENABLED,
    LangfuseTracer,
    TraceAttributes,
    build_langfuse_tracer,
    connect_langfuse,
    mask_personal_data,
)

ATTRIBUTES = TraceAttributes(session_id="lote-7", version="0.1.0", environment="development")


class FakeObservation:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def update(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)


class FakeClient:
    def __init__(self, flush_error: Exception | None = None) -> None:
        self.flush_error = flush_error
        self.flushes = 0
        self.observations: list[tuple[dict[str, Any], FakeObservation]] = []
        self.scores: list[dict[str, Any]] = []

    @contextmanager
    def start_as_current_observation(self, **kwargs: Any) -> Iterator[FakeObservation]:
        observation = FakeObservation()
        self.observations.append((kwargs, observation))
        yield observation

    def score_current_trace(self, **kwargs: Any) -> None:
        self.scores.append(kwargs)

    def flush(self) -> None:
        self.flushes += 1
        if self.flush_error:
            raise self.flush_error

    def auth_check(self) -> bool:
        return True


def fake_tracer(client: FakeClient) -> LangfuseTracer:
    # The fake mirrors only the client methods the adapter calls, not the whole Langfuse type.
    return LangfuseTracer(client, callback_handler=object())  # type: ignore[arg-type]


def test_root_span_is_contract_analysis_with_input_and_metadata() -> None:
    client = FakeClient()

    with fake_tracer(client).open_root({"original": "a.png"}, {"run_id": "r1"}, ATTRIBUTES):
        pass

    [(kwargs, _)] = client.observations
    assert kwargs == {
        "name": "contract-analysis",
        "as_type": "span",
        "input": {"original": "a.png"},
        "metadata": {"run_id": "r1"},
    }


def test_root_span_is_marked_as_error_when_the_run_fails() -> None:
    client = FakeClient()

    with (
        pytest.raises(UpstreamError),
        fake_tracer(client).open_root({}, {}, ATTRIBUTES),
    ):
        raise UpstreamError("vision call failed")

    [(_, observation)] = client.observations
    assert observation.updates == [
        {"level": "ERROR", "status_message": "UpstreamError: vision call failed"}
    ]


def test_flush_delegates_to_the_client() -> None:
    client = FakeClient()

    fake_tracer(client).flush()

    assert client.flushes == 1


def test_a_failing_flush_becomes_observability_error_without_foreign_text() -> None:
    client = FakeClient(flush_error=RuntimeError("Cláusula secreta"))

    with pytest.raises(ObservabilityError) as caught:
        fake_tracer(client).flush()

    assert "Cláusula" not in str(caught.value)
    assert caught.value.exit_code == 5


def test_categorical_scores_are_sent_as_categorical() -> None:
    client = FakeClient()

    fake_tracer(client).score("outcome", "ok")

    assert client.scores == [{"name": "outcome", "value": "ok", "data_type": "CATEGORICAL"}]


def test_a_client_that_cannot_start_becomes_observability_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_client(**_: Any) -> None:
        raise ValueError("bad base url sk-lf-secret")

    monkeypatch.setattr(observability, "Langfuse", broken_client)

    with pytest.raises(ObservabilityError) as caught:
        build_langfuse_tracer(
            public_key="pk-lf", secret_key="sk-lf", host="https://x.test", environment="development"
        )

    assert "secret" not in str(caught.value)


def in_memory_tracer(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[LangfuseTracer, Langfuse, InMemorySpanExporter]:
    """Real SDK with spans kept in memory and a dead host, so nothing reaches a server."""
    exporter = InMemorySpanExporter()
    clients: list[Langfuse] = []

    def in_memory_client(**kwargs: Any) -> Langfuse:
        client = Langfuse(**kwargs, span_exporter=exporter, flush_at=1)
        # The host is dead on purpose; the real auth check would need the network.
        client.auth_check = lambda: True  # type: ignore[method-assign]
        clients.append(client)
        return client

    monkeypatch.setattr(observability, "Langfuse", in_memory_client)
    tracer = build_langfuse_tracer(
        public_key=f"pk-lf-{uuid.uuid4()}",
        secret_key="sk-lf",
        host="http://127.0.0.1:9",
        environment="development",
    )
    return tracer, clients[0], exporter


def test_real_sdk_nests_stages_under_the_root_with_propagated_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracer, client, exporter = in_memory_tracer(monkeypatch)
    try:
        with tracer.open_root({}, {}, ATTRIBUTES):
            trace_id = tracer.trace_id
            with tracer.stage("parse_original_contract", {}):
                pass
        tracer.flush()
    finally:
        client.shutdown()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    root, child = spans["contract-analysis"], spans["parse_original_contract"]
    assert trace_id is not None
    assert child.parent is not None and root.context is not None
    assert child.parent.span_id == root.context.span_id
    recorded = str(dict(child.attributes or {}))
    assert "lote-7" in recorded
    assert "0.1.0" in recorded
    assert "legalmove" in recorded
    assert "contract-analysis" in recorded


class AuthCheckingClient(FakeClient):
    def __init__(self, auth: bool | Exception, **_: Any) -> None:
        super().__init__()
        self.auth = auth
        self.auth_checks = 0

    def auth_check(self) -> bool:
        self.auth_checks += 1
        if isinstance(self.auth, Exception):
            raise self.auth
        return self.auth


def build_with_auth(monkeypatch: pytest.MonkeyPatch, auth: bool | Exception) -> AuthCheckingClient:
    clients: list[AuthCheckingClient] = []

    def client(**kwargs: Any) -> AuthCheckingClient:
        clients.append(AuthCheckingClient(auth, **kwargs))
        return clients[-1]

    monkeypatch.setattr(observability, "Langfuse", client)
    monkeypatch.setattr(observability, "CallbackHandler", lambda **_: object())
    build_langfuse_tracer(
        public_key="pk-lf-key",
        secret_key="sk-lf-key",
        host="https://x.test",
        environment="development",
    )
    return clients[0]


def test_tracer_initialisation_confirms_langfuse_is_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert build_with_auth(monkeypatch, True).auth_checks == 1


def test_rejected_credentials_fail_initialisation(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ObservabilityError) as caught:
        build_with_auth(monkeypatch, False)

    assert str(caught.value) == "tracer initialisation failed: auth check rejected"


def test_an_unreachable_host_fails_initialisation_without_echoing_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = ConnectionError("https://x.test sk-lf-key body: secret")

    with pytest.raises(ObservabilityError) as caught:
        build_with_auth(monkeypatch, error)

    assert str(caught.value) == "tracer initialisation failed: ConnectionError"


def test_connect_langfuse_is_the_one_place_mask_and_media_are_wired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The eval publisher shares this client set-up, so the SDK's per-key settings always match."""
    received: dict[str, Any] = {}

    def client(**kwargs: Any) -> AuthCheckingClient:
        received.update(kwargs)
        return AuthCheckingClient(True)

    monkeypatch.setattr(observability, "Langfuse", client)
    monkeypatch.setenv(LANGFUSE_MEDIA_UPLOAD_ENABLED, "true")

    connected = connect_langfuse(
        public_key="pk-lf-key", secret_key="sk-lf-key", host="https://x.test", environment="staging"
    )

    assert received["mask"] is mask_personal_data
    assert received["environment"] == "staging"
    assert received["base_url"] == "https://x.test"
    assert os.environ[LANGFUSE_MEDIA_UPLOAD_ENABLED] == "false"
    assert isinstance(connected, AuthCheckingClient) and connected.auth_checks == 1
