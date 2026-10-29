"""Section Comparison: segment both documents by ref, pair Sections and compare their text."""

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from models import ILLEGIBLE_MARK, ContextMap
from text_normalization import normalize_for_grounding

# Spanish change-instruction phrasing, matched on normalized text. These phrases must never
# appear in the ContextualizationAgent prompt, or both signals would fail together (plan §4.1).
# The Kind Heuristic in checks.py reads them too; they live here because the closing-tail rule
# needs them and checks imports this module, never the reverse.
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


def segment_both_documents(
    context_map: ContextMap, original_text: str, amendment_text: str
) -> tuple[DocumentSegments, DocumentSegments]:
    sections = context_map.sections
    original = segment_document({s.section_id: s.original_ref for s in sections}, original_text)
    amendment = segment_document({s.section_id: s.amendment_ref for s in sections}, amendment_text)
    return original, amendment


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
