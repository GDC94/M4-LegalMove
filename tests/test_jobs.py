"""Job domain: states, transitions, uuid4 hex identity and progress events (decisions 108, 111)."""

import re

import pytest
from pydantic import ValidationError

from change_details import ChangeDetail
from jobs import (
    EventKind,
    InvalidTransitionError,
    Job,
    JobEvent,
    JobFinishedEvent,
    JobState,
    JobUpdate,
    StageEvent,
    StageStatus,
    is_terminal,
    job_event_payload,
    stage_event_payload,
    to_progress_event,
)
from models import AmendmentKind, ChangeType, ContractChangeOutput, Topic
from stages import StageName

OWNER = "a" * 64
ENVELOPE = {"error": "UpstreamError", "stage": "parse", "message": "m", "run_id": "r"}
CACHED_DONE: dict[str, object] = {
    "stage": "parse_original_contract",
    "status": "done",
    "at": "1970-01-01T00:00:00.000Z",
    "cached": True,
}


def new_job() -> Job:
    return Job.create(OWNER, now=100.0)


def output() -> ContractChangeOutput:
    return ContractChangeOutput(
        sections_changed=["1"],
        topics_touched=["price_payment"],
        summary_of_the_change="1 — modified",
    )


def test_a_new_job_is_pending_with_a_uuid4_hex_id() -> None:
    job = new_job()

    assert re.fullmatch(r"[0-9a-f]{32}", job.id)
    assert job.id[12] == "4"
    assert (job.state, job.owner_key_hash, job.created_at) == (JobState.PENDING, OWNER, 100.0)
    assert (job.trace_id, job.output, job.error, job.finished_at) == (None, None, None, None)


def test_every_job_gets_its_own_id() -> None:
    assert new_job().id != new_job().id


def test_the_five_states_exist() -> None:
    assert {state.value for state in JobState} == {
        "pending",
        "running",
        "done",
        "human_review",
        "failed",
    }


def test_pending_moves_to_running_and_running_to_each_terminal_state() -> None:
    running = new_job().transition(JobState.RUNNING, now=101.0)

    done = running.transition(JobState.DONE, now=102.0, update=JobUpdate(output=output()))
    review = running.transition(JobState.HUMAN_REVIEW, now=102.0, update=JobUpdate(error=ENVELOPE))
    failed = running.transition(JobState.FAILED, now=102.0, update=JobUpdate(error=ENVELOPE))

    assert (done.state, review.state, failed.state) == (
        JobState.DONE,
        JobState.HUMAN_REVIEW,
        JobState.FAILED,
    )
    assert done.output == output()
    assert failed.error == ENVELOPE
    assert done.finished_at == 102.0
    assert running.finished_at is None


def test_the_trace_id_travels_with_the_transition() -> None:
    running = new_job().transition(JobState.RUNNING, now=101.0)

    failed = running.transition(
        JobState.FAILED, now=102.0, update=JobUpdate(error=ENVELOPE, trace_id="trace-1")
    )

    assert failed.trace_id == "trace-1"


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (JobState.PENDING, JobState.DONE),
        (JobState.PENDING, JobState.FAILED),
        (JobState.PENDING, JobState.PENDING),
        (JobState.RUNNING, JobState.PENDING),
        (JobState.RUNNING, JobState.RUNNING),
        (JobState.DONE, JobState.RUNNING),
        (JobState.FAILED, JobState.DONE),
        (JobState.HUMAN_REVIEW, JobState.FAILED),
    ],
)
def test_invalid_transitions_are_rejected(start: JobState, target: JobState) -> None:
    job = reach(start)

    with pytest.raises(InvalidTransitionError):
        job.transition(target, now=200.0)


def reach(state: JobState) -> Job:
    job = new_job()
    if state is JobState.PENDING:
        return job
    running = job.transition(JobState.RUNNING, now=101.0)
    if state is JobState.RUNNING:
        return running
    return running.transition(state, now=102.0)


def test_only_the_three_final_states_are_terminal() -> None:
    assert {state for state in JobState if is_terminal(state)} == {
        JobState.DONE,
        JobState.HUMAN_REVIEW,
        JobState.FAILED,
    }


def test_a_job_event_orders_by_seq_and_carries_a_payload() -> None:
    event = JobEvent(seq=3, kind="stage", payload={"stage": "extraction_agent"})

    assert (event.seq, event.kind, event.payload) == (3, "stage", {"stage": "extraction_agent"})


def test_a_stage_event_payload_names_the_stage_its_status_and_a_utc_millisecond_time() -> None:
    payload = stage_event_payload(StageName.EXTRACTION_AGENT, StageStatus.STARTED, at=1.4125)

    assert payload == {
        "stage": "extraction_agent",
        "status": "started",
        "at": "1970-01-01T00:00:01.412Z",
    }


def test_a_job_event_payload_carries_the_terminal_state() -> None:
    payload = job_event_payload(JobState.HUMAN_REVIEW, at=1_793_000_000.0)

    assert payload == {"status": "human_review", "at": "2026-10-26T07:33:20.000Z"}


def test_a_job_event_payload_rejects_a_state_that_is_not_terminal() -> None:
    with pytest.raises(ValueError, match="running"):
        job_event_payload(JobState.RUNNING, at=0.0)


def test_a_logged_stage_event_becomes_the_public_stage_event() -> None:
    logged = JobEvent(
        seq=4,
        kind=EventKind.STAGE,
        payload=stage_event_payload(StageName.EXTRACTION_AGENT, StageStatus.FAILED, at=0.0),
    )

    event = to_progress_event(logged)

    assert isinstance(event, StageEvent)
    assert event.model_dump(mode="json") == {
        "seq": 4,
        "kind": "stage",
        "stage": "extraction_agent",
        "status": "failed",
        "at": "1970-01-01T00:00:00.000Z",
    }


def test_a_logged_job_event_becomes_the_public_terminal_event() -> None:
    logged = JobEvent(seq=11, kind=EventKind.JOB, payload=job_event_payload(JobState.DONE, at=0.0))

    event = to_progress_event(logged)

    assert isinstance(event, JobFinishedEvent)
    assert event.model_dump(mode="json") == {
        "seq": 11,
        "kind": "job",
        "status": "done",
        "at": "1970-01-01T00:00:00.000Z",
    }


def test_a_logged_event_outside_the_contract_is_rejected() -> None:
    logged = JobEvent(seq=1, kind=EventKind.JOB, payload={"status": "running", "at": "x"})

    with pytest.raises(ValidationError):
        to_progress_event(logged)


def test_a_stage_served_from_the_parse_cache_carries_cached_true_and_no_other_does() -> None:
    cached = stage_event_payload(
        StageName.PARSE_ORIGINAL_CONTRACT, StageStatus.DONE, at=0.0, cached=True
    )
    fresh = stage_event_payload(StageName.PARSE_ORIGINAL_CONTRACT, StageStatus.DONE, at=0.0)

    assert cached["cached"] is True
    assert "cached" not in fresh


def test_a_logged_cached_stage_event_keeps_cached_and_an_uncached_one_omits_it() -> None:
    cached = to_progress_event(JobEvent(seq=2, kind=EventKind.STAGE, payload=CACHED_DONE))
    fresh = to_progress_event(
        JobEvent(seq=2, kind=EventKind.STAGE, payload={**CACHED_DONE, "cached": None})
    )

    assert isinstance(cached, StageEvent)
    assert cached.cached is True
    assert cached.model_dump(mode="json")["cached"] is True
    assert "cached" not in fresh.model_dump(mode="json")


def test_cached_false_is_outside_the_contract_because_cached_is_omitted_instead() -> None:
    logged = JobEvent(seq=2, kind=EventKind.STAGE, payload={**CACHED_DONE, "cached": False})

    with pytest.raises(ValidationError):
        to_progress_event(logged)


def test_the_change_details_and_the_amendment_kind_travel_with_the_done_transition() -> None:
    detail = ChangeDetail(
        section_id="1",
        section_title="Price",
        change_type=ChangeType.MODIFIED,
        topic=Topic.PRICE_PAYMENT,
        description="d",
        original_quote="a",
        amendment_quote="b",
    )
    running = new_job().transition(JobState.RUNNING, now=101.0)

    done = running.transition(
        JobState.DONE,
        now=102.0,
        update=JobUpdate(output=output(), changes=[detail], amendment_kind=AmendmentKind.DELTA),
    )

    assert done.changes == [detail]
    assert done.amendment_kind is AmendmentKind.DELTA
    assert (new_job().changes, new_job().amendment_kind) == (None, None)
