"""OpenAPI contract: the progress stream documents both event shapes as a `kind` union."""

from typing import Any

from api_doubles import Harness

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


def test_the_events_endpoint_documents_last_event_id_and_the_api_key() -> None:
    operation = openapi()["paths"][EVENTS_PATH]["get"]

    headers = {
        param["name"].lower() for param in operation["parameters"] if param["in"] == "header"
    }
    assert "last-event-id" in headers
    assert operation["security"] == [{"APIKeyHeader": []}]
    assert {"401", "404"} <= set(operation["responses"])
