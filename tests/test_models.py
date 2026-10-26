"""Domain models: Parsed Contract, Context Map, Changes, outputs and the Summary (plan §2.4, §3)."""

from collections.abc import Callable

import pytest
from pydantic import BaseModel, ValidationError

from models import (
    NO_CHANGES,
    TOPIC_DESCRIPTIONS,
    AmendmentKind,
    Change,
    ChangeType,
    ContextMap,
    ContractChangeOutput,
    DocumentRole,
    ExtractionResult,
    ParsedContract,
    SectionIdParts,
    SectionMapping,
    Topic,
    parse_section_id,
    render_summary,
)


def make_contract(text: str = "Cláusula Primera. Objeto.") -> ParsedContract:
    return ParsedContract(
        role=DocumentRole.ORIGINAL,
        source_path="data/original.png",
        image_sha256="a" * 64,
        text=text,
    )


def test_document_roles_serialise_as_plain_strings() -> None:
    assert [role.value for role in DocumentRole] == ["original", "amendment"]


def test_illegible_count_is_derived_from_the_text() -> None:
    contract = make_contract("Monto [ILLEGIBLE] pesos, plazo [ILLEGIBLE] días.")

    assert contract.illegible_count == 2


def test_clean_text_has_no_illegible_marks() -> None:
    assert make_contract().illegible_count == 0


@pytest.mark.parametrize("text", ["", "   \n\t"])
def test_text_must_not_be_blank(text: str) -> None:
    with pytest.raises(ValidationError):
        make_contract(text)


def test_parsed_contract_is_immutable() -> None:
    contract = make_contract()

    with pytest.raises(ValidationError):
        contract.text = "otro"


# --- Amendment Kind, Section Mapping and Context Map (plan §2.2, §2.4, §3.3) ---


def make_mapping(section_id: str = "5", **overrides: object) -> SectionMapping:
    fields: dict[str, object] = {
        "section_id": section_id,
        "title": "Precio",
        "original_ref": "Cláusula Quinta",
        "amendment_ref": "Cláusula Quinta",
        "purpose": "Fija el precio.",
    }
    fields.update(overrides)
    return SectionMapping.model_validate(fields)


def make_map(*section_ids: str, kind: AmendmentKind = AmendmentKind.RESTATED) -> ContextMap:
    return ContextMap(
        reasoning="Reproduce el contrato completo.",
        amendment_kind=kind,
        sections=[make_mapping(section_id) for section_id in section_ids],
    )


def test_amendment_kinds_serialise_as_plain_strings() -> None:
    assert [kind.value for kind in AmendmentKind] == ["restated", "delta", "unknown"]


@pytest.mark.parametrize(
    "section_id", ["5", "5.2", "12.3.1", "§7", "new:5", "new:5.2", "new:§7", "10"]
)
def test_section_id_accepts_the_canonical_grammar(section_id: str) -> None:
    assert make_mapping(section_id).section_id == section_id


@pytest.mark.parametrize(
    "section_id",
    [
        "",
        "0",
        "05",
        "5.",
        ".5",
        "5.0",
        "5ª",
        "V",
        "Cláusula 5",
        "5 Precio",
        " 5",
        "§",
        "§0",
        "§7.1",
        "new:",
        "NEW:5",
        "new: 5",
        "new:new:5",
        "-5",
    ],
)
def test_section_id_rejects_anything_outside_the_grammar(section_id: str) -> None:
    with pytest.raises(ValidationError, match="canonical grammar"):
        make_mapping(section_id)


def test_section_id_error_does_not_echo_the_rejected_value() -> None:
    with pytest.raises(ValidationError) as caught:
        make_mapping("Cláusula secreta")

    assert "secreta" not in caught.value.errors()[0]["msg"]


def test_section_mapping_allows_missing_title_and_one_missing_ref() -> None:
    mapping = make_mapping("new:8", title=None, original_ref=None)

    assert mapping.title is None
    assert mapping.original_ref is None


def test_context_map_keeps_its_sections_in_the_given_order() -> None:
    context_map = make_map("1", "2", "new:3")

    assert [s.section_id for s in context_map.sections] == ["1", "2", "new:3"]


def test_context_map_rejects_duplicate_section_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate section_id"):
        make_map("1", "2", "1")


def test_context_map_may_be_empty_for_an_unknown_amendment() -> None:
    assert make_map(kind=AmendmentKind.UNKNOWN).sections == []


def test_context_map_lists_reasoning_before_the_decision_fields() -> None:
    assert list(ContextMap.model_fields) == ["reasoning", "amendment_kind", "sections"]


@pytest.mark.parametrize(
    ("section_id", "expected"),
    [
        ("5", SectionIdParts(is_new=False, is_positional=False, numbers=(5,))),
        ("5.2", SectionIdParts(is_new=False, is_positional=False, numbers=(5, 2))),
        ("§7", SectionIdParts(is_new=False, is_positional=True, numbers=(7,))),
        ("new:8", SectionIdParts(is_new=True, is_positional=False, numbers=(8,))),
        ("new:§3", SectionIdParts(is_new=True, is_positional=True, numbers=(3,))),
    ],
)
def test_parse_section_id_splits_a_canonical_id(section_id: str, expected: SectionIdParts) -> None:
    assert parse_section_id(section_id) == expected


# --- Change, ExtractionResult, ContractChangeOutput and the Summary (plan §3.3, §3.4) ---


def make_change(
    section_id: str = "3", change_type: str = "modified", **overrides: object
) -> Change:
    fields: dict[str, object] = {
        "section_id": section_id,
        "change_type": change_type,
        "topic": "price_payment",
        "description": "El precio mensual pasa de $ 1.500.000 a $ 1.850.000.",
        "original_quote": "$ 1.500.000",
        "amendment_quote": "$ 1.850.000",
    }
    fields.update(overrides)
    return Change.model_validate(fields)


def make_extraction(**overrides: object) -> ExtractionResult:
    fields: dict[str, object] = {
        "reasoning": "Cambia el precio y se agrega confidencialidad.",
        "changes": [
            make_change(),
            make_change(
                "new:8",
                "added",
                topic="confidentiality",
                description="Se agrega una cláusula de confidencialidad.",
                original_quote=None,
                amendment_quote="Cláusula Octava — Confidencialidad",
            ),
        ],
        "sections_changed": ["3", "new:8"],
        "topics_touched": ["price_payment", "confidentiality"],
        "summary_of_the_change": "Sube el precio y se agrega confidencialidad.",
    }
    fields.update(overrides)
    return ExtractionResult.model_validate(fields)


def test_change_types_and_topics_serialise_as_plain_strings() -> None:
    assert [kind.value for kind in ChangeType] == ["added", "removed", "modified"]
    assert Topic.PRICE_PAYMENT.value == "price_payment"
    assert Topic.OTHER.value == "other"


def test_every_topic_has_a_description() -> None:
    assert set(TOPIC_DESCRIPTIONS) == set(Topic)
    assert all(description.strip() for description in TOPIC_DESCRIPTIONS.values())


def test_a_modification_needs_both_quotes() -> None:
    assert make_change().original_quote == "$ 1.500.000"
    for missing in ("original_quote", "amendment_quote"):
        with pytest.raises(ValidationError, match=f"modified change needs an {missing}"):
            make_change(**{missing: None})


def test_an_addition_quotes_only_the_amendment() -> None:
    added = make_change("new:8", "added", original_quote=None)

    assert added.amendment_quote == "$ 1.850.000"
    with pytest.raises(ValidationError, match="added change must not have an original_quote"):
        make_change("new:8", "added")
    with pytest.raises(ValidationError, match="added change needs an amendment_quote"):
        make_change("new:8", "added", original_quote=None, amendment_quote=None)


def test_a_removal_needs_its_original_quote_and_leaves_the_amendment_quote_to_the_checks() -> None:
    with_instruction = make_change("5", "removed")
    restated = make_change("5", "removed", amendment_quote=None)

    assert with_instruction.amendment_quote is not None
    assert restated.amendment_quote is None
    with pytest.raises(ValidationError, match="removed change needs an original_quote"):
        make_change("5", "removed", original_quote=None)


def test_a_blank_quote_counts_as_missing() -> None:
    with pytest.raises(ValidationError, match="modified change needs an original_quote"):
        make_change(original_quote="   ")


def test_a_change_needs_a_description_and_a_canonical_section_id() -> None:
    with pytest.raises(ValidationError, match="description must not be blank"):
        make_change(description=" ")
    with pytest.raises(ValidationError, match="canonical grammar") as caught:
        make_change("Cláusula secreta")
    assert "secreta" not in caught.value.errors()[0]["msg"]


def test_extraction_result_lists_reasoning_first_then_evidence_then_the_three_fields() -> None:
    assert list(ExtractionResult.model_fields) == [
        "reasoning",
        "changes",
        "sections_changed",
        "topics_touched",
        "summary_of_the_change",
    ]


def test_extraction_result_does_not_inherit_from_the_public_output() -> None:
    assert not issubclass(ExtractionResult, ContractChangeOutput)
    assert ExtractionResult.__bases__ == (BaseModel,)


def test_a_consistent_extraction_is_accepted() -> None:
    result = make_extraction()

    assert result.sections_changed == ["3", "new:8"]


def test_sections_changed_must_equal_the_sections_of_the_changes() -> None:
    with pytest.raises(ValidationError, match=r"missing from sections_changed: new:8"):
        make_extraction(sections_changed=["3"])
    with pytest.raises(ValidationError, match=r"not backed by a change: 4"):
        make_extraction(sections_changed=["3", "4", "new:8"])


def test_topics_touched_must_equal_the_topics_of_the_changes() -> None:
    with pytest.raises(ValidationError, match=r"missing from topics_touched: confidentiality"):
        make_extraction(topics_touched=["price_payment"])
    with pytest.raises(ValidationError, match=r"not backed by a change: penalties"):
        make_extraction(topics_touched=["price_payment", "penalties", "confidentiality"])


def test_topics_touched_follows_the_first_appearance_in_changes() -> None:
    with pytest.raises(ValidationError, match="topics_touched must follow"):
        make_extraction(topics_touched=["confidentiality", "price_payment"])


def test_sections_changed_follows_the_canonical_order() -> None:
    with pytest.raises(ValidationError, match="sections_changed breaks the canonical order"):
        make_extraction(sections_changed=["new:8", "3"])


def test_consistency_errors_have_their_own_error_type() -> None:
    with pytest.raises(ValidationError) as caught:
        make_extraction(sections_changed=["3"])

    assert caught.value.errors()[0]["type"] == "inconsistent_extraction"


def test_list_entries_must_be_canonical_ids_and_topic_values_without_echoing_them() -> None:
    with pytest.raises(ValidationError, match="canonical grammar") as caught:
        make_extraction(sections_changed=["3", "Cláusula secreta"])
    assert "secreta" not in caught.value.errors()[0]["msg"]
    with pytest.raises(ValidationError, match="not a topic value") as caught:
        make_extraction(topics_touched=["price_payment", "secreto"])
    assert "secreto" not in caught.value.errors()[0]["msg"]


def test_no_changes_means_empty_lists_and_the_fixed_summary() -> None:
    empty = make_extraction(
        changes=[], sections_changed=[], topics_touched=[], summary_of_the_change=NO_CHANGES
    )

    assert empty.changes == []
    assert NO_CHANGES == "No se identificaron cambios."
    with pytest.raises(ValidationError, match="summary must be exactly"):
        make_extraction(
            changes=[], sections_changed=[], topics_touched=[], summary_of_the_change="Nada."
        )


# --- the shared list validators, exercised through both models ---


def make_output(**overrides: object) -> ContractChangeOutput:
    fields: dict[str, object] = {
        "sections_changed": ["3"],
        "topics_touched": ["price_payment"],
        "summary_of_the_change": "3 — modified: Sube el precio.",
    }
    fields.update(overrides)
    return ContractChangeOutput.model_validate(fields)


@pytest.mark.parametrize("build", [make_output, make_extraction], ids=["output", "extraction"])
def test_lists_reject_blank_strings_and_duplicates(build: Callable[..., BaseModel]) -> None:
    with pytest.raises(ValidationError, match="must not contain blank strings"):
        build(sections_changed=["3", " "])
    with pytest.raises(ValidationError, match="must not contain duplicates") as caught:
        build(topics_touched=["price_payment", "price_payment"])
    assert "price_payment" not in caught.value.errors()[0]["msg"]


@pytest.mark.parametrize("build", [make_output, make_extraction], ids=["output", "extraction"])
def test_lists_are_empty_together(build: Callable[..., BaseModel]) -> None:
    with pytest.raises(ValidationError, match="empty together"):
        build(topics_touched=[])


@pytest.mark.parametrize("build", [make_output, make_extraction], ids=["output", "extraction"])
def test_the_no_changes_phrase_is_reserved_for_empty_lists(
    build: Callable[..., BaseModel],
) -> None:
    with pytest.raises(ValidationError, match="reserved for an empty result"):
        build(summary_of_the_change=NO_CHANGES)


def test_an_empty_public_output_needs_the_fixed_summary() -> None:
    output = make_output(sections_changed=[], topics_touched=[], summary_of_the_change=NO_CHANGES)

    assert output.summary_of_the_change == NO_CHANGES
    with pytest.raises(ValidationError, match="summary must be exactly"):
        make_output(sections_changed=[], topics_touched=[], summary_of_the_change="")


def test_public_output_has_exactly_the_three_required_fields() -> None:
    schema = ContractChangeOutput.model_json_schema()

    assert list(schema["properties"]) == [
        "sections_changed",
        "topics_touched",
        "summary_of_the_change",
    ]
    assert schema["properties"]["sections_changed"]["items"] == {"type": "string"}
    assert schema["properties"]["topics_touched"]["items"] == {"type": "string"}
    assert schema["properties"]["summary_of_the_change"]["type"] == "string"


# --- the deterministic Summary (plan §3.3, decision 56) ---


def test_summary_without_changes_is_the_fixed_phrase() -> None:
    assert render_summary([]) == "No se identificaron cambios."


def test_summary_has_one_sentence_per_change_in_order() -> None:
    changes = make_extraction().changes

    assert render_summary(changes) == (
        "3 — modified: El precio mensual pasa de $ 1.500.000 a $ 1.850.000. "
        "new:8 — added: Se agrega una cláusula de confidencialidad."
    )


def test_summary_closes_each_sentence_and_collapses_whitespace() -> None:
    change = make_change(description="  Sube   el precio\n mensual ")

    assert render_summary([change, change]) == (
        "3 — modified: Sube el precio mensual. 3 — modified: Sube el precio mensual."
    )


@pytest.mark.parametrize(
    "description",
    [
        'Pasa de "A" a "B."',
        "Pasa de «A» a «B.»",
        "Pasa de \u201cA\u201d a \u201cB.\u201d",
        "Se aclara el plazo (doce meses.)",
        "¿Cambia el plazo?)",
    ],
)
def test_summary_keeps_punctuation_that_closes_inside_quotes_or_parentheses(
    description: str,
) -> None:
    assert render_summary([make_change(description=description)]) == (
        f"3 — modified: {description}"
    )


def test_summary_still_closes_a_quoted_ending_without_punctuation() -> None:
    change = make_change(description='Pasa a "B"')

    assert render_summary([change]) == '3 — modified: Pasa a "B".'


def test_quote_rule_failures_have_their_own_error_type() -> None:
    with pytest.raises(ValidationError) as caught:
        make_change(original_quote=None)

    assert caught.value.errors()[0]["type"] == "quote_rule_violation"
