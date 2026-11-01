"""In-memory adapters: job store with TTL, event log and sliding-window rate limiter."""

import threading

import pytest

from jobs import InvalidTransitionError, Job, JobState, JobUpdate
from jobs_memory import InMemoryJobEvents, InMemoryJobStore, InMemoryRateLimiter

OWNER = "a" * 64
OTHER = "b" * 64
TTL = 3600


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_store(clock: FakeClock) -> InMemoryJobStore:
    return InMemoryJobStore(ttl_seconds=TTL, clock=clock)


def test_a_stored_job_is_read_back_by_id_and_owner() -> None:
    clock = FakeClock()
    store = make_store(clock)
    job = Job.create(OWNER, now=clock())

    store.create(job)

    assert store.get(job.id, OWNER) == job


def test_another_key_and_an_unknown_id_read_nothing() -> None:
    clock = FakeClock()
    store = make_store(clock)
    job = Job.create(OWNER, now=clock())
    store.create(job)

    assert store.get(job.id, OTHER) is None
    assert store.get("0" * 32, OWNER) is None


def test_transition_updates_the_stored_job_and_validates_the_edge() -> None:
    clock = FakeClock()
    store = make_store(clock)
    job = Job.create(OWNER, now=clock())
    store.create(job)

    running = store.transition(job.id, JobState.RUNNING)
    failed = store.transition(
        job.id, JobState.FAILED, JobUpdate(error={"error": "UnexpectedError"}, trace_id="t1")
    )

    assert running.state is JobState.RUNNING
    assert store.get(job.id, OWNER) == failed
    assert failed.trace_id == "t1"
    with pytest.raises(InvalidTransitionError):
        store.transition(job.id, JobState.RUNNING)


def test_a_terminal_job_expires_after_the_ttl_and_is_swept_on_read() -> None:
    clock = FakeClock()
    store = make_store(clock)
    job = Job.create(OWNER, now=clock())
    store.create(job)
    store.transition(job.id, JobState.RUNNING)
    store.transition(job.id, JobState.FAILED, JobUpdate(error={}))

    clock.now += TTL - 1
    assert store.get(job.id, OWNER) is not None
    clock.now += 1

    assert store.get(job.id, OWNER) is None


def test_the_ttl_counts_from_the_end_of_the_job_not_from_its_creation() -> None:
    clock = FakeClock()
    store = make_store(clock)
    job = Job.create(OWNER, now=clock())
    store.create(job)
    store.transition(job.id, JobState.RUNNING)
    clock.now += TTL * 2  # a long run must not make the job vanish while it is live

    assert store.get(job.id, OWNER) is not None

    store.transition(job.id, JobState.FAILED, JobUpdate(error={}))
    clock.now += TTL - 1

    assert store.get(job.id, OWNER) is not None


def test_expire_reports_how_many_jobs_it_dropped() -> None:
    clock = FakeClock()
    store = make_store(clock)
    for _ in range(2):
        job = Job.create(OWNER, now=clock())
        store.create(job)
        store.transition(job.id, JobState.RUNNING)
        store.transition(job.id, JobState.FAILED, JobUpdate(error={}))
    clock.now += TTL

    assert store.expire() == 2
    assert store.expire() == 0


def test_discard_forgets_a_job_and_ignores_an_unknown_one() -> None:
    clock = FakeClock()
    store = make_store(clock)
    job = Job.create(OWNER, now=clock())
    store.create(job)

    store.discard(job.id)
    store.discard(job.id)

    assert store.get(job.id, OWNER) is None


def test_the_rate_limiter_allows_n_requests_per_window_and_rejects_the_next() -> None:
    clock = FakeClock()
    limiter = InMemoryRateLimiter(requests=3, window_seconds=60, clock=clock)

    results = [limiter.consume(OWNER) for _ in range(4)]

    assert results == [True, True, True, False]


def test_the_rate_limiter_allows_again_when_the_window_slides() -> None:
    clock = FakeClock()
    limiter = InMemoryRateLimiter(requests=2, window_seconds=60, clock=clock)
    limiter.consume(OWNER)
    clock.now += 30
    limiter.consume(OWNER)
    assert limiter.consume(OWNER) is False

    clock.now += 30  # the first request leaves the window, the second is still inside

    assert limiter.consume(OWNER) is True
    assert limiter.consume(OWNER) is False


def test_each_key_has_its_own_allowance() -> None:
    limiter = InMemoryRateLimiter(requests=1, window_seconds=60, clock=FakeClock())

    assert limiter.consume(OWNER) is True
    assert limiter.consume(OTHER) is True
    assert limiter.consume(OWNER) is False


def test_events_get_a_growing_seq_per_job_and_are_read_from_a_seq() -> None:
    events = InMemoryJobEvents()

    first = events.append("job-1", "stage", {"stage": "parse"})
    second = events.append("job-1", "stage", {"stage": "extraction"})
    other = events.append("job-2", "stage", {})

    assert (first.seq, second.seq, other.seq) == (1, 2, 1)
    assert events.read_since("job-1", 0) == [first, second]
    assert events.read_since("job-1", 1) == [second]
    assert events.read_since("unknown", 0) == []


def test_wait_for_returns_immediately_when_there_is_something_to_read() -> None:
    events = InMemoryJobEvents()
    event = events.append("job-1", "stage", {})

    assert events.wait_for("job-1", 0, timeout_seconds=5) == [event]


def test_wait_for_wakes_up_when_an_event_is_appended() -> None:
    events = InMemoryJobEvents()
    waiter_result: list[object] = []

    waiter = threading.Thread(
        target=lambda: waiter_result.extend(events.wait_for("job-1", 0, timeout_seconds=5))
    )
    waiter.start()
    appended = events.append("job-1", "stage", {})
    waiter.join(timeout=5)

    assert waiter_result == [appended]


def test_wait_for_times_out_with_nothing() -> None:
    events = InMemoryJobEvents()

    assert events.wait_for("job-1", 0, timeout_seconds=0.01) == []


def finished_job(store: InMemoryJobStore, clock: FakeClock) -> Job:
    job = Job.create(OWNER, now=clock())
    store.create(job)
    store.transition(job.id, JobState.RUNNING)
    return store.transition(job.id, JobState.DONE)


def test_a_read_checks_only_the_requested_job_and_leaves_the_sweep_to_expire() -> None:
    clock = FakeClock()
    store = make_store(clock)
    polled, other = finished_job(store, clock), finished_job(store, clock)
    clock.now += TTL

    assert store.get(polled.id, OWNER) is None  # the requested job is dropped on read
    assert store.expire() == 1  # the other one was left for the sweep
    assert store.get(other.id, OWNER) is None


def test_recording_the_trace_id_keeps_the_state_and_is_not_a_transition() -> None:
    clock = FakeClock()
    store = make_store(clock)
    job = Job.create(OWNER, now=clock())
    store.create(job)
    store.transition(job.id, JobState.RUNNING)

    store.record_trace(job.id, "t1")

    stored = store.get(job.id, OWNER)
    assert stored is not None
    assert (stored.state, stored.trace_id, stored.finished_at) == (JobState.RUNNING, "t1", None)
    assert store.transition(job.id, JobState.DONE).trace_id == "t1"
