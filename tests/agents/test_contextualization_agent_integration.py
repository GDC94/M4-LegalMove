"""Real structured output and Langfuse: run only with `uv run pytest -m integration -k context`."""

import json
import time
from pathlib import Path
from typing import Any

import pytest
from langchain_openai import ChatOpenAI
from langfuse import get_client
from langfuse.api.core import ApiError
from pydantic import ValidationError

from agents.contextualization_agent import (
    CONTEXTUALIZATION_PROMPT_VERSION,
    ContextualizationInput,
    build_contextualization_agent,
    build_structured_context_models,
)
from config import Settings
from models import AmendmentKind, ContextMap, DocumentRole, ParsedContract
from observability import build_langfuse_tracer

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent.parent
CONTRACTS = ROOT / "data/test_contracts"
REQUIRED_KEYS = {"openai_api_key", "langfuse_public_key", "langfuse_secret_key"}
TRACE_POLL_SECONDS = 90
SPAN_METADATA = {
    "amendment_kind",
    "kind_heuristic",
    "context_retries",
    "contextualization_prompt_version",
}


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


def source_input(pair: str) -> ContextualizationInput:
    """The source texts stand in for Parsed Contracts, isolating the agent from vision."""

    def parsed(role: DocumentRole) -> ParsedContract:
        path = CONTRACTS / pair / f"source_{role.value}.txt"
        text = path.read_text(encoding="utf-8")
        return ParsedContract(role=role, source_path=str(path), image_sha256="0" * 64, text=text)

    return ContextualizationInput(
        original=parsed(DocumentRole.ORIGINAL), amendment=parsed(DocumentRole.AMENDMENT)
    )


def contextualize_in_own_trace(settings: Settings, pair: str) -> tuple[str, ContextMap]:
    tracer = build_langfuse_tracer(
        public_key=settings.langfuse_public_key.get_secret_value(),
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        host=settings.langfuse_host,
        environment=settings.app_env,
    )
    chat_model = ChatOpenAI(
        model_name=settings.contextualization_model,
        temperature=settings.llm_temperature,
        request_timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        openai_api_key=settings.openai_api_key,
    )
    agent = build_contextualization_agent(build_structured_context_models(chat_model), tracer)
    client = get_client(public_key=settings.langfuse_public_key.get_secret_value())
    with client.start_as_current_observation(name="context-integration-test", as_type="span"):
        context_map = agent.invoke(source_input(pair))
        trace_id = client.get_current_trace_id()
    assert trace_id
    return trace_id, context_map


def fetch_agent_span_metadata(settings: Settings, trace_id: str) -> dict[str, Any]:
    """Poll the v2 observations API; the v1 trace endpoint answers 410 on Langfuse v4 Cloud."""
    client = get_client(public_key=settings.langfuse_public_key.get_secret_value())
    client.flush()
    deadline = time.monotonic() + TRACE_POLL_SECONDS
    while time.monotonic() < deadline:
        try:
            response = client.api.observations.get_many(
                trace_id=trace_id, fields="core,basic,metadata", limit=100
            )
            spans = [o for o in response.data if o.name == "contextualization_agent"]
            if spans:
                raw = spans[0].metadata
                return dict(json.loads(raw) if isinstance(raw, str) else raw)
        except ApiError:  # ingestion is eventually consistent: empty or 404 at first
            pass
        time.sleep(2)
    pytest.fail(f"contextualization_agent span missing from trace {trace_id}")


def test_restated_pair_a_maps_seven_sections_and_one_new(settings: Settings) -> None:
    trace_id, context_map = contextualize_in_own_trace(settings, "pair_a")

    assert context_map.amendment_kind is AmendmentKind.RESTATED
    assert [s.section_id for s in context_map.sections] == [
        *(str(number) for number in range(1, 8)),
        "new:8",
    ]
    metadata = fetch_agent_span_metadata(settings, trace_id)
    assert set(metadata) >= SPAN_METADATA
    assert metadata["amendment_kind"] == "restated"
    assert str(metadata["contextualization_prompt_version"]) == CONTEXTUALIZATION_PROMPT_VERSION


def test_delta_pair_b_resolves_its_instructions_to_original_sections(settings: Settings) -> None:
    trace_id, context_map = contextualize_in_own_trace(settings, "pair_b")

    assert context_map.amendment_kind is AmendmentKind.DELTA
    instructed = {s.section_id for s in context_map.sections if s.amendment_ref is not None}
    assert {"2", "5"} <= instructed
    assert not any(s.section_id.startswith("new:") for s in context_map.sections)
    metadata = fetch_agent_span_metadata(settings, trace_id)
    assert set(metadata) >= SPAN_METADATA
    assert metadata["kind_heuristic"] == "delta"
