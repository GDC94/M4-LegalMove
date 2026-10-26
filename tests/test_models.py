"""Domain models: ParsedContract, enums, Section Mapping and Context Map (plan §2.4, §3.2, §3.3)."""

import pytest
from pydantic import ValidationError

from models import (
    AmendmentKind,
    ContextMap,
    DocumentRole,
    ParsedContract,
    SectionIdParts,
    SectionMapping,
    parse_section_id,
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
