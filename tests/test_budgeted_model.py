"""Budgeted models: every attempt consults the run budget and gets min(per call, remaining)."""

from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig, RunnableLambda

from agents.structured_output import StructuredModels
from budget import AttemptLimits, CallBudget
from budgeted_model import BudgetedModel, TimeoutPolicy, build_budgeted_structured_models
from errors import UpstreamError


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class RecordingFactory:
    """Builds a stub model per attempt and remembers the timeout each one received."""

    def __init__(self) -> None:
        self.limits: list[AttemptLimits] = []
        self.configs: list[RunnableConfig] = []

    def __call__(self, limits: AttemptLimits) -> RunnableLambda[Any, str]:
        self.limits.append(limits)
        return RunnableLambda(self._respond)

    def _respond(self, value: str, config: RunnableConfig) -> str:
        self.configs.append(config)
        return f"answer to {value}"


def policy(clock: FakeClock, stage: str = "parse") -> TimeoutPolicy:
    return TimeoutPolicy(
        budget=CallBudget(300, clock=clock), per_call_seconds=60, max_retries=2, stage=stage
    )


def test_each_attempt_builds_its_model_with_the_planned_timeout_and_retries() -> None:
    clock = FakeClock()
    factory = RecordingFactory()
    model = BudgetedModel(factory, policy(clock))

    first = model.invoke("uno")
    clock.now = 280
    second = model.invoke("dos", config=RunnableConfig(tags=["vision_parsing"]))

    assert (first, second) == ("answer to uno", "answer to dos")
    assert factory.limits == [AttemptLimits(60, 2), AttemptLimits(20, 0)]
    assert factory.configs[1]["tags"] == ["vision_parsing"]


def test_a_spent_budget_never_builds_or_calls_the_model() -> None:
    clock = FakeClock()
    factory = RecordingFactory()
    model = BudgetedModel(factory, policy(clock, stage="extraction"))
    clock.now = 301

    with pytest.raises(UpstreamError) as caught:
        model.invoke("tres")

    assert caught.value.stage == "extraction"
    assert factory.limits == []


def test_structured_models_keep_attempt_and_retry_apart_under_one_budget() -> None:
    clock = FakeClock()
    planned: list[AttemptLimits] = []

    def build_models(limits: AttemptLimits) -> StructuredModels:
        planned.append(limits)
        return StructuredModels(
            attempt=RunnableLambda(lambda _: {"which": "attempt"}),
            retry=RunnableLambda(lambda _: {"which": "retry"}),
        )

    models = build_budgeted_structured_models(build_models, policy(clock, "contextualization"))
    clock.now = 250

    assert models.attempt.invoke("prompt") == {"which": "attempt"}
    assert models.retry.invoke("prompt") == {"which": "retry"}
    assert planned == [AttemptLimits(50, 0), AttemptLimits(50, 0)]
