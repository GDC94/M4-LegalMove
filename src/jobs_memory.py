"""In-memory adapters of the job ports: store with TTL, event log and sliding-window limiter."""

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence

from jobs import Job, JobEvent, JobState, JobUpdate, is_terminal


class InMemoryJobStore:
    """A lock-protected dict; terminal jobs live `ttl_seconds` after they finished."""

    def __init__(self, *, ttl_seconds: float, clock: Callable[[], float] = time.time) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def create(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def get(self, job_id: str, owner_key_hash: str) -> Job | None:
        """Checks only this job, so a poll never pays for a full sweep; `expire` does that."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and self._is_expired(job, self._clock()):
                del self._jobs[job_id]
                job = None
        return job if job is not None and job.owner_key_hash == owner_key_hash else None

    def transition(self, job_id: str, target: JobState, update: JobUpdate | None = None) -> Job:
        with self._lock:
            moved = self._jobs[job_id].transition(target, now=self._clock(), update=update)
            self._jobs[job_id] = moved
            return moved

    def record_trace(self, job_id: str, trace_id: str) -> None:
        """Not a transition: a running job shows its trace before it finishes."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                self._jobs[job_id] = job.with_trace(trace_id)

    def discard(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)

    def expire(self) -> int:
        with self._lock:
            return self._sweep()

    def _sweep(self) -> int:
        now = self._clock()
        expired = [job_id for job_id, job in self._jobs.items() if self._is_expired(job, now)]
        for job_id in expired:
            del self._jobs[job_id]
        return len(expired)

    def _is_expired(self, job: Job, now: float) -> bool:
        return (
            is_terminal(job.state)
            and job.finished_at is not None
            and now - job.finished_at >= self._ttl_seconds
        )


class InMemoryJobEvents:
    """Append-only list per job with a growing `seq`; a Condition wakes the waiting readers."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._events: dict[str, list[JobEvent]] = defaultdict(list)

    def append(self, job_id: str, kind: str, payload: Mapping[str, object]) -> JobEvent:
        with self._condition:
            log = self._events[job_id]
            event = JobEvent(seq=len(log) + 1, kind=kind, payload=dict(payload))
            log.append(event)
            self._condition.notify_all()
            return event

    def read_since(self, job_id: str, seq: int) -> Sequence[JobEvent]:
        with self._condition:
            return self._after(job_id, seq)

    def wait_for(self, job_id: str, seq: int, timeout_seconds: float) -> Sequence[JobEvent]:
        with self._condition:
            self._condition.wait_for(lambda: bool(self._after(job_id, seq)), timeout_seconds)
            return self._after(job_id, seq)

    def _after(self, job_id: str, seq: int) -> list[JobEvent]:
        # `.get`, not indexing: a defaultdict would create a log for every id a client asks about.
        return [event for event in self._events.get(job_id, []) if event.seq > seq]


class InMemoryRateLimiter:
    """Sliding window per key hash: at most `requests` consumptions in `window_seconds`."""

    def __init__(
        self, *, requests: int, window_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._requests = requests
        self._window_seconds = window_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def consume(self, key_hash: str) -> bool:
        with self._lock:
            now = self._clock()
            hits = self._hits[key_hash]
            while hits and now - hits[0] >= self._window_seconds:
                hits.popleft()
            if len(hits) >= self._requests:
                return False
            hits.append(now)
            return True
