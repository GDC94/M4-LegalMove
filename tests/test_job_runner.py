"""Job execution: outcome mapping, temp directory, flush, trace id and the bounded thread pool."""

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from budget import CallBudget
from cli_doubles import RecordingLifecycleTracer
from composition import Container
from errors import (
    InvalidExtractionError,
    ObservabilityError,
    UpstreamError,
    ValidationIssue,
)
from job_runner import JobServices, ThreadPoolJobRunner, UploadedImages, execute_job
from jobs import Job, JobState, QueueFullError
from jobs_memory import InMemoryJobStore
from models import DocumentRole, ParsedContract
from pipeline import PipelineStages
from pipeline_doubles import StubStages

OWNER = "a" * 64
IMAGES = UploadedImages(original=b"original-bytes", amendment=b"amendment-bytes")
ISSUE = ValidationIssue(code="ungrounded_quote", path="changes[0].original_quote", message="m")


class Harness:
    """One job through execute_job with doubles; records where the images were written."""

    def __init__(
        self,
        failures: dict[str, BaseException] | None = None,
        budget: CallBudget | None = None,
    ) -> None:
        self.tracer = RecordingLifecycleTracer()
        self.store = InMemoryJobStore(ttl_seconds=3600)
        self.paths: list[Path] = []
        self.contents: list[bytes] = []
        self.builds = 0
        self.build_error: Exception | None = None
        self._stub = StubStages(self.tracer, failures=failures or {})
        self._budget = budget
        self.job = Job.create(OWNER, now=0.0)
        self.store.create(self.job)

    def parse(self, path: Path, role: DocumentRole) -> ParsedContract:
        self.paths.append(path)
        self.contents.append(path.read_bytes())
        return self._stub.parse_contract_image(path, role)

    def build_container(self) -> Container:
        self.builds += 1
        if self.build_error:
            raise self.build_error
        ports = self._stub.ports()
        stages = PipelineStages(self.parse, ports.contextualize, ports.extract)
        return Container(
            tracer=self.tracer,
            stages=stages,
            budget=self._budget or CallBudget(300),
            environment="development",
            root_metadata={"extraction_model": "gpt-4o"},
        )

    def run(self) -> Job:
        execute_job(self.job.id, IMAGES, JobServices(self.store, self.build_container))
        stored = self.store.get(self.job.id, OWNER)
        assert stored is not None
        return stored


def test_a_successful_analysis_ends_done_with_the_public_output_and_the_trace_id() -> None:
    harness = Harness()

    job = harness.run()

    assert job.state is JobState.DONE
    assert job.output is not None
    assert job.output.sections_changed == ["1"]
    assert job.error is None
    assert job.trace_id == "trace-abc"


def test_the_images_reach_the_parser_as_files_with_the_uploaded_bytes() -> None:
    harness = Harness()

    harness.run()

    assert harness.contents == [IMAGES.original, IMAGES.amendment]
    assert harness.paths[0].parent == harness.paths[1].parent


def test_the_root_span_uses_the_job_id_as_session_and_run_id() -> None:
    harness = Harness()

    harness.run()

    [(root, attributes)] = harness.tracer.roots
    assert attributes.session_id == harness.job.id
    assert root.metadata["run_id"] == harness.job.id
    assert root.metadata["extraction_model"] == "gpt-4o"


def test_human_review_ends_with_the_cli_envelope_and_its_issues() -> None:
    harness = Harness(failures={"extraction_agent": InvalidExtractionError([ISSUE])})

    job = harness.run()

    assert job.state is JobState.HUMAN_REVIEW
    assert job.output is None
    assert job.error == {
        "error": "InvalidExtractionError",
        "stage": "extraction",
        "message": "extraction is still invalid after one retry: ungrounded_quote",
        "run_id": harness.job.id,
        "trace_id": "trace-abc",
        "issues": [
            {"code": "ungrounded_quote", "path": "changes[0].original_quote", "message": "m"}
        ],
    }


def test_a_domain_error_ends_failed_with_its_own_message() -> None:
    harness = Harness(failures={"parse_original_contract": UpstreamError("vision call failed")})

    job = harness.run()

    assert job.state is JobState.FAILED
    assert job.error is not None
    assert (job.error["error"], job.error["stage"]) == ("UpstreamError", "parse")
    assert job.error["message"] == "vision call failed"


def test_an_unexpected_error_ends_failed_with_a_sanitised_envelope_and_logs_the_stack(
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = Harness(failures={"extraction_agent": KeyError("Cláusula secreta")})

    with caplog.at_level(logging.ERROR, logger="legalmove"):
        job = harness.run()

    assert job.state is JobState.FAILED
    assert job.error is not None
    assert job.error["error"] == "UnexpectedError"
    assert job.error["message"] == "unexpected failure; see the logs"
    assert "secreta" not in repr(job.error)
    assert 'File "' in caplog.text
    assert "secreta" not in caplog.text


def test_an_exhausted_budget_ends_failed_with_the_upstream_message() -> None:
    ticks = iter([0.0, 400.0, 400.0, 400.0, 400.0])
    harness = Harness(budget=CallBudget(300, clock=lambda: next(ticks, 400.0)))

    job = harness.run()

    assert job.state is JobState.FAILED
    assert job.error is not None
    assert job.error["error"] == "UpstreamError"
    assert job.error["message"] == "pipeline time budget of 300 s exhausted"


def test_a_container_that_cannot_be_built_ends_failed_without_a_flush() -> None:
    harness = Harness()
    harness.build_error = ObservabilityError("tracer initialisation failed: ValueError")

    job = harness.run()

    assert job.state is JobState.FAILED
    assert job.error is not None
    assert job.error["error"] == "ObservabilityError"
    assert job.error["trace_id"] is None
    assert harness.tracer.flushes == 0


@pytest.mark.parametrize(
    "failure",
    [None, InvalidExtractionError([ISSUE]), UpstreamError("down"), KeyError("x")],
    ids=["done", "human_review", "failed", "unexpected"],
)
def test_the_temp_directory_is_gone_in_every_terminal_state(failure: BaseException | None) -> None:
    harness = Harness(failures={"extraction_agent": failure} if failure else {})

    harness.run()

    assert harness.paths
    assert not harness.paths[0].parent.exists()


def test_the_tracer_flushes_once_per_job_in_every_outcome() -> None:
    ok, failed = Harness(), Harness(failures={"extraction_agent": KeyError("x")})

    ok.run()
    failed.run()

    assert (ok.tracer.flushes, failed.tracer.flushes) == (1, 1)


def test_a_failed_flush_is_logged_and_keeps_the_terminal_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = Harness()
    harness.tracer.flush_error = ObservabilityError("trace flush failed: ConnectError")

    with caplog.at_level(logging.ERROR, logger="legalmove"):
        job = harness.run()

    assert job.state is JobState.DONE
    assert "trace flush failed" in caplog.text


def test_the_job_is_running_while_the_pipeline_works() -> None:
    harness = Harness()
    states: list[JobState] = []
    harness._stub.on_call = lambda _: states.append(
        harness.store.get(harness.job.id, OWNER).state  # type: ignore[union-attr]
    )

    harness.run()

    assert set(states) == {JobState.RUNNING}


def test_a_running_job_exposes_its_trace_id_as_soon_as_the_root_opens() -> None:
    harness = Harness()
    trace_ids: list[str | None] = []

    def record(_: object) -> None:
        stored = harness.store.get(harness.job.id, OWNER)
        trace_ids.append(stored.trace_id if stored else None)

    harness._stub.on_call = record

    harness.run()

    assert trace_ids
    assert set(trace_ids) == {"trace-abc"}


class Gate:
    """Holds worker threads inside a task until the test lets them go."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = threading.Semaphore(0)
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()

    def task(self) -> Callable[[], None]:
        def work() -> None:
            with self._lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            self.started.release()
            self.release.wait(timeout=5)
            with self._lock:
                self.active -= 1

        return work


def test_concurrency_never_exceeds_the_worker_limit() -> None:
    runner = ThreadPoolJobRunner(max_workers=2, max_pending=10)
    gate = Gate()
    for _ in range(5):
        runner.submit(gate.task())
    for _ in range(2):
        assert gate.started.acquire(timeout=5)

    assert gate.started.acquire(timeout=0.05) is False
    assert gate.peak == 2
    gate.release.set()
    runner.shutdown()
    assert gate.peak == 2


def test_the_queue_rejects_once_the_pending_limit_is_reached() -> None:
    runner = ThreadPoolJobRunner(max_workers=1, max_pending=2)
    gate = Gate()
    runner.submit(gate.task())
    assert gate.started.acquire(timeout=5)  # the worker is busy; what follows only queues

    runner.submit(gate.task())
    runner.submit(gate.task())
    with pytest.raises(QueueFullError):
        runner.submit(gate.task())

    gate.release.set()
    runner.shutdown()


def test_a_slot_frees_when_a_pending_job_starts() -> None:
    runner = ThreadPoolJobRunner(max_workers=1, max_pending=1)
    gate = Gate()
    runner.submit(gate.task())
    assert gate.started.acquire(timeout=5)
    runner.submit(gate.task())
    gate.release.set()
    assert gate.started.acquire(timeout=5)  # the queued job started, so the queue is empty

    runner.submit(lambda: None)
    runner.shutdown()


def test_a_task_that_raises_does_not_kill_the_pool(caplog: pytest.LogCaptureFixture) -> None:
    runner = ThreadPoolJobRunner(max_workers=1, max_pending=2)
    done = threading.Event()

    def explode() -> None:
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR, logger="legalmove"):
        runner.submit(explode)
        runner.submit(done.set)
        runner.shutdown()

    assert done.is_set()
    assert "RuntimeError" in caplog.text


def wait_for(condition: Callable[[], bool], timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


def test_shutdown_cancels_the_queued_jobs_and_lets_the_running_one_finish() -> None:
    runner = ThreadPoolJobRunner(max_workers=1, max_pending=5)
    gate = Gate()
    ran: list[str] = []
    runner.submit(gate.task())
    assert gate.started.acquire(timeout=5)
    runner.submit(lambda: ran.append("queued-1"))
    runner.submit(lambda: ran.append("queued-2"))

    stopping = threading.Thread(target=runner.shutdown)
    stopping.start()
    cancelled = wait_for(lambda: runner.pending == 0)  # cancelled jobs give their slot back
    gate.release.set()
    stopping.join(timeout=5)

    assert cancelled
    assert ran == []
    assert gate.active == 0  # the running job was not interrupted


def test_a_submit_after_shutdown_is_a_full_queue_and_takes_no_slot() -> None:
    runner = ThreadPoolJobRunner(max_workers=1, max_pending=2)
    runner.shutdown()

    with pytest.raises(QueueFullError):
        runner.submit(lambda: None)

    assert runner.pending == 0
