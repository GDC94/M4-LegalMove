"""Monotonic time budget of one run: no model attempt starts once it is spent (plan §5.1)."""

import time
from collections.abc import Callable
from dataclasses import dataclass

from errors import UpstreamError


@dataclass(frozen=True)
class AttemptLimits:
    """Per-request timeout and transport retries one attempt may spend."""

    timeout: float
    retries: int


class CallBudget:
    """Deadline shared by every model attempt of a run; transport retries live inside each one."""

    def __init__(self, total_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._total_seconds = total_seconds
        self._deadline = clock() + total_seconds

    def ensure_remaining(self, *, stage: str) -> float:
        """Return the seconds left, or fail the Stage before it spends anything."""
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            raise UpstreamError(
                f"pipeline time budget of {self._total_seconds:g} s exhausted", stage=stage
            )
        return remaining

    def plan_attempt(
        self, per_call_seconds: float, max_retries: int, *, stage: str
    ) -> AttemptLimits:
        """The SDK re-sends with the same timeout, so retries must fit the remaining budget too."""
        remaining = self.ensure_remaining(stage=stage)
        timeout = min(per_call_seconds, remaining)
        affordable_retries = int(remaining // timeout) - 1
        return AttemptLimits(timeout=timeout, retries=max(0, min(max_retries, affordable_retries)))
