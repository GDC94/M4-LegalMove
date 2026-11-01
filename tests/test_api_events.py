"""SSE progress stream: order, resume, close, keep-alive, auth and 404 (decision 111)."""

import asyncio
import json
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import httpx
import pytest
from fastapi.testclient import TestClient

import api
import api_runtime
from api import create_app
from api_doubles import KEY, OTHER_KEY, Harness, auth, make_settings, submit
from budget import CallBudget
from cli_doubles import RecordingLifecycleTracer
from composition import Container
from errors import UpstreamError
from event_stream import KEEP_ALIVE_COMMENT, KEEP_ALIVE_SECONDS, EventFeed, stream_events
from jobs import (
    EventKind,
    JobEvent,
    JobState,
    StageStatus,
    job_event_payload,
    stage_event_payload,
)
from jobs_memory import InMemoryJobEvents
from pipeline_doubles import StubStages
from stages import StageName

STAGES = [
    "parse_original_contract",
    "parse_amendment_contract",
    "contextualization_agent",
    "extraction_agent",
    "output_validation",
]
STAGE = StageName.EXTRACTION_AGENT


@dataclass(frozen=True)
class Frame:
    """One SSE frame as a browser would split it: its id, its data, or a comment."""

    id: str | None
    data: dict[str, object] | None
    comment: str | None


def parse_frames(body: str) -> list[Frame]:
    frames = []
    for block in body.split("\n\n"):
        if not block:
            continue
        fields = dict(line.split(": ", 1) for line in block.split("\n") if not line.startswith(":"))
        comments = [line[1:].strip() for line in block.split("\n") if line.startswith(":")]
        data = json.loads(fields["data"]) if "data" in fields else None
        frames.append(Frame(fields.get("id"), data, comments[0] if comments else None))
    return frames


def stream(
    client: TestClient, job_id: str, key: str = KEY, last_event_id: str | None = None
) -> httpx.Response:
    """The whole stream, read until the server closes it."""
    headers = auth(key) | ({"Last-Event-ID": last_event_id} if last_event_id else {})
    with client.stream("GET", f"/analyses/{job_id}/events", headers=headers) as response:
        response.read()
        return response


def statuses(frames: list[Frame]) -> list[tuple[object, object, object]]:
    return [
        (frame.data["kind"], frame.data.get("stage"), frame.data["status"])
        for frame in frames
        if frame.data
    ]


def finished_job(harness: Harness, client: TestClient) -> str:
    job_id = submit(client)
    harness.runner.run_all()
    return job_id


def expected_success() -> list[tuple[object, object, object]]:
    stage_events = [("stage", stage, status) for stage in STAGES for status in ("started", "done")]
    return [*stage_events, ("job", None, "done")]


def test_a_finished_job_streams_every_event_in_order_then_closes() -> None:
    harness = Harness()

    with harness.client() as client:
        response = stream(client, finished_job(harness, client))

    frames = parse_frames(response.text)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert statuses(frames) == expected_success()
    assert [frame.id for frame in frames] == [str(seq) for seq in range(1, 12)]
    assert [frame.data["seq"] for frame in frames if frame.data] == list(range(1, 12))


def test_each_event_carries_seq_kind_stage_status_and_at() -> None:
    harness = Harness()

    with harness.client() as client:
        frames = parse_frames(stream(client, finished_job(harness, client)).text)

    first, last = frames[0].data, frames[-1].data
    assert first is not None
    assert last is not None
    assert set(first) == {"seq", "kind", "stage", "status", "at"}
    assert set(last) == {"seq", "kind", "status", "at"}
    assert str(first["at"]).endswith("Z")


def test_a_failing_stage_streams_failed_and_the_failed_job_event() -> None:
    harness = Harness(failures={"parse_amendment_contract": UpstreamError("vision call failed")})

    with harness.client() as client:
        frames = parse_frames(stream(client, finished_job(harness, client)).text)

    assert statuses(frames) == [
        ("stage", "parse_original_contract", "started"),
        ("stage", "parse_original_contract", "done"),
        ("stage", "parse_amendment_contract", "started"),
        ("stage", "parse_amendment_contract", "failed"),
        ("job", None, "failed"),
    ]


@dataclass
class ReleasingEvents:
    """Keeps the stream's first batch, then releases the Stage the job is held in."""

    release: threading.Event
    inner: InMemoryJobEvents = field(default_factory=InMemoryJobEvents)
    first_batch: list[JobEvent] | None = None

    def append(self, job_id: str, kind: str, payload: Mapping[str, object]) -> JobEvent:
        return self.inner.append(job_id, kind, payload)

    def read_since(self, job_id: str, seq: int) -> Sequence[JobEvent]:
        return self.inner.read_since(job_id, seq)

    def discard(self, job_id: str) -> None:
        self.inner.discard(job_id)

    def wait_for(self, job_id: str, seq: int, timeout_seconds: float) -> Sequence[JobEvent]:
        batch = self.inner.wait_for(job_id, seq, timeout_seconds)
        if self.first_batch is None:
            self.first_batch = list(batch)
            self.release.set()
        return batch


def logged_statuses(events: Sequence[JobEvent]) -> list[tuple[object, object, object]]:
    return [(event.kind, event.payload.get("stage"), event.payload["status"]) for event in events]


def test_a_stream_opened_while_the_job_runs_receives_every_event_live() -> None:
    reached, release = threading.Event(), threading.Event()

    def hold_the_extraction(name: str) -> None:
        if name == "extraction_agent":
            reached.set()
            release.wait(timeout=5)

    events = ReleasingEvents(release)
    harness = Harness(events=events, on_stage=hold_the_extraction)

    with harness.client() as client:
        job_id = submit(client)
        worker = threading.Thread(target=harness.runner.run_all)
        worker.start()
        assert reached.wait(timeout=5)
        response = stream(client, job_id)
        worker.join(timeout=5)

    assert events.first_batch is not None
    assert logged_statuses(events.first_batch) == expected_success()[:7]
    assert statuses(parse_frames(response.text)) == expected_success()


def test_last_event_id_resumes_from_the_next_event_without_loss_or_duplicates() -> None:
    harness = Harness()

    with harness.client() as client:
        job_id = finished_job(harness, client)
        response = stream(client, job_id, last_event_id="4")

    frames = parse_frames(response.text)
    assert [frame.id for frame in frames] == [str(seq) for seq in range(5, 12)]
    assert statuses(frames) == expected_success()[4:]


def test_a_client_that_already_has_the_terminal_event_gets_an_empty_stream() -> None:
    harness = Harness()

    with harness.client() as client:
        job_id = finished_job(harness, client)
        response = stream(client, job_id, last_event_id="11")

    assert response.status_code == 200
    assert response.text == ""


def test_a_last_event_id_too_long_to_be_a_number_replays_from_the_start() -> None:
    harness = Harness()

    with harness.client() as client:
        job_id = finished_job(harness, client)
        response = stream(client, job_id, last_event_id="9" * 5000)

    assert response.status_code == 200
    assert parse_frames(response.text)[0].id == "1"


def test_an_unreadable_last_event_id_replays_from_the_start() -> None:
    harness = Harness()

    with harness.client() as client:
        job_id = finished_job(harness, client)
        response = stream(client, job_id, last_event_id="nope")

    assert parse_frames(response.text)[0].id == "1"


@dataclass
class ScriptedEvents:
    """A JobEvents whose waits answer from a script; it records each timeout and thread."""

    inner: InMemoryJobEvents = field(default_factory=InMemoryJobEvents)
    empty_waits: int = 0
    timeouts: list[float] = field(default_factory=list)
    on_loop: list[bool] = field(default_factory=list)

    def append(self, job_id: str, kind: str, payload: Mapping[str, object]) -> JobEvent:
        return self.inner.append(job_id, kind, payload)

    def read_since(self, job_id: str, seq: int) -> Sequence[JobEvent]:
        return self.inner.read_since(job_id, seq)

    def discard(self, job_id: str) -> None:
        self.inner.discard(job_id)

    def wait_for(self, job_id: str, seq: int, timeout_seconds: float) -> Sequence[JobEvent]:
        """An empty answer stands for a timeout that ran out without a new event."""
        self.timeouts.append(timeout_seconds)
        self.on_loop.append(is_on_an_event_loop())
        if self.empty_waits:
            self.empty_waits -= 1
            return []
        return self.inner.read_since(job_id, seq)


def is_on_an_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def test_a_quiet_stream_sends_a_keep_alive_comment_every_fifteen_seconds() -> None:
    events = ScriptedEvents(empty_waits=2)
    harness = Harness(events=events)

    with harness.client() as client:
        frames = parse_frames(stream(client, finished_job(harness, client)).text)

    assert [frame.comment for frame in frames[:2]] == ["keep-alive", "keep-alive"]
    assert statuses(frames[2:]) == expected_success()
    assert KEEP_ALIVE_SECONDS == 15
    assert set(events.timeouts) == {15}


def test_the_wait_for_events_never_runs_on_the_event_loop() -> None:
    events = ScriptedEvents(empty_waits=1)
    harness = Harness(events=events)

    with harness.client() as client:
        stream(client, finished_job(harness, client))

    assert events.on_loop
    assert not any(events.on_loop)


def test_a_disconnected_client_stops_the_generator_before_it_waits() -> None:
    events = ScriptedEvents()

    async def disconnected() -> bool:
        return True

    async def collect() -> list[str]:
        feed = EventFeed(events=events, job_id="job-1", is_disconnected=disconnected)
        return [frame async for frame in stream_events(feed, last_seq=0)]

    assert asyncio.run(collect()) == []
    assert events.timeouts == []


def test_without_a_valid_key_the_stream_is_401() -> None:
    harness = Harness()

    with harness.client() as client:
        job_id = finished_job(harness, client)
        missing = client.get(f"/analyses/{job_id}/events")
        wrong = client.get(f"/analyses/{job_id}/events", headers=auth("nope"))

    assert (missing.status_code, wrong.status_code) == (401, 401)


def test_an_unknown_id_or_another_keys_job_is_404() -> None:
    harness = Harness()

    with harness.client() as client:
        job_id = finished_job(harness, client)
        unknown = client.get("/analyses/" + "0" * 32 + "/events", headers=auth())
        foreign = client.get(f"/analyses/{job_id}/events", headers=auth(OTHER_KEY))

    assert (unknown.status_code, foreign.status_code) == (404, 404)


def test_an_expired_job_is_404() -> None:
    harness = Harness(settings=make_settings(api_job_ttl_seconds=60))

    with harness.client() as client:
        job_id = finished_job(harness, client)
        harness.clock.now += 61
        response = client.get(f"/analyses/{job_id}/events", headers=auth())

    assert response.status_code == 404


class FakeLangfuseClient:
    def flush(self) -> None:
        pass


def test_the_default_runtime_streams_the_events_its_jobs_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def assemble(_: object, tracer: RecordingLifecycleTracer) -> Container:
        return Container(tracer, StubStages(tracer).ports(), CallBudget(300), "development", {})

    monkeypatch.setattr(api, "load_api_settings", make_settings)
    monkeypatch.setattr(api_runtime, "connect_langfuse", lambda _: FakeLangfuseClient())
    monkeypatch.setattr(api_runtime, "build_tracer", lambda *_: RecordingLifecycleTracer())
    monkeypatch.setattr(api_runtime, "assemble_container", assemble)

    with TestClient(create_app()) as client:
        response = stream(client, submit(client))

    assert statuses(parse_frames(response.text)) == expected_success()


def test_the_sweep_of_an_expired_job_also_drops_its_events() -> None:
    harness = Harness(settings=make_settings(api_job_ttl_seconds=60))

    with harness.client() as client:
        expired = finished_job(harness, client)
        harness.clock.now += 61
        submit(client)

    assert harness.events.read_since(expired, 0) == []


@dataclass
class FinishingEvents:
    """A running job's log that publishes the terminal event while the first wait is open."""

    inner: InMemoryJobEvents = field(default_factory=InMemoryJobEvents)
    waits: int = 0

    def append(self, job_id: str, kind: str, payload: Mapping[str, object]) -> JobEvent:
        return self.inner.append(job_id, kind, payload)

    def read_since(self, job_id: str, seq: int) -> Sequence[JobEvent]:
        return self.inner.read_since(job_id, seq)

    def discard(self, job_id: str) -> None:
        self.inner.discard(job_id)

    def wait_for(self, job_id: str, seq: int, timeout_seconds: float) -> Sequence[JobEvent]:
        self.waits += 1
        if self.waits == 1:
            self.append(job_id, EventKind.JOB, job_event_payload(JobState.DONE, at=0.0))
        return self.inner.read_since(job_id, seq)


def test_a_cursor_past_the_log_of_a_running_job_still_gets_the_terminal_event() -> None:
    events = FinishingEvents()
    events.append("job-1", EventKind.STAGE, stage_event_payload(STAGE, StageStatus.STARTED, at=0.0))
    checks = iter([False, False, False, True])

    async def disconnected_after_three_waits() -> bool:
        return next(checks)

    async def collect() -> list[str]:
        feed = EventFeed(
            events=events, job_id="job-1", is_disconnected=disconnected_after_three_waits
        )
        return [frame async for frame in stream_events(feed, last_seq=999)]

    frames = parse_frames("".join(asyncio.run(collect())))

    assert statuses(frames) == [("job", None, "done")]
    assert events.waits == 1


def test_a_client_that_leaves_after_a_quiet_wait_stops_the_generator() -> None:
    events = ScriptedEvents(empty_waits=1)
    checks = iter([False, True])

    async def disconnected_after_one_wait() -> bool:
        return next(checks)

    async def collect() -> list[str]:
        feed = EventFeed(events=events, job_id="job-1", is_disconnected=disconnected_after_one_wait)
        return [frame async for frame in stream_events(feed, last_seq=0)]

    assert asyncio.run(collect()) == [KEEP_ALIVE_COMMENT]
    assert events.timeouts == [KEEP_ALIVE_SECONDS]
