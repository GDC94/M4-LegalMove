"""Deterministic Checks: map and extraction rules, grounding, Kind Heuristic, soft signals."""

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from errors import RetryFeedback, ValidationIssue
from models import (
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
from section_comparison import (
    DELTA_MARKERS,
    SectionPair,
    TextComparison,
    compare_section,
    describe_text_difference,
    drop_illegible_marks,
    pair_sections,
    segment_both_documents,
)
from text_normalization import normalize_for_grounding


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
        *check_number_parentheticals(result, original_text, amendment_text),
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


def unchanged_text_feedback(section_id: str, index: int) -> RetryFeedback:
    # Canonical ID only: the identical text is the model's own Quote, no need to repeat it.
    message = (
        f"section {section_id} is reported as modified, but its text is identical in the "
        "original contract and the amendment"
    )
    detail = f"{message}; a Section whose text does not change is not a Change, so drop it"
    return build_feedback("unchanged_text_reported", f"changes[{index}]", message, detail)


# Spanish cardinal words the literal number rule (decision 95) writes before "(N)". A closed
# lexicon, so "Anexo (1) del" or "Cláusula (2)" never count as a number written in words.
CARDINAL_WORDS = frozenset(
    [
        "uno",
        "un",
        "una",
        "dos",
        "tres",
        "cuatro",
        "cinco",
        "seis",
        "siete",
        "ocho",
        "nueve",
        "diez",
        "once",
        "doce",
        "trece",
        "catorce",
        "quince",
        "dieciséis",
        "diecisiete",
        "dieciocho",
        "diecinueve",
        "veinte",
        "veintiuno",
        "veintiún",
        "veintiuna",
        "veintidós",
        "veintitrés",
        "veinticuatro",
        "veinticinco",
        "veintiséis",
        "veintisiete",
        "veintiocho",
        "veintinueve",
        "treinta",
        "cuarenta",
        "cincuenta",
        "sesenta",
        "setenta",
        "ochenta",
        "noventa",
        "cien",
        "ciento",
        "doscientos",
        "doscientas",
        "trescientos",
        "trescientas",
        "cuatrocientos",
        "cuatrocientas",
        "quinientos",
        "quinientas",
        "seiscientos",
        "seiscientas",
        "setecientos",
        "setecientas",
        "ochocientos",
        "ochocientas",
        "novecientos",
        "novecientas",
        "mil",
        "millón",
        "millones",
    ]
)
# A number written in words is the maximal run of cardinal words, joined directly or by these
# connectors ("cuarenta y cinco", "veinte por ciento", "un mil"); the run starts and ends with a
# cardinal word.
CARDINAL_CONNECTORS = frozenset({"y", "por"})
# Alone, these are usually articles ("una vez firmada"), so a run made only of one never counts.
ARTICLE_CARDINALS = frozenset({"un", "una", "uno"})
NUMERAL = re.compile(r"\(\s*\d[\d.,]*\s*%?\s*\)")
NUMBER_TOKEN = re.compile(rf"{NUMERAL.pattern}|\w+|[^\w\s]")
WORD = re.compile(r"\w+")


@dataclass(frozen=True)
class NumberPhrase:
    """A cardinal run, its "(N)" if the text gives one, and the word that follows them."""

    words: str
    numeral: str | None
    following: str

    @property
    def bare(self) -> str:
        return f"{self.words} {self.following}"


def check_number_parentheticals(
    result: ExtractionResult, original_text: str, amendment_text: str
) -> Iterator[RetryFeedback]:
    """A number the documents write as "dos (2)" keeps its "(2)" in the output (decision 95).

    "dos (2) trimestres" in either document fires on an output that writes "dos trimestres",
    unless a document also writes "dos trimestres" itself.
    """
    documents = [normalize_for_grounding(text) for text in (original_text, amendment_text)]
    numbers = find_number_parentheticals(documents)
    if not numbers:
        return
    fields = [("summary_of_the_change", result.summary_of_the_change)]
    fields += [(f"changes[{i}].description", c.description) for i, c in enumerate(result.changes)]
    for path, text in fields:
        written = bare_numbers(normalize_for_grounding(text))
        for phrase in numbers.values():
            if phrase.bare in written:
                yield number_parenthetical_feedback(path, phrase)


def find_number_parentheticals(documents: Sequence[str]) -> dict[str, NumberPhrase]:
    """Bare form -> its "(N)" phrase, skipping a bare form the documents also write as is."""
    phrases = [phrase for document in documents for phrase in find_number_phrases(document)]
    written_bare = {phrase.bare for phrase in phrases if phrase.numeral is None}
    numbers: dict[str, NumberPhrase] = {}
    for phrase in phrases:
        counts = phrase.numeral is not None and phrase.words not in ARTICLE_CARDINALS
        if counts and phrase.bare not in written_bare:
            numbers.setdefault(phrase.bare, phrase)
    return numbers


def bare_numbers(normalized_text: str) -> set[str]:
    return {phrase.bare for phrase in find_number_phrases(normalized_text) if not phrase.numeral}


def find_number_phrases(normalized_text: str) -> Iterator[NumberPhrase]:
    tokens = NUMBER_TOKEN.findall(normalized_text)
    index = 0
    while index < len(tokens):
        if tokens[index] not in CARDINAL_WORDS:
            index += 1
            continue
        end = extend_cardinal_run(tokens, index)
        phrase = read_number_phrase(tokens, index, end)
        if phrase is not None:
            yield phrase
        index = end


def extend_cardinal_run(tokens: Sequence[str], start: int) -> int:
    """The exclusive end of the maximal cardinal run that starts at start."""
    end = start + 1
    while True:
        if is_cardinal_at(tokens, end):
            end += 1
        elif (
            end < len(tokens)
            and tokens[end] in CARDINAL_CONNECTORS
            and is_cardinal_at(tokens, end + 1)
        ):
            end += 2
        else:
            return end


def is_cardinal_at(tokens: Sequence[str], index: int) -> bool:
    return index < len(tokens) and tokens[index] in CARDINAL_WORDS


def read_number_phrase(tokens: Sequence[str], start: int, end: int) -> NumberPhrase | None:
    """The run plus its "(N)" and the next word; "(18)." or "(18) [illegible]" have none."""
    numeral = tokens[end] if end < len(tokens) and NUMERAL.fullmatch(tokens[end]) else None
    following = end + 1 if numeral else end
    if following >= len(tokens) or not WORD.fullmatch(tokens[following]):
        return None
    return NumberPhrase(" ".join(tokens[start:end]), numeral, tokens[following])


def number_parenthetical_feedback(path: str, phrase: NumberPhrase) -> RetryFeedback:
    # The public message names the field only; the detail quotes just the number and its noun.
    number = f"{phrase.words} {phrase.numeral}"
    message = (
        f"{path} writes a number in words without the numeral in parentheses "
        "that the documents give it"
    )
    detail = (
        f"{message}; keep the number as {number!r}: "
        f"write {f'{number} {phrase.following}'!r}, not {phrase.bare!r}"
    )
    return build_feedback("number_parenthetical_dropped", path, message, detail)


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


def jaccard(left: set[str], right: set[str]) -> float:
    """1.0 when both sets are empty: agreeing that nothing changed is agreement."""
    union = left | right
    return len(left & right) / len(union) if union else 1.0
