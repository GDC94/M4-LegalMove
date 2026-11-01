"""Job execution on a bounded thread pool; with main.py, the owner of the tracer lifecycle."""

import logging
import tempfile
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from composition import (
    Container,
    RunContext,
    build_envelope,
    describe_request,
    describe_result,
    log_unexpected_failure,
)
from config import APP_VERSION
from errors import NeedsHumanReviewError, ObservabilityError
from jobs import JobState, JobStore, JobUpdate, QueueFullError
from observability import TraceAttributes
from pipeline import AnalysisRequest, AnalysisResult, run_analysis

logger = logging.getLogger("legalmove")

JOB_DIRECTORY_PREFIX = "legalmove-job-"


@dataclass(frozen=True)
class UploadedImages:
    """The two validated uploads of a job; they only touch the disk inside the worker."""

    original: bytes
    amendment: bytes


@dataclass(frozen=True)
class JobServices:
    """What a worker needs: the store and a factory of one fresh Container (tracer, budget)."""

    store: JobStore
    build_container: Callable[[], Container]


@dataclass(frozen=True)
class Outcome:
    state: JobState
    update: JobUpdate


def execute_job(job_id: str, images: UploadedImages, services: JobServices) -> None:
    """Runs inside the worker thread; the job always ends in a terminal state."""
    services.store.transition(job_id, JobState.RUNNING)
    context = RunContext(run_id=job_id)
    container: Container | None = None
    try:
        with tempfile.TemporaryDirectory(prefix=JOB_DIRECTORY_PREFIX) as directory:
            container = services.build_container()
            request = write_images(Path(directory), images)
            outcome = analyse(container, request, context, trace_recorder(services.store, job_id))
    except Exception as error:
        outcome = describe_failure(error, context)
    try:
        services.store.transition(job_id, outcome.state, outcome.update)
    finally:
        if container is not None:
            flush_quietly(container)


def write_images(directory: Path, images: UploadedImages) -> AnalysisRequest:
    original, amendment = directory / "original", directory / "amendment"
    original.write_bytes(images.original)
    amendment.write_bytes(images.amendment)
    return AnalysisRequest(original_path=original, amendment_path=amendment)


def trace_recorder(store: JobStore, job_id: str) -> Callable[[str], None]:
    return lambda trace_id: store.record_trace(job_id, trace_id)


def analyse(
    container: Container,
    request: AnalysisRequest,
    context: RunContext,
    record_trace: Callable[[str], None],
) -> Outcome:
    result = run_in_root(container, request, context, record_trace)
    return Outcome(JobState.DONE, JobUpdate(trace_id=context.trace_id, output=result.output))


def run_in_root(
    container: Container,
    request: AnalysisRequest,
    context: RunContext,
    record_trace: Callable[[str], None],
) -> AnalysisResult:
    """Same root span as the CLI's; the job id is its run_id and its Langfuse session."""
    attributes = TraceAttributes(
        session_id=context.run_id, version=APP_VERSION, environment=container.environment
    )
    metadata = {"run_id": context.run_id, **container.root_metadata}
    tracer = container.tracer
    with tracer.open_root(describe_request(request), metadata, attributes) as root:
        context.trace_id = tracer.trace_id
        if context.trace_id is not None:
            record_trace(context.trace_id)
        try:
            result = run_analysis(request, container.stages, tracer, container.budget)
        except Exception as error:
            root.set_output(build_envelope(error, context))
            raise
        root.set_metadata(describe_result(result))
        root.set_output(result.output.model_dump(mode="json"))
        return result


def describe_failure(error: Exception, context: RunContext) -> Outcome:
    log_unexpected_failure(error)
    state = JobState.HUMAN_REVIEW if isinstance(error, NeedsHumanReviewError) else JobState.FAILED
    envelope = build_envelope(error, context)
    return Outcome(state, JobUpdate(trace_id=context.trace_id, error=envelope))


def flush_quietly(container: Container) -> None:
    try:
        container.tracer.flush()
    except ObservabilityError as error:
        logger.error("%s; the job result is kept", error)


class ThreadPoolJobRunner:
    """`max_workers` run at once and `max_pending` wait; the next submit is rejected."""

    def __init__(self, *, max_workers: int, max_pending: int) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="job")
        self._max_pending = max_pending
        self._pending = 0
        self._lock = threading.Lock()

    def submit(self, work: Callable[[], None]) -> None:
        """QueueFullError when the queue is full or the pool is shut down; either way, retry."""
        with self._lock:
            if self._pending >= self._max_pending:
                raise QueueFullError("job queue is full")
            self._pending += 1
        try:
            future = self._executor.submit(self._run, work)
        except RuntimeError:
            self._release_slot()
            raise QueueFullError("job runner is shut down") from None
        future.add_done_callback(self._release_if_cancelled)

    @property
    def pending(self) -> int:
        """Jobs submitted that have neither started nor been cancelled."""
        with self._lock:
            return self._pending

    def shutdown(self) -> None:
        """Queued jobs are cancelled (no temp dir exists yet); running jobs finish."""
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _release_slot(self) -> None:
        with self._lock:
            self._pending -= 1

    def _release_if_cancelled(self, future: Future[None]) -> None:
        # A cancelled job never reaches _run, so its slot is given back here.
        if future.cancelled():
            self._release_slot()

    def _run(self, work: Callable[[], None]) -> None:
        self._release_slot()
        try:
            work()
        # A job's own failure is already stored; this keeps a bug in `work` from vanishing.
        except Exception as error:
            logger.error("job task crashed: %s", type(error).__name__)
