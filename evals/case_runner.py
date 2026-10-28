"""Runs one Golden Case through main.py's composition root and records it as a CaseRun."""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from errors import (
    LegalMoveError,
    NeedsHumanReviewError,
    UnclassifiableAmendmentError,
    UnresolvedReferenceError,
)
from eval_case import CaseRun, ChangeRecord, GoldenCase
from judge import JudgeVerdict
from main import UNEXPECTED_EXIT_CODE, CliArguments, Container, RunContext, run_traced
from models import AmendmentKind
from pipeline import AnalysisResult

JudgeSummary = Callable[[str, GoldenCase], JudgeVerdict]

logger = logging.getLogger("legalmove.evals")


@dataclass(frozen=True)
class CaseRunner:
    """Each case gets a fresh Container: the time budget of a run starts when it is built."""

    compose: Callable[[], Container]
    judge: JudgeSummary
    session_id: str

    def __call__(self, case: GoldenCase) -> CaseRun:
        run = self.run_pipeline(case)
        if run.output is None or not case.expects_output:
            return run
        verdict = self.judge_summary(run.output.summary_of_the_change, case)
        if verdict is None:
            return run
        return run.model_copy(
            update={"judge_score": float(verdict.score), "judge_reasoning": verdict.reasoning}
        )

    def run_pipeline(self, case: GoldenCase) -> CaseRun:
        """Same path as the CLI; a case failure is a result, a composition failure stops the run."""
        container = self.compose()
        arguments = CliArguments(case.original_image, case.amendment_image, self.session_id)
        context = RunContext(run_id=uuid.uuid4().hex)
        try:
            result = run_traced(container, arguments, context)
        except Exception as error:
            return record_failure(case.case_id, error, context.trace_id)
        return record_success(case.case_id, result, context.trace_id)

    def judge_summary(self, summary: str, case: GoldenCase) -> JudgeVerdict | None:
        """A Judge failure must not discard a paid pipeline run; its score is N/A instead."""
        try:
            return self.judge(summary, case)
        # The Judge is an external model call with no documented error type; only the type is
        # logged because its message may quote the Summary.
        except Exception as error:
            logger.warning("judge failed on %s: %s", case.case_id, type(error).__name__)
            return None


def record_success(case_id: str, result: AnalysisResult, trace_id: str | None) -> CaseRun:
    return CaseRun(
        case_id=case_id,
        exit_code=0,
        trace_id=trace_id,
        output=result.output,
        amendment_kind=result.context_map.amendment_kind,
        changes=tuple(
            ChangeRecord(
                section_id=change.section_id, change_type=change.change_type, topic=change.topic
            )
            for change in result.extraction.changes
        ),
        parsed_original=result.original.text,
        parsed_amendment=result.amendment.text,
    )


def record_failure(case_id: str, error: Exception, trace_id: str | None) -> CaseRun:
    """Mirrors main.report_failure: known errors keep their name and exit code."""
    if not isinstance(error, LegalMoveError):
        logger.error("unexpected failure on %s: %s", case_id, type(error).__name__)
        return CaseRun(
            case_id=case_id,
            exit_code=UNEXPECTED_EXIT_CODE,
            error="UnexpectedError",
            trace_id=trace_id,
        )
    issues = error.issues if isinstance(error, NeedsHumanReviewError) else ()
    return CaseRun(
        case_id=case_id,
        exit_code=error.exit_code,
        error=type(error).__name__,
        issue_codes=tuple(issue.code for issue in issues),
        trace_id=trace_id,
        amendment_kind=kind_implied_by(error),
    )


def kind_implied_by(error: LegalMoveError) -> AmendmentKind | None:
    """The kind a Human Review error proves on its own, with no Context Map to read."""
    if isinstance(error, UnclassifiableAmendmentError):
        return AmendmentKind.UNKNOWN
    # Only a delta map can hold an unresolved reference (checks.find_unresolved_references).
    if isinstance(error, UnresolvedReferenceError):
        return AmendmentKind.DELTA
    return None
