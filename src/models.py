"""Pydantic models and enums shared by the parser, agents, checks and CLI."""

import re
from collections import Counter
from enum import StrEnum
from typing import Literal, NamedTuple, Self

from pydantic import BaseModel, ConfigDict, computed_field, field_validator, model_validator

ILLEGIBLE_MARK = "[ILLEGIBLE]"

VisionDetail = Literal["low", "high", "auto"]

NEW_SECTION_PREFIX = "new:"
POSITIONAL_MARK = "§"
# Positive integers joined by dots (5, 5.2) or a positional § ID (§7), optionally new:-prefixed.
SECTION_ID_GRAMMAR = re.compile(r"(?:new:)?(?:[1-9]\d*(?:\.[1-9]\d*)*|§[1-9]\d*)")


class DocumentRole(StrEnum):
    """Which of the two input documents a Parsed Contract comes from."""

    ORIGINAL = "original"
    AMENDMENT = "amendment"


class ParsedContract(BaseModel):
    """Verbatim text read from one document image, with its hash and Illegible Mark count."""

    model_config = ConfigDict(frozen=True)

    role: DocumentRole
    source_path: str
    image_sha256: str
    text: str

    @field_validator("text")
    @classmethod
    def check_text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value

    @computed_field  # type: ignore[prop-decorator]  # pydantic's documented computed_field idiom
    @property
    def illegible_count(self) -> int:
        """Counted, never reported by the model, so it cannot be hallucinated."""
        return self.text.count(ILLEGIBLE_MARK)


class AmendmentKind(StrEnum):
    """How an Amendment expresses its changes (plan §2.2)."""

    RESTATED = "restated"
    DELTA = "delta"
    UNKNOWN = "unknown"


class SectionIdParts(NamedTuple):
    """A canonical Section ID split into the facts the order checks compare."""

    is_new: bool
    is_positional: bool
    numbers: tuple[int, ...]


def parse_section_id(section_id: str) -> SectionIdParts:
    """Split an ID that already matches the canonical grammar."""
    is_new = section_id.startswith(NEW_SECTION_PREFIX)
    body = section_id.removeprefix(NEW_SECTION_PREFIX)
    is_positional = body.startswith(POSITIONAL_MARK)
    numbers = tuple(int(part) for part in body.removeprefix(POSITIONAL_MARK).split("."))
    return SectionIdParts(is_new=is_new, is_positional=is_positional, numbers=numbers)


# The LLM sees these models through a strict json_schema: no Field constraints, only validators
# (decision 25). extra="forbid" emits the additionalProperties: false that strict mode requires.
class SectionMapping(BaseModel):
    """One row of the Context Map: how one Section appears in each document. Never a Change."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section_id: str
    title: str | None
    original_ref: str | None
    amendment_ref: str | None
    purpose: str

    @field_validator("section_id")
    @classmethod
    def check_section_id_grammar(cls, value: str) -> str:
        # The value is not echoed: a malformed ID may carry document text into stderr.
        if not SECTION_ID_GRAMMAR.fullmatch(value):
            raise ValueError("section_id does not follow the canonical grammar")
        return value


class ContextMap(BaseModel):
    """The ContextualizationAgent's result: Amendment Kind plus one Section Mapping per Section."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reasoning: str
    amendment_kind: AmendmentKind
    sections: list[SectionMapping]

    @model_validator(mode="after")
    def check_unique_section_ids(self) -> Self:
        counts = Counter(section.section_id for section in self.sections)
        duplicates = sorted(section_id for section_id, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(f"duplicate section_id values: {', '.join(duplicates)}")
        return self
