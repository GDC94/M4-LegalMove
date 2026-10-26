"""Monotonic run budget: consulted before every attempt, never started when spent (§5.1)."""

import pytest

from budget import AttemptLimits, CallBudget
from errors import UpstreamError


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_ensure_remaining_passes_with_budget_left_and_fails_once_spent() -> None:
    clock = FakeClock()
    budget = CallBudget(10, clock=clock)

    budget.ensure_remaining(stage="parse")
    clock.now += 11

    with pytest.raises(UpstreamError):
        budget.ensure_remaining(stage="parse")


def plan_after(elapsed: float, per_call: float = 60, max_retries: int = 2) -> AttemptLimits:
    clock = FakeClock()
    budget = CallBudget(300, clock=clock)
    clock.now += elapsed
    return budget.plan_attempt(per_call, max_retries, stage="parse")


def test_plenty_of_budget_keeps_the_per_call_timeout_and_every_retry() -> None:
    assert plan_after(0) == AttemptLimits(timeout=60, retries=2)


def test_retries_shrink_to_what_the_budget_can_still_pay() -> None:
    assert plan_after(150) == AttemptLimits(timeout=60, retries=1)


def test_budget_for_exactly_one_call_allows_no_retry() -> None:
    assert plan_after(240) == AttemptLimits(timeout=60, retries=0)


def test_budget_below_one_per_call_timeout_shrinks_the_timeout_and_allows_no_retry() -> None:
    assert plan_after(270) == AttemptLimits(timeout=30, retries=0)


def test_zero_configured_retries_stay_zero() -> None:
    assert plan_after(0, max_retries=0) == AttemptLimits(timeout=60, retries=0)


def test_planning_on_a_spent_budget_still_raises() -> None:
    with pytest.raises(UpstreamError) as caught:
        plan_after(300)

    assert caught.value.stage == "parse"
