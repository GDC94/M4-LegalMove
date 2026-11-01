"""ProgressTracer: a TraceLifecycle decorator that logs one job's Stage events (decision 111)."""

import time
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING

from langchain_core.callbacks import BaseCallbackHandler

from jobs import EventKind, JobEvents, StageStatus, stage_event_payload
from observability import StageSpan, TraceAttributes, TraceLifecycle
from stages import StageName

# The metadata key image_parser records on a parse span (README, Tracing).
CACHE_HIT_KEY = "cache_hit"


class ProgressStageSpan:
    """Delegates every update untouched and remembers whether the Stage reported a cache hit."""

    def __init__(self, inner: StageSpan) -> None:
        self._inner = inner
        self.cache_hit = False

    def set_output(self, output: object) -> None:
        self._inner.set_output(output)

    def set_metadata(self, metadata: Mapping[str, object]) -> None:
        if CACHE_HIT_KEY in metadata:
            self.cache_hit = metadata[CACHE_HIT_KEY] is True
        self._inner.set_metadata(metadata)

    def set_error(self, error: BaseException) -> None:
        self._inner.set_error(error)


class ProgressTracer:
    """Delegates everything to the real tracer; `stage()` also logs started, done or failed."""

    def __init__(
        self,
        inner: TraceLifecycle,
        events: JobEvents,
        job_id: str,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._inner = inner
        self._events = events
        self._job_id = job_id
        self._clock = clock

    @contextmanager
    def stage(
        self,
        name: str,
        input_data: Mapping[str, object],
        metadata: Mapping[str, object] | None = None,
    ) -> Generator[StageSpan]:
        # The stream contract only knows StageName; any other span is traced, never published.
        stage = StageName(name) if name in StageName else None
        self._publish(stage, StageStatus.STARTED)
        try:
            with self._inner.stage(name, input_data, metadata) as span:
                progress_span = ProgressStageSpan(span)
                yield progress_span
        except BaseException:
            self._publish(stage, StageStatus.FAILED)
            raise
        self._publish(stage, StageStatus.DONE, cached=progress_span.cache_hit)

    def open_root(
        self,
        input_data: Mapping[str, object],
        metadata: Mapping[str, object],
        attributes: TraceAttributes,
    ) -> AbstractContextManager[StageSpan]:
        return self._inner.open_root(input_data, metadata, attributes)

    def flush(self) -> None:
        self._inner.flush()

    def score(self, name: str, value: float | str) -> None:
        self._inner.score(name, value)

    @property
    def trace_id(self) -> str | None:
        return self._inner.trace_id

    @property
    def callbacks(self) -> Sequence[BaseCallbackHandler]:
        return self._inner.callbacks

    def _publish(
        self, stage: StageName | None, status: StageStatus, *, cached: bool = False
    ) -> None:
        if stage is not None:
            payload = stage_event_payload(stage, status, at=self._clock(), cached=cached)
            self._events.append(self._job_id, EventKind.STAGE, payload)


if TYPE_CHECKING:
    # Static Liskov check: the decorator stands wherever the real tracer and span stand.
    _PROGRESS_TRACER: type[TraceLifecycle] = ProgressTracer
    _PROGRESS_STAGE_SPAN: type[StageSpan] = ProgressStageSpan
