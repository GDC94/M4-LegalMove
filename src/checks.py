"""Deterministic Checks: Context Map and extraction rules, grounding and the Kind Heuristic."""

import re
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from enum import StrEnum

from errors import RetryFeedback, ValidationIssue
from models import (
    AmendmentKind,
    Change,
    ChangeType,
    ContextMap,
    ExtractionResult,
    SectionMapping,
    find_order_breaks,
    parse_section_id,
)

# NFKC already folds full-width forms; these are the variants it leaves alone: double quotes
# (curly, low-9, guillemets), single quotes (curly, low-9, prime, accents) and dashes (hyphen,
# non-breaking hyphen, figure, en, em, horizontal bar, minus sign).
QUOTES_AND_DASHES = str.maketrans(
    {
        **dict.fromkeys("\u201c\u201d\u201e\u201f\u00ab\u00bb", '"'),
        **dict.fromkeys("\u2018\u2019\u201a\u201b\u2032`\u00b4", "'"),
        **dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"),
    }
)


def normalize_for_grounding(text: str) -> str:
    """Grounding normalization: NFKC, casefold, unified quotes and dashes, collapsed whitespace."""
    folded = unicodedata.normalize("NFKC", text).casefold().translate(QUOTES_AND_DASHES)
    return " ".join(folded.split())


def is_grounded(fragment: str, text: str) -> bool:
    """Exact containment after normalization; never fuzzy, and a blank fragment proves nothing."""
    needle = normalize_for_grounding(fragment)
    return bool(needle) and needle in normalize_for_grounding(text)


def build_feedback(code: str, path: str, message: str, detail: str | None = None) -> RetryFeedback:
    issue = ValidationIssue(code=code, path=path, message=message)
    return RetryFeedback(issue=issue, detail=detail or message)


def check_context_map(
    context_map: ContextMap, original_text: str, amendment_text: str
) -> list[RetryFeedback]:
    """Every hard rule of the map at once, so the single retry can fix all of them together."""
    sections = context_map.sections
    return [
        *check_map_not_empty(context_map),
        *check_canonical_order(sections),
        *check_rows_have_a_ref(sections),
        *check_section_origins(sections),
        *check_refs_grounded(sections, original_text, amendment_text),
    ]


def check_map_not_empty(context_map: ContextMap) -> Iterator[RetryFeedback]:
    if context_map.amendment_kind is not AmendmentKind.UNKNOWN and not context_map.sections:
        yield build_feedback(
            "empty_context_map", "sections", "a restated or delta map must list its sections"
        )


def check_canonical_order(sections: Sequence[SectionMapping]) -> Iterator[RetryFeedback]:
    """Original Sections first in document order, then new: Sections in Amendment order."""
    section_ids = [section.section_id for section in sections]
    for index in find_order_breaks(section_ids):
        yield order_feedback(index, section_ids[index])


def order_feedback(index: int, section_id: str) -> RetryFeedback:
    # Section IDs passed the grammar, so they are canonical identifiers, never document text.
    message = (
        f"section {section_id} breaks the canonical order: original sections first in "
        "document order, then new: sections in amendment order"
    )
    return build_feedback("section_order", f"sections[{index}].section_id", message)


def check_rows_have_a_ref(sections: Sequence[SectionMapping]) -> Iterator[RetryFeedback]:
    for index, section in enumerate(sections):
        if section.original_ref is None and section.amendment_ref is None:
            yield build_feedback(
                "empty_section_mapping",
                f"sections[{index}]",
                f"section {section.section_id} has neither original_ref nor amendment_ref",
            )


def check_section_origins(sections: Sequence[SectionMapping]) -> Iterator[RetryFeedback]:
    """A new: Section exists only in the Amendment; any other ID exists in the original."""
    for index, section in enumerate(sections):
        if section.original_ref is None and section.amendment_ref is None:
            continue  # already reported as an empty row
        is_new = parse_section_id(section.section_id).is_new
        if is_new == (section.original_ref is not None):
            expected = "must not have" if is_new else "must have"
            yield build_feedback(
                "section_origin_mismatch",
                f"sections[{index}].original_ref",
                f"section {section.section_id} {expected} an original_ref",
            )


def check_refs_grounded(
    sections: Sequence[SectionMapping], original_text: str, amendment_text: str
) -> Iterator[RetryFeedback]:
    for index, section in enumerate(sections):
        refs = (
            ("original_ref", section.original_ref, original_text, "original contract"),
            ("amendment_ref", section.amendment_ref, amendment_text, "amendment"),
        )
        for field, ref, text, document in refs:
            if ref is not None and not is_grounded(ref, text):
                path = f"sections[{index}].{field}"
                yield ungrounded_feedback("ungrounded_reference", path, field, ref, document)


def ungrounded_feedback(
    code: str, path: str, field: str, fragment: str, document: str
) -> RetryFeedback:
    """The public message names the field only; the retry detail quotes the text to correct it."""
    message = f"{field} does not appear verbatim in the {document}"
    detail = f"{field} {fragment!r} does not appear verbatim in the {document}; copy it exactly"
    return build_feedback(code, path, message, detail)


SectionRows = Mapping[str, SectionMapping]


def check_extraction(
    result: ExtractionResult, context_map: ContextMap, original_text: str, amendment_text: str
) -> list[RetryFeedback]:
    """Every hard rule of the extraction at once, so the single retry can fix all of them."""
    rows = {section.section_id: section for section in context_map.sections}
    kind = context_map.amendment_kind
    return [
        *check_section_references(result.changes, rows),
        *check_change_types(result.changes, rows, kind),
        *check_removed_quotes(result.changes, kind),
        *check_structural_changes(result.changes, context_map),
        *check_extraction_order(result, context_map),
        *check_quotes_grounded(result.changes, original_text, amendment_text),
    ]


def check_section_references(
    changes: Sequence[Change], rows: SectionRows
) -> Iterator[RetryFeedback]:
    for index, change in enumerate(changes):
        if change.section_id not in rows:
            yield build_feedback(
                "unresolved_section_reference",
                f"changes[{index}].section_id",
                f"section {change.section_id} is not in the context map",
            )


def check_change_types(
    changes: Sequence[Change], rows: SectionRows, kind: AmendmentKind
) -> Iterator[RetryFeedback]:
    """Added and removed are whole Sections (decision 46); restated presence is unambiguous."""
    for index, change in enumerate(changes):
        row = rows.get(change.section_id)
        if row is not None and not fits_row(change.change_type, row, kind):
            yield build_feedback(
                "change_type_mismatch",
                f"changes[{index}].change_type",
                f"a {change.change_type} change does not fit section {change.section_id}, "
                f"which appears {describe_presence(row)}",
            )


def fits_row(change_type: ChangeType, row: SectionMapping, kind: AmendmentKind) -> bool:
    in_original = row.original_ref is not None
    if change_type is ChangeType.ADDED or not in_original:
        return change_type is ChangeType.ADDED and not in_original
    # A delta instruction may or may not be mapped, so only restated decides by presence.
    if kind is not AmendmentKind.RESTATED:
        return True
    return (row.amendment_ref is not None) == (change_type is ChangeType.MODIFIED)


def describe_presence(row: SectionMapping) -> str:
    if row.original_ref is None:
        return "only in the amendment"
    if row.amendment_ref is None:
        return "only in the original contract"
    return "in both documents"


def check_removed_quotes(changes: Sequence[Change], kind: AmendmentKind) -> Iterator[RetryFeedback]:
    """The Kind-dependent Quote rule that Change cannot check on its own (decision 16)."""
    for index, change in enumerate(changes):
        if change.change_type is not ChangeType.REMOVED:
            continue
        has_quote = change.amendment_quote is not None
        if kind is AmendmentKind.DELTA and not has_quote:
            message = "a delta removal needs the amendment_quote of its instruction"
        elif kind is AmendmentKind.RESTATED and has_quote:
            message = "a restated removal must not have an amendment_quote"
        else:
            continue
        path = f"changes[{index}].amendment_quote"
        yield build_feedback("removed_quote_mismatch", path, message)


def check_structural_changes(
    changes: Sequence[Change], context_map: ContextMap
) -> Iterator[RetryFeedback]:
    """In restated, a row present on one side only demands its added or removed (§2.5)."""
    if context_map.amendment_kind is not AmendmentKind.RESTATED:
        return
    reported = {(change.section_id, change.change_type) for change in changes}
    for section in context_map.sections:
        required = required_structural_change(section)
        if required is not None and (section.section_id, required) not in reported:
            yield build_feedback(
                "missing_structural_change",
                "changes",
                f"section {section.section_id} appears {describe_presence(section)} "
                f"and needs a {required} change",
            )


def required_structural_change(section: SectionMapping) -> ChangeType | None:
    if section.original_ref is None:
        return ChangeType.ADDED
    if section.amendment_ref is None:
        return ChangeType.REMOVED
    return None


def check_extraction_order(
    result: ExtractionResult, context_map: ContextMap
) -> Iterator[RetryFeedback]:
    """Changes and sections_changed follow the map's canonical order (§2.4)."""
    positions = {section.section_id: i for i, section in enumerate(context_map.sections)}
    change_ids = [change.section_id for change in result.changes]
    yield from check_map_order(change_ids, positions, "changes[{}].section_id")
    yield from check_map_order(result.sections_changed, positions, "sections_changed[{}]")


def check_map_order(
    section_ids: Sequence[str], positions: Mapping[str, int], path_template: str
) -> Iterator[RetryFeedback]:
    highest = -1
    for index, section_id in enumerate(section_ids):
        position = positions.get(section_id)
        if position is None:
            continue  # already reported as an unresolved reference
        if position < highest:
            yield build_feedback(
                "section_order",
                path_template.format(index),
                f"section {section_id} breaks the context map order",
            )
        highest = max(highest, position)


def quote_sources(
    change: Change, original_text: str, amendment_text: str
) -> tuple[tuple[str, str | None, str, str], ...]:
    return (
        ("original_quote", change.original_quote, original_text, "original contract"),
        ("amendment_quote", change.amendment_quote, amendment_text, "amendment"),
    )


def check_quotes_grounded(
    changes: Sequence[Change], original_text: str, amendment_text: str
) -> Iterator[RetryFeedback]:
    """Any ungrounded Quote is a hard failure; there is no threshold (decision 28)."""
    for index, change in enumerate(changes):
        for field, quote, text, document in quote_sources(change, original_text, amendment_text):
            if quote is not None and not is_grounded(quote, text):
                path = f"changes[{index}].{field}"
                yield ungrounded_feedback("ungrounded_quote", path, field, quote, document)


def compute_grounding_rate(
    result: ExtractionResult, original_text: str, amendment_text: str
) -> float | None:
    """Grounded Quotes over all Quotes; None (N/A) without a denominator (decision 47)."""
    verdicts = [
        is_grounded(quote, text)
        for change in result.changes
        for _, quote, text, _ in quote_sources(change, original_text, amendment_text)
        if quote is not None
    ]
    return sum(verdicts) / len(verdicts) if verdicts else None


class KindHeuristic(StrEnum):
    """Second signal against the agent; absence of Delta markers is never evidence of Restated."""

    DELTA = "delta"
    INCONCLUSIVE = "inconclusive"


# Spanish change-instruction phrasing, matched on normalized text. These phrases must never
# appear in the ContextualizationAgent prompt, or both signals would fail together (plan §4.1).
DELTA_MARKERS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bqueda(?:n|r[aá]n?)?\s+redactad[ao]s?\b",
        r"\b(?:se\s+dejan?|d[eé]jan?se)\s+sin\s+efecto\b",
        r"\b(?:se\s+incorporan?|incorp[oó]ran?se)\b",
        r"\blas\s+partes\s+acuerdan\s+modificar\b",
        r"\b(?:se\s+modifican?|modif[ií]can?se)\b",
        r"\b(?:se\s+sustituyen?|sustit[uú]yen?se|se\s+reemplazan?|reempl[aá]zan?se)\b",
        r"\b(?:se\s+derogan?|der[oó]gan?se)\b",
    )
)


def detect_kind_heuristic(amendment_text: str) -> KindHeuristic:
    normalized = normalize_for_grounding(amendment_text)
    if any(marker.search(normalized) for marker in DELTA_MARKERS):
        return KindHeuristic.DELTA
    return KindHeuristic.INCONCLUSIVE
