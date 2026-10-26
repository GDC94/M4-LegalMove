"""Phase 1 plain renderer: black text on a white A4 page at 150 dpi (plan §6.5)."""

from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from render_plain import (
    PAGE_SIZE,
    FontNotFoundError,
    PageOverflowError,
    load_page_font,
    render_plain_page,
    render_test_contracts,
)


@pytest.fixture
def page_font() -> ImageFont.FreeTypeFont:
    try:
        return load_page_font()
    except FontNotFoundError:
        pytest.skip("rendering needs DejaVu Sans or Arial; none is installed on this machine")


def test_page_is_a4_at_150_dpi() -> None:
    assert PAGE_SIZE == (1240, 1754)


def test_renders_black_text_on_a_white_png(
    tmp_path: Path, page_font: ImageFont.FreeTypeFont
) -> None:
    output = tmp_path / "page.png"

    render_plain_page("Cláusula Primera — Objeto.\n\nTexto del contrato.", output)

    with Image.open(output) as image:
        assert image.format == "PNG"
        assert image.size == PAGE_SIZE
        grey = image.convert("L")
        assert grey.getpixel((0, 0)) == 255
        assert grey.getextrema()[0] < 50


def test_long_paragraphs_wrap_inside_the_margins(
    tmp_path: Path, page_font: ImageFont.FreeTypeFont
) -> None:
    output = tmp_path / "page.png"

    render_plain_page("palabra " * 120, output)

    with Image.open(output) as image:
        right_margin = image.convert("L").crop((PAGE_SIZE[0] - 80, 0, *PAGE_SIZE))
        assert right_margin.getextrema()[0] == 255


def test_text_longer_than_one_page_is_rejected(
    tmp_path: Path, page_font: ImageFont.FreeTypeFont
) -> None:
    with pytest.raises(PageOverflowError):
        render_plain_page("línea\n" * 200, tmp_path / "page.png")


def test_renders_every_source_text_next_to_it(
    tmp_path: Path, page_font: ImageFont.FreeTypeFont
) -> None:
    pair = tmp_path / "pair_a"
    pair.mkdir()
    (pair / "source_original.txt").write_text("Cláusula Primera.", encoding="utf-8")
    (pair / "source_amendment.txt").write_text("Cláusula Primera bis.", encoding="utf-8")

    rendered = render_test_contracts(tmp_path)

    assert sorted(path.name for path in rendered) == ["amendment.png", "original.png"]
    assert (pair / "original.png").is_file()
    assert (pair / "amendment.png").is_file()


def glyph_pixels(font: ImageFont.FreeTypeFont, char: str) -> bytes:
    canvas = Image.new("L", (60, 60), 0)
    ImageDraw.Draw(canvas).text((10, 10), char, font=font, fill=255)
    return canvas.tobytes()


SPANISH_GLYPHS = "áéíóúüñÁÉÍÓÚÑ—°«»¿¡"
UNASSIGNED_CODEPOINT = "͸"  # renders as the font's .notdef box


def test_font_has_real_glyphs_for_spanish_text(page_font: ImageFont.FreeTypeFont) -> None:
    missing_box = glyph_pixels(page_font, UNASSIGNED_CODEPOINT)

    tofu = [char for char in SPANISH_GLYPHS if glyph_pixels(page_font, char) == missing_box]
    assert tofu == []


def test_a_token_wider_than_the_line_is_hard_broken(
    tmp_path: Path, page_font: ImageFont.FreeTypeFont
) -> None:
    output = tmp_path / "page.png"

    render_plain_page("https://example.com/" + "x" * 400, output)

    with Image.open(output) as image:
        right_margin = image.convert("L").crop((PAGE_SIZE[0] - 80, 0, *PAGE_SIZE))
        assert right_margin.getextrema()[0] == 255


def test_windows_line_endings_render_like_unix_ones(
    tmp_path: Path, page_font: ImageFont.FreeTypeFont
) -> None:
    render_plain_page("Cláusula Primera.\r\n\r\nTexto.", tmp_path / "crlf.png")
    render_plain_page("Cláusula Primera.\n\nTexto.", tmp_path / "lf.png")

    assert (tmp_path / "crlf.png").read_bytes() == (tmp_path / "lf.png").read_bytes()
