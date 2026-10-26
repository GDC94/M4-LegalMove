"""The Langfuse mask hook hides identifiers and images, never amounts (plan §5.6)."""

import base64
import hashlib
import io
import re
from datetime import UTC, datetime
from enum import Enum

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from PIL import Image
from pydantic import BaseModel

from observability import mask_personal_data


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("Juan Pérez, DNI 30.123.456, domiciliado", "DNI"),
        ("D.N.I. N° 30123456", "DNI"),
        ("DNI Nro. 30.123.456", "DNI"),
        ("DNI nro 30123456", "DNI"),
        ("documento nacional de identidad 7.654.321", "DNI"),
        ("CUIT 30-71234567-9", "TAX_ID"),
        ("CUIL: 20-30123456-7", "TAX_ID"),
        ("C.U.I.T. N° 30712345679", "TAX_ID"),
        ("CUIT 30 71234567 9", "TAX_ID"),
        ("CBU 0110599520000001234567 del Banco", "CBU"),
        ("CBU 0110 5995 2000 0001 2345 67", "CBU"),
        ("CBU 0110599-5 2000000123456-7", "CBU"),
        ("IBAN ES91 2100 0418 4502 0005 1332", "IBAN"),
        ("IBAN DE89370400440532013000", "IBAN"),
        ("iban es91 2100 0418 4502 0005 1332", "IBAN"),
        ("escribir a legal@proveedor.com.ar hoy", "EMAIL"),
        ("Tel. +54 9 11 4567-8901", "PHONE"),
        ("teléfono (011) 4567-8901", "PHONE"),
        ("celular 11 4567 8901", "PHONE"),
        ("llamar al 1145678901", "PHONE"),
        ("llamar al +5491145678901", "PHONE"),
        ("llamar al +54 9 11 45678901", "PHONE"),
    ],
)
def test_each_identifier_pattern_is_masked_without_leaking_digits(text: str, label: str) -> None:
    masked = mask_personal_data(data=text)

    assert f"[REDACTED_{label}]" in masked
    assert not re.search(r"\d", masked), masked


@pytest.mark.parametrize(
    "text",
    [
        "El precio mensual es de $ 1.500.000 (pesos un millón quinientos mil).",
        "USD 12.000 pagaderos el 2026-10-26.",
        "Cláusula Quinta: plazo de 24 meses desde el 01/03/2026.",
        "Resultados del ejercicio 2025-2026.",
        "Vigente durante el año 2024 2025.",
        "Conforme a la Ley 27.401 art. 1234 5678.",
        "Se adjunta el documento 1.234.567 del expediente.",
    ],
)
def test_amounts_dates_laws_and_plain_numbers_are_not_masked(text: str) -> None:
    assert mask_personal_data(data=text) == text


def test_nested_structures_are_masked_recursively() -> None:
    data = {
        "messages": [{"role": "user", "content": "mail legal@proveedor.com"}],
        "metadata": ("CUIT 30-71234567-9", 3, None),
    }

    masked = mask_personal_data(data=data)

    assert "legal@proveedor.com" not in str(masked)
    assert "30-71234567-9" not in str(masked)
    assert masked["metadata"][1] == 3
    assert masked["metadata"][2] is None


def test_non_text_values_pass_through() -> None:
    assert mask_personal_data(data=42) == 42
    assert mask_personal_data(data=None) is None


def test_base64_images_are_replaced_by_hash_and_dimensions() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), "white").save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()
    data = {"image_url": {"url": f"data:image/png;base64,{encoded}", "detail": "high"}}

    masked = mask_personal_data(data=data)

    url = masked["image_url"]["url"]
    assert encoded not in url
    assert "sha256=" in url
    assert "64x48" in url


def test_undecodable_base64_image_is_still_removed() -> None:
    masked = mask_personal_data(data="data:image/jpeg;base64,QUJDRA==")

    assert "QUJDRA==" not in masked
    assert "image omitted" in masked


def test_langchain_message_objects_are_masked() -> None:
    """Chain runs hand the mask BaseMessage objects, not dicts; they must not pass through."""
    encoded = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
    messages = [
        SystemMessage("CUIT 30-71234567-9"),
        HumanMessage(
            content=[
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}
            ]
        ),
    ]

    masked = str(mask_personal_data(data=messages))

    assert encoded[:20] not in masked
    assert "base64," not in masked
    assert "30-71234567-9" not in masked


def test_unknown_objects_are_masked_through_their_text() -> None:
    class Opaque:
        def __str__(self) -> str:
            return "mail legal@proveedor.com"

    assert "legal@proveedor.com" not in str(mask_personal_data(data=Opaque()))


def test_model_with_a_non_json_field_is_masked_field_by_field() -> None:
    class Opaque:
        def __str__(self) -> str:
            return "opaque"

    message = AIMessage(content="mail legal@proveedor.com", response_metadata={"raw": Opaque()})

    masked = mask_personal_data(data=message)

    assert masked["content"] == "mail [REDACTED_EMAIL]"
    assert masked["response_metadata"]["raw"] == "opaque"


def test_enums_and_datetimes_keep_their_json_form() -> None:
    class Color(Enum):
        RED = "red"

    moment = datetime(2026, 10, 26, tzinfo=UTC)

    assert mask_personal_data(data=Color.RED) == "red"
    assert mask_personal_data(data=moment) == "2026-10-26T00:00:00Z"


def test_raw_bytes_are_replaced_by_their_hash_and_size() -> None:
    content = b"\x89PNG\r\n\x1a\n" + b"0" * 64

    masked = mask_personal_data(data=content)

    assert masked == f"[bytes sha256={hashlib.sha256(content).hexdigest()} {len(content)}]"


def test_digit_runs_inside_a_sha256_are_not_masked_as_cbu() -> None:
    digest = "ab" + "1" * 22 + "cd" + "0" * 38

    assert mask_personal_data(data={"image_sha256": digest}) == {"image_sha256": digest}


def test_binary_bytes_nested_in_a_model_never_reach_the_trace() -> None:
    class Upload(BaseModel):
        content: bytes

    raw = b"\x89PNG\r\n\x1a\n\xff\xfe" + b"\x00" * 32

    masked = str(mask_personal_data(data=Upload(content=raw)))

    assert base64.b64encode(raw).decode()[:12] not in masked
    assert "\\x89PNG" not in masked
    assert "omitted" in masked
