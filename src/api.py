"""HTTP API (FastAPI), the second composition root: jobs run in the background; poll or stream."""

import hmac
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, Security, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, UploadFile

from api_contract import (
    LENGTH_REQUIRED_DETAIL,
    MISSING_IMAGE_DETAIL,
    NOT_FOUND_DETAIL,
    QUEUE_FULL_DETAIL,
    RATE_LIMITED_DETAIL,
    TOO_LARGE_DETAIL,
    UNAUTHORIZED_DETAIL,
    AnalysisBody,
    AnalysisCreated,
    document_problems,
)
from api_runtime import ApiRuntime, build_api_runtime, expire_jobs
from api_settings import hash_api_key, load_api_settings
from composition import build_image_limits
from errors import InvalidImageError
from event_stream import EventFeed, parse_last_event_id, stream_events
from image_parser import ImageLimits, detect_mime_type, inspect_image
from job_runner import UploadedImages, execute_job
from jobs import Job, JobState, ProgressEvent, QueueFullError, format_event_time

API_KEY_HEADER = "X-API-Key"
UPLOAD_FIELDS = ("original", "amendment")
# Multipart framing and the form fields around the two files.
MULTIPART_OVERHEAD_BYTES = 1_048_576
QUEUE_RETRY_AFTER_SECONDS = 5

api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)
router = APIRouter()


def get_runtime(request: Request) -> ApiRuntime:
    runtime: ApiRuntime = request.app.state.runtime
    return runtime


Runtime = Annotated[ApiRuntime, Depends(get_runtime)]


def authenticate(runtime: Runtime, key: Annotated[str | None, Security(api_key_header)]) -> str:
    """The hash of the caller's key; the digests are compared in constant time."""
    if key is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, UNAUTHORIZED_DETAIL)
    candidate = hash_api_key(key)
    matches = [hmac.compare_digest(candidate, known) for known in runtime.settings.key_hashes]
    if not any(matches):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, UNAUTHORIZED_DETAIL)
    return candidate


Owner = Annotated[str, Depends(authenticate)]


def limit_rate(runtime: Runtime, owner: Owner) -> str:
    if not runtime.limiter.consume(owner):
        retry_after = str(runtime.settings.api_rate_limit_window_seconds)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            RATE_LIMITED_DETAIL,
            headers={"Retry-After": retry_after},
        )
    return owner


RateLimitedOwner = Annotated[str, Depends(limit_rate)]


@router.get("/health")
def check_health() -> dict[str, str]:
    """Liveness only: it touches neither Langfuse nor OpenAI."""
    return {"status": "ok"}


@router.post(
    "/analyses",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_202_ACCEPTED: {"model": AnalysisCreated},
        **document_problems(401, 411, 413, 422, 429, 503),
    },
)
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


@router.get(
    "/analyses/{job_id}",
    response_model=None,
    responses={status.HTTP_200_OK: {"model": AnalysisBody}, **document_problems(401, 404)},
)
def read_analysis(job_id: str, runtime: Runtime, owner: Owner) -> dict[str, object]:
    job = runtime.store.get(job_id, owner)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND_DETAIL)
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
    responses=document_problems(401, 404),
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND_DETAIL)
    feed = EventFeed(events=runtime.events, job_id=job_id, is_disconnected=request.is_disconnected)
    return StreamingResponse(
        stream_events(feed, last_seq=parse_last_event_id(last_event_id)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def reject_unbounded_request(request: Request, limit: int) -> None:
    """Only a declared length bounds what the multipart parser spools, so it is required."""
    declared = request.headers.get("content-length", "")
    if not declared.isdigit():
        raise HTTPException(status.HTTP_411_LENGTH_REQUIRED, LENGTH_REQUIRED_DETAIL)
    if int(declared) > 2 * limit + MULTIPART_OVERHEAD_BYTES:
        raise payload_too_large(limit)


def payload_too_large(limit: int) -> HTTPException:
    return HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, TOO_LARGE_DETAIL.format(limit=limit))


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
                status.HTTP_422_UNPROCESSABLE_CONTENT, MISSING_IMAGE_DETAIL.format(name=name)
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
            QUEUE_FULL_DETAIL,
            headers={"Retry-After": str(QUEUE_RETRY_AFTER_SECONDS)},
        ) from None
    return job


def describe_change_details(job: Job) -> dict[str, object]:
    """Literal quotes, unmasked on purpose: the caller owns the documents (plan decision 114)."""
    details: dict[str, object] = {}
    if job.changes is not None:
        details["changes"] = [change.model_dump(mode="json") for change in job.changes]
    if job.amendment_kind is not None:
        details["amendment_kind"] = job.amendment_kind.value
    return details


def describe_job(job: Job) -> dict[str, object]:
    body: dict[str, object] = {
        "id": job.id,
        "status": job.state.value,
        "created_at": format_event_time(job.created_at),
        "trace_id": job.trace_id,
    }
    if job.state is JobState.DONE and job.output is not None:
        body["output"] = job.output.model_dump(mode="json")
        body.update(describe_change_details(job))
    if job.error is not None:
        body["error"] = dict(job.error)
    return body


def build_default_runtime() -> ApiRuntime:
    """Fails start-up on bad settings or an unreachable Langfuse; no request ever sees that."""
    return build_api_runtime(load_api_settings())


def create_app(build_runtime: Callable[[], ApiRuntime] = build_default_runtime) -> FastAPI:
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
