"""Architecture: models the LLM sees stay inside OpenAI's strict json_schema subset (plan §6.2)."""

from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import BaseModel

from models import ContextMap, SectionMapping

LLM_FACING_MODELS: list[type[BaseModel]] = [ContextMap, SectionMapping]

# Keywords strict mode accepts; anything else (minLength, default, uniqueItems…) fails the test.
STRICT_KEYWORDS = {
    "$defs",
    "$ref",
    "additionalProperties",
    "anyOf",
    "const",
    "description",
    "enum",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "items",
    "maxItems",
    "maximum",
    "minItems",
    "minimum",
    "multipleOf",
    "pattern",
    "properties",
    "required",
    "title",
    "type",
}
# Under these keywords the keys are user names (fields, definitions), not schema keywords.
NAME_MAPS = {"properties", "$defs"}


def schema_nodes(node: Any) -> Iterator[dict[str, Any]]:
    """Every subschema of a JSON schema, skipping over the name maps themselves."""
    if isinstance(node, list):
        for item in node:
            yield from schema_nodes(item)
        return
    if not isinstance(node, dict):
        return
    yield node
    for keyword, value in node.items():
        children = value.values() if keyword in NAME_MAPS else [value]
        for child in children:
            yield from schema_nodes(child)


def test_schema_nodes_reports_a_forbidden_keyword_nested_in_properties() -> None:
    schema = {"type": "object", "properties": {"minLength": {"type": "string", "minLength": 1}}}

    keywords = {key for node in schema_nodes(schema) for key in node}

    assert "minLength" in keywords
    assert keywords - STRICT_KEYWORDS == {"minLength"}


@pytest.mark.parametrize("model", LLM_FACING_MODELS, ids=lambda model: model.__name__)
def test_llm_facing_schema_uses_only_strict_keywords(model: type[BaseModel]) -> None:
    keywords = {key for node in schema_nodes(model.model_json_schema()) for key in node}

    assert keywords - STRICT_KEYWORDS == set()


@pytest.mark.parametrize("model", LLM_FACING_MODELS, ids=lambda model: model.__name__)
def test_llm_facing_objects_forbid_extra_keys_and_require_every_field(
    model: type[BaseModel],
) -> None:
    objects = [
        node for node in schema_nodes(model.model_json_schema()) if node.get("type") == "object"
    ]

    assert objects
    for node in objects:
        assert node["additionalProperties"] is False
        assert set(node["required"]) == set(node["properties"])
