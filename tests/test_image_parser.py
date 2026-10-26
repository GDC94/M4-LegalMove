"""Image validation, encoding, parse cache and the vision parser (plan §4.5, §5.1, §6.1)."""

import base64
import hashlib
import io
import os
import struct
import zlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import openai
import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI
from PIL import Image
from pydantic import SecretStr

from errors import IllegibleDocumentError, InvalidImageError, UpstreamError
from image_parser import (
    VISION_PROMPT,
    VISION_PROMPT_VERSION,
    ContractImageParser,
    ImageLimits,
    ParseCache,
    VisionConfig,
    build_cache_key,
    build_contract_image_parser,
    build_vision_chat_model,
    validate_image,
)
from models import DocumentRole
from observability import RecordingTracer
from parser_doubles import RecordingChatModel, png_bytes

LIMITS = ImageLimits(max_bytes=1_000_000, min_side=10, max_side=200, max_pixels=40_000)


def jpeg_bytes(width: int = 40, height: int = 30) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def png_with_declared_size(width: int, height: int) -> bytes:
    """A PNG whose header claims huge dimensions, like a decompression bomb."""
    data = bytearray(png_bytes())
    ihdr_start = 8 + 4  # signature, then the IHDR length field
    data[ihdr_start + 4 : ihdr_start + 12] = struct.pack(">II", width, height)
    crc = zlib.crc32(bytes(data[ihdr_start : ihdr_start + 17]))
    data[ihdr_start + 17 : ihdr_start + 21] = struct.pack(">I", crc)
    return bytes(data)


def write(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def test_valid_png_is_described_without_decoding_twice(tmp_path: Path) -> None:
    content = png_bytes(40, 30)

    image = validate_image(write(tmp_path, "doc.png", content), LIMITS)

    assert image.mime_type == "image/png"
    assert (image.width, image.height) == (40, 30)
    assert image.content == content
    assert image.sha256 == hashlib.sha256(content).hexdigest()


def test_valid_jpeg_is_accepted(tmp_path: Path) -> None:
    image = validate_image(write(tmp_path, "doc.jpg", jpeg_bytes()), LIMITS)

    assert image.mime_type == "image/jpeg"


def test_multi_picture_jpeg_is_accepted_as_jpeg(tmp_path: Path) -> None:
    """Phone cameras write MPO: JPEG magic bytes that Pillow reports as format "MPO"."""
    buffer = io.BytesIO()
    frames = [Image.new("RGB", (40, 30), "white"), Image.new("RGB", (40, 30), "black")]
    frames[0].save(buffer, format="MPO", save_all=True, append_images=frames[1:])

    image = validate_image(write(tmp_path, "photo.jpg", buffer.getvalue()), LIMITS)

    assert image.mime_type == "image/jpeg"


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvalidImageError):
        validate_image(tmp_path / "absent.png", LIMITS)


@pytest.mark.parametrize(
    "content",
    [
        b"GIF89a" + b"\x00" * 64,
        b"%PDF-1.7\n" + b"\x00" * 64,
        b"",
    ],
)
def test_unsupported_magic_bytes_are_rejected(tmp_path: Path, content: bytes) -> None:
    with pytest.raises(InvalidImageError, match="JPEG or PNG"):
        validate_image(write(tmp_path, "doc.png", content), LIMITS)


def test_extension_does_not_override_magic_bytes(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30)).save(buffer, format="GIF")

    with pytest.raises(InvalidImageError):
        validate_image(write(tmp_path, "doc.png", buffer.getvalue()), LIMITS)


def test_file_over_byte_limit_is_rejected(tmp_path: Path) -> None:
    content = png_bytes()
    limits = ImageLimits(max_bytes=len(content) - 1, min_side=10, max_side=200, max_pixels=40_000)

    with pytest.raises(InvalidImageError, match="bytes"):
        validate_image(write(tmp_path, "doc.png", content), limits)


@pytest.mark.parametrize(("width", "height"), [(9, 30), (40, 9), (201, 30), (40, 201)])
def test_sides_outside_limits_are_rejected(tmp_path: Path, width: int, height: int) -> None:
    with pytest.raises(InvalidImageError, match="side"):
        validate_image(write(tmp_path, "doc.png", png_bytes(width, height)), LIMITS)


def test_too_many_pixels_is_rejected(tmp_path: Path) -> None:
    limits = ImageLimits(max_bytes=1_000_000, min_side=10, max_side=200, max_pixels=199 * 199)

    with pytest.raises(InvalidImageError, match="pixels"):
        validate_image(write(tmp_path, "doc.png", png_bytes(200, 200)), limits)


def test_decompression_bomb_is_rejected_before_decoding(tmp_path: Path) -> None:
    bomb = png_with_declared_size(30_000, 30_000)
    # Our own limits are lifted so only Pillow's bomb guard can stop this file.
    permissive = ImageLimits(max_bytes=1_000_000, min_side=1, max_side=10**6, max_pixels=10**12)

    with pytest.raises(InvalidImageError, match="decompression bomb"):
        validate_image(write(tmp_path, "bomb.png", bomb), permissive)


def test_truncated_png_is_rejected(tmp_path: Path) -> None:
    content = png_bytes()

    with pytest.raises(InvalidImageError):
        validate_image(write(tmp_path, "doc.png", content[: len(content) // 2]), LIMITS)


# --- parse cache -------------------------------------------------------------------------

KEY_PARTS = {
    "image_sha256": "a" * 64,
    "prompt": "Transcribe.",
    "model": "gpt-4o",
    "detail": "high",
}


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def make_cache(tmp_path: Path, clock: FakeClock | None = None) -> ParseCache:
    return ParseCache(tmp_path / "cache", ttl_hours=24, clock=clock or FakeClock())


def test_cache_key_is_stable_for_the_same_inputs() -> None:
    assert build_cache_key(**KEY_PARTS) == build_cache_key(**KEY_PARTS)


@pytest.mark.parametrize(
    ("part", "value"),
    [
        ("image_sha256", "b" * 64),
        ("prompt", "Transcribe!"),
        ("model", "gpt-4.1"),
        ("detail", "low"),
    ],
)
def test_cache_key_changes_with_every_component(part: str, value: str) -> None:
    assert build_cache_key(**{**KEY_PARTS, part: value}) != build_cache_key(**KEY_PARTS)


def test_cache_key_hashes_the_prompt_content_not_a_version_label() -> None:
    expected_prompt_hash = hashlib.sha256(b"Transcribe.").hexdigest()
    expected = hashlib.sha256(f"{'a' * 64}:{expected_prompt_hash}:gpt-4o:high".encode()).hexdigest()

    assert build_cache_key(**KEY_PARTS) == expected


def test_cache_miss_on_empty_cache(tmp_path: Path) -> None:
    assert make_cache(tmp_path).read("k" * 64) is None


def test_cache_returns_what_was_written(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)

    cache.write("k" * 64, "Cláusula Primera.")

    assert cache.read("k" * 64) == "Cláusula Primera."


def test_cache_directory_and_entries_are_private(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)

    cache.write("k" * 64, "texto")

    directory = tmp_path / "cache"
    entries = list(directory.iterdir())
    assert directory.stat().st_mode & 0o777 == 0o700
    assert [entry.stat().st_mode & 0o777 for entry in entries] == [0o600]


def test_cache_tightens_an_existing_permissive_directory(tmp_path: Path) -> None:
    directory = tmp_path / "cache"
    directory.mkdir(mode=0o755)

    make_cache(tmp_path).write("k" * 64, "texto")

    assert directory.stat().st_mode & 0o777 == 0o700


def test_cache_write_leaves_no_temporary_files(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)

    cache.write("k" * 64, "primero")
    cache.write("k" * 64, "segundo")

    assert len(list((tmp_path / "cache").iterdir())) == 1
    assert cache.read("k" * 64) == "segundo"


def test_failed_write_keeps_the_previous_entry_and_reports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = make_cache(tmp_path)
    cache.write("k" * 64, "primero")

    def fail_replace(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("image_parser.os.replace", fail_replace)
    stored = cache.write("k" * 64, "segundo")
    monkeypatch.undo()

    assert stored is False
    assert cache.read("k" * 64) == "primero"
    assert len(list((tmp_path / "cache").iterdir())) == 1


def test_write_where_the_directory_cannot_exist_degrades(tmp_path: Path) -> None:
    (tmp_path / "cache").write_text("a file, not a directory", encoding="utf-8")

    assert make_cache(tmp_path).write("k" * 64, "texto") is False


@contextmanager
def directory_mode(path: Path, mode: int) -> Iterator[None]:
    path.chmod(mode)
    try:
        yield
    finally:
        path.chmod(0o700)


def test_unreadable_cache_directory_is_a_miss(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache.write("k" * 64, "texto")

    with directory_mode(tmp_path / "cache", 0o000):
        assert cache.read("k" * 64) is None


def test_corrupt_entry_that_cannot_be_deleted_is_still_a_miss(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache.write("k" * 64, "texto")
    next((tmp_path / "cache").iterdir()).write_bytes(b"{not json")

    with directory_mode(tmp_path / "cache", 0o500):
        assert cache.read("k" * 64) is None


def test_writing_sweeps_stale_temporary_files_only(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = make_cache(tmp_path, clock)
    cache.write("a" * 64, "texto")
    stale = tmp_path / "cache" / "stale.tmp"
    fresh = tmp_path / "cache" / "fresh.tmp"
    stale.write_bytes(b"partial")
    fresh.write_bytes(b"partial")
    old = clock.now - 24 * 3600 - 1
    os.utime(stale, (old, old))
    os.utime(fresh, (clock.now, clock.now))

    cache.write("b" * 64, "texto")

    assert not stale.exists()
    assert fresh.exists()


def test_expired_entry_is_a_miss_and_is_deleted(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = make_cache(tmp_path, clock)
    cache.write("k" * 64, "texto")

    clock.now += 24 * 3600 + 1

    assert cache.read("k" * 64) is None
    assert list((tmp_path / "cache").iterdir()) == []


def test_entry_within_ttl_is_a_hit(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = make_cache(tmp_path, clock)
    cache.write("k" * 64, "texto")

    clock.now += 24 * 3600 - 1

    assert cache.read("k" * 64) == "texto"


@pytest.mark.parametrize(
    "content", [b"{not json", b'{"text": 3, "created_at": 1}', b'["texto"]', b"\xff\xfe"]
)
def test_corrupt_entry_is_a_miss_and_is_deleted(tmp_path: Path, content: bytes) -> None:
    cache = make_cache(tmp_path)
    cache.write("k" * 64, "texto")
    entry = next((tmp_path / "cache").iterdir())
    entry.write_bytes(content)

    assert cache.read("k" * 64) is None
    assert not entry.exists()


def test_writing_sweeps_other_expired_entries(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = make_cache(tmp_path, clock)
    cache.write("old" + "k" * 61, "viejo")
    clock.now += 24 * 3600 + 1

    cache.write("new" + "k" * 61, "nuevo")

    assert len(list((tmp_path / "cache").iterdir())) == 1
    assert cache.read("new" + "k" * 61) == "nuevo"


# --- vision parser -----------------------------------------------------------------------

CONFIG = VisionConfig(model="gpt-4o", detail="high", illegible_max_marks=2, limits=LIMITS)


def make_parser(
    model: RecordingChatModel,
    tracer: RecordingTracer,
    cache: ParseCache | None = None,
    config: VisionConfig = CONFIG,
) -> ContractImageParser:
    return build_contract_image_parser(model.runnable, tracer, config, cache)


def test_parse_returns_a_parsed_contract(tmp_path: Path) -> None:
    content = png_bytes()
    path = write(tmp_path, "original.png", content)
    model = RecordingChatModel(reply="Cláusula Primera. [ILLEGIBLE] pesos.")

    contract = make_parser(model, RecordingTracer()).parse_contract_image(
        path, DocumentRole.ORIGINAL
    )

    assert contract.role is DocumentRole.ORIGINAL
    assert contract.source_path == str(path)
    assert contract.image_sha256 == hashlib.sha256(content).hexdigest()
    assert contract.text == "Cláusula Primera. [ILLEGIBLE] pesos."
    assert contract.illegible_count == 1


def test_parse_sends_the_prompt_and_the_base64_image(tmp_path: Path) -> None:
    content = png_bytes()
    model = RecordingChatModel()

    make_parser(model, RecordingTracer()).parse_contract_image(
        write(tmp_path, "doc.png", content), DocumentRole.ORIGINAL
    )

    [(messages, _)] = model.calls
    system, human = messages
    assert system.content == VISION_PROMPT
    [image_block] = human.content
    assert image_block == {
        "type": "image_url",
        "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(content).decode(),
            "detail": "high",
        },
    }


class SpyHandler(BaseCallbackHandler):
    def __init__(self) -> None:
        self.run_names: list[str | None] = []

    def on_chain_start(self, *_: Any, **kwargs: Any) -> None:
        self.run_names.append(kwargs.get("name"))


class TracerWithCallbacks(RecordingTracer):
    def __init__(self, handler: BaseCallbackHandler) -> None:
        super().__init__()
        self.handler = handler

    @property
    def callbacks(self) -> Sequence[BaseCallbackHandler]:
        return (self.handler,)


def test_parse_reports_the_model_call_to_the_tracer_callbacks(tmp_path: Path) -> None:
    handler = SpyHandler()

    make_parser(RecordingChatModel(), TracerWithCallbacks(handler)).parse_contract_image(
        write(tmp_path, "doc.png", png_bytes()), DocumentRole.ORIGINAL
    )

    assert handler.run_names == ["vision_parsing"]


@pytest.mark.parametrize(
    "instruction",
    ["verbatim", "[ILLEGIBLE]", "Do not summarize", "plain text", "numbering"],
)
def test_vision_prompt_demands_a_literal_reading(instruction: str) -> None:
    assert instruction.lower() in VISION_PROMPT.lower()


def test_parse_traces_a_span_named_after_the_role(tmp_path: Path) -> None:
    content = png_bytes(40, 30)
    tracer = RecordingTracer()
    model = RecordingChatModel(reply="Cláusula Primera.")

    make_parser(model, tracer).parse_contract_image(
        write(tmp_path, "amendment.png", content), DocumentRole.AMENDMENT
    )

    [stage] = tracer.stages
    assert stage.name == "parse_amendment_contract"
    assert stage.input == {
        "image_sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
        "width": 40,
        "height": 30,
    }
    assert stage.output == "Cláusula Primera."
    assert stage.metadata == {
        "cache_hit": False,
        "illegible_count": 0,
        "vision_prompt_version": VISION_PROMPT_VERSION,
        "vision_prompt_sha256": hashlib.sha256(VISION_PROMPT.encode()).hexdigest(),
        "vision_model": "gpt-4o",
        "vision_detail": "high",
    }


def test_second_parse_of_the_same_image_is_a_cache_hit(tmp_path: Path) -> None:
    path = write(tmp_path, "doc.png", png_bytes())
    model = RecordingChatModel(reply="Cláusula Primera.")
    tracer = RecordingTracer()
    parser = make_parser(model, tracer, ParseCache(tmp_path / "cache", ttl_hours=24))

    first = parser.parse_contract_image(path, DocumentRole.ORIGINAL)
    second = parser.parse_contract_image(path, DocumentRole.ORIGINAL)

    assert second == first
    assert len(model.calls) == 1
    assert [stage.metadata["cache_hit"] for stage in tracer.stages] == [False, True]


def test_changing_the_detail_misses_the_cache(tmp_path: Path) -> None:
    path = write(tmp_path, "doc.png", png_bytes())
    model = RecordingChatModel()
    cache = ParseCache(tmp_path / "cache", ttl_hours=24)
    low = VisionConfig(model="gpt-4o", detail="low", illegible_max_marks=2, limits=LIMITS)

    make_parser(model, RecordingTracer(), cache).parse_contract_image(path, DocumentRole.ORIGINAL)
    make_parser(model, RecordingTracer(), cache, low).parse_contract_image(
        path, DocumentRole.ORIGINAL
    )

    assert len(model.calls) == 2


def test_without_cache_every_parse_calls_the_model(tmp_path: Path) -> None:
    path = write(tmp_path, "doc.png", png_bytes())
    model = RecordingChatModel()
    parser = make_parser(model, RecordingTracer())

    parser.parse_contract_image(path, DocumentRole.ORIGINAL)
    parser.parse_contract_image(path, DocumentRole.ORIGINAL)

    assert len(model.calls) == 2


def test_illegible_marks_at_the_threshold_are_accepted(tmp_path: Path) -> None:
    model = RecordingChatModel(reply="a [ILLEGIBLE] b [ILLEGIBLE]")

    contract = make_parser(model, RecordingTracer()).parse_contract_image(
        write(tmp_path, "doc.png", png_bytes()), DocumentRole.ORIGINAL
    )

    assert contract.illegible_count == 2


def test_illegible_marks_over_the_threshold_fail_and_mark_the_span(tmp_path: Path) -> None:
    model = RecordingChatModel(reply="[ILLEGIBLE] [ILLEGIBLE] [ILLEGIBLE]")
    tracer = RecordingTracer()

    with pytest.raises(IllegibleDocumentError) as raised:
        make_parser(model, tracer).parse_contract_image(
            write(tmp_path, "doc.png", png_bytes()), DocumentRole.ORIGINAL
        )

    assert "[ILLEGIBLE] [ILLEGIBLE]" not in str(raised.value)
    assert tracer.stages[0].error is raised.value
    assert tracer.stages[0].metadata["illegible_count"] == 3


def test_blank_parsed_text_is_illegible(tmp_path: Path) -> None:
    model = RecordingChatModel(reply="   ")

    with pytest.raises(IllegibleDocumentError):
        make_parser(model, RecordingTracer()).parse_contract_image(
            write(tmp_path, "doc.png", png_bytes()), DocumentRole.ORIGINAL
        )


def test_api_failure_becomes_upstream_error(tmp_path: Path) -> None:
    timeout = openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com"))
    model = RecordingChatModel(error=timeout)
    tracer = RecordingTracer()

    with pytest.raises(UpstreamError):
        make_parser(model, tracer).parse_contract_image(
            write(tmp_path, "doc.png", png_bytes()), DocumentRole.ORIGINAL
        )

    assert isinstance(tracer.stages[0].error, UpstreamError)


def test_invalid_image_never_reaches_the_model(tmp_path: Path) -> None:
    model = RecordingChatModel()

    with pytest.raises(InvalidImageError):
        make_parser(model, RecordingTracer()).parse_contract_image(
            write(tmp_path, "doc.png", b"GIF89a"), DocumentRole.ORIGINAL
        )

    assert model.calls == []


def test_vision_chat_model_has_bounded_retries_and_timeout() -> None:
    model = build_vision_chat_model(
        model="gpt-4o", temperature=0.0, timeout_seconds=60, max_retries=2, api_key=SecretStr("sk")
    )

    assert isinstance(model, ChatOpenAI)
    assert (model.model_name, model.temperature) == ("gpt-4o", 0.0)
    assert (model.request_timeout, model.max_retries) == (60, 2)


def test_rejected_illegible_result_is_not_cached(tmp_path: Path) -> None:
    path = write(tmp_path, "doc.png", png_bytes())
    cache = ParseCache(tmp_path / "cache", ttl_hours=24)
    model = RecordingChatModel(reply="[ILLEGIBLE] [ILLEGIBLE] [ILLEGIBLE]")
    parser = make_parser(model, RecordingTracer(), cache)

    for _ in range(2):
        with pytest.raises(IllegibleDocumentError):
            parser.parse_contract_image(path, DocumentRole.ORIGINAL)

    assert len(model.calls) == 2
    assert list((tmp_path / "cache").glob("*.json")) == []


def test_cache_write_failure_does_not_fail_a_paid_parse(tmp_path: Path) -> None:
    (tmp_path / "cache").write_text("a file, not a directory", encoding="utf-8")
    tracer = RecordingTracer()
    parser = make_parser(RecordingChatModel(), tracer, ParseCache(tmp_path / "cache", 24))

    contract = parser.parse_contract_image(
        write(tmp_path, "doc.png", png_bytes()), DocumentRole.ORIGINAL
    )

    assert contract.text
    assert tracer.stages[0].metadata["cache_write_failed"] is True
