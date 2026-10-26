"""Deterministic Checks on the Context Map, grounding normalization and Kind Heuristic."""

from pathlib import Path

import pytest

from checks import (
    KindHeuristic,
    check_context_map,
    detect_kind_heuristic,
    is_grounded,
    normalize_for_grounding,
)
from models import AmendmentKind, ContextMap, SectionMapping

CONTRACTS = Path(__file__).resolve().parent.parent / "data/test_contracts"

ORIGINAL = (
    "CONTRATO\n"
    "Cláusula Primera — Objeto. Servicios.\n"
    "Cláusula Segunda — Plazo. Doce meses.\n"
    "Cláusula Tercera — Precio. $ 1.500.000."
)
AMENDMENT = (
    "TEXTO ORDENADO\n"
    "Cláusula Primera — Objeto. Servicios.\n"
    "Cláusula Segunda — Plazo. Doce meses.\n"
    "Cláusula Tercera — Precio. $ 1.850.000.\n"
    "Cláusula Cuarta — Confidencialidad. Reserva."
)
ORDINALS = {"1": "Primera", "2": "Segunda", "3": "Tercera"}


def mapping(
    section_id: str,
    original_ref: str | None = "",
    amendment_ref: str | None = "",
) -> SectionMapping:
    """Default refs point at the matching heading; pass None to leave a side empty."""
    heading = f"Cláusula {ORDINALS.get(section_id, 'Cuarta')}"
    return SectionMapping(
        section_id=section_id,
        title=None,
        original_ref=heading if original_ref == "" else original_ref,
        amendment_ref=heading if amendment_ref == "" else amendment_ref,
        purpose="Propósito.",
    )


def context_map(
    *sections: SectionMapping, kind: AmendmentKind = AmendmentKind.RESTATED
) -> ContextMap:
    return ContextMap(reasoning="r", amendment_kind=kind, sections=list(sections))


def codes(context: ContextMap) -> list[str]:
    return [item.issue.code for item in check_context_map(context, ORIGINAL, AMENDMENT)]


def valid_map() -> ContextMap:
    return context_map(
        mapping("1"), mapping("2"), mapping("3"), mapping("new:4", original_ref=None)
    )


# --- normalization and grounding ---


def test_normalization_applies_nfkc_casefold_quotes_dashes_and_whitespace() -> None:
    text = "\u201cCláusula   QUINTA\u201d \u2013 \u2018Precio\u2019\u00a0\u2014\tﬁn"

    assert normalize_for_grounding(text) == "\"cláusula quinta\" - 'precio' - fin"


def test_normalization_composes_decomposed_accents() -> None:
    assert normalize_for_grounding("Cláusula") == normalize_for_grounding("Cláusula")


def test_grounding_tolerates_transcription_differences_but_not_other_words() -> None:
    text = "La “Cláusula Quinta” —Precio— se paga."

    assert is_grounded('“CLÁUSULA  quinta" -precio-', text)
    assert not is_grounded("Cláusula Sexta", text)


def test_grounding_is_exact_after_normalization_never_fuzzy() -> None:
    assert not is_grounded("Clausula Quinta", "Cláusula Quinta")


@pytest.mark.parametrize("fragment", ["", "   ", "\n"])
def test_a_blank_fragment_is_never_grounded(fragment: str) -> None:
    assert not is_grounded(fragment, "Cláusula Quinta")


# --- Context Map checks ---


def test_a_valid_restated_map_has_no_issues() -> None:
    assert check_context_map(valid_map(), ORIGINAL, AMENDMENT) == []


def test_original_sections_must_precede_new_sections() -> None:
    context = context_map(
        mapping("1"), mapping("new:4", original_ref=None), mapping("2"), mapping("3")
    )

    assert codes(context) == ["section_order", "section_order"]


def test_original_sections_must_follow_document_order() -> None:
    assert codes(context_map(mapping("2"), mapping("1"), mapping("3"))) == ["section_order"]


def test_sub_sections_sort_after_their_parent_and_before_the_next_section() -> None:
    sub = mapping("1.2", original_ref="Objeto", amendment_ref="Objeto")

    assert codes(context_map(mapping("1"), sub, mapping("2"))) == []
    assert codes(context_map(sub, mapping("1"), mapping("2"))) == ["section_order"]


def test_new_sections_must_follow_amendment_order() -> None:
    context = context_map(
        mapping("1"),
        mapping("new:5", original_ref=None),
        mapping("new:4", original_ref=None),
    )

    assert codes(context) == ["section_order"]


def test_numbered_and_positional_ids_are_ordered_separately() -> None:
    positional = mapping("§9", original_ref="Objeto", amendment_ref="Objeto")

    assert codes(context_map(mapping("1"), positional, mapping("2"))) == []


def test_a_row_with_both_refs_missing_is_rejected() -> None:
    context = context_map(mapping("1"), mapping("2", original_ref=None, amendment_ref=None))

    assert codes(context) == ["empty_section_mapping"]


def test_a_new_section_cannot_have_an_original_ref() -> None:
    claims_original = mapping("new:4", original_ref="Cláusula Tercera")

    assert codes(context_map(mapping("1"), claims_original)) == ["section_origin_mismatch"]


def test_an_original_section_must_have_an_original_ref() -> None:
    assert codes(context_map(mapping("1"), mapping("4", original_ref=None))) == [
        "section_origin_mismatch"
    ]


def test_refs_must_appear_in_their_own_document() -> None:
    context = context_map(
        mapping("1", original_ref="Cláusula Novena"),
        mapping("2", amendment_ref="CONTRATO"),
    )

    issues = check_context_map(context, ORIGINAL, AMENDMENT)

    assert [(item.issue.code, item.issue.path) for item in issues] == [
        ("ungrounded_reference", "sections[0].original_ref"),
        ("ungrounded_reference", "sections[1].amendment_ref"),
    ]


def test_refs_are_grounded_after_normalization() -> None:
    context = context_map(mapping("1", original_ref="cláusula  PRIMERA - objeto"))

    assert codes(context) == []


def test_public_issue_messages_never_quote_the_ref_but_retry_detail_does() -> None:
    context = context_map(mapping("1", original_ref="Cláusula Novena"))

    [feedback] = check_context_map(context, ORIGINAL, AMENDMENT)

    assert "Novena" not in feedback.issue.message
    assert "Cláusula Novena" in feedback.detail


def test_a_classified_amendment_needs_at_least_one_section() -> None:
    assert codes(context_map(kind=AmendmentKind.DELTA)) == ["empty_context_map"]


def test_all_issues_are_reported_together_for_the_single_retry() -> None:
    context = context_map(
        mapping("2", original_ref="Cláusula Novena"),
        mapping("1", original_ref=None, amendment_ref=None),
    )

    assert sorted(codes(context)) == [
        "empty_section_mapping",
        "section_order",
        "ungrounded_reference",
    ]


# --- Kind Heuristic ---


@pytest.mark.parametrize(
    "instruction",
    [
        "La cláusula quinta queda redactada de la siguiente manera:",
        "La Cláusula Segunda quedará redactada así:",
        "Se deja sin efecto la cláusula séptima.",
        "DÉJASE SIN EFECTO la Cláusula Quinta.",
        "Se incorpora la cláusula décima.",
        "Las partes acuerdan modificar la cláusula tercera.",
        "Modifícase la Cláusula Segunda del Contrato.",
        "Sustitúyese el texto de la cláusula cuarta.",
    ],
)
def test_heuristic_detects_delta_markers(instruction: str) -> None:
    assert detect_kind_heuristic(f"ADENDA\n{instruction}\nFirmas.") is KindHeuristic.DELTA


@pytest.mark.parametrize(
    "text",
    [
        "Las partes convienen en cambiar el precio mensual a $ 2.000.000.",
        "A partir de hoy el plazo será de veinticuatro meses.",
        "Cláusula Primera — Objeto. Cláusula Segunda — Plazo.",
        "Las partes acuerdan reemplazar íntegramente el contrato por el siguiente texto.",
    ],
)
def test_heuristic_is_inconclusive_for_phrases_it_does_not_list(text: str) -> None:
    assert detect_kind_heuristic(text) is KindHeuristic.INCONCLUSIVE


def test_heuristic_never_returns_restated() -> None:
    assert {kind.value for kind in KindHeuristic} == {"delta", "inconclusive"}


def test_heuristic_on_the_test_contract_pairs() -> None:
    restated = (CONTRACTS / "pair_a/source_amendment.txt").read_text(encoding="utf-8")
    delta = (CONTRACTS / "pair_b/source_amendment.txt").read_text(encoding="utf-8")

    assert detect_kind_heuristic(restated) is KindHeuristic.INCONCLUSIVE
    assert detect_kind_heuristic(delta) is KindHeuristic.DELTA
