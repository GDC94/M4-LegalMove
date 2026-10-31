"""ClaudeSummaryJudge: the second Judge's request, its classification of replies and its factory."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import anthropic
import pytest

import claude_judge
from claude_judge import VERDICT_FORMAT, ClaudeSummaryJudge, build_claude_judge
from eval_doubles import golden_case
from judge import JUDGE_SYSTEM_PROMPT, JudgeVerdict, render_judge_input
from settings import EvalSettings

MODEL = "claude-opus-5-5"


@dataclass(frozen=True)
class FakeUsage:
    input_tokens: int = 1200
    output_tokens: int = 340


@dataclass(frozen=True)
class FakeStopDetails:
    category: str | None


@dataclass(frozen=True)
class FakeTextBlock:
    type: str = "text"
    text: str = ""


@dataclass(frozen=True)
class FakeThinkingBlock:
    type: str = "thinking"


VERDICT_TEXT = JudgeVerdict(reasoning="Covers every fact.", score=8).model_dump_json()


@dataclass(frozen=True)
class FakeResponse:
    model: str = MODEL
    stop_reason: str | None = "end_turn"
    stop_details: FakeStopDetails | None = None
    usage: FakeUsage = FakeUsage()
    content: tuple[Any, ...] = (FakeThinkingBlock(), FakeTextBlock(text=VERDICT_TEXT))


class FakeMessages:
    def __init__(self, reply: FakeResponse | Exception) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class FakeClient:
    def __init__(self, reply: FakeResponse | Exception) -> None:
        self.messages = FakeMessages(reply)


def make_clock() -> Callable[[], float]:
    ticks = iter([1.0, 3.5])
    return lambda: next(ticks)


def make_judge(reply: FakeResponse | Exception, model: str = MODEL) -> tuple[Any, FakeClient]:
    client = FakeClient(reply)
    judge = ClaudeSummaryJudge(client, model, "medium", clock=make_clock())
    return judge, client


def test_the_request_uses_the_configured_model_effort_and_output_format() -> None:
    judge, client = make_judge(FakeResponse())

    judge("summary", golden_case())

    (call,) = client.messages.calls
    assert call["model"] == MODEL
    assert call["output_config"] == {"effort": "medium", "format": VERDICT_FORMAT}
    assert call["max_tokens"] == 8000
    assert "output_format" not in call
    assert "temperature" not in call
    assert "thinking" not in call


def test_the_request_carries_the_shared_rubric_and_user_message() -> None:
    case = golden_case()
    judge, client = make_judge(FakeResponse())

    judge("the summary", case)

    (call,) = client.messages.calls
    assert call["system"] == JUDGE_SYSTEM_PROMPT
    assert call["messages"] == [
        {"role": "user", "content": render_judge_input("the summary", case)}
    ]


def test_a_valid_reply_is_scored_with_usage_model_and_latency() -> None:
    judge, _ = make_judge(FakeResponse(model="claude-opus-5-5-20261001"))

    result = judge("summary", golden_case())

    assert result.status == "scored"
    assert result.score == 8
    assert result.reasoning == "Covers every fact."
    assert result.model == "claude-opus-5-5-20261001"
    assert (result.input_tokens, result.output_tokens) == (1200, 340)
    assert result.latency_s == 2.5


def test_a_refusal_keeps_its_category_and_never_a_score() -> None:
    reply = FakeResponse(
        stop_reason="refusal",
        stop_details=FakeStopDetails("reasoning_extraction"),
        content=(FakeTextBlock(text="I cannot grade this."),),
    )
    judge, _ = make_judge(reply)

    result = judge("summary", golden_case())

    assert result.status == "refused"
    assert result.refusal_category == "reasoning_extraction"
    assert result.score is None
    assert (result.input_tokens, result.output_tokens) == (1200, 340)


def test_a_refusal_without_details_has_no_category() -> None:
    judge, _ = make_judge(FakeResponse(stop_reason="refusal", content=()))

    result = judge("summary", golden_case())

    assert result.status == "refused"
    assert result.refusal_category is None


def test_a_reply_cut_by_max_tokens_is_an_error_that_keeps_its_tokens() -> None:
    truncated = FakeTextBlock(text='{"reasoning": "partial')
    judge, _ = make_judge(FakeResponse(stop_reason="max_tokens", content=(truncated,)))

    result = judge("summary", golden_case())

    assert result.status == "error"
    assert result.score is None
    assert (result.input_tokens, result.output_tokens) == (1200, 340)


def test_a_reply_without_a_text_block_is_an_error() -> None:
    judge, _ = make_judge(FakeResponse(content=(FakeThinkingBlock(),)))

    assert judge("summary", golden_case()).status == "error"


def test_a_score_outside_the_range_is_an_error_and_never_a_score() -> None:
    bad = FakeTextBlock(text='{"reasoning": "x", "score": 11}')
    judge, _ = make_judge(FakeResponse(content=(bad,)))

    result = judge("summary", golden_case())

    assert result.status == "error"
    assert result.score is None
    assert result.model == MODEL
    assert (result.input_tokens, result.output_tokens) == (1200, 340)


def test_an_sdk_failure_is_an_error_that_keeps_the_requested_model_and_latency() -> None:
    judge, _ = make_judge(anthropic.AnthropicError("boom"))

    result = judge("summary", golden_case())

    assert result.status == "error"
    assert result.model == MODEL
    assert result.latency_s == 2.5


def test_a_substituted_model_invalidates_the_comparison(
    caplog: pytest.LogCaptureFixture,
) -> None:
    judge, _ = make_judge(FakeResponse(model="claude-opus-4-8"))

    result = judge("summary", golden_case())

    assert result.status == "error"
    assert result.model == "claude-opus-4-8"
    assert result.score is None
    assert "second judge served claude-opus-4-8, not claude-opus-5-5" in caplog.text


def claude_settings(**overrides: Any) -> EvalSettings:
    keys = {"openai_api_key": "sk-test", "langfuse_public_key": "pk", "langfuse_secret_key": "sk"}
    return EvalSettings(_env_file=None, parse_cache_enabled=False, **{**keys, **overrides})


def test_the_factory_returns_none_without_an_anthropic_key() -> None:
    assert build_claude_judge(claude_settings()) is None


def test_the_factory_builds_the_client_from_the_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, Any] = {}

    client = FakeClient(FakeResponse())

    def fake_anthropic(**kwargs: Any) -> FakeClient:
        received.update(kwargs)
        return client

    monkeypatch.setattr(claude_judge.anthropic, "Anthropic", fake_anthropic)
    settings = claude_settings(
        anthropic_api_key="sk-ant-test",
        claude_judge_model="claude-opus-4-8",
        claude_judge_effort="high",
        llm_timeout_seconds=45,
        llm_max_retries=1,
    )

    judge = build_claude_judge(settings)

    assert isinstance(judge, ClaudeSummaryJudge)
    assert received == {"api_key": "sk-ant-test", "timeout": 45, "max_retries": 1}
    judge("summary", golden_case())
    (call,) = client.messages.calls
    assert call["model"] == "claude-opus-4-8"
    assert call["output_config"]["effort"] == "high"
