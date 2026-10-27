"""Observability port, its Langfuse adapter, a recording double and personal-data masking."""

import base64
import binascii
import hashlib
import io
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from langchain_core.callbacks import BaseCallbackHandler
from langfuse import Langfuse, LangfuseSpan, propagate_attributes
from langfuse.langchain import CallbackHandler
from PIL import Image
from pydantic_core import PydanticSerializationError, to_jsonable_python

from errors import LegalMoveError, ObservabilityError

TRACE_NAME = "contract-analysis"

# Read by langfuse 4.17.0 when a client is built; tests run the real SDK to catch a rename.
LANGFUSE_MEDIA_UPLOAD_ENABLED = "LANGFUSE_MEDIA_UPLOAD_ENABLED"

IMAGE_DATA_URL = re.compile(r"data:image/[\w.+-]+;base64,([A-Za-z0-9+/=]+)")

NUMBER_LABEL = r"(?:N(?:ro|°|º|o)?\.?\s*)?:?\s*"

# Order matters: specific shapes go first so a looser pattern cannot eat part of them.
# Bare numbers are only masked when their shape is unambiguous (22-digit CBU, 10-digit
# phone, area code + 4 + 4); DNI needs its label because "1.234.567" may be any number.
IDENTIFIER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[REDACTED_EMAIL]"),
    (
        re.compile(r"(?i)\b[A-Z]{2}\d{2} ?[A-Z0-9]{4}(?: ?\d{4}){2,6}(?: ?\d{1,3})?\b"),
        "[REDACTED_IBAN]",
    ),
    (re.compile(r"(?<![\w.,])\d(?:[ -]?\d){21}(?![.,]?\d)(?!\w)"), "[REDACTED_CBU]"),
    (re.compile(r"\b\d{2}-\d{8}-\d\b"), "[REDACTED_TAX_ID]"),
    (
        re.compile(rf"(?i)\bC\.?U\.?I\.?[TL]\.?\s*{NUMBER_LABEL}\d{{2}}[ .-]?\d{{8}}[ .-]?\d\b"),
        "[REDACTED_TAX_ID]",
    ),
    (
        re.compile(
            rf"(?i)\b(?:D\.?N\.?I\.?|documento\s+nacional\s+de\s+identidad)\s*{NUMBER_LABEL}"
            r"\d{1,2}\.?\d{3}\.?\d{3}\b"
        ),
        "[REDACTED_DNI]",
    ),
    (re.compile(r"\+\d{1,3}(?:[ -]?\(?\d\)?){8,13}"), "[REDACTED_PHONE]"),
    (re.compile(r"\(\d{2,5}\) ?\d{3,4}[ -]?\d{4}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"(?<![\d.,])\b0?\d{2,4}[ -]\d{3,4}[ -]\d{4}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"(?<![\d.,])\b\d{10}\b(?![.,]\d)"), "[REDACTED_PHONE]"),
)


# Any: the Langfuse MaskFunction contract takes and returns arbitrary JSON-like data.
def mask_personal_data(*, data: Any, **_: Any) -> Any:
    """Langfuse `mask` hook: drop images and identifiers; amounts stay because they are not PII."""
    plain = to_plain_data(data)
    if isinstance(plain, str):
        return mask_text(plain)
    if isinstance(plain, dict):
        return {key: mask_personal_data(data=value) for key, value in plain.items()}
    if isinstance(plain, list | tuple):
        return [mask_personal_data(data=item) for item in plain]
    return plain


# Any: mirrors the mask hook, which receives whatever the SDK or LangChain hands it.
def to_plain_data(data: Any) -> Any:
    """Fail closed: chain runs pass LangChain message objects, which must not skip the mask."""
    if data is None or isinstance(data, str | int | float | bool | dict | list | tuple):
        return data
    if isinstance(data, bytes | bytearray):
        return describe_bytes(bytes(data))
    # fallback=str keeps one odd field from failing the whole mask, which would blank the span.
    # bytes_mode="utf8" fails on binary content (an image, say) instead of emitting base64
    # without a data: prefix, which the image pattern could not recognise.
    try:
        return to_jsonable_python(data, fallback=str, bytes_mode="utf8")
    except PydanticSerializationError, ValueError:
        return f"[{type(data).__name__} omitted: not serialisable]"


def describe_bytes(content: bytes) -> str:
    """Raw bytes may be an image without a data: prefix; keep only their hash and size."""
    return f"[bytes sha256={hashlib.sha256(content).hexdigest()} {len(content)}]"


def mask_text(text: str) -> str:
    masked = IMAGE_DATA_URL.sub(describe_image, text)
    for pattern, replacement in IDENTIFIER_PATTERNS:
        masked = pattern.sub(replacement, masked)
    return masked


def describe_image(match: re.Match[str]) -> str:
    """Replace an inline image with its hash and size, the only image facts a trace may hold."""
    try:
        content = base64.b64decode(match.group(1), validate=True)
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
    except binascii.Error, OSError, ValueError, Image.DecompressionBombError:
        return "[image omitted]"
    return f"[image sha256={hashlib.sha256(content).hexdigest()} {width}x{height}]"


class StageSpan(Protocol):
    """Handle to update the open span of one Stage without knowing the tracing backend."""

    def set_output(self, output: object) -> None: ...

    def set_metadata(self, metadata: Mapping[str, object]) -> None: ...

    def set_error(self, error: BaseException) -> None: ...


class PipelineTracer(Protocol):
    """Port through which business modules trace Stages; lifecycle and flush belong to main."""

    def stage(
        self,
        name: str,
        input_data: Mapping[str, object],
        metadata: Mapping[str, object] | None = None,
    ) -> AbstractContextManager[StageSpan]: ...

    def score(self, name: str, value: float | str) -> None: ...

    @property
    def trace_id(self) -> str | None: ...

    @property
    def callbacks(self) -> Sequence[BaseCallbackHandler]: ...


@dataclass(frozen=True)
class TraceAttributes:
    """Trace-level attributes the root span propagates to every Stage (plan §5.3)."""

    session_id: str | None
    version: str
    environment: str
    tags: tuple[str, ...] = ("legalmove",)


class TraceLifecycle(PipelineTracer, Protocol):
    """What only main.py may use: the root span of a run and the confirmed flush (plan §5.7)."""

    def open_root(
        self,
        input_data: Mapping[str, object],
        metadata: Mapping[str, object],
        attributes: TraceAttributes,
    ) -> AbstractContextManager[StageSpan]: ...

    def flush(self) -> None: ...


def describe_error(error: BaseException) -> str:
    """Domain messages are sanitised; foreign ones may quote documents, so only the type goes."""
    if isinstance(error, LegalMoveError):
        return f"{type(error).__name__}: {error}"
    return type(error).__name__


@dataclass
class RecordedStage:
    """Everything a test needs to assert about one traced Stage."""

    name: str
    input: dict[str, object]
    metadata: dict[str, object]
    output: object = None
    error: BaseException | None = None

    def set_output(self, output: object) -> None:
        self.output = output

    def set_metadata(self, metadata: Mapping[str, object]) -> None:
        self.metadata.update(metadata)

    def set_error(self, error: BaseException) -> None:
        self.error = error


@dataclass
class RecordingTracer:
    """Test double of PipelineTracer: records stages and scores, never touches the network."""

    stages: list[RecordedStage] = field(default_factory=list)
    scores: list[tuple[str, float | str]] = field(default_factory=list)

    @contextmanager
    def stage(
        self,
        name: str,
        input_data: Mapping[str, object],
        metadata: Mapping[str, object] | None = None,
    ) -> Iterator[StageSpan]:
        recorded = RecordedStage(name=name, input=dict(input_data), metadata=dict(metadata or {}))
        self.stages.append(recorded)
        try:
            yield recorded
        except BaseException as error:
            recorded.set_error(error)
            raise

    def score(self, name: str, value: float | str) -> None:
        self.scores.append((name, value))

    @property
    def trace_id(self) -> str | None:
        return None

    @property
    def callbacks(self) -> Sequence[BaseCallbackHandler]:
        return ()


class LangfuseStageSpan:
    """StageSpan backed by one Langfuse observation."""

    def __init__(self, observation: LangfuseSpan) -> None:
        self._observation = observation

    def set_output(self, output: object) -> None:
        self._observation.update(output=output)

    def set_metadata(self, metadata: Mapping[str, object]) -> None:
        self._observation.update(metadata=dict(metadata))

    def set_error(self, error: BaseException) -> None:
        self._observation.update(level="ERROR", status_message=describe_error(error))


class LangfuseTracer:
    """PipelineTracer adapter over a Langfuse client whose `mask` hook is already registered."""

    def __init__(self, client: Langfuse, callback_handler: BaseCallbackHandler) -> None:
        self._client = client
        self._callbacks = (callback_handler,)

    @contextmanager
    def stage(
        self,
        name: str,
        input_data: Mapping[str, object],
        metadata: Mapping[str, object] | None = None,
    ) -> Iterator[StageSpan]:
        with self._client.start_as_current_observation(
            name=name, as_type="span", input=dict(input_data), metadata=dict(metadata or {})
        ) as observation:
            span = LangfuseStageSpan(observation)
            try:
                yield span
            except BaseException as error:
                span.set_error(error)
                raise

    @contextmanager
    def open_root(
        self,
        input_data: Mapping[str, object],
        metadata: Mapping[str, object],
        attributes: TraceAttributes,
    ) -> Iterator[StageSpan]:
        """The root is an ordinary span; propagation inside it reaches every later Stage."""
        with (
            self.stage(TRACE_NAME, input_data, metadata) as root,
            propagate_attributes(
                trace_name=TRACE_NAME,
                session_id=attributes.session_id,
                version=attributes.version,
                environment=attributes.environment,
                tags=list(attributes.tags),
            ),
        ):
            yield root

    def flush(self) -> None:
        """Fail closed: a success may only be printed once the trace was handed over."""
        try:
            self._client.flush()
        # The SDK documents no error type for flush; any failure leaves delivery unconfirmed.
        except Exception as error:
            raise ObservabilityError(f"trace flush failed: {type(error).__name__}") from None

    def score(self, name: str, value: float | str) -> None:
        if isinstance(value, str):
            self._client.score_current_trace(name=name, value=value, data_type="CATEGORICAL")
        else:
            self._client.score_current_trace(name=name, value=value)

    @property
    def trace_id(self) -> str | None:
        return self._client.get_current_trace_id()

    @property
    def callbacks(self) -> Sequence[BaseCallbackHandler]:
        return self._callbacks


def build_langfuse_tracer(
    *, public_key: str, secret_key: str, host: str, environment: str
) -> LangfuseTracer:
    """The pipeline's tracer over a client from `connect_langfuse`, so masking is never optional."""
    client = connect_langfuse(
        public_key=public_key, secret_key=secret_key, host=host, environment=environment
    )
    try:
        callback_handler = CallbackHandler(public_key=public_key)
    # The SDK documents no error type for start-up; only the type goes out.
    except Exception as error:
        raise ObservabilityError(f"tracer initialisation failed: {type(error).__name__}") from None
    return LangfuseTracer(client, callback_handler=callback_handler)


def connect_langfuse(*, public_key: str, secret_key: str, host: str, environment: str) -> Langfuse:
    """The only place a Langfuse client is built: masking is wired here, unconditionally.

    The auth check runs before any model call: a run whose trace cannot be delivered must fail
    before it spends anything (plan §5.7).

    The SDK keeps one resource manager per public key, and mask, media and environment are fixed
    when the first client for that key is built. Every client of the process (the pipeline tracer
    and the eval publisher) comes from here, so whichever is first, the settings are the same.
    """
    # The SDK turns data: URIs into uploaded media BEFORE the mask runs, so the mask would only
    # see a media reference while the image bytes leave the process. The only switch is this
    # variable, read when the client is built; assign it so a user environment cannot re-enable it.
    os.environ[LANGFUSE_MEDIA_UPLOAD_ENABLED] = "false"
    try:
        client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=host,
            environment=environment,
            mask=mask_personal_data,
        )
        reachable = client.auth_check()
    # The SDK documents no error type for start-up; its messages may echo keys, hosts or response
    # bodies, so only the type goes out.
    except Exception as error:
        raise ObservabilityError(f"tracer initialisation failed: {type(error).__name__}") from None
    if not reachable:
        raise ObservabilityError("tracer initialisation failed: auth check rejected")
    return client


if TYPE_CHECKING:
    # Static Liskov check: mypy proves the double and the adapter satisfy the same ports.
    _RECORDING_TRACER: type[PipelineTracer] = RecordingTracer
    _LANGFUSE_TRACER: type[PipelineTracer] = LangfuseTracer
    _LANGFUSE_LIFECYCLE: type[TraceLifecycle] = LangfuseTracer
    _RECORDED_STAGE: type[StageSpan] = RecordedStage
    _LANGFUSE_STAGE_SPAN: type[StageSpan] = LangfuseStageSpan
