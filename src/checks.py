"""Deterministic Checks: map and extraction rules, grounding, Kind Heuristic, soft signals."""

import re
import unicodedata
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from errors import RetryFeedback, ValidationIssue
from models import (
    ILLEGIBLE_MARK,
    AmendmentKind,
    Change,
    ChangeType,
    ContextMap,
    ExtractionResult,
    InstructionKind,
    SectionMapping,
    Topic,
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
        *check_instructions(context_map),
        *check_refs_grounded(sections, original_text, amendment_text),
    ]


def find_unresolved_references(context_map: ContextMap) -> list[ValidationIssue]:
    """Delta new: rows an instruction rewrites or removes: the referenced Section is missing.

    Terminal (decision 60 applied to references): the retry would see the same evidence, so
    these go straight to Human Review instead of feeding the retry.
    """
    if context_map.amendment_kind is not AmendmentKind.DELTA:
        return []
    return [
        ValidationIssue(
            code="unresolved_reference",
            path=f"sections[{index}]",
            # Canonical IDs and enum values only: they passed the grammar, so no document text.
            message=(
                f"section {section.section_id} is mapped as new, but its instruction "
                f"{section.instruction} it: the amendment references a section the original "
                "contract does not have"
            ),
        )
        for index, section in enumerate(context_map.sections)
        if is_unresolved_reference(section)
    ]


# A new: row that an instruction rewrites or removes: that Section has nothing to rewrite or
# remove in the original. A new: row with none is a filling mistake and gets the retry.
UNRESOLVABLE_INSTRUCTIONS = frozenset({InstructionKind.REWRITES, InstructionKind.REMOVES})


def is_unresolved_reference(section: SectionMapping) -> bool:
    is_new = parse_section_id(section.section_id).is_new
    return is_new and section.instruction in UNRESOLVABLE_INSTRUCTIONS


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


def check_instructions(context_map: ContextMap) -> Iterator[RetryFeedback]:
    """Restated has no instructions; a delta row an instruction names says what it does."""
    kind = context_map.amendment_kind
    for index, section in enumerate(context_map.sections):
        message = describe_instruction_mismatch(section, kind)
        if message is not None:
            yield build_feedback("instruction_mismatch", f"sections[{index}].instruction", message)


def describe_instruction_mismatch(section: SectionMapping, kind: AmendmentKind) -> str | None:
    if kind is AmendmentKind.RESTATED and section.instruction is not InstructionKind.NONE:
        return (
            f"section {section.section_id} has instruction {section.instruction}, but a "
            "restated amendment has no instructions: use none"
        )
    if kind is AmendmentKind.DELTA:
        return describe_delta_instruction_mismatch(section)
    return None


def describe_delta_instruction_mismatch(section: SectionMapping) -> str | None:
    # Unresolved References belong to find_unresolved_references, which ends the run instead.
    if is_unresolved_reference(section):
        return None
    if (
        section.instruction is InstructionKind.ADDS
        and not parse_section_id(section.section_id).is_new
    ):
        return (
            f"section {section.section_id} exists in the original contract, so its instruction "
            "cannot be adds: only a new: section is added"
        )
    named = section.amendment_ref is not None
    if named != (section.instruction is not InstructionKind.NONE):
        return (
            f"section {section.section_id} has instruction {section.instruction} "
            f"{'with' if named else 'without'} an amendment_ref: in a delta map, a row has "
            "adds, rewrites or removes exactly when an instruction names it"
        )
    return None


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
    pairs = pair_sections(context_map, original_text, amendment_text)
    return [
        *check_section_references(result.changes, rows),
        *check_delta_targets(result.changes, context_map),
        *check_change_types(result.changes, rows, kind),
        *check_removed_quotes(result.changes, kind),
        *check_structural_changes(result.changes, context_map),
        *check_extraction_order(result, context_map),
        *check_quotes_grounded(result.changes, original_text, amendment_text),
        *check_missing_text_changes(result, kind, pairs),
        *check_unchanged_text_reports(result.changes, pairs),
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


def check_delta_targets(
    changes: Sequence[Change], context_map: ContextMap
) -> Iterator[RetryFeedback]:
    """In delta, only a Section an instruction targets can change.

    Delta Quotes are grounded against the whole Amendment, so a Change on an untargeted Section
    could borrow another Section's instruction and still pass grounding.
    """
    if context_map.amendment_kind is not AmendmentKind.DELTA:
        return
    untargeted = {s.section_id for s in context_map.sections if s.amendment_ref is None}
    targeted = [s.section_id for s in context_map.sections if s.amendment_ref is not None]
    # Canonical IDs only: they passed the map's grammar, so they hold no document text.
    hint = f"; the amendment targets only section_ids: {', '.join(targeted) or 'none'}"
    for index, change in enumerate(changes):
        if change.section_id in untargeted:
            message = (
                f"section {change.section_id} is not targeted by any instruction "
                "of the delta amendment"
            )
            path = f"changes[{index}].section_id"
            yield build_feedback("untargeted_delta_change", path, message, message + hint)


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


# Characters of differing words quoted per Section in the retry detail. Beyond it only the
# Section is named, so text injected inside a Section is never fed back to the model at length.
TEXT_DIFFERENCE_QUOTE_LIMIT = 80
NORMALIZED_ILLEGIBLE_MARK = normalize_for_grounding(ILLEGIBLE_MARK)
# Words one Illegible Mark may stand for on the other side; a longer run is a real difference.
ILLEGIBLE_RUN_LIMIT = 3
TOKEN = re.compile(rf"{re.escape(NORMALIZED_ILLEGIBLE_MARK)}|\w+|[^\w\s]")


class TextComparison(StrEnum):
    """The one verdict both text guards read; unknown fires neither of them (fail-safe)."""

    SAME = "same"
    DIFFERENT = "different"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DocumentSegments:
    """Each segmentable Section's text, and which one (if any) runs to the document end."""

    texts: dict[str, str]
    open_ended: str | None


@dataclass(frozen=True)
class SectionPair:
    """A Section segmented in both documents; open_* marks a segment that runs to the end."""

    section_id: str
    original: str
    amendment: str
    heading: str
    original_open: bool
    amendment_open: bool
    labels: Mapping[str, str]  # every map row's Section Label, shared by all the pairs


def pair_sections(
    context_map: ContextMap, original_text: str, amendment_text: str
) -> list[SectionPair]:
    """Sections segmentable in both documents, in map order; the rest never fire."""
    original, amendment = segment_both_documents(context_map, original_text, amendment_text)
    labels = extract_section_labels(context_map)
    return [
        SectionPair(
            sid,
            original.texts[sid],
            amendment.texts[sid],
            normalize_for_grounding(section.original_ref or ""),
            sid == original.open_ended,
            sid == amendment.open_ended,
            labels,
        )
        for section in context_map.sections
        if (sid := section.section_id) in original.texts and sid in amendment.texts
    ]


def check_missing_text_changes(
    result: ExtractionResult, kind: AmendmentKind, pairs: Sequence[SectionPair]
) -> Iterator[RetryFeedback]:
    """Restated with no Changes while a paired Section's text differs (decision 94).

    A word comparison, not DIFF_RATIO_THRESHOLD: a one-word modal edit ("podrá" -> "deberá")
    sits above that ratio, and an injected note claiming nothing changed hid exactly that edit.
    """
    if kind is not AmendmentKind.RESTATED or result.changes:
        return
    differing = find_differing_sections(pairs)
    if not differing:
        return
    noun = "section" if len(differing) == 1 else "sections"
    # Canonical IDs only in the public message; the detail quotes the few differing words.
    message = (
        f"no change was reported, but the text of {noun} "
        f"{', '.join(pair.section_id for pair in differing)} differs between "
        "the original contract and the amendment"
    )
    quoted = "; ".join(
        describe_text_difference(
            pair.section_id,
            drop_illegible_marks(pair.original),
            drop_illegible_marks(pair.amendment),
        )
        for pair in differing
    )
    detail = f"{message}; compare them again: {quoted}"
    yield build_feedback("missing_text_change", "changes", message, detail)


def find_differing_sections(pairs: Sequence[SectionPair]) -> list[SectionPair]:
    return [pair for pair in pairs if compare_section(pair) is TextComparison.DIFFERENT]


def segment_both_documents(
    context_map: ContextMap, original_text: str, amendment_text: str
) -> tuple[DocumentSegments, DocumentSegments]:
    sections = context_map.sections
    original = segment_document({s.section_id: s.original_ref for s in sections}, original_text)
    amendment = segment_document({s.section_id: s.amendment_ref for s in sections}, amendment_text)
    return original, amendment


def check_unchanged_text_reports(
    changes: Sequence[Change], pairs: Sequence[SectionPair]
) -> Iterator[RetryFeedback]:
    """A modified Change on a Section whose text is the same in both documents (decision 99).

    Segments, not the model's Quotes, decide: a run quoted the whole Section back as its own
    rewrite. A Section not segmentable on both sides never fires (fail-safe).
    """
    by_id = {pair.section_id: pair for pair in pairs}
    for section_id, index in first_modified_changes(changes).items():
        pair = by_id.get(section_id)
        if pair is not None and compare_section(pair) is TextComparison.SAME:
            yield unchanged_text_feedback(section_id, index)


def first_modified_changes(changes: Sequence[Change]) -> dict[str, int]:
    """Section ID -> index of its first modified Change: one issue per Section."""
    modified: dict[str, int] = {}
    for index, change in enumerate(changes):
        if change.change_type is ChangeType.MODIFIED:
            modified.setdefault(change.section_id, index)
    return modified


def compare_section(pair: SectionPair) -> TextComparison:
    """Same, different or unknown text, tolerating a quoted restatement of the Section."""
    plain = compare_texts(pair.original, pair.amendment, pair.original_open, pair.amendment_open)
    if plain is not TextComparison.DIFFERENT or not pair.amendment.startswith('"'):
        return plain
    body = unwrap_restatement(pair)
    if body is None:
        return TextComparison.UNKNOWN
    # The closing quote bounds the body, so whatever followed it is no longer compared.
    return compare_texts(pair.original, body, pair.original_open, amendment_open=False)


# After a quoted restatement: its closing quote, optional punctuation and, at most, the bare
# enumerator of the next instruction ("segundo.", "2."). Anything else may change the Section.
CLOSED_RESTATEMENT_TAIL = re.compile(r'"([.,;:]?)(?: \w+[.)])?')
# Or the following instructions, when every one of them names only other Sections.
FOLLOWING_INSTRUCTION = re.compile(r'"([.,;:]?) (.+)')
# Instruction enumerators survive normalization ("segundo.", "décimo primero.", "3."). No list of
# them existed, so this is the minimal one: Spanish ordinal stems up to twelfth, plus numerals.
ORDINAL_WORD = (
    r"(?:primer|segund|tercer|cuart|quint|sext|s[eé]ptim|octav|noven|d[eé]cim"
    r"|und[eé]cim|duod[eé]cim)[oa]"
)
# An enumerator opens an instruction only at the start or after a sentence or a closing quote,
# so "la cláusula segunda." inside a sentence does not split it.
INSTRUCTION_START = re.compile(rf'(?:^|(?<=[.:;"] ))(?:\d+|(?:{ORDINAL_WORD} )?{ORDINAL_WORD})\. ')


def unwrap_restatement(pair: SectionPair) -> str | None:
    """The quoted text without its quotes and repeated heading; None if anything unsafe follows.

    The first closing quote with a safe tail wins: a quote inside the body leaves the body short,
    so it can only compare as different, never as the same text.
    """
    inner = pair.amendment.removeprefix('"').strip().removeprefix(pair.heading).strip()
    for quote in re.finditer('"', inner):
        tail = inner[quote.start() :]
        closed = CLOSED_RESTATEMENT_TAIL.fullmatch(tail)
        following = FOLLOWING_INSTRUCTION.fullmatch(tail)
        if closed is None and following is not None and names_only_other_sections(pair, following):
            closed = following
        if closed is not None:
            # '"texto".' is the usual Spanish order: the period ends the restated text.
            return inner[: quote.start()].strip() + closed.group(1)
    return None


def names_only_other_sections(pair: SectionPair, following: re.Match[str]) -> bool:
    """Every following instruction names Sections, all of them map rows other than this one.

    Text before the first enumerator, an instruction naming none, this one or one the map cannot
    resolve, or a Delta Marker after an instruction's last named Section is unknown: it may
    change this Section. The closing formula stays inside the last instruction (decision 99).
    """
    text = following.group(2)
    starts = [match.start() for match in INSTRUCTION_START.finditer(text)]
    if not starts or starts[0] != 0:
        return False
    chunks = [text[begin:end] for begin, end in zip(starts, [*starts[1:], len(text)], strict=True)]
    return all(names_only_others(pair, chunk) for chunk in chunks)


def names_only_others(pair: SectionPair, chunk: str) -> bool:
    keywords = {label.split()[0] for label in pair.labels.values()}
    if not keywords:
        return False
    mentions = re.finditer("|".join(rf"\b{re.escape(word)}" for word in keywords), chunk)
    named = [resolve_section_label(chunk, mention.start(), pair.labels) for mention in mentions]
    resolved = [found for found in named if found is not None]
    if not named or len(resolved) < len(named):
        return False
    if any(section_id == pair.section_id for section_id, _ in resolved):
        return False
    # Past the last named Section: the closing formula, or an unnumbered instruction to reject.
    remainder = chunk[max(end for _, end in resolved) :]
    return not any(marker.search(remainder) for marker in DELTA_MARKERS)


def resolve_section_label(
    text: str, position: int, labels: Mapping[str, str]
) -> tuple[str, int] | None:
    """The row whose label is written at position and where it ends; None if none or tied."""
    found = [
        (len(label), sid)
        for sid, label in labels.items()
        if text.startswith(label, position) and not is_word_char(text, position + len(label))
    ]
    longest = max((length for length, _ in found), default=0)
    winners = [sid for length, sid in found if length == longest]
    return (winners[0], position + longest) if len(winners) == 1 else None


def is_word_char(text: str, position: int) -> bool:
    return position < len(text) and text[position].isalnum()


# A Section Label is a ref's leading words before its title separator: "cláusula quinta" in
# "Cláusula Quinta — Depósito.". The map's own refs are the vocabulary; no ordinal list.
LABEL_END = re.compile(r"\s-|[.:,;]")
LABEL_MIN_WORDS = 2  # a keyword and a number


def extract_section_labels(context_map: ContextMap) -> dict[str, str]:
    """Labels of the original refs only: a delta amendment_ref is the instruction, not a label.

    A label needs a keyword and a number ("cláusula quinta", "artículo v"); a bare "5" could
    match any figure, so such a row stays unresolvable and its mentions stay unknown.
    """
    labels: dict[str, str] = {}
    for section in context_map.sections:
        ref = normalize_for_grounding(section.original_ref or "")
        label = LABEL_END.split(ref, maxsplit=1)[0].strip()
        if len(label.split()) >= LABEL_MIN_WORDS:
            labels[section.section_id] = label
    return labels


def compare_texts(
    original: str, amendment: str, original_open: bool, amendment_open: bool
) -> TextComparison:
    """Word comparison where Illegible Marks and text past the last Section prove nothing.

    An open segment runs to the end of its document, so it may carry closing, signature or
    date text after the Section: only a difference that closing text cannot explain counts.
    """
    old, new = TOKEN.findall(original), TOKEN.findall(amendment)
    if without_marks(old) == without_marks(new):
        return TextComparison.SAME
    # Both open: any difference may sit in the closing text, so none is evidence.
    if original_open and amendment_open:
        return TextComparison.UNKNOWN
    opcodes = SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
    if opcodes and is_open_tail(opcodes[-1][0], original_open, amendment_open):
        opcodes = opcodes[:-1]
    unexplained = [
        (old[i1:i2], new[j1:j2])
        for tag, i1, i2, j1, j2 in opcodes
        if tag != "equal" and not is_illegible_run(old[i1:i2], new[j1:j2])
    ]
    return TextComparison.DIFFERENT if unexplained else TextComparison.UNKNOWN


def is_open_tail(tag: str, original_open: bool, amendment_open: bool) -> bool:
    """Trailing words only the open side has: closing text or the Section's own end, unknown."""
    return (tag == "delete" and original_open) or (tag == "insert" and amendment_open)


def without_marks(tokens: Sequence[str]) -> list[str]:
    return [token for token in tokens if token != NORMALIZED_ILLEGIBLE_MARK]


def is_illegible_run(old: Sequence[str], new: Sequence[str]) -> bool:
    """One side is only Illegible Marks, each standing for a short run of the other's words."""
    for marks, other in ((old, new), (new, old)):
        if marks and not without_marks(marks):
            words = [token for token in without_marks(other) if token[0].isalnum()]
            return len(words) <= ILLEGIBLE_RUN_LIMIT * len(marks)
    return False


def unchanged_text_feedback(section_id: str, index: int) -> RetryFeedback:
    # Canonical ID only: the identical text is the model's own Quote, no need to repeat it.
    message = (
        f"section {section_id} is reported as modified, but its text is identical in the "
        "original contract and the amendment"
    )
    detail = f"{message}; a Section whose text does not change is not a Change, so drop it"
    return build_feedback("unchanged_text_reported", f"changes[{index}]", message, detail)


def drop_illegible_marks(segment: str) -> str:
    # An Illegible Mark is the vision model admitting it could not read, not a textual change.
    return " ".join(segment.replace(NORMALIZED_ILLEGIBLE_MARK, " ").split())


def describe_text_difference(section_id: str, original: str, amendment: str) -> str:
    """The differing words as 'old' -> 'new'; past the limit, the Section ID alone."""
    old_words, new_words = original.split(), amendment.split()
    matcher = SequenceMatcher(None, old_words, new_words, autojunk=False)
    edits = ", ".join(
        f"{' '.join(old_words[i1:i2])!r} -> {' '.join(new_words[j1:j2])!r}"
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    )
    if len(edits) > TEXT_DIFFERENCE_QUOTE_LIMIT:
        return f"section {section_id}"
    return f"section {section_id}: {edits}"


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


def compute_kind_agreement(kind: AmendmentKind, heuristic: KindHeuristic) -> int | None:
    """1 or 0 only when the heuristic saw Delta markers; inconclusive is N/A (decision 47)."""
    if heuristic is KindHeuristic.INCONCLUSIVE:
        return None
    return int(kind is AmendmentKind.DELTA)


def compute_new_section_gap(context_map: ContextMap) -> int | None:
    """1 when numbered delta new: Sections do not continue the original numbering, else 0.

    Soft (decision 45): a legitimate Amendment may skip numbers, but a gap is how an invented
    Section for an Unresolved Reference tends to look. Positional and sub-section new: IDs
    carry no comparable number and are skipped; N/A when nothing is left to compare.
    """
    if context_map.amendment_kind is not AmendmentKind.DELTA:
        return None
    parts = [parse_section_id(section.section_id) for section in context_map.sections]
    numbered = [p for p in parts if not p.is_positional]
    original_max = max((p.numbers[0] for p in numbered if not p.is_new), default=None)
    added = [p.numbers[0] for p in numbered if p.is_new and len(p.numbers) == 1]
    if original_max is None or not added:
        return None
    contiguous = list(range(original_max + 1, original_max + 1 + len(added)))
    return int(added != contiguous)


def compute_other_topic_rate(changes: Sequence[Change]) -> float | None:
    """Share of Changes whose Topic is other; N/A without Changes (decision 47)."""
    if not changes:
        return None
    return sum(change.topic is Topic.OTHER for change in changes) / len(changes)


# A Section counts as changed below this ratio; calibrated with the calibration Split (T9),
# never an environment variable (decision 20).
DIFF_RATIO_THRESHOLD = 0.97


@dataclass(frozen=True)
class DiffAgreement:
    """Jaccard of difflib's and the ExtractionAgent's changed Sections, with its coverage.

    `value` is None (N/A) when no Section could be segmented on both sides; never 0.
    """

    value: float | None
    sections_diffed: int
    sections_total: int


def is_changed_by_diff(original_segment: str, amendment_segment: str) -> bool:
    # autojunk off: its popularity heuristic would skew the ratio of longer Sections.
    matcher = SequenceMatcher(None, original_segment, amendment_segment, autojunk=False)
    return matcher.ratio() < DIFF_RATIO_THRESHOLD


def compute_diff_agreement(
    context_map: ContextMap,
    sections_changed: Sequence[str],
    original_text: str,
    amendment_text: str,
) -> DiffAgreement | None:
    """Soft signal for Restated only (§2.5); Delta has nothing to diff, so it is N/A."""
    # Validated maps passed check_refs_grounded, so every ref is found: coverage only drops
    # on ambiguous or colliding refs and empty segments.
    if context_map.amendment_kind is not AmendmentKind.RESTATED:
        return None
    segments = segment_both_documents(context_map, original_text, amendment_text)
    original, amendment = (document.texts for document in segments)
    sections = context_map.sections
    paired = [s.section_id for s in sections if s.original_ref and s.amendment_ref]
    diffed = [
        section_id for section_id in paired if section_id in original and section_id in amendment
    ]
    by_diff = {sid for sid in diffed if is_changed_by_diff(original[sid], amendment[sid])}
    by_agent = set(sections_changed) & set(diffed)
    value = jaccard(by_diff, by_agent) if diffed else None
    return DiffAgreement(value, len(diffed), len(paired))


def segment_document(refs: Mapping[str, str | None], text: str) -> DocumentSegments:
    """Each located ref's text after its heading, up to the next ref occurrence of any row.

    A ref found once, at a position no other ref shares, and followed by text is segmentable;
    every other ref is left out and lowers coverage, but all its occurrences still cut.
    """
    normalized = normalize_for_grounding(text)
    needles = {
        sid: needle for sid, ref in refs.items() if ref and (needle := normalize_for_grounding(ref))
    }
    occurrences = {sid: find_occurrences(needle, normalized) for sid, needle in needles.items()}
    cuts = sorted({position for found in occurrences.values() for position in found})
    shared = Counter(found[0] for found in occurrences.values() if len(found) == 1)
    starts = {
        sid: found[0] + len(needles[sid])
        for sid, found in occurrences.items()
        if len(found) == 1 and shared[found[0]] == 1
    }
    texts = {
        sid: segment
        for sid, start in starts.items()
        if (segment := cut_segment(normalized, start, cuts))
    }
    open_ended = next((sid for sid in texts if all(cut < starts[sid] for cut in cuts)), None)
    return DocumentSegments(texts, open_ended)


def find_occurrences(needle: str, normalized_text: str) -> list[int]:
    return [match.start() for match in re.finditer(re.escape(needle), normalized_text)]


def cut_segment(normalized_text: str, start: int, cuts: Sequence[int]) -> str:
    """Up to the next ref occurrence at or after start, stripped; adjacent refs leave it empty."""
    end = next((cut for cut in cuts if cut >= start), len(normalized_text))
    return normalized_text[start:end].strip()


def jaccard(left: set[str], right: set[str]) -> float:
    """1.0 when both sets are empty: agreeing that nothing changed is agreement."""
    union = left | right
    return len(left & right) / len(union) if union else 1.0
