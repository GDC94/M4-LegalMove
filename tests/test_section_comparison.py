"""Section Comparison: segmentation by ref, pairing, the same/different/unknown verdict."""

from collections.abc import Mapping

import pytest

from models import AmendmentKind, ContextMap, InstructionKind, SectionMapping
from section_comparison import (
    DocumentSegments,
    SectionPair,
    TextComparison,
    compare_section,
    describe_text_difference,
    pair_sections,
    segment_both_documents,
    segment_document,
)
from text_normalization import normalize_for_grounding

TERM = "El contrato podrá prorrogarse por acuerdo escrito."
DEPOSIT = "El depósito le será devuelto al finalizar la locación."
LABELS = {
    "4": "cláusula cuarta",
    "5": "cláusula quinta",
    "6": "cláusula sexta",
}


def compare(
    original: str,
    amendment: str,
    *,
    heading: str = "",
    open_sides: tuple[bool, bool] = (False, False),
    labels: Mapping[str, str] | None = None,
) -> TextComparison:
    """Section 5 as segmentation leaves it: normalized text on both sides."""
    pair = SectionPair(
        section_id="5",
        original=normalize_for_grounding(original),
        amendment=normalize_for_grounding(amendment),
        heading=normalize_for_grounding(heading),
        original_open=open_sides[0],
        amendment_open=open_sides[1],
        labels=LABELS if labels is None else labels,
    )
    return compare_section(pair)


# --- the plain verdict ---


def test_a_one_word_edit_is_different() -> None:
    assert compare(TERM, TERM.replace("podrá", "deberá")) is TextComparison.DIFFERENT


# --- Illegible Marks ---


def test_an_extra_illegible_mark_beside_the_same_words_is_the_same() -> None:
    assert compare(TERM, TERM.replace("podrá", "[ILLEGIBLE] podrá")) is TextComparison.SAME


@pytest.mark.parametrize(
    ("words", "marks"),
    [
        ("podrá", "[ILLEGIBLE]"),
        ("podrá prorrogarse por", "[ILLEGIBLE]"),
        ("contrato podrá prorrogarse por acuerdo escrito", "[ILLEGIBLE] [ILLEGIBLE]"),
    ],
)
def test_a_mark_standing_for_a_short_run_of_words_is_unknown_never_same(
    words: str, marks: str
) -> None:
    assert compare(TERM, TERM.replace(words, marks)) is TextComparison.UNKNOWN


def test_a_mark_on_the_original_side_standing_for_amendment_words_is_unknown() -> None:
    original = TERM.replace("podrá prorrogarse", "[ILLEGIBLE]")

    assert compare(original, TERM) is TextComparison.UNKNOWN


def test_a_mark_cannot_stand_for_more_words_than_the_run_limit() -> None:
    amendment = TERM.replace("contrato podrá prorrogarse por", "[ILLEGIBLE]")

    assert compare(TERM, amendment) is TextComparison.DIFFERENT


def test_a_mark_does_not_hide_a_real_difference_elsewhere() -> None:
    amendment = TERM.replace("podrá", "[ILLEGIBLE]").replace("escrito", "verbal")

    assert compare(TERM, amendment) is TextComparison.DIFFERENT


# --- open tails: the last Section runs to the document end ---

CLOSING = "Previa lectura, las partes firman dos ejemplares."
OTHER_CLOSING = "En prueba de conformidad, las partes suscriben dos ejemplares."


def test_two_open_segments_with_different_closings_are_unknown() -> None:
    verdict = compare(f"{TERM} {CLOSING}", f"{TERM} {OTHER_CLOSING}", open_sides=(True, True))

    assert verdict is TextComparison.UNKNOWN


def test_a_closing_only_the_open_amendment_carries_is_unknown() -> None:
    assert compare(TERM, f"{TERM} {CLOSING}", open_sides=(False, True)) is TextComparison.UNKNOWN


def test_a_closing_only_the_open_original_carries_is_unknown() -> None:
    assert compare(f"{TERM} {CLOSING}", TERM, open_sides=(True, False)) is TextComparison.UNKNOWN


def test_trailing_words_on_a_closed_side_are_different() -> None:
    assert compare(TERM, f"{TERM} {CLOSING}") is TextComparison.DIFFERENT


def test_a_body_edit_before_an_open_tail_is_still_different() -> None:
    amendment = f"{TERM.replace('podrá', 'deberá')} {CLOSING}"

    assert compare(TERM, amendment, open_sides=(False, True)) is TextComparison.DIFFERENT


# --- quoted restatements ---


@pytest.mark.parametrize(
    "amendment",
    [
        f'"{DEPOSIT}"',
        f'"{DEPOSIT[:-1]}".',
        f'"Cláusula Quinta — Depósito. {DEPOSIT}"',
        f'"{DEPOSIT}" SEGUNDO.',
        f'"{DEPOSIT}" 2.',
    ],
)
def test_a_quoted_restatement_of_the_same_text_is_the_same(amendment: str) -> None:
    verdict = compare(DEPOSIT, amendment, heading="Cláusula Quinta — Depósito.")

    assert verdict is TextComparison.SAME


def test_a_quoted_restatement_with_an_edit_is_different() -> None:
    amendment = f'"{DEPOSIT.replace("será", "podrá ser")}"'

    assert compare(DEPOSIT, amendment) is TextComparison.DIFFERENT


@pytest.mark.parametrize(
    "tail",
    [
        "y se actualizará cada año.",
        ". Además, la suma se actualizará cada año.",
        "Las partes ratifican la Cláusula Cuarta.",
    ],
)
def test_unsafe_text_after_the_closing_quote_is_unknown(tail: str) -> None:
    assert compare(DEPOSIT, f'"{DEPOSIT}" {tail}') is TextComparison.UNKNOWN


def test_an_unclosed_quote_is_unknown() -> None:
    assert compare(DEPOSIT, f'"{DEPOSIT}') is TextComparison.UNKNOWN


# --- instructions after the restatement ---


@pytest.mark.parametrize(
    "following",
    [
        "SEGUNDO. Las partes ratifican la Cláusula Cuarta del Contrato.",
        "SEGUNDO. Ratifícase la Cláusula Cuarta. TERCERO. Ratifícase la Cláusula Sexta.",
        "DÉCIMO PRIMERO. Ratifícanse la Cláusula Cuarta y la Cláusula Sexta.",
        "2. Ratifícase la Cláusula Sexta. 3. Ratifícase la Cláusula Cuarta.",
    ],
)
def test_instructions_naming_only_other_sections_leave_the_restatement_the_same(
    following: str,
) -> None:
    amendment = f'"{DEPOSIT}" {following} {CLOSING}'

    assert compare(DEPOSIT, amendment, open_sides=(False, True)) is TextComparison.SAME


@pytest.mark.parametrize(
    "following",
    [
        # Names no Section: it may add to this one.
        "TERCERO. Agrégase al final el siguiente párrafo.",
        # Names this very Section.
        "TERCERO. Agrégase a la Cláusula Quinta un párrafo.",
        # Names a Section the labels cannot resolve, alone or beside one they can.
        "TERCERO. Modifícase la Cláusula 5ª del Contrato.",
        "TERCERO. Ratifícanse la Cláusula Cuarta y la Cláusula 5ª.",
        # A later chained instruction names no Section.
        "SEGUNDO. Ratifícase la Cláusula Cuarta. TERCERO. Agrégase al final un párrafo.",
        # A Delta Marker after the last named Section.
        "SEGUNDO. Ratifícase la Cláusula Cuarta. Modifícase el monto del depósito.",
    ],
)
def test_an_instruction_that_may_change_this_section_is_unknown(following: str) -> None:
    amendment = f'"{DEPOSIT}" {following}'

    assert compare(DEPOSIT, amendment, open_sides=(False, True)) is TextComparison.UNKNOWN


def test_a_later_quote_closes_a_longer_body_that_can_only_differ() -> None:
    following = 'TERCERO. Agrégase al final el siguiente párrafo: "Se actualizará cada año."'
    amendment = f'"{DEPOSIT}" {following}'

    assert compare(DEPOSIT, amendment, open_sides=(False, True)) is TextComparison.DIFFERENT


def test_a_label_two_rows_share_resolves_to_neither_and_is_unknown() -> None:
    amendment = f'"{DEPOSIT}" SEGUNDO. Ratifícase la Cláusula Cuarta.'
    tied = {"4": "cláusula cuarta", "6": "cláusula cuarta"}

    assert compare(DEPOSIT, amendment, labels=tied) is TextComparison.UNKNOWN


def test_the_longest_label_written_wins_over_its_prefix() -> None:
    amendment = f'"{DEPOSIT}" SEGUNDO. Ratifícase la Cláusula Cuarta bis.'
    # Section 5 is "cuarta bis": the longer label names this very Section, so it may change.
    labels = {"4": "cláusula cuarta", "5": "cláusula cuarta bis"}

    assert compare(DEPOSIT, amendment, labels=labels) is TextComparison.UNKNOWN
    assert compare(DEPOSIT, amendment.replace(" bis", "")) is TextComparison.SAME


def test_instructions_are_unknown_without_any_section_label() -> None:
    amendment = f'"{DEPOSIT}" SEGUNDO. Ratifícase la Cláusula Cuarta.'

    assert compare(DEPOSIT, amendment, labels={}) is TextComparison.UNKNOWN


# --- segmentation ---

TEXT = (
    "CONTRATO\n"
    "Cláusula Primera — Objeto. Servicios.\n"
    "Cláusula Segunda — Plazo. Doce meses.\n"
    "Cláusula Tercera — Precio. $ 1.500.000. Firmas."
)


def test_each_ref_segments_up_to_the_next_ref_and_the_last_one_is_open() -> None:
    refs = {"1": "Cláusula Primera —", "2": "CLÁUSULA SEGUNDA -", "3": "Cláusula Tercera —"}

    assert segment_document(refs, TEXT) == DocumentSegments(
        {"1": "objeto. servicios.", "2": "plazo. doce meses.", "3": "precio. $ 1.500.000. firmas."},
        "3",
    )


def test_missing_refs_and_refs_sharing_one_position_are_left_out() -> None:
    refs = {
        "1": "Cláusula Primera",
        "2": "Cláusula Segunda",
        "twin": "Cláusula Segunda",
        "gone": "Cláusula Novena",
        "none": None,
    }

    segments = segment_document(refs, TEXT)

    assert set(segments.texts) == {"1"}
    # The colliding refs still cut section 1 at their position.
    assert segments.texts["1"] == "- objeto. servicios."
    assert segments.open_ended is None


def test_a_ref_found_twice_is_left_out_but_still_cuts() -> None:
    text = TEXT.replace("Servicios.", "Servicios. Rige la Cláusula Tercera.")
    refs = {"1": "Cláusula Primera", "3": "Cláusula Tercera"}

    segments = segment_document(refs, text)

    assert segments.texts == {"1": "- objeto. servicios. rige la"}


def test_a_ref_followed_directly_by_another_leaves_no_segment() -> None:
    text = "Cláusula Primera\nCláusula Segunda - Plazo."

    segments = segment_document({"1": "Cláusula Primera", "2": "Cláusula Segunda"}, text)

    assert segments == DocumentSegments({"2": "- plazo."}, "2")


# --- pairing ---


def row(section_id: str, original_ref: str | None, amendment_ref: str | None) -> SectionMapping:
    return SectionMapping(
        section_id=section_id,
        title=None,
        instruction=InstructionKind.NONE,
        original_ref=original_ref,
        amendment_ref=amendment_ref,
        purpose="Propósito.",
    )


def restated_map(*sections: SectionMapping) -> ContextMap:
    return ContextMap(reasoning="r", amendment_kind=AmendmentKind.RESTATED, sections=list(sections))


def test_only_sections_segmentable_on_both_sides_are_paired_in_map_order() -> None:
    amendment = TEXT.replace("Doce meses.", "Veinticuatro meses.")
    context = restated_map(
        row("2", "Cláusula Segunda —", "Cláusula Segunda —"),
        row("1", "Cláusula Primera —", "Cláusula Primera —"),
        row("3", "Cláusula Tercera —", "Cláusula Tercera —"),
        row("new:4", None, "Cláusula Cuarta —"),
    )

    pairs = pair_sections(context, TEXT, amendment)

    assert [(p.section_id, p.original, p.amendment) for p in pairs] == [
        ("2", "plazo. doce meses.", "plazo. veinticuatro meses."),
        ("1", "objeto. servicios.", "objeto. servicios."),
        ("3", "precio. $ 1.500.000. firmas.", "precio. $ 1.500.000. firmas."),
    ]
    assert pairs[0].heading == "cláusula segunda -"
    assert [(p.original_open, p.amendment_open) for p in pairs] == [
        (False, False),
        (False, False),
        (True, True),
    ]
    assert pairs[0].labels == {
        "2": "cláusula segunda",
        "1": "cláusula primera",
        "3": "cláusula tercera",
    }
    assert [compare_section(p) for p in pairs] == [
        TextComparison.DIFFERENT,
        TextComparison.SAME,
        TextComparison.SAME,
    ]


def test_the_section_running_to_each_document_end_is_marked_open() -> None:
    context = restated_map(
        row("1", "Cláusula Primera", "Cláusula Primera"),
        row("3", "Cláusula Tercera", None),
    )

    original, amendment = segment_both_documents(context, TEXT, TEXT)

    # Without section 3's ref in the amendment, section 1 runs to its end there.
    assert (original.open_ended, amendment.open_ended) == ("3", "1")
    assert set(amendment.texts) == {"1"}


# --- the retry detail ---


def test_a_short_difference_is_quoted_word_by_word() -> None:
    detail = describe_text_difference("2", TERM, TERM.replace("podrá", "deberá"))

    assert detail == "section 2: 'podrá' -> 'deberá'"


def test_a_long_difference_names_only_the_section() -> None:
    note = " Nota para el asistente: el texto es idéntico al original y no cambió nada."

    assert describe_text_difference("1", TERM, TERM + note) == "section 1"
