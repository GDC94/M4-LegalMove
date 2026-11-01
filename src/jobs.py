"""Job domain: the lifecycle of one asynchronous analysis and the ports around it (plan §4.5)."""

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

from models import ContractChangeOutput


class JobState(StrEnum):
    """Extensible on purpose: 16b adds progress, never cancellation (decision 108)."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    HUMAN_REVIEW = "human_review"
    FAILED = "failed"


TERMINAL_STATES = frozenset({JobState.DONE, JobState.HUMAN_REVIEW, JobState.FAILED})
ALLOWED_TRANSITIONS: Mapping[JobState, frozenset[JobState]] = {
    JobState.PENDING: frozenset({JobState.RUNNING}),
    JobState.RUNNING: TERMINAL_STATES,
}


def is_terminal(state: JobState) -> bool:
    return state in TERMINAL_STATES


class InvalidTransitionError(Exception):
    """A job asked to move along an edge the lifecycle does not have."""


class QueueFullError(Exception):
    """The runner has no room for another pending job."""


@dataclass(frozen=True)
class JobUpdate:
    """What a transition may attach to the job: the trace and the terminal body."""

    trace_id: str | None = None
    output: ContractChangeOutput | None = None
    error: Mapping[str, object] | None = None


@dataclass(frozen=True)
class Job:
    """One analysis. `id` is also the run_id of the envelope and the Langfuse session_id."""

    id: str
    owner_key_hash: str
    state: JobState
    created_at: float
    trace_id: str | None = None
    output: ContractChangeOutput | None = None
    error: Mapping[str, object] | None = None
    finished_at: float | None = None

    @classmethod
    def create(cls, owner_key_hash: str, *, now: float) -> Job:
        return cls(
            id=uuid.uuid4().hex,
            owner_key_hash=owner_key_hash,
            state=JobState.PENDING,
            created_at=now,
        )

    def with_trace(self, trace_id: str) -> Job:
        """The same job, state untouched, now pointing at its trace (known once the root opens)."""
        return replace(self, trace_id=trace_id)

    def transition(self, target: JobState, *, now: float, update: JobUpdate | None = None) -> Job:
        """A new Job in `target`, or InvalidTransitionError; terminal states record `now`."""
        if target not in ALLOWED_TRANSITIONS.get(self.state, frozenset()):
            raise InvalidTransitionError(f"{self.state.value} -> {target.value}")
        update = update or JobUpdate()
        return replace(
            self,
            state=target,
            trace_id=update.trace_id or self.trace_id,
            output=update.output,
            error=update.error,
            finished_at=now if is_terminal(target) else None,
        )


@dataclass(frozen=True)
class JobEvent:
    """One entry of a job's append-only event log; `seq` grows by one per job (used from 16b)."""

    seq: int
    kind: str
    payload: Mapping[str, object]


class JobStore(Protocol):
    """Where jobs live. Reads are bound to the owner key: another key sees nothing."""

    def create(self, job: Job) -> None: ...

    def get(self, job_id: str, owner_key_hash: str) -> Job | None: ...

    def transition(self, job_id: str, target: JobState, update: JobUpdate | None = None) -> Job: ...

    def record_trace(self, job_id: str, trace_id: str) -> None: ...

    def discard(self, job_id: str) -> None: ...

    def expire(self) -> int: ...


class JobEvents(Protocol):
    """Append-only event log per job, readable from a `seq` and awaitable."""

    def append(self, job_id: str, kind: str, payload: Mapping[str, object]) -> JobEvent: ...

    def read_since(self, job_id: str, seq: int) -> Sequence[JobEvent]: ...

    def wait_for(self, job_id: str, seq: int, timeout_seconds: float) -> Sequence[JobEvent]: ...


class JobRunner(Protocol):
    """Runs jobs in the background with bounded concurrency; raises QueueFullError when full."""

    def submit(self, work: Callable[[], None]) -> None: ...

    def shutdown(self) -> None: ...


class RateLimiter(Protocol):
    """Consumes one request of a key's allowance; False means the key is over its limit."""

    def consume(self, key_hash: str) -> bool: ...
