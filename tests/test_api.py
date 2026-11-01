"""HTTP API: auth, limits, validation order, job polling and start-up (plan §4.5, decision 108)."""

import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import api
import api_runtime
from api import create_app
from api_doubles import (
    ISSUE,
    KEY,
    OTHER_KEY,
    Harness,
    auth,
    files,
    make_settings,
    png,
    poll,
    post,
    submit,
)
from api_settings import hash_api_key
from budget import CallBudget
from cli_doubles import RecordingLifecycleTracer
from composition import Container
from errors import InvalidExtractionError, ObservabilityError, UpstreamError
from job_runner import ThreadPoolJobRunner
from jobs import JobState
from pipeline_doubles import StubStages


@pytest.fixture
def harness() -> Iterator[Harness]:
    yield Harness()


def test_post_answers_202_with_an_id_without_running_the_analysis(harness: Harness) -> None:
    with harness.client() as client:
        response = post(client)

    assert response.status_code == 202
    job_id = response.json()["id"]
    assert len(job_id) == 32
    assert len(harness.runner.tasks) == 1  # queued, not executed
    stored = harness.store.get(job_id, hash_api_key(KEY))
    assert stored is not None
    assert stored.state is JobState.PENDING


def test_a_missing_or_wrong_key_is_401_and_creates_nothing(harness: Harness) -> None:
    with harness.client() as client:
        missing = post(client, key=None)
        wrong = post(client, key="nope")

    assert (missing.status_code, wrong.status_code) == (401, 401)
    assert harness.runner.tasks == []


def test_a_wrong_key_does_not_spend_the_rate_limit_of_a_valid_one() -> None:
    harness = Harness(settings=make_settings(api_rate_limit_requests=1))

    with harness.client() as client:
        post(client, key="nope")
        assert post(client).status_code == 202


def test_past_the_rate_limit_is_429_with_retry_after() -> None:
    harness = Harness(settings=make_settings(api_rate_limit_requests=2))

    with harness.client() as client:
        statuses = [post(client).status_code for _ in range(3)]
        rejected = post(client)

    assert statuses == [202, 202, 429]
    assert rejected.headers["Retry-After"] == "60"
    assert len(harness.runner.tasks) == 2


def test_the_rate_limit_is_per_key() -> None:
    harness = Harness(settings=make_settings(api_rate_limit_requests=1))

    with harness.client() as client:
        assert post(client, key=KEY).status_code == 202
        assert post(client, key=OTHER_KEY).status_code == 202
        assert post(client, key=KEY).status_code == 429


def test_an_upload_over_the_limit_is_413_and_creates_nothing() -> None:
    harness = Harness(settings=make_settings(api_max_upload_bytes=1_000))

    with harness.client() as client:
        response = post(client, original=png(color=1) + b"\0" * 2_000)

    assert response.status_code == 413
    assert harness.runner.tasks == []


def test_a_declared_body_far_over_the_limit_is_413_before_it_is_parsed() -> None:
    harness = Harness(settings=make_settings(api_max_upload_bytes=1_000))

    with harness.client() as client:
        response = client.post(
            "/analyses",
            content=b"x",
            headers={**auth(), "Content-Length": "50000000", "Content-Type": "multipart/form-data"},
        )

    assert response.status_code == 413


def chunked(body: bytes) -> Iterator[bytes]:
    yield body


def test_a_body_without_content_length_is_411_and_creates_nothing(harness: Harness) -> None:
    boundary = "b0undary"
    body = f"--{boundary}--\r\n".encode()

    with harness.client() as client:
        response = client.post(
            "/analyses",
            content=chunked(body),
            headers={**auth(), "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

    assert response.status_code == 411
    assert harness.store.created == []
    assert harness.runner.tasks == []


def test_a_missing_content_length_still_answers_401_first(harness: Harness) -> None:
    with harness.client() as client:
        response = client.post("/analyses", content=chunked(b"x"))

    assert response.status_code == 401


def test_an_image_over_the_image_limit_is_413_even_if_the_upload_limit_is_higher() -> None:
    settings = make_settings(max_image_bytes=1_000, api_max_upload_bytes=10_000_000)
    harness = Harness(settings=settings)

    with harness.client() as client:
        response = post(client, original=png(color=1) + b"\0" * 5_000)

    assert response.status_code == 413
    assert harness.store.created == []


def test_oversize_wins_over_a_missing_part() -> None:
    harness = Harness(settings=make_settings(api_max_upload_bytes=1_000))

    with harness.client() as client:
        response = client.post(
            "/analyses",
            files={"amendment": ("a.png", b"x" * 5_000, "image/png")},
            headers=auth(),
        )

    assert response.status_code == 413


def test_an_invalid_image_is_422_with_the_image_error_and_no_job() -> None:
    harness = Harness()

    with harness.client() as client:
        response = post(client, amendment=b"not an image")

    assert response.status_code == 422
    assert "amendment" in response.json()["detail"]
    assert "image must be JPEG or PNG" in response.json()["detail"]
    assert harness.runner.tasks == []


def test_a_small_image_is_422_with_the_dimension_message(harness: Harness) -> None:
    with harness.client() as client:
        response = post(client, original=png(width=100, height=100))

    assert response.status_code == 422
    assert "image side must be between" in response.json()["detail"]


def test_a_missing_part_is_422(harness: Harness) -> None:
    with harness.client() as client:
        response = client.post(
            "/analyses", files={"original": ("o.png", png(), "image/png")}, headers=auth()
        )

    assert response.status_code == 422
    assert "amendment" in response.json()["detail"]


def test_a_part_that_is_not_a_file_is_422(harness: Harness) -> None:
    with harness.client() as client:
        response = client.post(
            "/analyses",
            data={"original": "text"},
            files={"amendment": ("a.png", png(), "image/png")},
            headers=auth(),
        )

    assert response.status_code == 422


def test_a_request_with_more_parts_than_the_two_images_is_rejected(harness: Harness) -> None:
    extra = [*files().items(), ("third", ("c.png", png(), "image/png"))]

    with harness.client() as client:
        response = client.post("/analyses", files=extra, headers=auth())

    assert response.status_code == 400
    assert harness.runner.tasks == []


def test_the_checks_run_in_the_documented_order() -> None:
    harness = Harness(settings=make_settings(api_rate_limit_requests=1, api_max_upload_bytes=1_000))

    with harness.client() as client:
        unauthorised = post(client, key=None, original=b"x" * 5_000)
        post(client)  # spends the allowance of the key
        limited = post(client, original=b"x" * 5_000)

    assert (unauthorised.status_code, limited.status_code) == (401, 429)


def test_oversize_wins_over_invalid_image() -> None:
    harness = Harness(settings=make_settings(api_max_upload_bytes=1_000))

    with harness.client() as client:
        response = post(client, original=b"x" * 5_000, amendment=b"bad")

    assert response.status_code == 413


def test_a_full_queue_is_503_with_retry_after_and_leaves_no_job() -> None:
    harness = Harness()
    harness.runner.full = True

    with harness.client() as client:
        response = post(client)

    assert response.status_code == 503
    assert int(response.headers["Retry-After"]) > 0
    assert harness.store.created
    for job_id in harness.store.created:
        assert harness.store.get(job_id, hash_api_key(KEY)) is None


def test_a_runner_already_shut_down_is_503_and_leaves_no_job() -> None:
    runner = ThreadPoolJobRunner(max_workers=1, max_pending=1)
    runner.shutdown()
    harness = Harness(runner=runner)  # type: ignore[arg-type]  # the real runner, not the double

    with harness.client() as client:
        response = post(client)

    assert response.status_code == 503
    assert "Retry-After" in response.headers
    assert runner.pending == 0
    assert harness.store.get(harness.store.created[0], hash_api_key(KEY)) is None


def test_a_pending_job_shows_its_state_without_a_body(harness: Harness) -> None:
    with harness.client() as client:
        job_id = submit(client)
        body = poll(client, job_id).json()

    assert set(body) == {"id", "status", "created_at", "trace_id"}
    assert body["id"] == job_id
    assert body["status"] == "pending"
    assert body["trace_id"] is None
    # The same shape as the events' `at`: UTC, milliseconds and a `Z`.
    assert body["created_at"] == "1970-01-01T00:16:40.000Z"


def test_a_running_job_shows_running(harness: Harness) -> None:
    with harness.client() as client:
        job_id = submit(client)
        harness.store.transition(job_id, JobState.RUNNING)

        assert poll(client, job_id).json()["status"] == "running"


def test_a_done_job_returns_the_public_output(harness: Harness) -> None:
    with harness.client() as client:
        job_id = submit(client)
        harness.runner.run_all()
        body = poll(client, job_id).json()

    assert body["status"] == "done"
    assert body["trace_id"] == "trace-abc"
    assert body["output"]["sections_changed"] == ["1"]
    assert set(body["output"]) == {"sections_changed", "topics_touched", "summary_of_the_change"}
    assert "error" not in body


def test_a_done_job_returns_the_change_details_and_the_amendment_kind(harness: Harness) -> None:
    with harness.client() as client:
        job_id = submit(client)
        harness.runner.run_all()
        body = poll(client, job_id).json()

    assert body["amendment_kind"] == "restated"
    assert body["changes"] == [
        {
            "section_id": "1",
            "section_title": "Precio",
            "change_type": "modified",
            "topic": "price_payment",
            "description": "El precio pasa de $ 1.500.000 a $ 1.850.000",
            "original_quote": "$ 1.500.000",
            "amendment_quote": "$ 1.850.000",
        }
    ]


def test_only_a_done_job_carries_the_change_details(harness: Harness) -> None:
    review = Harness(failures={"extraction_agent": InvalidExtractionError([ISSUE])})
    failed = Harness(failures={"parse_original_contract": UpstreamError("down")})
    bodies = []
    with harness.client() as client:
        pending_id = submit(client)
        bodies.append(poll(client, pending_id).json())
        harness.store.transition(pending_id, JobState.RUNNING)
        bodies.append(poll(client, pending_id).json())
    for other in (review, failed):
        with other.client() as client:
            job_id = submit(client)
            other.runner.run_all()
            bodies.append(poll(client, job_id).json())

    assert len(bodies) == 4
    for body in bodies:
        assert "changes" not in body
        assert "amendment_kind" not in body


def test_a_human_review_job_returns_the_cli_envelope_with_issues() -> None:
    harness = Harness(failures={"extraction_agent": InvalidExtractionError([ISSUE])})

    with harness.client() as client:
        job_id = submit(client)
        harness.runner.run_all()
        body = poll(client, job_id).json()

    assert body["status"] == "human_review"
    assert "output" not in body
    assert body["error"] == {
        "error": "InvalidExtractionError",
        "stage": "extraction",
        "message": "extraction is still invalid after one retry: ungrounded_quote",
        "run_id": job_id,
        "trace_id": "trace-abc",
        "issues": [
            {"code": "ungrounded_quote", "path": "changes[0].original_quote", "message": "m"}
        ],
    }


def test_a_failed_job_returns_the_envelope_without_issues() -> None:
    harness = Harness(failures={"parse_original_contract": UpstreamError("vision call failed")})

    with harness.client() as client:
        job_id = submit(client)
        harness.runner.run_all()
        body = poll(client, job_id).json()

    assert body["status"] == "failed"
    assert body["error"]["error"] == "UpstreamError"
    assert body["error"]["message"] == "vision call failed"
    assert "issues" not in body["error"]


def test_an_unknown_id_is_404(harness: Harness) -> None:
    with harness.client() as client:
        assert poll(client, "0" * 32).status_code == 404


def test_a_job_of_another_key_is_404(harness: Harness) -> None:
    with harness.client() as client:
        job_id = submit(client)

        assert poll(client, job_id, key=OTHER_KEY).status_code == 404
        assert poll(client, job_id).status_code == 200


def test_an_expired_job_is_404(harness: Harness) -> None:
    with harness.client() as client:
        job_id = submit(client)
        harness.runner.run_all()
        harness.clock.now += harness.settings.api_job_ttl_seconds

        assert poll(client, job_id).status_code == 404


def test_polling_needs_a_valid_key(harness: Harness) -> None:
    with harness.client() as client:
        job_id = submit(client)

        assert client.get(f"/analyses/{job_id}").status_code == 401
        assert poll(client, job_id, key="nope").status_code == 401


def test_polling_is_not_rate_limited() -> None:
    harness = Harness(settings=make_settings(api_rate_limit_requests=1))

    with harness.client() as client:
        job_id = submit(client)

        assert [poll(client, job_id).status_code for _ in range(5)] == [200] * 5


def test_health_needs_no_key_and_touches_nothing() -> None:
    harness = Harness()

    with harness.client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert harness.runner.tasks == []


def test_the_runtime_is_built_at_start_up_and_closed_at_shutdown() -> None:
    harness = Harness()

    assert harness.runtime_builds == 0
    with harness.client():
        assert harness.runtime_builds == 1
        assert harness.closed == 0

    assert harness.closed == 1


class FakeLangfuseClient:
    def __init__(self) -> None:
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1


def test_the_api_does_not_start_when_langfuse_cannot_be_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(_: object) -> None:
        raise ObservabilityError("tracer initialisation failed: auth check rejected")

    monkeypatch.setattr(api, "load_api_settings", make_settings)
    monkeypatch.setattr(api_runtime, "connect_langfuse", refuse)

    with pytest.raises(ObservabilityError, match="auth check rejected"), TestClient(create_app()):
        pass


def test_the_default_runtime_wires_limits_from_the_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(api_max_concurrent_jobs=3, api_max_pending_jobs=7)
    client = FakeLangfuseClient()
    received: dict[str, int] = {}

    class SpyRunner(ThreadPoolJobRunner):
        def __init__(self, *, max_workers: int, max_pending: int) -> None:
            received.update(max_workers=max_workers, max_pending=max_pending)
            super().__init__(max_workers=max_workers, max_pending=max_pending)

    monkeypatch.setattr(api, "load_api_settings", lambda: settings)
    monkeypatch.setattr(api_runtime, "connect_langfuse", lambda _: client)
    monkeypatch.setattr(api_runtime, "ThreadPoolJobRunner", SpyRunner)

    with TestClient(create_app()):
        pass

    assert received == {"max_workers": 3, "max_pending": 7}
    assert client.flushes == 1  # the final flush at shutdown


def test_every_job_gets_its_own_tracer_rooted_in_its_own_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings()
    client = FakeLangfuseClient()
    tracers: list[RecordingLifecycleTracer] = []

    def build_tracer(connected: object, _: object) -> RecordingLifecycleTracer:
        assert connected is client
        tracers.append(RecordingLifecycleTracer())
        return tracers[-1]

    def assemble(_: object, tracer: RecordingLifecycleTracer) -> Container:
        return Container(tracer, StubStages(tracer).ports(), CallBudget(300), "development", {})

    monkeypatch.setattr(api, "load_api_settings", lambda: settings)
    monkeypatch.setattr(api_runtime, "connect_langfuse", lambda _: client)
    monkeypatch.setattr(api_runtime, "build_tracer", build_tracer)
    monkeypatch.setattr(api_runtime, "assemble_container", assemble)

    with TestClient(create_app()) as http:
        ids = [submit(http), submit(http)]
        wait_until_terminal(http, ids)
        bodies = [poll(http, job_id).json() for job_id in ids]

    assert len(tracers) == 2
    assert {tracer.roots[0][1].session_id for tracer in tracers} == set(ids)
    assert [body["trace_id"] for body in bodies] == ["trace-abc", "trace-abc"]
    assert [tracer.flushes for tracer in tracers] == [1, 1]


def wait_until_terminal(client: TestClient, ids: list[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if all(poll(client, job_id).json()["status"] == "done" for job_id in ids):
            return
        time.sleep(0.02)
    raise AssertionError("jobs did not finish")
