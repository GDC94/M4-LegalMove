"""Deterministic Checks: Context Map rules, grounding normalization and the Kind Heuristic."""

import re
import unicodedata
from collections.abc import Iterator, Sequence
from enum import StrEnum

from errors import RetryFeedback, ValidationIssue
from models import AmendmentKind, ContextMap, SectionMapping, parse_section_id

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
    """Original Sections first in document order, then new: Sections in Amendment order.

    Numbered and positional IDs are compared only among themselves: their relative position
    cannot be derived from the IDs alone.
    """
    seen_new = False
    highest: dict[tuple[bool, bool], tuple[int, ...]] = {}
    for index, section in enumerate(sections):
        parts = parse_section_id(section.section_id)
        shape = (parts.is_new, parts.is_positional)
        previous = highest.get(shape)
        if (seen_new and not parts.is_new) or (previous is not None and parts.numbers < previous):
            yield order_feedback(index, section.section_id)
        seen_new = seen_new or parts.is_new
        highest[shape] = max(previous or parts.numbers, parts.numbers)


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
                yield ungrounded_feedback(f"sections[{index}].{field}", field, ref, document)


def ungrounded_feedback(path: str, field: str, ref: str, document: str) -> RetryFeedback:
    """The public message names the field only; the retry detail quotes the ref to correct it."""
    message = f"{field} does not appear verbatim in the {document}"
    detail = f"{field} {ref!r} does not appear verbatim in the {document}; copy it exactly"
    return build_feedback("ungrounded_reference", path, message, detail)


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
