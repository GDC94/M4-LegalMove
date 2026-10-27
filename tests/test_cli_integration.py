"""Real end-to-end CLI runs: run only with `uv run pytest -m integration -k cli` on request."""

import io
import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langfuse import Langfuse, get_client
from langfuse.api.core import ApiError
from pydantic import ValidationError

from config import APP_VERSION, Settings, load_settings
from main import CliStreams, Composition, build_container, run_cli
from models import ContractChangeOutput
from observability import TraceAttributes, build_langfuse_tracer

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "data/test_contracts"
REQUIRED_KEYS = {"openai_api_key", "langfuse_public_key", "langfuse_secret_key"}
TRACE_POLL_SECONDS = 90
CLI_TIMEOUT_SECONDS = 400
SPAN_NAMES = {
    "contract-analysis",
    "parse_original_contract",
    "parse_amendment_contract",
    "contextualization_agent",
    "extraction_agent",
    "output_validation",
}
SCORE_NAMES = {
    "outcome",
    "schema_valid",
    "consistency",
    "grounding_rate",
    "context_retries",
    "extraction_retries",
}
# Synthetic DNI that must never reach Langfuse through the CallbackHandler.
FAKE_DNI = "DNI 12.345.678"
HTTP_NOT_FOUND = 404


@pytest.fixture
def settings() -> Settings:
    """Keys come from the shell or .env; only missing keys skip, any other problem fails."""
    try:
        return Settings(_env_file=ROOT / ".env")
    except ValidationError as error:
        problems = {
            (item["type"], item["loc"][0] if item["loc"] else None) for item in error.errors()
        }
        if problems and problems <= {("missing", key) for key in REQUIRED_KEYS}:
            pytest.skip("OpenAI and Langfuse keys are required")
        raise


def run_cli_process(pair: str, session_id: str) -> subprocess.CompletedProcess[str]:
    """The public command itself; the parse cache is off so every vision call is traced."""
    return subprocess.run(
        [
            sys.executable,
            "src/main.py",
            str(CONTRACTS / pair / "original.png"),
            str(CONTRACTS / pair / "amendment.png"),
            "--session-id",
            session_id,
        ],
        cwd=ROOT,
        env={**os.environ, "PARSE_CACHE_ENABLED": "false"},
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT_SECONDS,
        check=False,
    )


@pytest.fixture
def langfuse_client(settings: Settings) -> Langfuse:
    """get_client only finds a client this process built; without one every read answers 401."""
    build_langfuse_tracer(
        public_key=settings.langfuse_public_key.get_secret_value(),
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        host=settings.langfuse_host,
        environment=settings.app_env,
    )
    return get_client(public_key=settings.langfuse_public_key.get_secret_value())


def poll[T](fetch: Callable[[], T | None], what: str) -> T:
    """Ingestion is eventually consistent: only empty results or 404 mean "not there yet"."""
    deadline = time.monotonic() + TRACE_POLL_SECONDS
    while time.monotonic() < deadline:
        try:
            found = fetch()
        except ApiError as error:
            if error.status_code != HTTP_NOT_FOUND:
                # Status only: bodies may echo keys or trace content.
                pytest.fail(f"{what}: Langfuse API answered {error.status_code}")
            found = None
        if found is not None:
            return found
        time.sleep(2)
    pytest.fail(f"{what} missing after {TRACE_POLL_SECONDS} s")


def fetch_observations(
    client: Langfuse, trace_id: str, ready: Callable[[list[Any]], bool]
) -> list[Any]:
    def fetch() -> list[Any] | None:
        response = client.api.observations.get_many(
            trace_id=trace_id, fields="core,basic,io,metadata", limit=100
        )
        return response.data if ready(response.data) else None

    return poll(fetch, f"observations of trace {trace_id}")


def fetch_score_names(client: Langfuse, trace_id: str) -> set[str]:
    def fetch() -> set[str] | None:
        names = {
            score.name
            for score in client.api.scores_v3.get_many_v3(trace_id=trace_id, limit=100).data
        }
        return names if names >= SCORE_NAMES else None

    return poll(fetch, f"scores of trace {trace_id}")


def assert_public_run(pair: str, client: Langfuse) -> ContractChangeOutput:
    session_id = f"cli-integration-{uuid.uuid4()}"
    completed = run_cli_process(pair, session_id)

    assert completed.returncode == 0, completed.stderr[-2000:]
    output = ContractChangeOutput.model_validate(json.loads(completed.stdout))
    final = json.loads(completed.stderr.strip().splitlines()[-1])
    assert set(final) == {"run_id", "trace_id"}
    assert final["run_id"]
    assert final["trace_id"]

    observations = fetch_observations(
        client, final["trace_id"], lambda data: {o.name for o in data} >= SPAN_NAMES
    )
    generations = [o for o in observations if o.type == "GENERATION"]
    assert len(generations) >= 4  # two vision calls and one per agent attempt
    root = next(o for o in observations if o.name == "contract-analysis")
    assert root.session_id == session_id
    assert root.version == APP_VERSION
    assert fetch_score_names(client, final["trace_id"]) >= SCORE_NAMES
    return output


def test_cli_restated_pair_a_end_to_end(langfuse_client: Langfuse) -> None:
    output = assert_public_run("pair_a", langfuse_client)

    assert output.sections_changed == ["3", "new:8"]
    assert output.topics_touched == ["price_payment", "confidentiality"]


def test_cli_delta_pair_b_end_to_end(langfuse_client: Langfuse) -> None:
    output = assert_public_run("pair_b", langfuse_client)

    assert output.sections_changed == ["2", "5"]
    assert output.topics_touched == ["term_duration", "penalties"]


def test_callback_handler_masks_a_fake_dni_before_it_reaches_langfuse(
    settings: Settings,
) -> None:
    tracer = build_langfuse_tracer(
        public_key=settings.langfuse_public_key.get_secret_value(),
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        host=settings.langfuse_host,
        environment=settings.app_env,
    )
    model = FakeListChatModel(responses=[f"Recibido el {FAKE_DNI}."])
    attributes = TraceAttributes(session_id=None, version=APP_VERSION, environment=settings.app_env)
    with tracer.open_root({}, {}, attributes):
        trace_id = tracer.trace_id
        model.invoke(f"El firmante es {FAKE_DNI}.", config={"callbacks": list(tracer.callbacks)})
    tracer.flush()
    assert trace_id

    client = get_client(public_key=settings.langfuse_public_key.get_secret_value())
    observations = fetch_observations(
        client, trace_id, lambda data: any(o.type == "GENERATION" for o in data)
    )
    recorded = str([(o.input, o.output, o.metadata) for o in observations])
    assert "12.345.678" not in recorded
    assert "[REDACTED_DNI]" in recorded


def test_a_failing_trace_flush_leaves_stdout_empty_and_exits_five(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_flush(self: Langfuse) -> None:
        raise RuntimeError("controlled flush failure")

    monkeypatch.setattr(Langfuse, "flush", failing_flush)
    stdout, stderr = io.StringIO(), io.StringIO()
    composition = Composition(
        load_settings=lambda: load_settings(ROOT / ".env"), compose=build_container
    )
    argv = [str(CONTRACTS / "pair_a/original.png"), str(CONTRACTS / "pair_a/amendment.png")]

    exit_code = run_cli(argv, CliStreams(stdout, stderr), composition)

    assert exit_code == 5
    assert stdout.getvalue() == ""
    envelope = json.loads(stderr.getvalue().strip().splitlines()[-1])
    assert envelope["error"] == "ObservabilityError"
    assert envelope["run_id"]
