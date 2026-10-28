"""Human Review errors and the public/internal validation contracts (plan §4.4, §4.5)."""

import pytest

from errors import (
    InvalidContextMapError,
    InvalidExtractionError,
    LegalMoveError,
    NeedsHumanReviewError,
    RetryFeedback,
    UnclassifiableAmendmentError,
    UnresolvedReferenceError,
    UpstreamError,
    UsageError,
    ValidationIssue,
)


def test_human_review_errors_share_exit_code_ten() -> None:
    for error_type in (
        UnclassifiableAmendmentError,
        InvalidContextMapError,
        InvalidExtractionError,
        UnresolvedReferenceError,
    ):
        assert issubclass(error_type, NeedsHumanReviewError)
        assert issubclass(error_type, LegalMoveError)
        assert error_type.exit_code == 10


def test_contextualization_errors_name_their_stage() -> None:
    assert UnclassifiableAmendmentError.stage == "contextualization"
    assert InvalidContextMapError.stage == "contextualization"
    assert UnresolvedReferenceError.stage == "contextualization"


def test_unresolved_reference_carries_its_issues_and_a_sanitised_message() -> None:
    issues = [ValidationIssue(code="unresolved_reference", path="sections[2]", message="x")]

    error = UnresolvedReferenceError(issues)

    assert error.issues == tuple(issues)
    assert str(error) == "the amendment references 1 section the original contract does not have"


def test_unclassifiable_amendment_carries_no_issues() -> None:
    assert UnclassifiableAmendmentError("amendment kind is unknown").issues == ()


def test_invalid_context_map_carries_its_issues_and_a_sanitised_message() -> None:
    issues = [
        ValidationIssue(code="ungrounded_reference", path="sections[0].original_ref", message="x"),
        ValidationIssue(code="empty_section_mapping", path="sections[1]", message="y"),
    ]

    error = InvalidContextMapError(issues)

    assert error.issues == tuple(issues)
    assert str(error) == (
        "context map is still invalid after one retry: empty_section_mapping, ungrounded_reference"
    )


def test_retry_feedback_exposes_only_its_sanitised_issue_publicly() -> None:
    issue = ValidationIssue(
        code="ungrounded_reference", path="sections[0].original_ref", message="m"
    )
    feedback = RetryFeedback(issue=issue, detail='original_ref "Cláusula Novena" was not found')

    assert feedback.issue == issue
    assert "Novena" not in repr(feedback.issue)


def test_validation_contracts_are_immutable() -> None:
    issue = ValidationIssue(code="c", path="p", message="m")

    with pytest.raises(AttributeError):
        issue.code = "other"  # type: ignore[misc]


def test_upstream_error_defaults_to_the_parse_stage() -> None:
    assert UpstreamError("vision call failed").stage == "parse"


def test_upstream_error_names_the_stage_that_called_the_model() -> None:
    error = UpstreamError("contextualization call failed", stage="contextualization")

    assert error.stage == "contextualization"
    assert error.exit_code == 5
    assert UpstreamError("other").stage == "parse"


def test_invalid_extraction_names_its_stage_and_carries_sanitised_issues() -> None:
    issues = [
        ValidationIssue(code="ungrounded_quote", path="changes[0].original_quote", message="x"),
        ValidationIssue(
            code="unresolved_section_reference", path="changes[1].section_id", message="y"
        ),
    ]

    error = InvalidExtractionError(issues)

    assert InvalidExtractionError.stage == "extraction"
    assert error.issues == tuple(issues)
    assert str(error) == (
        "extraction is still invalid after one retry: "
        "ungrounded_quote, unresolved_section_reference"
    )


def test_cli_usage_error_has_exit_code_two_and_the_cli_stage() -> None:
    assert issubclass(UsageError, LegalMoveError)
    assert UsageError.exit_code == 2
    assert UsageError("missing amendment").stage == "cli"
