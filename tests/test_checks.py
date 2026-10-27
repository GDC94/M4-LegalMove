"""Deterministic Checks: Context Map, extraction, grounding, Kind Heuristic and soft signals."""

from pathlib import Path

import pytest

from checks import (
    DIFF_RATIO_THRESHOLD,
    DiffAgreement,
    KindHeuristic,
    check_context_map,
    check_extraction,
    compute_diff_agreement,
    compute_grounding_rate,
    compute_kind_agreement,
    compute_other_topic_rate,
    detect_kind_heuristic,
    is_changed_by_diff,
    is_grounded,
    normalize_for_grounding,
)
from models import (
    NO_CHANGES,
    AmendmentKind,
    Change,
    ChangeType,
    ContextMap,
    ExtractionResult,
    SectionMapping,
    Topic,
)

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


# --- extraction checks (plan §2.5, §3.3, §4.4) ---

DELTA_AMENDMENT = (
    "ADENDA\n"
    "PRIMERO. Modifícase la Cláusula Tercera: Precio. $ 1.850.000.\n"
    "SEGUNDO. Déjase sin efecto la Cláusula Segunda."
)


def change(
    section_id: str,
    change_type: ChangeType,
    original_quote: str | None,
    amendment_quote: str | None,
    topic: Topic = Topic.PRICE_PAYMENT,
) -> Change:
    return Change(
        section_id=section_id,
        change_type=change_type,
        topic=topic,
        description="Descripción.",
        original_quote=original_quote,
        amendment_quote=amendment_quote,
    )


def price_change() -> Change:
    return change("3", ChangeType.MODIFIED, "$ 1.500.000", "$ 1.850.000")


def confidentiality_change() -> Change:
    return change(
        "new:4", ChangeType.ADDED, None, "Confidencialidad. Reserva.", Topic.CONFIDENTIALITY
    )


def extraction(*changes: Change) -> ExtractionResult:
    sections = list(dict.fromkeys(c.section_id for c in changes))
    topics = list(dict.fromkeys(c.topic.value for c in changes))
    return ExtractionResult(
        reasoning="r",
        changes=list(changes),
        sections_changed=sections,
        topics_touched=topics,
        summary_of_the_change="Resumen." if changes else NO_CHANGES,
    )


def delta_map() -> ContextMap:
    return context_map(
        mapping("2", amendment_ref="Déjase sin efecto la Cláusula Segunda"),
        mapping("3", amendment_ref="Modifícase la Cláusula Tercera"),
        kind=AmendmentKind.DELTA,
    )


def extraction_issues(
    result: ExtractionResult, context: ContextMap | None = None, amendment: str = AMENDMENT
) -> list[tuple[str, str]]:
    feedback = check_extraction(result, context or valid_map(), ORIGINAL, amendment)
    return [(item.issue.code, item.issue.path) for item in feedback]


def test_a_grounded_restated_extraction_has_no_issues() -> None:
    assert extraction_issues(extraction(price_change(), confidentiality_change())) == []


def test_a_change_must_reference_a_section_of_the_map() -> None:
    unknown = change("9", ChangeType.MODIFIED, "Precio", "Precio")

    issues = extraction_issues(extraction(price_change(), unknown, confidentiality_change()))

    assert ("unresolved_section_reference", "changes[1].section_id") in issues


def test_an_unresolved_reference_message_names_only_the_canonical_id() -> None:
    unknown = change("9", ChangeType.MODIFIED, "Precio", "Precio")

    [feedback] = [
        item
        for item in check_extraction(extraction(unknown), valid_map(), ORIGINAL, AMENDMENT)
        if item.issue.code == "unresolved_section_reference"
    ]

    assert feedback.issue.message == "section 9 is not in the context map"


def test_restated_removal_has_no_amendment_quote() -> None:
    removal = change("2", ChangeType.REMOVED, "Plazo. Doce meses.", "Plazo. Doce meses.")
    context = context_map(mapping("1"), mapping("2", amendment_ref=None), mapping("3"))

    assert extraction_issues(extraction(removal), context) == [
        ("removed_quote_mismatch", "changes[0].amendment_quote")
    ]


def test_delta_removal_quotes_the_instruction() -> None:
    quoted = change("2", ChangeType.REMOVED, "Plazo. Doce meses.", "Déjase sin efecto")
    unquoted = change("2", ChangeType.REMOVED, "Plazo. Doce meses.", None)

    assert extraction_issues(extraction(quoted), delta_map(), DELTA_AMENDMENT) == []
    assert extraction_issues(extraction(unquoted), delta_map(), DELTA_AMENDMENT) == [
        ("removed_quote_mismatch", "changes[0].amendment_quote")
    ]


def test_every_quote_must_be_grounded_in_its_own_document() -> None:
    invented = change("3", ChangeType.MODIFIED, "$ 1.850.000", "$ 9.999.999")

    assert extraction_issues(extraction(invented, confidentiality_change())) == [
        ("ungrounded_quote", "changes[0].original_quote"),
        ("ungrounded_quote", "changes[0].amendment_quote"),
    ]


def test_quotes_are_grounded_after_normalization() -> None:
    spaced = change("3", ChangeType.MODIFIED, "PRECIO.  $ 1.500.000", "precio. $ 1.850.000")

    assert extraction_issues(extraction(spaced, confidentiality_change())) == []


def test_ungrounded_quote_message_never_quotes_but_retry_detail_does() -> None:
    invented = change("3", ChangeType.MODIFIED, "monto secreto", "$ 1.850.000")

    [feedback] = check_extraction(
        extraction(invented, confidentiality_change()), valid_map(), ORIGINAL, AMENDMENT
    )

    assert "secreto" not in feedback.issue.message
    assert "monto secreto" in feedback.detail


def test_an_addition_targets_a_section_absent_from_the_original() -> None:
    wrong = change("3", ChangeType.ADDED, None, "$ 1.850.000")

    assert extraction_issues(extraction(wrong, confidentiality_change())) == [
        ("change_type_mismatch", "changes[0].change_type")
    ]


def test_a_new_section_can_only_be_added() -> None:
    wrong = change("new:4", ChangeType.MODIFIED, "Precio", "Confidencialidad. Reserva.")

    issues = extraction_issues(extraction(price_change(), wrong))

    assert ("change_type_mismatch", "changes[1].change_type") in issues


def test_restated_section_only_in_the_amendment_needs_its_addition() -> None:
    assert extraction_issues(extraction(price_change())) == [
        ("missing_structural_change", "changes")
    ]


def test_restated_section_only_in_the_original_needs_its_removal() -> None:
    context = context_map(mapping("1"), mapping("2", amendment_ref=None), mapping("3"))

    assert extraction_issues(extraction(price_change()), context) == [
        ("missing_structural_change", "changes")
    ]


def test_restated_modification_needs_the_section_on_both_sides() -> None:
    context = context_map(mapping("1"), mapping("2"), mapping("3", amendment_ref=None))
    removal = change("3", ChangeType.REMOVED, "$ 1.500.000", None)

    assert extraction_issues(extraction(price_change()), context) == [
        ("change_type_mismatch", "changes[0].change_type"),
        ("missing_structural_change", "changes"),
    ]
    assert extraction_issues(extraction(removal), context) == []


def test_restated_removal_needs_the_section_gone_from_the_amendment() -> None:
    removal = change("2", ChangeType.REMOVED, "Plazo. Doce meses.", None)

    issues = extraction_issues(extraction(removal, confidentiality_change()))

    assert issues == [("change_type_mismatch", "changes[0].change_type")]


def test_delta_has_no_structural_obligation() -> None:
    context = context_map(
        mapping("1", amendment_ref=None),
        mapping("2", amendment_ref="Déjase sin efecto la Cláusula Segunda"),
        mapping("3", amendment_ref="Modifícase la Cláusula Tercera"),
        kind=AmendmentKind.DELTA,
    )

    assert extraction_issues(extraction(price_change()), context, DELTA_AMENDMENT) == []


def test_changes_follow_the_context_map_order() -> None:
    result = ExtractionResult(
        reasoning="r",
        changes=[confidentiality_change(), price_change()],
        sections_changed=["3", "new:4"],
        topics_touched=["confidentiality", "price_payment"],
        summary_of_the_change="Resumen.",
    )

    assert extraction_issues(result) == [("section_order", "changes[1].section_id")]


def test_sections_changed_is_a_subsequence_of_the_map_order() -> None:
    context = context_map(
        mapping("1"),
        mapping("§2", original_ref="Objeto", amendment_ref="Objeto"),
        mapping("3"),
        mapping("new:4", original_ref=None),
    )
    positional = change("§2", ChangeType.MODIFIED, "Objeto", "Objeto", Topic.OBJECT_SCOPE)
    # Numbered and positional IDs pass the model's shape check in either order; the map decides.
    result = extraction(price_change(), positional, confidentiality_change())

    assert extraction_issues(result, context) == [
        ("section_order", "changes[1].section_id"),
        ("section_order", "sections_changed[1]"),
    ]


def test_all_extraction_issues_are_reported_together_for_the_single_retry() -> None:
    unknown = change("9", ChangeType.MODIFIED, "Precio", "inventado")

    codes_found = {code for code, _ in extraction_issues(extraction(price_change(), unknown))}

    assert codes_found == {
        "unresolved_section_reference",
        "ungrounded_quote",
        "missing_structural_change",
    }


def test_an_empty_extraction_still_owes_the_structural_changes() -> None:
    assert extraction_issues(extraction()) == [("missing_structural_change", "changes")]


def test_structural_issue_messages_never_quote_documents() -> None:
    context = context_map(mapping("1"), mapping("2", amendment_ref=None), mapping("3"))
    wrong = change("3", ChangeType.ADDED, None, "$ 1.850.000")

    feedback = check_extraction(extraction(wrong), context, ORIGINAL, AMENDMENT)

    found = {item.issue.code for item in feedback}
    assert {"change_type_mismatch", "missing_structural_change"} <= found
    document_words = {"Cláusula", "Plazo", "Precio", "1.850.000", "Doce", "Segunda"}
    for item in feedback:
        if item.issue.code in {"change_type_mismatch", "missing_structural_change"}:
            text = f"{item.issue.message} {item.detail}"
            assert not [word for word in document_words if word in text]


def untargeted_delta_map() -> ContextMap:
    return context_map(
        mapping("1", amendment_ref=None),
        mapping("2", amendment_ref="Déjase sin efecto la Cláusula Segunda"),
        mapping("3", amendment_ref=None),
        kind=AmendmentKind.DELTA,
    )


def untargeted_removal() -> Change:
    # The real pair B failure: an untouched Section reported as removed, quoting another
    # Section's instruction, which is grounded because delta quotes match the whole amendment.
    return change(
        "1", ChangeType.REMOVED, "Objeto. Servicios.", "Déjase sin efecto", Topic.OBJECT_SCOPE
    )


def targeted_removal() -> Change:
    return change("2", ChangeType.REMOVED, "Plazo. Doce meses.", "Déjase sin efecto")


def test_a_delta_change_on_a_section_the_amendment_does_not_target_fails() -> None:
    result = extraction(untargeted_removal(), targeted_removal())

    assert extraction_issues(result, untargeted_delta_map(), DELTA_AMENDMENT) == [
        ("untargeted_delta_change", "changes[0].section_id")
    ]


def test_a_delta_change_on_a_targeted_section_passes() -> None:
    result = extraction(targeted_removal())

    assert extraction_issues(result, untargeted_delta_map(), DELTA_AMENDMENT) == []


def test_restated_changes_are_not_subject_to_the_delta_target_rule() -> None:
    context = context_map(mapping("1"), mapping("2", amendment_ref=None), mapping("3"))
    removal = change("2", ChangeType.REMOVED, "Plazo. Doce meses.", None)

    assert extraction_issues(extraction(removal), context) == []


def test_untargeted_delta_feedback_names_canonical_ids_only() -> None:
    result = extraction(untargeted_removal(), targeted_removal())

    [feedback] = check_extraction(result, untargeted_delta_map(), ORIGINAL, DELTA_AMENDMENT)

    assert feedback.issue.message == (
        "section 1 is not targeted by any instruction of the delta amendment"
    )
    assert feedback.detail.endswith("; the amendment targets only section_ids: 2")
    document_words = {"Cláusula", "Objeto", "Servicios", "Déjase", "Plazo", "Segunda"}
    text = f"{feedback.issue.message} {feedback.detail}"
    assert not [word for word in document_words if word in text]


def test_grounding_rate_counts_grounded_quotes_over_all_quotes() -> None:
    half = change("3", ChangeType.MODIFIED, "$ 1.500.000", "$ 9.999.999")

    assert compute_grounding_rate(extraction(half), ORIGINAL, AMENDMENT) == 0.5
    full = extraction(price_change(), confidentiality_change())
    assert compute_grounding_rate(full, ORIGINAL, AMENDMENT) == 1.0


def test_grounding_rate_is_not_applicable_without_changes() -> None:
    assert compute_grounding_rate(extraction(), ORIGINAL, AMENDMENT) is None


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


# --- soft signals: diff_agreement ---


def diff(
    context: ContextMap, sections_changed: list[str], amendment: str = AMENDMENT
) -> DiffAgreement | None:
    return compute_diff_agreement(context, sections_changed, ORIGINAL, amendment)


def test_threshold_is_a_constant_of_the_checks() -> None:
    assert DIFF_RATIO_THRESHOLD == 0.97


def test_a_ratio_exactly_at_the_threshold_is_unchanged() -> None:
    original = "a" * 100
    at_threshold = "a" * 97 + "bcd"  # 2 * 97 / 200 == 0.97
    below_threshold = "a" * 96 + "bcde"  # 0.96

    assert not is_changed_by_diff(original, original)
    assert not is_changed_by_diff(original, at_threshold)
    assert is_changed_by_diff(original, below_threshold)


def test_diff_agreement_is_one_when_difflib_and_the_agent_mark_the_same_sections() -> None:
    assert diff(valid_map(), ["3", "new:4"]) == DiffAgreement(1.0, 3, 3)


def test_diff_agreement_is_the_jaccard_of_both_sets() -> None:
    # difflib marks {3}; the agent marks {2, 3}: one shared over two in the union.
    assert diff(valid_map(), ["2", "3", "new:4"]) == DiffAgreement(0.5, 3, 3)
    assert diff(valid_map(), ["2"]) == DiffAgreement(0.0, 3, 3)


def test_only_sections_present_in_both_documents_are_diffed() -> None:
    # new:4 has no original side: the hard structural check owns it, not difflib. It never
    # enters the total, and the agent's mark on it never enters the agent's set.
    assert diff(valid_map(), ["3"]) == DiffAgreement(1.0, 3, 3)
    assert diff(valid_map(), ["new:4"]) == DiffAgreement(0.0, 3, 3)


def test_segmentation_locates_refs_after_grounding_normalization() -> None:
    shouting = context_map(
        mapping("1"),
        mapping("2"),
        mapping("3", original_ref="CLÁUSULA   TERCERA", amendment_ref="cláusula tercera"),
        mapping("new:4", original_ref=None),
    )

    assert diff(shouting, ["3", "new:4"]) == DiffAgreement(1.0, 3, 3)


def test_each_section_is_cut_at_the_next_ref_in_document_order() -> None:
    # Without the new:4 cut, section 3 of the amendment would swallow "Cuarta" and still be
    # changed; with section 3 unchanged, the cut is what keeps it equal.
    amendment = AMENDMENT.replace("$ 1.850.000", "$ 1.500.000")

    assert diff(valid_map(), ["new:4"], amendment) == DiffAgreement(1.0, 3, 3)


def test_a_ref_that_is_not_found_leaves_its_section_out_and_lowers_coverage() -> None:
    missing = context_map(
        mapping("1", original_ref="Cláusula Novena"),
        mapping("2"),
        mapping("3"),
        mapping("new:4", original_ref=None),
    )

    # Section 1 drops out of both sets, so the agent's mark on it cannot count.
    assert diff(missing, ["1", "3", "new:4"]) == DiffAgreement(1.0, 2, 3)
    assert diff(missing, ["1"]) == DiffAgreement(0.0, 2, 3)


def test_diff_agreement_is_one_when_both_sets_are_empty_with_coverage() -> None:
    unchanged = AMENDMENT.replace("$ 1.850.000", "$ 1.500.000")

    assert diff(valid_map(), ["new:4"], unchanged) == DiffAgreement(1.0, 3, 3)


def test_diff_agreement_is_not_applicable_without_segmentable_sections() -> None:
    lost = context_map(
        mapping("1", original_ref="Artículo I"),
        mapping("2", original_ref="Artículo II"),
        mapping("new:4", original_ref=None),
    )

    result = diff(lost, ["2"])

    assert result == DiffAgreement(None, 0, 2)
    assert result is not None and result.value is None  # N/A, never 0


def test_diff_agreement_is_not_applicable_in_delta() -> None:
    assert diff(delta_map(), ["2"]) is None


# --- soft signals: kind_agreement and other_topic_rate ---


def test_kind_agreement_compares_a_delta_heuristic_with_the_agent() -> None:
    assert compute_kind_agreement(AmendmentKind.DELTA, KindHeuristic.DELTA) == 1
    assert compute_kind_agreement(AmendmentKind.RESTATED, KindHeuristic.DELTA) == 0


@pytest.mark.parametrize("kind", [AmendmentKind.DELTA, AmendmentKind.RESTATED])
def test_kind_agreement_is_not_applicable_when_the_heuristic_is_inconclusive(
    kind: AmendmentKind,
) -> None:
    assert compute_kind_agreement(kind, KindHeuristic.INCONCLUSIVE) is None


def test_other_topic_rate_is_the_share_of_other_topics() -> None:
    other = change("2", ChangeType.MODIFIED, "Doce meses", "Doce meses", Topic.OTHER)

    assert compute_other_topic_rate(extraction(other, price_change()).changes) == 0.5
    assert compute_other_topic_rate(extraction(price_change()).changes) == 0.0


def test_other_topic_rate_is_not_applicable_without_changes() -> None:
    assert compute_other_topic_rate(extraction().changes) is None


# --- soft signals: segmentation edge cases ---


def two_section_map(original_ref: str, amendment_ref: str) -> ContextMap:
    return context_map(
        mapping("1", original_ref=original_ref, amendment_ref=amendment_ref), mapping("2")
    )


def test_a_heading_only_renumbering_is_not_flagged_as_changed() -> None:
    original = "Cláusula Primera - Precio. Diez.\nCláusula Segunda - Plazo. Doce meses."
    amendment = "Cláusula 1 - Precio. Diez.\nCláusula Segunda - Plazo. Doce meses."

    result = compute_diff_agreement(
        two_section_map("Cláusula Primera", "Cláusula 1"), [], original, amendment
    )

    assert result == DiffAgreement(1.0, 2, 2)


def test_refs_sharing_the_same_text_collide_and_are_not_segmentable() -> None:
    duplicated = context_map(
        mapping("1"),
        mapping("2", original_ref="Cláusula Primera", amendment_ref="Cláusula Primera"),
        mapping("3"),
        mapping("new:4", original_ref=None),
    )

    assert diff(duplicated, ["3", "new:4"]) == DiffAgreement(1.0, 1, 3)


def test_a_ref_that_also_appears_as_an_earlier_cross_reference_is_not_segmentable() -> None:
    cross = " Rige lo dispuesto en la Cláusula Tercera."
    original = ORIGINAL.replace("Servicios.", "Servicios." + cross)
    amendment = AMENDMENT.replace("Servicios.", "Servicios." + cross)

    result = compute_diff_agreement(valid_map(), ["3", "new:4"], original, amendment)

    # Section 3 is ambiguous; its occurrences still cut the neighbours, so 1 and 2 stay equal.
    assert result == DiffAgreement(1.0, 2, 3)


def test_adjacent_refs_and_a_ref_at_the_end_leave_empty_segments_out() -> None:
    original = "Cláusula Primera\nCláusula Segunda - Plazo. Doce meses.\nCláusula Tercera"
    amendment = "Cláusula Primera\nCláusula Segunda - Plazo. Doce meses.\nCláusula Tercera"
    headings_only = context_map(mapping("1"), mapping("2"), mapping("3"))

    result = compute_diff_agreement(headings_only, [], original, amendment)

    assert result == DiffAgreement(1.0, 1, 3)


def test_a_new_section_inserted_mid_amendment_cuts_the_section_before_it() -> None:
    original = "Cláusula Primera - Objeto. Servicios.\nCláusula Segunda - Plazo. Doce meses."
    amendment = (
        "Cláusula Primera - Objeto. Servicios.\n"
        "Cláusula Nueva - Confidencialidad. Reserva total de la información.\n"
        "Cláusula Segunda - Plazo. Doce meses."
    )
    inserted = context_map(
        mapping("1"),
        mapping("2"),
        mapping("new:3", original_ref=None, amendment_ref="Cláusula Nueva"),
    )

    result = compute_diff_agreement(inserted, ["new:3"], original, amendment)

    assert result == DiffAgreement(1.0, 2, 2)
