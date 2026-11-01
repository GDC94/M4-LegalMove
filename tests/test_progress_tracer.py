"""ProgressTracer: publishes Stage events and leaves the trace untouched (decision 111)."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field

import pytest
from langchain_core.callbacks import BaseCallbackHandler

from cli_doubles import RecordingLifecycleTracer
from errors import UpstreamError
from jobs_memory import InMemoryJobEvents
from observability import LangfuseTracer, PipelineTracer, TraceAttributes
from progress_tracer import ProgressTracer
from stages import StageName

JOB_ID = "job-1"
ATTRIBUTES = TraceAttributes(session_id=JOB_ID, version="0.1.0", environment="development")


class Ticks:
    """A clock that moves one second per reading, from the epoch."""

    def __init__(self) -> None:
        self.now = -1.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


def decorate(inner: RecordingLifecycleTracer) -> tuple[ProgressTracer, InMemoryJobEvents]:
    events = InMemoryJobEvents()
    return ProgressTracer(inner, events, JOB_ID, clock=Ticks()), events


def published(events: InMemoryJobEvents) -> list[tuple[int, str, object, object, object]]:
    return [
        (
            event.seq,
            event.kind,
            event.payload.get("stage"),
            event.payload["status"],
            event.payload["at"],
        )
        for event in events.read_since(JOB_ID, 0)
    ]


def test_a_stage_publishes_started_then_done_with_a_growing_seq() -> None:
    tracer, events = decorate(RecordingLifecycleTracer())

    with tracer.stage(StageName.PARSE_ORIGINAL_CONTRACT, {"path": "original"}):
        assert [event.payload["status"] for event in events.read_since(JOB_ID, 0)] == ["started"]

    assert published(events) == [
        (1, "stage", "parse_original_contract", "started", "1970-01-01T00:00:00.000Z"),
        (2, "stage", "parse_original_contract", "done", "1970-01-01T00:00:01.000Z"),
    ]


def test_a_failing_stage_publishes_failed_and_reraises_the_original_error() -> None:
    tracer, events = decorate(RecordingLifecycleTracer())
    error = UpstreamError("vision call failed")

    with pytest.raises(UpstreamError) as raised, tracer.stage(StageName.EXTRACTION_AGENT, {}):
        raise error

    assert raised.value is error
    assert [(seq, status) for seq, _, _, status, _ in published(events)] == [
        (1, "started"),
        (2, "failed"),
    ]


def test_every_stage_of_a_run_publishes_in_order() -> None:
    tracer, events = decorate(RecordingLifecycleTracer())

    for stage in StageName:
        with tracer.stage(stage, {}):
            pass

    assert [(stage, status) for _, _, stage, status, _ in published(events)] == [
        (stage.value, status) for stage in StageName for status in ("started", "done")
    ]


def test_a_name_outside_stage_name_is_traced_but_not_published() -> None:
    inner = RecordingLifecycleTracer()
    tracer, events = decorate(inner)

    with tracer.stage("ad-hoc", {}):
        pass

    assert [stage.name for stage in inner.stages] == ["ad-hoc"]
    assert events.read_since(JOB_ID, 0) == []


def test_the_wrapped_span_delegates_output_metadata_and_error() -> None:
    inner = RecordingLifecycleTracer()
    tracer, _ = decorate(inner)
    error = ValueError("x")

    with tracer.stage(StageName.OUTPUT_VALIDATION, {}, {"a": 1}) as span:
        span.set_output({"ok": True})
        span.set_metadata({"b": 2})
        span.set_error(error)

    [recorded] = inner.stages
    assert recorded.output == {"ok": True}
    assert recorded.metadata == {"a": 1, "b": 2}
    assert recorded.error is error


@dataclass
class CallbackTracer(RecordingLifecycleTracer):
    """The recording tracer plus one LangChain handler, so the delegation is observable."""

    handler: BaseCallbackHandler = field(default_factory=BaseCallbackHandler)

    @property
    def callbacks(self) -> Sequence[BaseCallbackHandler]:
        return (self.handler,)


def test_root_flush_score_trace_id_and_callbacks_pass_through_untouched() -> None:
    inner = CallbackTracer(current_trace_id="trace-xyz")
    tracer, events = decorate(inner)

    with tracer.open_root({"original": "o"}, {"run_id": JOB_ID}, ATTRIBUTES) as root:
        root.set_output({"done": True})
    tracer.score("diff_agreement", 1.0)
    tracer.flush()

    assert tracer.trace_id == "trace-xyz"
    assert tracer.callbacks == (inner.handler,)
    assert [(stage.name, stage.output) for stage in inner.stages] == [
        ("contract-analysis", {"done": True})
    ]
    assert inner.roots[0][1] == ATTRIBUTES
    assert inner.scores == [("diff_agreement", 1.0)]
    assert inner.flushes == 1
    assert events.read_since(JOB_ID, 0) == []  # the root is not a Stage


def run_scenario(tracer: PipelineTracer) -> None:
    """Two Stages, one of them failing, with span updates and a score."""
    with tracer.stage(StageName.PARSE_ORIGINAL_CONTRACT, {"path": "o"}, {"m": 1}) as span:
        span.set_output("text")
        span.set_metadata({"cache_hit": False})
    with pytest.raises(UpstreamError), tracer.stage(StageName.EXTRACTION_AGENT, {}):
        raise SCENARIO_ERROR
    tracer.score("schema_valid", "true")


SCENARIO_ERROR = UpstreamError("extraction call failed")


def test_the_recording_tracer_sees_the_same_trace_with_and_without_the_decorator() -> None:
    plain, decorated = RecordingLifecycleTracer(), RecordingLifecycleTracer()

    run_scenario(plain)
    run_scenario(decorate(decorated)[0])

    assert decorated.stages == plain.stages
    assert decorated.scores == plain.scores


class FakeObservation:
    def __init__(self) -> None:
        self.updates: list[dict[str, object]] = []

    def update(self, **kwargs: object) -> None:
        self.updates.append(kwargs)


class FakeLangfuseClient:
    """Records the observations the Langfuse adapter opens and how it updates them."""

    def __init__(self) -> None:
        self.observations: list[tuple[dict[str, object], FakeObservation]] = []
        self.scores: list[dict[str, object]] = []

    @contextmanager
    def start_as_current_observation(self, **kwargs: object) -> Iterator[FakeObservation]:
        observation = FakeObservation()
        self.observations.append((kwargs, observation))
        yield observation

    def score_current_trace(self, **kwargs: object) -> None:
        self.scores.append(kwargs)

    def get_current_trace_id(self) -> str:
        return "trace-abc"

    def recorded(self) -> list[tuple[dict[str, object], list[dict[str, object]]]]:
        return [(kwargs, observation.updates) for kwargs, observation in self.observations]


def test_the_langfuse_adapter_emits_the_same_observations_with_and_without_the_decorator() -> None:
    plain_client, decorated_client = FakeLangfuseClient(), FakeLangfuseClient()
    # The fake stands in for the Langfuse client, which has no Protocol to type it against.
    plain = LangfuseTracer(plain_client, BaseCallbackHandler())  # type: ignore[arg-type]
    decorated = LangfuseTracer(decorated_client, BaseCallbackHandler())  # type: ignore[arg-type]

    run_scenario(plain)
    run_scenario(ProgressTracer(decorated, InMemoryJobEvents(), JOB_ID, clock=Ticks()))

    assert decorated_client.recorded() == plain_client.recorded()
    assert decorated_client.scores == plain_client.scores


def test_events_go_to_the_log_of_this_job_only() -> None:
    events = InMemoryJobEvents()
    tracer = ProgressTracer(RecordingLifecycleTracer(), events, "job-7", clock=Ticks())

    with tracer.stage(StageName.EXTRACTION_AGENT, {}):
        pass

    assert events.read_since(JOB_ID, 0) == []
    assert [event.seq for event in events.read_since("job-7", 0)] == [1, 2]


def test_a_stage_whose_span_records_a_cache_hit_publishes_done_with_cached() -> None:
    tracer, events = decorate(RecordingLifecycleTracer())

    with tracer.stage(StageName.PARSE_ORIGINAL_CONTRACT, {}) as span:
        span.set_metadata({"cache_hit": True, "illegible_count": 0})
        span.set_metadata({"cache_write_failed": True})

    started, done = events.read_since(JOB_ID, 0)
    assert "cached" not in started.payload
    assert done.payload["status"] == "done"
    assert done.payload["cached"] is True


@pytest.mark.parametrize("metadata", [{"cache_hit": False}, {"illegible_count": 0}, {}])
def test_a_stage_without_a_cache_hit_publishes_done_without_cached(
    metadata: dict[str, object],
) -> None:
    tracer, events = decorate(RecordingLifecycleTracer())

    with tracer.stage(StageName.PARSE_AMENDMENT_CONTRACT, {}) as span:
        span.set_metadata(metadata)

    assert all("cached" not in event.payload for event in events.read_since(JOB_ID, 0))


def test_a_failed_stage_never_claims_the_cache_even_after_a_hit() -> None:
    tracer, events = decorate(RecordingLifecycleTracer())

    with pytest.raises(UpstreamError), tracer.stage(StageName.PARSE_ORIGINAL_CONTRACT, {}) as span:
        span.set_metadata({"cache_hit": True})
        raise SCENARIO_ERROR

    assert all("cached" not in event.payload for event in events.read_since(JOB_ID, 0))
