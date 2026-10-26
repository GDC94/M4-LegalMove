"""Real structured output and Langfuse: run with `uv run pytest -m integration -k extraction`."""

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
from checks import is_grounded
from config import Settings
from models import DocumentRole, ExtractionResult, ParsedContract
from observability import PipelineTracer, build_langfuse_tracer

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent.parent
CONTRACTS = ROOT / "data/test_contracts"
REQUIRED_KEYS = {"openai_api_key", "langfuse_public_key", "langfuse_secret_key"}
TRACE_POLL_SECONDS = 90
SPAN_METADATA = {
    "extraction_prompt_version",
    "extraction_retries",
    "schema_valid",
    "consistency",
    "grounding_rate",
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


def source_contracts(pair: str) -> ContextualizationInput:
    """The source texts stand in for Parsed Contracts, isolating the agents from vision."""

    def parsed(role: DocumentRole) -> ParsedContract:
        path = CONTRACTS / pair / f"source_{role.value}.txt"
        text = path.read_text(encoding="utf-8")
        return ParsedContract(role=role, source_path=str(path), image_sha256="0" * 64, text=text)

    return ContextualizationInput(
        original=parsed(DocumentRole.ORIGINAL), amendment=parsed(DocumentRole.AMENDMENT)
    )


def chat_model(settings: Settings, model_name: str) -> ChatOpenAI:
    return ChatOpenAI(
        model_name=model_name,
        temperature=settings.llm_temperature,
        request_timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        openai_api_key=settings.openai_api_key,
    )


def build_tracer(settings: Settings) -> PipelineTracer:
    return build_langfuse_tracer(
        public_key=settings.langfuse_public_key.get_secret_value(),
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        host=settings.langfuse_host,
        environment=settings.app_env,
    )


def extract_in_own_trace(
    settings: Settings, pair: str
) -> tuple[str, ExtractionResult, ContextualizationInput]:
    """The real agent 1 builds the map, so agent 2 sees exactly what the pipeline will give it."""
    tracer = build_tracer(settings)
    contextualize = build_contextualization_agent(
        build_structured_context_models(chat_model(settings, settings.contextualization_model)),
        tracer,
    )
    extract = build_extraction_agent(
        build_structured_extraction_models(chat_model(settings, settings.extraction_model)),
        tracer,
    )
    contracts = source_contracts(pair)
    client = get_client(public_key=settings.langfuse_public_key.get_secret_value())
    with client.start_as_current_observation(name="extraction-integration-test", as_type="span"):
        context_map = contextualize.invoke(contracts)
        result = extract.invoke(
            ExtractionInput(
                context_map=context_map, original=contracts.original, amendment=contracts.amendment
            )
        )
        trace_id = client.get_current_trace_id()
    assert trace_id
    return trace_id, result, contracts


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
            spans = [o for o in response.data if o.name == "extraction_agent"]
            if spans:
                raw = spans[0].metadata
                return dict(json.loads(raw) if isinstance(raw, str) else raw)
        except ApiError:  # ingestion is eventually consistent: empty or 404 at first
            pass
        time.sleep(2)
    pytest.fail(f"extraction_agent span missing from trace {trace_id}")


def change_facts(result: ExtractionResult) -> set[tuple[str, str, str]]:
    return {(c.section_id, c.change_type.value, c.topic.value) for c in result.changes}


def assert_quotes_grounded(result: ExtractionResult, contracts: ContextualizationInput) -> None:
    for change in result.changes:
        if change.original_quote is not None:
            assert is_grounded(change.original_quote, contracts.original.text)
        if change.amendment_quote is not None:
            assert is_grounded(change.amendment_quote, contracts.amendment.text)


def assert_span_metadata(settings: Settings, trace_id: str) -> None:
    metadata = fetch_agent_span_metadata(settings, trace_id)
    assert set(metadata) >= SPAN_METADATA
    assert str(metadata["extraction_prompt_version"]) == EXTRACTION_PROMPT_VERSION
    assert float(metadata["schema_valid"]) == 1
    assert float(metadata["consistency"]) == 1
    assert float(metadata["grounding_rate"]) == 1.0


def test_restated_pair_a_reports_the_price_change_and_the_new_confidentiality_section(
    settings: Settings,
) -> None:
    trace_id, result, contracts = extract_in_own_trace(settings, "pair_a")

    assert change_facts(result) == {
        ("3", "modified", "price_payment"),
        ("new:8", "added", "confidentiality"),
    }
    assert result.sections_changed == ["3", "new:8"]
    assert_quotes_grounded(result, contracts)
    assert_span_metadata(settings, trace_id)


def test_delta_pair_b_reports_the_term_change_and_the_removed_penalties(
    settings: Settings,
) -> None:
    trace_id, result, contracts = extract_in_own_trace(settings, "pair_b")

    assert change_facts(result) == {
        ("2", "modified", "term_duration"),
        ("5", "removed", "penalties"),
    }
    [removal] = [c for c in result.changes if c.change_type.value == "removed"]
    assert removal.amendment_quote is not None
    assert result.sections_changed == ["2", "5"]
    assert_quotes_grounded(result, contracts)
    assert_span_metadata(settings, trace_id)
