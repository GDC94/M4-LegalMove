"""Seeded Pillow Degradations that make a rendered page look like a real scan."""

import io
import random
from collections.abc import Callable, Sequence

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from golden_case import Degradation, DegradationOp
from render_plain import load_page_font

STAMP_FONT_PX = 96
STAMP_ANGLE = 18

Degrade = Callable[[Image.Image, Degradation, random.Random], Image.Image]


def png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def rotate(image: Image.Image, degradation: Degradation, rng: random.Random) -> Image.Image:
    return image.rotate(degradation.amount, resample=Image.Resampling.BICUBIC, fillcolor="white")


def add_noise(image: Image.Image, degradation: Degradation, rng: random.Random) -> Image.Image:
    # Noise comes from the seeded generator, never from Pillow's unseeded effect_noise.
    noise = Image.frombytes("L", image.size, rng.randbytes(image.width * image.height))
    return Image.blend(image, noise.convert("RGB"), degradation.amount)


def blur(image: Image.Image, degradation: Degradation, rng: random.Random) -> Image.Image:
    return image.filter(ImageFilter.GaussianBlur(degradation.amount))


def compress_jpeg(image: Image.Image, degradation: Degradation, rng: random.Random) -> Image.Image:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=int(degradation.amount))
    with Image.open(buffer) as compressed:
        return compressed.convert("RGB")


def downscale(image: Image.Image, degradation: Degradation, rng: random.Random) -> Image.Image:
    """Lose resolution but keep the page size, like a low-dpi scan scaled back up."""
    small = (
        max(1, int(image.width * degradation.amount)),
        max(1, int(image.height * degradation.amount)),
    )
    reduced = image.resize(small, Image.Resampling.BILINEAR)
    return reduced.resize(image.size, Image.Resampling.BILINEAR)


def stamp(image: Image.Image, degradation: Degradation, rng: random.Random) -> Image.Image:
    """A translucent red rubber stamp, tilted, at a seeded position over the text."""
    font = ImageFont.truetype(load_page_font().path, STAMP_FONT_PX)
    text = degradation.text or "COPIA"
    left, top, right, bottom = (int(edge) for edge in font.getbbox(text))
    mark = Image.new("RGBA", (right - left, bottom - top), (0, 0, 0, 0))
    fill = (200, 0, 0, int(255 * degradation.amount))
    ImageDraw.Draw(mark).text((-left, -top), text, font=font, fill=fill)
    tilted = mark.rotate(STAMP_ANGLE, expand=True)
    position = (
        rng.randrange(max(1, image.width - tilted.width)),
        rng.randrange(max(1, image.height - tilted.height)),
    )
    canvas = image.convert("RGBA")
    canvas.alpha_composite(tilted.crop((0, 0, *image.size)), position)
    return canvas.convert("RGB")


DEGRADERS: dict[DegradationOp, Degrade] = {
    DegradationOp.ROTATE: rotate,
    DegradationOp.NOISE: add_noise,
    DegradationOp.BLUR: blur,
    DegradationOp.JPEG: compress_jpeg,
    DegradationOp.DOWNSCALE: downscale,
    DegradationOp.STAMP: stamp,
}


def degrade_image(
    image: Image.Image, degradations: Sequence[Degradation], seed: int
) -> Image.Image:
    """Apply the declared Degradations in order; the same seed always yields the same bytes."""
    rng = random.Random(seed)
    degraded = image.convert("RGB")
    for degradation in degradations:
        degraded = DEGRADERS[degradation.op](degraded, degradation, rng)
    return degraded
