"""HTTP API (FastAPI), the second composition root: jobs run in the background; poll or stream."""

import asyncio
import hashlib
import hmac
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, Security, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import Field, SecretStr, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, UploadFile

from composition import (
    assemble_container,
    build_image_limits,
    build_tracer,
    connect_langfuse,
    flush_client,
)
from config import Settings, describe_problems
from errors import ConfigurationError, InvalidImageError
from image_parser import ImageLimits, detect_mime_type, inspect_image
from job_runner import JobServices, ThreadPoolJobRunner, UploadedImages, execute_job
from jobs import (
    EventKind,
    Job,
    JobEvent,
    JobEvents,
    JobRunner,
    JobState,
    JobStore,
    ProgressEvent,
    QueueFullError,
    RateLimiter,
    format_event_time,
    to_progress_event,
)
from jobs_memory import InMemoryJobEvents, InMemoryJobStore, InMemoryRateLimiter

logger = logging.getLogger("legalmove")

API_KEY_HEADER = "X-API-Key"
UPLOAD_FIELDS = ("original", "amendment")
# Multipart framing and the form fields around the two files.
MULTIPART_OVERHEAD_BYTES = 1_048_576
QUEUE_RETRY_AFTER_SECONDS = 5
# No real seq gets near 20 digits; a longer header is noise, and `int()` refuses past 4300.
MAX_EVENT_ID_DIGITS = 20
# Proxies and browsers drop an idle connection; a comment every 15 s keeps the stream open.
KEEP_ALIVE_SECONDS = 15.0
KEEP_ALIVE_COMMENT = ": keep-alive\n\n"


class ApiSettings(Settings):
    """Runtime Settings plus the API-only knobs; API_KEYS does not exist for the CLI."""

    api_keys: SecretStr
    api_max_concurrent_jobs: int = Field(default=2, ge=1)
    api_max_pending_jobs: int = Field(default=10, ge=1)
    api_rate_limit_requests: int = Field(default=10, ge=1)
    api_rate_limit_window_seconds: int = Field(default=60, ge=1)
    api_max_upload_bytes: int | None = Field(default=None, ge=1)
    api_job_ttl_seconds: int = Field(default=3600, ge=1)

    @field_validator("api_keys")
    @classmethod
    def check_there_is_a_key(cls, value: SecretStr) -> SecretStr:
        if not split_keys(value.get_secret_value()):
            raise ValueError("API_KEYS must hold at least one key")
        return value

    @property
    def key_hashes(self) -> tuple[str, ...]:
        """Keys are only ever kept as sha256 digests."""
        return tuple(hash_api_key(key) for key in split_keys(self.api_keys.get_secret_value()))

    @property
    def max_upload_bytes(self) -> int:
        """Capped at the image limit, so an oversized image is always a 413, never a 422."""
        return min(self.api_max_upload_bytes or self.max_image_bytes, self.max_image_bytes)


def split_keys(raw: str) -> list[str]:
    return [key.strip() for key in raw.split(",") if key.strip()]


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def load_api_settings(env_file: Path | None = Path(".env")) -> ApiSettings:
    """Build ApiSettings or raise ConfigurationError naming only the offending fields."""
    try:
        # pyright synthesizes __init__ from the fields and misses pydantic-settings' _env_file.
        return ApiSettings(_env_file=env_file)  # pyright: ignore[reportCallIssue]
    except ValidationError as error:
        # Drop the cause: pydantic errors echo input values, which may be secrets.
        raise ConfigurationError(f"invalid API settings: {describe_problems(error)}") from None


@dataclass(frozen=True)
class ApiRuntime:
    """Everything the endpoints use, built once at start-up and closed at shutdown."""

    settings: ApiSettings
    store: JobStore
    limiter: RateLimiter
    runner: JobRunner
    events: JobEvents
    services: JobServices
    clock: Callable[[], float]
    close: Callable[[], None]


def build_api_runtime() -> ApiRuntime:
    """Fails start-up on bad settings or an unreachable Langfuse; no request ever sees that."""
    settings = load_api_settings()
    client = connect_langfuse(settings)
    store = InMemoryJobStore(ttl_seconds=settings.api_job_ttl_seconds)
    events = InMemoryJobEvents()
    runner = ThreadPoolJobRunner(
        max_workers=settings.api_max_concurrent_jobs, max_pending=settings.api_max_pending_jobs
    )

    def close() -> None:
        """Let running jobs finish, then hand the last traces over."""
        runner.shutdown()
        flush_client(client)

    return ApiRuntime(
        settings=settings,
        store=store,
        limiter=InMemoryRateLimiter(
            requests=settings.api_rate_limit_requests,
            window_seconds=settings.api_rate_limit_window_seconds,
        ),
        runner=runner,
        events=events,
        services=JobServices(
            store=store,
            events=events,
            build_tracer=lambda: build_tracer(client, settings),
            build_container=lambda tracer: assemble_container(settings, tracer),
        ),
        clock=time.time,
        close=close,
    )


api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)
router = APIRouter()


def get_runtime(request: Request) -> ApiRuntime:
    runtime: ApiRuntime = request.app.state.runtime
    return runtime


Runtime = Annotated[ApiRuntime, Depends(get_runtime)]


def authenticate(runtime: Runtime, key: Annotated[str | None, Security(api_key_header)]) -> str:
    """The hash of the caller's key; the digests are compared in constant time."""
    if key is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid API key")
    candidate = hash_api_key(key)
    matches = [hmac.compare_digest(candidate, known) for known in runtime.settings.key_hashes]
    if not any(matches):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid API key")
    return candidate


Owner = Annotated[str, Depends(authenticate)]


def limit_rate(runtime: Runtime, owner: Owner) -> str:
    if not runtime.limiter.consume(owner):
        retry_after = str(runtime.settings.api_rate_limit_window_seconds)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate limit exceeded",
            headers={"Retry-After": retry_after},
        )
    return owner


RateLimitedOwner = Annotated[str, Depends(limit_rate)]


@router.get("/health")
def check_health() -> dict[str, str]:
    """Liveness only: it touches neither Langfuse nor OpenAI."""
    return {"status": "ok"}


@router.post("/analyses", status_code=status.HTTP_202_ACCEPTED)
async def create_analysis(
    request: Request, runtime: Runtime, owner: RateLimitedOwner
) -> JSONResponse:
    """Checks in order: key, rate limit (both dependencies), length, size, images, queue."""
    expire_jobs(runtime)
    limit = runtime.settings.max_upload_bytes
    reject_unbounded_request(request, limit)
    images = await read_uploads(await request.form(max_files=2, max_fields=2), limit)
    await run_in_threadpool(validate_images, images, build_image_limits(runtime.settings))
    job = enqueue_job(runtime, owner, images)
    return JSONResponse({"id": job.id}, status_code=status.HTTP_202_ACCEPTED)


@router.get("/analyses/{job_id}", response_model=None)
def read_analysis(job_id: str, runtime: Runtime, owner: Owner) -> dict[str, object]:
    job = runtime.store.get(job_id, owner)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "analysis not found")
    return describe_job(job)


class EventStreamResponse(JSONResponse):
    """Documentation only: FastAPI writes a response model's schema for JSON response classes.

    The endpoint returns a StreamingResponse; this class makes the OpenAPI document the
    `text/event-stream` body as the `kind` union each `data:` line carries.
    """

    media_type = "text/event-stream"


@router.get(
    "/analyses/{job_id}/events",
    response_model=ProgressEvent,
    response_class=EventStreamResponse,
    responses={
        401: {"description": "missing or invalid API key"},
        404: {"description": "not found"},
    },
)
def stream_analysis_events(
    job_id: str,
    request: Request,
    runtime: Runtime,
    owner: Owner,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    """One SSE frame per event from `Last-Event-ID`; the stream closes after the `job` event."""
    if runtime.store.get(job_id, owner) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "analysis not found")
    feed = EventFeed(events=runtime.events, job_id=job_id, is_disconnected=request.is_disconnected)
    return StreamingResponse(
        stream_events(feed, last_seq=parse_last_event_id(last_event_id)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def expire_jobs(runtime: ApiRuntime) -> None:
    """A job's events leave with the job, so the log never outgrows the store."""
    for job_id in runtime.store.expire():
        runtime.events.discard(job_id)


def parse_last_event_id(value: str | None) -> int:
    """A missing or unreadable id replays from the start, which never loses an event."""
    if value is None or len(value) > MAX_EVENT_ID_DIGITS:
        return 0
    if not (value.isascii() and value.isdigit()):
        return 0
    return int(value)


@dataclass(frozen=True)
class EventFeed:
    """Where one stream reads from, and how it learns that its client went away."""

    events: JobEvents
    job_id: str
    is_disconnected: Callable[[], Awaitable[bool]]


async def stream_events(feed: EventFeed, *, last_seq: int) -> AsyncIterator[str]:
    """Waits run in a worker thread (`asyncio.to_thread`), so the event loop never blocks."""
    history = await asyncio.to_thread(feed.events.read_since, feed.job_id, 0)
    if has_ended_by(history, last_seq):
        return
    # A cursor past the log would skip the terminal event when it comes; one rule then covers
    # every path: deliver until the `job` event.
    seq = min(last_seq, history[-1].seq if history else 0)
    while not await feed.is_disconnected():
        batch = await asyncio.to_thread(feed.events.wait_for, feed.job_id, seq, KEEP_ALIVE_SECONDS)
        if not batch:
            yield KEEP_ALIVE_COMMENT
        for event in batch:
            yield format_event(event)
            seq = event.seq
            if event.kind == EventKind.JOB:
                return


def has_ended_by(history: Sequence[JobEvent], seq: int) -> bool:
    """The client already holds the terminal event, so waiting would only send keep-alives."""
    return any(event.kind == EventKind.JOB and event.seq <= seq for event in history)


def format_event(event: JobEvent) -> str:
    return f"id: {event.seq}\ndata: {to_progress_event(event).model_dump_json()}\n\n"


def reject_unbounded_request(request: Request, limit: int) -> None:
    """Only a declared length bounds what the multipart parser spools, so it is required."""
    declared = request.headers.get("content-length", "")
    if not declared.isdigit():
        raise HTTPException(status.HTTP_411_LENGTH_REQUIRED, "Content-Length is required")
    if int(declared) > 2 * limit + MULTIPART_OVERHEAD_BYTES:
        raise payload_too_large(limit)


def payload_too_large(limit: int) -> HTTPException:
    return HTTPException(
        status.HTTP_413_CONTENT_TOO_LARGE, f"each image must be at most {limit} bytes"
    )


async def read_uploads(form: FormData, limit: int) -> dict[str, bytes]:
    """Both parts as bytes; every size check runs before any presence check, so 413 wins."""
    uploads: dict[str, bytes] = {}
    for name in UPLOAD_FIELDS:
        part = form.get(name)
        if isinstance(part, UploadFile):
            uploads[name] = await read_part(part, limit)
    for name in UPLOAD_FIELDS:
        if name not in uploads:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"{name}: an image file is required"
            )
    return uploads


async def read_part(part: UploadFile, limit: int) -> bytes:
    """Reads one byte past the limit, so an oversize part is never loaded whole."""
    content = await part.read(limit + 1)
    if len(content) > limit:
        raise payload_too_large(limit)
    return content


def validate_images(uploads: dict[str, bytes], limits: ImageLimits) -> None:
    """The same checks the parser runs, synchronously, so a bad image never becomes a job."""
    for name in UPLOAD_FIELDS:
        try:
            check_upload(uploads[name], limits)
        except InvalidImageError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{name}: {error}") from None


def check_upload(content: bytes, limits: ImageLimits) -> None:
    if len(content) > limits.max_bytes:
        raise InvalidImageError(f"image exceeds {limits.max_bytes} bytes")
    inspect_image(content, detect_mime_type(content), limits)


def enqueue_job(runtime: ApiRuntime, owner: str, uploads: dict[str, bytes]) -> Job:
    """The job exists before the task can start; a rejected submit takes it back."""
    job = Job.create(owner, now=runtime.clock())
    images = UploadedImages(original=uploads["original"], amendment=uploads["amendment"])
    runtime.store.create(job)
    try:
        runtime.runner.submit(lambda: execute_job(job.id, images, runtime.services))
    except QueueFullError:
        runtime.store.discard(job.id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "the analysis queue is full; retry later",
            headers={"Retry-After": str(QUEUE_RETRY_AFTER_SECONDS)},
        ) from None
    return job


def describe_job(job: Job) -> dict[str, object]:
    body: dict[str, object] = {
        "id": job.id,
        "status": job.state.value,
        "created_at": format_event_time(job.created_at),
        "trace_id": job.trace_id,
    }
    if job.state is JobState.DONE and job.output is not None:
        body["output"] = job.output.model_dump(mode="json")
    if job.error is not None:
        body["error"] = dict(job.error)
    return body


def create_app(build_runtime: Callable[[], ApiRuntime] = build_api_runtime) -> FastAPI:
    """The runtime is built inside the lifespan, so importing this module reads no settings."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = build_runtime()
        app.state.runtime = runtime
        try:
            yield
        finally:
            runtime.close()

    app = FastAPI(title="LegalMove", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
