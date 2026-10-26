"""Real GPT-4o vision and Langfuse: run only with `uv run pytest -m integration -k parse`."""

import json
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langfuse import get_client
from langfuse.api.core import ApiError
from pydantic import ValidationError

from config import Settings
from image_parser import (
    ImageLimits,
    ParseCache,
    VisionConfig,
    build_contract_image_parser,
    build_vision_chat_model,
)
from models import DocumentRole, ParsedContract
from observability import build_langfuse_tracer

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent
PAIR_A_ORIGINAL = ROOT / "data/test_contracts/pair_a/original.png"
CLAUSES = ["Primera", "Segunda", "Tercera", "Cuarta", "Quinta", "Sexta", "Séptima"]
# Synthetic identifiers printed in the base contract; the mask hook must hide them.
SYNTHETIC_IDENTIFIERS = ["30-71234567-9", "legales@nubesur.com.ar"]
TRACE_POLL_SECONDS = 90


REQUIRED_KEYS = {"openai_api_key", "langfuse_public_key", "langfuse_secret_key"}


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


def normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def vision_config(settings: Settings) -> VisionConfig:
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


def counting_model(settings: Settings, calls: list[int]) -> RunnableLambda[Any, BaseMessage]:
    model = build_vision_chat_model(
        model=settings.vision_model,
        temperature=settings.llm_temperature,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        api_key=settings.openai_api_key,
    )

    def invoke(messages: list[BaseMessage], config: RunnableConfig) -> BaseMessage:
        calls.append(1)
        return model.invoke(messages, config=config)

    return RunnableLambda(invoke)


def parse_in_own_trace(
    settings: Settings, parse: Callable[[], ParsedContract]
) -> tuple[str, ParsedContract]:
    client = get_client(public_key=settings.langfuse_public_key.get_secret_value())
    with client.start_as_current_observation(name="parse-integration-test", as_type="span"):
        contract = parse()
        trace_id = client.get_current_trace_id()
    assert trace_id
    return trace_id, contract


OBSERVATION_FIELDS = "core,basic,io,metadata,usage"


def fetch_observations(settings: Settings, trace_id: str, *, expect_generation: bool) -> list[Any]:
    """Poll the v2 observations API; the v1 trace endpoint answers 410 on Langfuse v4 Cloud."""
    client = get_client(public_key=settings.langfuse_public_key.get_secret_value())
    client.flush()
    deadline = time.monotonic() + TRACE_POLL_SECONDS
    last_seen = "no response yet"
    while True:
        try:
            response = client.api.observations.get_many(
                trace_id=trace_id, fields=OBSERVATION_FIELDS, limit=100
            )
            observations = list(response.data)
            last_seen = f"observations={sorted((o.name or '', o.type) for o in observations)}"
            names = {o.name for o in observations}
            has_generation = any(o.type == "GENERATION" for o in observations)
            if "parse_original_contract" in names and (has_generation or not expect_generation):
                return observations
        except ApiError as error:  # ingestion is eventually consistent: empty or 404 at first
            last_seen = f"ApiError status={error.status_code}"
        if time.monotonic() > deadline:
            pytest.fail(
                f"trace {trace_id} incomplete after {TRACE_POLL_SECONDS} s; last seen: {last_seen}"
            )
        time.sleep(2)


def as_mapping(raw: Any) -> dict[str, Any]:
    """The v2 API returns input and metadata as raw JSON strings."""
    value = json.loads(raw) if isinstance(raw, str) else raw
    assert isinstance(value, dict)
    return value


def parse_span(observations: list[Any]) -> Any:
    [span] = [o for o in observations if o.name == "parse_original_contract"]
    return span


def build_counted_parser(
    settings: Settings, cache_dir: Path, calls: list[int]
) -> Callable[[], ParsedContract]:
    tracer = build_langfuse_tracer(
        public_key=settings.langfuse_public_key.get_secret_value(),
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        host=settings.langfuse_host,
        environment=settings.app_env,
    )
    cache = ParseCache(cache_dir, ttl_hours=settings.parse_cache_ttl_hours)
    parser = build_contract_image_parser(
        counting_model(settings, calls), tracer, vision_config(settings), cache
    )
    return lambda: parser.parse_contract_image(PAIR_A_ORIGINAL, DocumentRole.ORIGINAL)


def assert_traced_without_image_or_identifiers(observations: list[Any]) -> None:
    span = parse_span(observations)
    assert set(as_mapping(span.input)) == {"image_sha256", "bytes", "width", "height"}
    assert str(as_mapping(span.metadata)["cache_hit"]).lower() == "false"
    recorded = "\n".join(o.json() for o in observations)
    assert "base64," not in recorded
    assert "langfuseMedia" not in recorded
    for identifier in SYNTHETIC_IDENTIFIERS:
        assert identifier not in recorded
    generation = next(o for o in observations if o.type == "GENERATION")
    assert generation.usage_details


def test_parse_pair_a_original_with_real_vision_then_hit_the_cache(
    settings: Settings, tmp_path: Path
) -> None:
    calls: list[int] = []
    parse = build_counted_parser(settings, tmp_path / "cache", calls)

    first_trace_id, first = parse_in_own_trace(settings, parse)
    second_trace_id, second = parse_in_own_trace(settings, parse)

    for clause in CLAUSES:
        assert normalise(f"Cláusula {clause}") in normalise(first.text)
    assert first.illegible_count == 0
    assert second == first
    assert calls == [1]
    first_trace = fetch_observations(settings, first_trace_id, expect_generation=True)
    assert_traced_without_image_or_identifiers(first_trace)
    second_trace = fetch_observations(settings, second_trace_id, expect_generation=False)
    assert str(as_mapping(parse_span(second_trace).metadata)["cache_hit"]).lower() == "true"
    assert not any(o.type == "GENERATION" for o in second_trace)
