"""CLI doubles: a tracer with lifecycle and settings that never read the real .env."""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field

from config import Settings
from errors import ObservabilityError
from observability import RecordedStage, RecordingTracer, StageSpan, TraceAttributes


def fake_settings() -> Settings:
    """Fake keys and no .env: the real file holds secrets and must never be read by unit tests."""
    return Settings(
        _env_file=None,
        openai_api_key="sk-test",
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key="sk-lf-test",
    )


@dataclass
class RecordingLifecycleTracer(RecordingTracer):
    """Adds the root span and flush that only main.py may use."""

    current_trace_id: str | None = "trace-abc"
    flush_error: ObservabilityError | None = None
    on_flush: Callable[[], None] = lambda: None
    flushes: int = 0
    roots: list[tuple[RecordedStage, TraceAttributes]] = field(default_factory=list)

    @contextmanager
    def open_root(
        self,
        input_data: Mapping[str, object],
        metadata: Mapping[str, object],
        attributes: TraceAttributes,
    ) -> Iterator[StageSpan]:
        with self.stage("contract-analysis", input_data, metadata) as root:
            self.roots.append((self.stages[-1], attributes))
            yield root

    def flush(self) -> None:
        self.flushes += 1
        self.on_flush()
        if self.flush_error:
            raise self.flush_error

    @property
    def trace_id(self) -> str | None:
        return self.current_trace_id
