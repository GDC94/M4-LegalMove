"""Domain error hierarchy: each error declares its stage and CLI exit code."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import ClassVar


class LegalMoveError(Exception):
    """Base of every expected failure; messages never carry document text."""

    # Not a ClassVar: UpstreamError sets it per instance, since several stages call models.
    stage: str = "unknown"
    exit_code: ClassVar[int] = 1


class UsageError(LegalMoveError):
    """The command line arguments are invalid."""

    stage = "cli"
    exit_code = 2


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

    exit_code = 5

    def __init__(self, message: str, *, stage: str = "parse") -> None:
        super().__init__(message)
        self.stage = stage


class ObservabilityError(LegalMoveError):
    """The tracer could not be initialised or could not confirm its flush."""

    stage = "observability"
    exit_code = 5


class IllegibleDocumentError(LegalMoveError):
    """The Parsed Contract has more illegible marks than allowed."""

    stage = "parse"
    exit_code = 6


@dataclass(frozen=True)
class ValidationIssue:
    """Public reason for a Human Review: code, path and a message that never quotes documents."""

    code: str
    path: str
    message: str


@dataclass(frozen=True)
class RetryFeedback:
    """Internal corrective note for the retry prompt; only `detail` may quote the needed text."""

    issue: ValidationIssue
    detail: str


class NeedsHumanReviewError(LegalMoveError):
    """The system declines to answer and hands the case to a person (exit code 10)."""

    exit_code = 10

    def __init__(self, message: str, issues: Iterable[ValidationIssue] = ()) -> None:
        super().__init__(message)
        self.issues = tuple(issues)


class UnclassifiableAmendmentError(NeedsHumanReviewError):
    """The ContextualizationAgent classified the Amendment as Unknown; it is never retried."""

    stage = "contextualization"


class InvalidContextMapError(NeedsHumanReviewError):
    """The Context Map failed its hard checks again after the single semantic retry."""

    stage = "contextualization"

    def __init__(self, issues: Iterable[ValidationIssue]) -> None:
        issues = tuple(issues)
        codes = ", ".join(sorted({issue.code for issue in issues}))
        super().__init__(f"context map is still invalid after one retry: {codes}", issues)


class InvalidExtractionError(NeedsHumanReviewError):
    """The ExtractionResult failed its hard checks again after the single semantic retry."""

    stage = "extraction"

    def __init__(self, issues: Iterable[ValidationIssue]) -> None:
        issues = tuple(issues)
        codes = ", ".join(sorted({issue.code for issue in issues}))
        super().__init__(f"extraction is still invalid after one retry: {codes}", issues)
