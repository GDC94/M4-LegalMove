"""The API's runtime: store, rate limiter, runner and event log, wired once from ApiSettings."""

import time
from collections.abc import Callable
from dataclasses import dataclass

from api_settings import ApiSettings
from composition import assemble_container, build_tracer, connect_langfuse, flush_client
from job_runner import JobServices, ThreadPoolJobRunner
from jobs import JobEvents, JobRunner, JobStore, RateLimiter
from jobs_memory import InMemoryJobEvents, InMemoryJobStore, InMemoryRateLimiter


@dataclass(frozen=True)
class ApiRuntime:
    """Everything the endpoints use, built once at start-up and closed at shutdown."""

    settings: ApiSettings
    store: JobStore
    limiter: RateLimiter
    runner: JobRunner
    events: JobEvents
    services: JobServices
    clock: Callable[[], float]
    close: Callable[[], None]


def build_api_runtime(settings: ApiSettings) -> ApiRuntime:
    """Fails start-up on an unreachable Langfuse; no request ever sees that."""
    client = connect_langfuse(settings)
    store = InMemoryJobStore(ttl_seconds=settings.api_job_ttl_seconds)
    events = InMemoryJobEvents()
    runner = ThreadPoolJobRunner(
        max_workers=settings.api_max_concurrent_jobs, max_pending=settings.api_max_pending_jobs
    )

    def close() -> None:
        """Let running jobs finish, then hand the last traces over."""
        runner.shutdown()
        flush_client(client)

    return ApiRuntime(
        settings=settings,
        store=store,
        limiter=InMemoryRateLimiter(
            requests=settings.api_rate_limit_requests,
            window_seconds=settings.api_rate_limit_window_seconds,
        ),
        runner=runner,
        events=events,
        services=JobServices(
            store=store,
            events=events,
            build_tracer=lambda: build_tracer(client, settings),
            build_container=lambda tracer: assemble_container(settings, tracer),
        ),
        clock=time.time,
        close=close,
    )


def expire_jobs(runtime: ApiRuntime) -> None:
    """A job's events leave with the job, so the log never outgrows the store."""
    for job_id in runtime.store.expire():
        runtime.events.discard(job_id)
