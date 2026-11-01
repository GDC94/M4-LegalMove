"""OpenAPI contract: every body the web client parses is documented (16b events, 16c bodies)."""

from typing import Any

import pytest

from api_contract import AnalysisBody, AnalysisCreated, ApiProblem
from api_doubles import ISSUE, Harness, make_settings, poll, post
from errors import InvalidExtractionError, UpstreamError

EVENTS_PATH = "/analyses/{job_id}/events"
# Any: a parsed OpenAPI document is navigated key by key; `object` would need a cast per level.
type OpenApiDocument = dict[str, Any]


def openapi() -> OpenApiDocument:
    with Harness().client() as client:
        document: OpenApiDocument = client.get("/openapi.json").json()
    return document


def event_stream_schema(document: OpenApiDocument) -> OpenApiDocument:
    response = document["paths"][EVENTS_PATH]["get"]["responses"]["200"]
    assert set(response["content"]) == {"text/event-stream"}
    schema: OpenApiDocument = response["content"]["text/event-stream"]["schema"]
    return schema


def test_the_events_endpoint_documents_a_one_of_discriminated_by_kind() -> None:
    schema = event_stream_schema(openapi())

    assert schema["oneOf"] == [
        {"$ref": "#/components/schemas/StageEvent"},
        {"$ref": "#/components/schemas/JobFinishedEvent"},
    ]
    assert schema["discriminator"] == {
        "propertyName": "kind",
        "mapping": {
            "stage": "#/components/schemas/StageEvent",
            "job": "#/components/schemas/JobFinishedEvent",
        },
    }
    assert "type" not in schema


def test_each_event_shape_lists_its_fields_and_its_kind() -> None:
    schemas = openapi()["components"]["schemas"]
    stage, job = schemas["StageEvent"], schemas["JobFinishedEvent"]

    assert set(stage["required"]) == {"seq", "kind", "stage", "status", "at"}
    assert set(job["required"]) == {"seq", "kind", "status", "at"}
    assert stage["properties"]["kind"]["const"] == "stage"
    assert job["properties"]["kind"]["const"] == "job"
    assert stage["properties"]["at"]["format"] == "date-time"


def test_the_stage_event_documents_cached_as_an_optional_true() -> None:
    cached = openapi()["components"]["schemas"]["StageEvent"]["properties"]["cached"]

    # Exactly a `true` constant: the field is omitted rather than null, so no anyOf with null.
    assert {k: v for k, v in cached.items() if k not in {"title", "description"}} == {
        "const": True,
        "type": "boolean",
    }
    assert "cache" in cached["description"]


def test_the_events_endpoint_documents_last_event_id_and_the_api_key() -> None:
    operation = openapi()["paths"][EVENTS_PATH]["get"]

    headers = {
        param["name"].lower() for param in operation["parameters"] if param["in"] == "header"
    }
    assert "last-event-id" in headers
    assert operation["security"] == [{"APIKeyHeader": []}]
    assert {"401", "404"} <= set(operation["responses"])


ANALYSIS_PATH = "/analyses/{job_id}"


def json_schema(operation: OpenApiDocument, code: str) -> OpenApiDocument:
    schema: OpenApiDocument = operation["responses"][code]["content"]["application/json"]["schema"]
    return schema


def test_post_documents_the_created_body_and_every_refusal() -> None:
    operation = openapi()["paths"]["/analyses"]["post"]

    assert json_schema(operation, "202") == {"$ref": "#/components/schemas/AnalysisCreated"}
    for code in ("401", "411", "413", "422", "429", "503"):
        assert json_schema(operation, code) == {"$ref": "#/components/schemas/ApiProblem"}


def test_read_documents_the_analysis_body_and_its_refusals() -> None:
    operation = openapi()["paths"][ANALYSIS_PATH]["get"]

    assert json_schema(operation, "200") == {"$ref": "#/components/schemas/AnalysisBody"}
    for code in ("401", "404"):
        assert json_schema(operation, code) == {"$ref": "#/components/schemas/ApiProblem"}


def test_the_analysis_body_documents_its_fields_and_the_terminal_bodies() -> None:
    schemas = openapi()["components"]["schemas"]
    body, envelope = schemas["AnalysisBody"], schemas["ErrorEnvelope"]

    assert set(body["required"]) == {"id", "status", "created_at", "trace_id"}
    assert body["properties"]["status"] == {"$ref": "#/components/schemas/JobState"}
    assert schemas["JobState"]["enum"] == ["pending", "running", "done", "human_review", "failed"]
    assert {"$ref": "#/components/schemas/ContractChangeOutput"} in body["properties"]["output"][
        "anyOf"
    ]
    assert {"$ref": "#/components/schemas/ErrorEnvelope"} in body["properties"]["error"]["anyOf"]
    assert set(envelope["required"]) == {"error", "stage", "message", "run_id", "trace_id"}
    assert "issues" in envelope["properties"]


@pytest.mark.parametrize(
    "failures",
    [
        {},
        {"extraction_agent": InvalidExtractionError([ISSUE])},
        {"parse_original_contract": UpstreamError("vision call failed")},
    ],
    ids=["done", "human_review", "failed"],
)
def test_every_terminal_body_matches_the_documented_model(
    failures: dict[str, BaseException],
) -> None:
    harness = Harness(failures=failures)

    with harness.client() as client:
        job_id = post(client).json()["id"]
        pending = poll(client, job_id).json()
        harness.runner.run_all()
        finished = poll(client, job_id).json()

    assert AnalysisBody.model_validate(pending).status == "pending"
    assert AnalysisBody.model_validate(finished).model_dump(mode="json", exclude_unset=True) == (
        finished
    )


def test_the_created_body_and_a_refusal_match_their_documented_models() -> None:
    with Harness().client() as client:
        created = post(client).json()
        refused = post(client, key=None).json()

    assert AnalysisCreated.model_validate(created).id == created["id"]
    assert ApiProblem.model_validate(refused).detail == "missing or invalid API key"


def refusal_description(path: str, method: str, code: int) -> str:
    description: str = openapi()["paths"][path][method]["responses"][str(code)]["description"]
    return description


def test_each_documented_refusal_names_the_detail_the_api_sends() -> None:
    limited = Harness(settings=make_settings(api_rate_limit_requests=1))
    full = Harness()
    full.runner.full = True

    with Harness().client() as client:
        unauthorised = post(client, key=None).json()["detail"]
        missing = poll(client, "no-such-job").json()["detail"]
    with limited.client() as client:
        post(client)
        too_many = post(client).json()["detail"]
    with full.client() as client:
        queue_full = post(client).json()["detail"]

    assert unauthorised in refusal_description("/analyses", "post", 401)
    assert missing in refusal_description(ANALYSIS_PATH, "get", 404)
    assert missing in refusal_description(EVENTS_PATH, "get", 404)
    assert too_many in refusal_description("/analyses", "post", 429)
    assert queue_full in refusal_description("/analyses", "post", 503)
