"""The SSE progress stream of one job: frames, keep-alives and the `Last-Event-ID` cursor."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass

from jobs import EventKind, JobEvent, JobEvents, to_progress_event

# No real seq gets near 20 digits; a longer header is noise, and `int()` refuses past 4300.
MAX_EVENT_ID_DIGITS = 20
# Proxies and browsers drop an idle connection; a comment every 15 s keeps the stream open.
KEEP_ALIVE_SECONDS = 15.0
KEEP_ALIVE_COMMENT = ": keep-alive\n\n"


def parse_last_event_id(value: str | None) -> int:
    """A missing or unreadable id replays from the start, which never loses an event."""
    if value is None or len(value) > MAX_EVENT_ID_DIGITS:
        return 0
    if not (value.isascii() and value.isdigit()):
        return 0
    return int(value)


@dataclass(frozen=True)
class EventFeed:
    """Where one stream reads from, and how it learns that its client went away."""

    events: JobEvents
    job_id: str
    is_disconnected: Callable[[], Awaitable[bool]]


async def stream_events(feed: EventFeed, *, last_seq: int) -> AsyncIterator[str]:
    """Waits run in a worker thread (`asyncio.to_thread`), so the event loop never blocks."""
    history = await asyncio.to_thread(feed.events.read_since, feed.job_id, 0)
    if has_ended_by(history, last_seq):
        return
    # A cursor past the log would skip the terminal event when it comes; one rule then covers
    # every path: deliver until the `job` event.
    seq = min(last_seq, history[-1].seq if history else 0)
    while not await feed.is_disconnected():
        batch = await asyncio.to_thread(feed.events.wait_for, feed.job_id, seq, KEEP_ALIVE_SECONDS)
        if not batch:
            yield KEEP_ALIVE_COMMENT
        for event in batch:
            yield format_event(event)
            seq = event.seq
            if event.kind == EventKind.JOB:
                return


def has_ended_by(history: Sequence[JobEvent], seq: int) -> bool:
    """The client already holds the terminal event, so waiting would only send keep-alives."""
    return any(event.kind == EventKind.JOB and event.seq <= seq for event in history)


def format_event(event: JobEvent) -> str:
    return f"id: {event.seq}\ndata: {to_progress_event(event).model_dump_json()}\n\n"
