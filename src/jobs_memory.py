"""In-memory adapters of the job ports: store with TTL, event log and sliding-window limiter."""

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from threading import Condition

from jobs import Job, JobEvent, JobState, JobUpdate, is_terminal


class InMemoryJobStore:
    """A lock-protected dict; terminal jobs live `ttl_seconds` after they finished."""

    def __init__(self, *, ttl_seconds: float, clock: Callable[[], float] = time.time) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        # Dropped on read, not yet reported: the next sweep names them so callers can clean up.
        self._dropped: list[str] = []

    def create(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def get(self, job_id: str, owner_key_hash: str) -> Job | None:
        """Checks only this job, so a poll never pays for a full sweep; `expire` does that."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and self._is_expired(job, self._clock()):
                del self._jobs[job_id]
                self._dropped.append(job_id)
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

    def expire(self) -> list[str]:
        """The ids of every job dropped since the last call, swept now or on an earlier read."""
        with self._lock:
            expired, self._dropped = [*self._dropped, *self._sweep()], []
            return expired

    def _sweep(self) -> list[str]:
        now = self._clock()
        expired = [job_id for job_id, job in self._jobs.items() if self._is_expired(job, now)]
        for job_id in expired:
            del self._jobs[job_id]
        return expired

    def _is_expired(self, job: Job, now: float) -> bool:
        return (
            is_terminal(job.state)
            and job.finished_at is not None
            and now - job.finished_at >= self._ttl_seconds
        )


class JobLog:
    """One job's events, where `seq` is index + 1, and the Condition its readers wait on."""

    def __init__(self, lock: threading.Lock) -> None:
        self.events: list[JobEvent] = []
        self.condition = Condition(lock)

    def after(self, seq: int) -> list[JobEvent]:
        return self.events[max(seq, 0) :]


class InMemoryJobEvents:
    """Append-only list per job with a growing `seq`; an append wakes only that job's readers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._logs: dict[str, JobLog] = {}

    def append(self, job_id: str, kind: str, payload: Mapping[str, object]) -> JobEvent:
        with self._lock:
            log = self._log(job_id)
            event = JobEvent(seq=len(log.events) + 1, kind=kind, payload=dict(payload))
            log.events.append(event)
            log.condition.notify_all()
            return event

    def read_since(self, job_id: str, seq: int) -> Sequence[JobEvent]:
        with self._lock:
            # `.get`: a read never creates a log for an id a client merely asked about.
            log = self._logs.get(job_id)
            return log.after(seq) if log is not None else []

    def wait_for(self, job_id: str, seq: int, timeout_seconds: float) -> Sequence[JobEvent]:
        with self._lock:
            log = self._log(job_id)
            log.condition.wait_for(lambda: bool(log.after(seq)), timeout_seconds)
            return log.after(seq)

    def discard(self, job_id: str) -> None:
        with self._lock:
            self._logs.pop(job_id, None)

    def _log(self, job_id: str) -> JobLog:
        """Callers hold the lock; a reader may wait on a job that has not published yet."""
        log = self._logs.get(job_id)
        if log is None:
            log = self._logs[job_id] = JobLog(self._lock)
        return log


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
