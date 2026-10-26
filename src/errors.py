"""Domain error hierarchy: each error declares its stage and CLI exit code."""

from typing import ClassVar


class LegalMoveError(Exception):
    """Base of every expected failure; messages never carry document text."""

    stage: ClassVar[str] = "unknown"
    exit_code: ClassVar[int] = 1


class ConfigurationError(LegalMoveError):
    """Settings are missing or out of range."""

    stage = "composition"
    exit_code = 3


class InvalidImageError(LegalMoveError):
    """The input file is missing, not JPEG/PNG, or outside the image limits."""

    stage = "validation"
    exit_code = 4


class UpstreamError(LegalMoveError):
    """The model API kept failing after its transport retries, or timed out."""

    stage = "parse"
    exit_code = 5


class ObservabilityError(LegalMoveError):
    """The tracer could not be initialised or could not confirm its flush."""

    stage = "observability"
    exit_code = 5


class IllegibleDocumentError(LegalMoveError):
    """The Parsed Contract has more illegible marks than allowed."""

    stage = "parse"
    exit_code = 6
