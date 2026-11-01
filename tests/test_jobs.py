"""Job domain: states, valid transitions and the uuid4 hex identity (plan §4.5, decision 108)."""

import re

import pytest

from jobs import (
    InvalidTransitionError,
    Job,
    JobEvent,
    JobState,
    JobUpdate,
    is_terminal,
)
from models import ContractChangeOutput

OWNER = "a" * 64
ENVELOPE = {"error": "UpstreamError", "stage": "parse", "message": "m", "run_id": "r"}


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
