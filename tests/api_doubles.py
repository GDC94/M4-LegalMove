"""API test doubles: settings, a manual runner, a spying store and one wired runtime."""

import io
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from api import ApiRuntime, ApiSettings, create_app
from budget import CallBudget
from cli_doubles import RecordingLifecycleTracer
from composition import Container
from errors import ValidationIssue
from job_runner import JobServices
from jobs import Job, JobEvents, QueueFullError
from jobs_memory import InMemoryJobEvents, InMemoryJobStore, InMemoryRateLimiter
from observability import TraceLifecycle
from pipeline_doubles import StubStages

KEY = "key-one"
OTHER_KEY = "key-two"
ISSUE = ValidationIssue(code="ungrounded_quote", path="changes[0].original_quote", message="m")


def png(width: int = 600, height: int = 600, color: int = 255) -> bytes:
    buffer = io.BytesIO()
    Image.new("L", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def make_settings(**overrides: Any) -> ApiSettings:
    values = {
        "openai_api_key": "sk-test",
        "langfuse_public_key": "pk-lf-test",
        "langfuse_secret_key": "sk-lf-test",
        "api_keys": f"{KEY},{OTHER_KEY}",
        **overrides,
    }
    return ApiSettings(_env_file=None, **values)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class ManualRunner:
    """Keeps submitted work until the test runs it; can be told to be full."""

    tasks: list[Callable[[], None]] = field(default_factory=list)
    full: bool = False
    shutdowns: int = 0

    def submit(self, work: Callable[[], None]) -> None:
        if self.full:
            raise QueueFullError("full")
        self.tasks.append(work)

    def shutdown(self) -> None:
        self.shutdowns += 1

    def run_all(self) -> None:
        while self.tasks:
            self.tasks.pop(0)()


class SpyStore(InMemoryJobStore):
    """Remembers every job id it was asked to create, even if the job is discarded later."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.created: list[str] = []

    def create(self, job: Job) -> None:
        self.created.append(job.id)
        super().create(job)


@dataclass
class Harness:
    settings: ApiSettings = field(default_factory=make_settings)
    clock: FakeClock = field(default_factory=FakeClock)
    runner: ManualRunner = field(default_factory=ManualRunner)
    failures: dict[str, BaseException] = field(default_factory=dict)
    events: JobEvents = field(default_factory=InMemoryJobEvents)
    on_stage: Callable[[str], None] = lambda _: None
    closed: int = 0
    runtime_builds: int = 0

    def __post_init__(self) -> None:
        self.store = SpyStore(ttl_seconds=self.settings.api_job_ttl_seconds, clock=self.clock)
        self.limiter = InMemoryRateLimiter(
            requests=self.settings.api_rate_limit_requests,
            window_seconds=self.settings.api_rate_limit_window_seconds,
            clock=self.clock,
        )

    def build_container(self, tracer: TraceLifecycle) -> Container:
        stages = StubStages(tracer, failures=self.failures, on_call=self.on_stage)
        return Container(tracer, stages.ports(), CallBudget(300), "development", {})

    def build_runtime(self) -> ApiRuntime:
        self.runtime_builds += 1
        return ApiRuntime(
            settings=self.settings,
            store=self.store,
            limiter=self.limiter,
            runner=self.runner,
            events=self.events,
            services=JobServices(
                store=self.store,
                events=self.events,
                build_tracer=RecordingLifecycleTracer,
                build_container=self.build_container,
            ),
            clock=self.clock,
            close=self.close,
        )

    def close(self) -> None:
        self.closed += 1

    def client(self) -> TestClient:
        return TestClient(create_app(self.build_runtime))


def auth(key: str = KEY) -> dict[str, str]:
    return {"X-API-Key": key}


def files(original: bytes | None = None, amendment: bytes | None = None) -> dict[str, Any]:
    return {
        "original": ("original.png", original if original is not None else png(), "image/png"),
        "amendment": ("amendment.png", amendment if amendment is not None else png(), "image/png"),
    }


def post(client: TestClient, key: str | None = KEY, **uploads: Any) -> Any:
    return client.post("/analyses", files=files(**uploads), headers=auth(key) if key else {})


def poll(client: TestClient, job_id: str, key: str = KEY) -> Any:
    return client.get(f"/analyses/{job_id}", headers=auth(key))


def submit(client: TestClient) -> str:
    return str(post(client).json()["id"])
