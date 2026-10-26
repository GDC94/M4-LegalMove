"""ParsedContract and DocumentRole (plan §3.2, §3.3)."""

import pytest
from pydantic import ValidationError

from models import DocumentRole, ParsedContract


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
