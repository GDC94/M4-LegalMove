"""Render contract source text as a plain one-page PNG: black on white, A4 at 150 dpi."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PAGE_SIZE = (1240, 1754)
MARGIN_PX = 100
FONT_SIZE_PX = 22
LINE_HEIGHT_PX = 31
# Pillow's bundled default font has no glyphs for á, ñ or the em dash, so a system
# TrueType font with Latin-1 coverage is required. First match wins.
FONT_CANDIDATES = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
)

Font = ImageFont.FreeTypeFont


class FontNotFoundError(FileNotFoundError):
    """No TrueType font with Spanish glyph coverage is installed."""


class PageOverflowError(ValueError):
    """The text does not fit on one page; phase 1 documents are single pages."""


def load_page_font(candidates: tuple[Path, ...] = FONT_CANDIDATES) -> Font:
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=FONT_SIZE_PX)
    raise FontNotFoundError("install DejaVu Sans or Arial to render Spanish text")


def measure_width(text: str, font: Font) -> float:
    return ImageDraw.Draw(Image.new("L", (1, 1))).textlength(text, font=font)


def split_wide_token(token: str, font: Font, max_width: int) -> list[str]:
    """Hard-break a token (a long URL, an account number) that is wider than a whole line."""
    pieces = [""]
    for char in token:
        if pieces[-1] and measure_width(pieces[-1] + char, font) > max_width:
            pieces.append("")
        pieces[-1] += char
    return pieces


def wrap_paragraph(paragraph: str, font: Font, max_width: int) -> list[str]:
    """Greedy word wrap measured in pixels, so lines never cross the right margin."""
    tokens = [
        piece for word in paragraph.split() for piece in split_wide_token(word, font, max_width)
    ]
    lines: list[str] = []
    current = ""
    for token in tokens:
        candidate = f"{current} {token}".strip()
        if current and measure_width(candidate, font) > max_width:
            lines.append(current)
            current = token
        else:
            current = candidate
    return [*lines, current]


def layout_lines(text: str, font: Font) -> list[str]:
    max_width = PAGE_SIZE[0] - 2 * MARGIN_PX
    lines: list[str] = []
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    for paragraph in normalised.strip().split("\n"):
        lines.extend(wrap_paragraph(paragraph, font, max_width) if paragraph.strip() else [""])
    max_lines = (PAGE_SIZE[1] - 2 * MARGIN_PX) // LINE_HEIGHT_PX
    if len(lines) > max_lines:
        raise PageOverflowError(f"{len(lines)} lines do not fit in {max_lines}")
    return lines


def render_plain_page(text: str, output: Path) -> None:
    font = load_page_font()
    page = Image.new("RGB", PAGE_SIZE, "white")
    draw = ImageDraw.Draw(page)
    for index, line in enumerate(layout_lines(text, font)):
        draw.text((MARGIN_PX, MARGIN_PX + index * LINE_HEIGHT_PX), line, fill="black", font=font)
    page.save(output, format="PNG")
