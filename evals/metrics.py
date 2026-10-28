"""Eval checks of plan §6.6: pure (run, case) -> Ratio | None functions and their aggregation."""

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

import jiwer

from eval_case import CaseRun, GoldenCase
from golden_case import ANY_HUMAN_REVIEW, UNCLASSIFIABLE_ERROR, human_review_error_names
from models import AmendmentKind

HUMAN_REVIEW_EXIT_CODE = 10


@dataclass(frozen=True)
class Ratio:
    """A score with its denominator, so micro aggregates can pool cases; N/A is None, not 0/0."""

    numerator: float
    denominator: float

    def __post_init__(self) -> None:
        if self.denominator <= 0:
            raise ValueError("a Ratio needs a positive denominator; use None for N/A")

    @property
    def value(self) -> float:
        return self.numerator / self.denominator


Check = Callable[[CaseRun, GoldenCase], Ratio | None]


class Aggregation(StrEnum):
    """MICRO pools numerators and denominators; MEAN averages the per-case values."""

    MICRO = "micro"
    MEAN = "mean"


@dataclass(frozen=True)
class Aggregate:
    """An aggregated metric and how many cases it was evaluable on (plan §6.7)."""

    value: float | None
    evaluable: int


@dataclass(frozen=True)
class Metric:
    name: str
    check: Check
    aggregation: Aggregation
    lower_is_better: bool = False


def normalize_text(text: str) -> str:
    """NFKC, casefold and collapsed whitespace: layout and typography are not content."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


# "$ 1.150.000" and "$1.150.000", "99,9 %" and "99,9%" state the same fact; only the spacing
# that hugs a currency or percent sign is dropped, never any other character.
SIGN_SPACING = re.compile(r"(?<=\$) | (?=%)")


def normalize_fact_text(text: str) -> str:
    return SIGN_SPACING.sub("", normalize_text(text))


def collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def score_cer(run: CaseRun, case: GoldenCase) -> Ratio | None:
    """Character edits over both pages against their source text; line breaks are layout.

    A page without reference text has no CER of its own, so it is left out of the pool.
    """
    if run.parsed_original is None or run.parsed_amendment is None:
        return None
    pairs = [
        (collapse_whitespace(reference), collapse_whitespace(hypothesis))
        for reference, hypothesis in (
            (case.source_original, run.parsed_original),
            (case.source_amendment, run.parsed_amendment),
        )
        if reference.strip()
    ]
    if not pairs:
        return None
    references, hypotheses = zip(*pairs, strict=True)
    measured = jiwer.process_characters(list(references), list(hypotheses))
    edits = measured.substitutions + measured.deletions + measured.insertions
    reference_chars = measured.substitutions + measured.deletions + measured.hits
    return Ratio(edits, reference_chars) if reference_chars else None


def expected_kind(case: GoldenCase) -> AmendmentKind:
    """An Unclassifiable case is right only when the run also says Unknown."""
    if case.expected_error == UNCLASSIFIABLE_ERROR:
        return AmendmentKind.UNKNOWN
    return case.amendment_kind


def accepted_kinds(run: CaseRun, case: GoldenCase) -> set[AmendmentKind]:
    """Decision 79: a case expecting any Human Review may also stop there as Unknown."""
    if case.expected_error == ANY_HUMAN_REVIEW and run.exit_code == HUMAN_REVIEW_EXIT_CODE:
        return {expected_kind(case), AmendmentKind.UNKNOWN}
    return {expected_kind(case)}


def score_amendment_kind(run: CaseRun, case: GoldenCase) -> Ratio | None:
    """A run that never classified is N/A."""
    if run.amendment_kind is None:
        return None
    return Ratio(int(run.amendment_kind in accepted_kinds(run, case)), 1)


def predicted_sections(run: CaseRun) -> set[str]:
    """A run without output predicted nothing, so its misses still count against recall."""
    return set(run.output.sections_changed) if run.output else set()


def expected_sections(case: GoldenCase) -> set[str]:
    return set(case.output.sections_changed) if case.output else set()


def score_change_precision(run: CaseRun, case: GoldenCase) -> Ratio | None:
    predicted = predicted_sections(run)
    if not case.expects_output or not predicted:
        return None
    return Ratio(len(predicted & expected_sections(case)), len(predicted))


def score_change_recall(run: CaseRun, case: GoldenCase) -> Ratio | None:
    expected = expected_sections(case)
    if not case.expects_output or not expected:
        return None
    return Ratio(len(predicted_sections(run) & expected), len(expected))


def score_change_type(run: CaseRun, case: GoldenCase) -> Ratio | None:
    """change_type accuracy on the Sections both the run and the case report."""
    predicted = {change.section_id: change.change_type for change in run.changes}
    matched = [change for change in case.changes if change.section_id in predicted]
    if not case.expects_output or not matched:
        return None
    hits = sum(predicted[change.section_id] is change.change_type for change in matched)
    return Ratio(hits, len(matched))


def score_topic_jaccard(run: CaseRun, case: GoldenCase) -> Ratio | None:
    """Two empty sets agree fully, the convention of diff_agreement (plan §5.5)."""
    if not case.expects_output or case.output is None:
        return None
    predicted = set(run.output.topics_touched) if run.output else set()
    expected = set(case.output.topics_touched)
    union = predicted | expected
    if not union:
        return Ratio(1, 1)
    return Ratio(len(predicted & expected), len(union))


def score_key_facts(run: CaseRun, case: GoldenCase) -> Ratio | None:
    if not case.expects_output or not case.key_facts:
        return None
    summary = normalize_fact_text(run.output.summary_of_the_change) if run.output else ""
    found = sum(normalize_fact_text(fact) in summary for fact in case.key_facts)
    return Ratio(found, len(case.key_facts))


def score_judge(run: CaseRun, case: GoldenCase) -> Ratio | None:
    """The Judge's 1-10 verdict, gathered once per run so this check stays pure."""
    if run.judge_score is None:
        return None
    return Ratio(run.judge_score, 1)


def score_human_review(run: CaseRun, case: GoldenCase) -> Ratio | None:
    """Exit 10 with the expected error, named by its class or by one of its issue codes.

    A case expecting NeedsHumanReviewError accepts any of its subclasses.
    """
    if case.expected_error is None:
        return None
    reasons = {run.error, *run.issue_codes}
    expected = (
        human_review_error_names()
        if case.expected_error == ANY_HUMAN_REVIEW
        else {case.expected_error}
    )
    hit = run.exit_code == HUMAN_REVIEW_EXIT_CODE and bool(expected & reasons)
    return Ratio(int(hit), 1)


METRICS: tuple[Metric, ...] = (
    Metric("cer", score_cer, Aggregation.MICRO, lower_is_better=True),
    Metric("amendment_kind_accuracy", score_amendment_kind, Aggregation.MICRO),
    Metric("change_precision", score_change_precision, Aggregation.MICRO),
    Metric("change_recall", score_change_recall, Aggregation.MICRO),
    Metric("change_type_accuracy", score_change_type, Aggregation.MICRO),
    Metric("topic_jaccard", score_topic_jaccard, Aggregation.MEAN),
    Metric("key_facts_coverage", score_key_facts, Aggregation.MICRO),
    Metric("judge_score", score_judge, Aggregation.MEAN),
    Metric("human_review", score_human_review, Aggregation.MICRO),
)


def metric_named(name: str) -> Metric:
    return next(metric for metric in METRICS if metric.name == name)


def score_case(run: CaseRun, case: GoldenCase) -> dict[str, Ratio | None]:
    return {metric.name: metric.check(run, case) for metric in METRICS}


def aggregate_metric(aggregation: Aggregation, ratios: Iterable[Ratio | None]) -> Aggregate:
    """N/A scores are left out of both the value and the evaluable count."""
    evaluable = [ratio for ratio in ratios if ratio is not None]
    if not evaluable:
        return Aggregate(value=None, evaluable=0)
    if aggregation is Aggregation.MICRO:
        pooled = sum(ratio.numerator for ratio in evaluable)
        return Aggregate(pooled / sum(ratio.denominator for ratio in evaluable), len(evaluable))
    return Aggregate(sum(ratio.value for ratio in evaluable) / len(evaluable), len(evaluable))


def aggregate_scores(
    scored: Sequence[Mapping[str, Ratio | None]],
) -> dict[str, Aggregate]:
    """Every metric over a group of scored cases."""
    return {
        metric.name: aggregate_metric(
            metric.aggregation, (scores[metric.name] for scores in scored)
        )
        for metric in METRICS
    }
