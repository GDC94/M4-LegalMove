"""Pydantic models and enums shared by the parser, agents, checks and CLI."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, computed_field, field_validator

ILLEGIBLE_MARK = "[ILLEGIBLE]"

VisionDetail = Literal["low", "high", "auto"]


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
