"""Model adapter that consults the run budget before each attempt and bounds its limits."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import Runnable, RunnableConfig

from agents.structured_output import StructuredModel, StructuredModels
from budget import AttemptLimits, CallBudget


@dataclass(frozen=True)
class TimeoutPolicy:
    """The shared run budget, the configured per-call limits and the Stage a spent budget fails."""

    budget: CallBudget
    per_call_seconds: float
    max_retries: int
    stage: str


class BudgetedModel[In, Out](Runnable[In, Out]):
    """Builds the model per attempt: timeout and retries are fixed when the client is built."""

    def __init__(
        self, build_model: Callable[[AttemptLimits], Runnable[In, Out]], policy: TimeoutPolicy
    ) -> None:
        self._build_model = build_model
        self._policy = policy

    # Any: mirrors Runnable.invoke, whose extra keyword arguments are model-specific.
    def invoke(self, input: In, config: RunnableConfig | None = None, **kwargs: Any) -> Out:
        policy = self._policy
        limits = policy.budget.plan_attempt(
            policy.per_call_seconds, policy.max_retries, stage=policy.stage
        )
        # Delegating keeps the caller's config, so its callbacks and run name reach the model.
        return self._build_model(limits).invoke(input, config, **kwargs)


def build_budgeted_structured_models(
    build_models: Callable[[AttemptLimits], StructuredModels], policy: TimeoutPolicy
) -> StructuredModels:
    """Attempt and retry each spend from the same budget; the retry keeps its temperature 0."""
    attempt: StructuredModel = BudgetedModel(lambda limits: build_models(limits).attempt, policy)
    retry: StructuredModel = BudgetedModel(lambda limits: build_models(limits).retry, policy)
    return StructuredModels(attempt=attempt, retry=retry)
