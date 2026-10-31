"""Second Judge on Claude: same rubric and user message as the first, only the model differs."""

import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

import anthropic
from pydantic import ValidationError

from eval_case import GoldenCase, SecondJudgeResult
from judge import JUDGE_SYSTEM_PROMPT, JudgeVerdict, render_judge_input
from settings import EvalSettings

logger = logging.getLogger(__name__)

# Thinking counts inside this budget and Opus 5.5 cannot switch it off.
MAX_OUTPUT_TOKENS = 8000


# Same schema messages.parse would send; parse itself is avoided because it validates the text
# before returning, hiding stop_reason, stop_details and usage when the reply is not a verdict.
VERDICT_FORMAT: dict[str, Any] = {"type": "json_schema", "schema": JudgeVerdict.model_json_schema()}


class CreatingMessages(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class MessagingClient(Protocol):
    @property
    def messages(self) -> CreatingMessages: ...


def build_error_result(model: str, latency_s: float) -> SecondJudgeResult:
    return SecondJudgeResult(status="error", model=model, latency_s=latency_s)


def parse_verdict(response: Any) -> JudgeVerdict | None:
    text = "".join(block.text for block in response.content if block.type == "text")
    try:
        return JudgeVerdict.model_validate_json(text)
    except ValidationError as error:
        # Type only: the message quotes the reply.
        logger.warning("second judge reply is not a verdict: %s", type(error).__name__)
        return None


def classify_response(response: Any, requested_model: str, latency_s: float) -> SecondJudgeResult:
    """Turn a reply into a result; only a complete verdict from the requested model is a score."""
    fields: dict[str, Any] = {
        "model": response.model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "latency_s": latency_s,
    }
    # A substituted model would compare two different Judges without saying so.
    if not response.model.startswith(requested_model):
        logger.warning("second judge served %s, not %s", response.model, requested_model)
        return SecondJudgeResult(status="error", **fields)
    if response.stop_reason == "refusal":
        details = response.stop_details
        category = details.category if details else None
        return SecondJudgeResult(status="refused", refusal_category=category, **fields)
    verdict = None if response.stop_reason == "max_tokens" else parse_verdict(response)
    if verdict is None:
        return SecondJudgeResult(status="error", **fields)
    return SecondJudgeResult(
        status="scored", score=verdict.score, reasoning=verdict.reasoning, **fields
    )


class ClaudeSummaryJudge:
    """Second Judge on Claude: the rubric and user message of SummaryJudge, a different model."""

    def __init__(
        self,
        client: MessagingClient,
        model: str,
        effort: str,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._client = client
        self._model = model
        self._effort = effort
        self._clock = clock

    def __call__(self, summary: str, case: GoldenCase) -> SecondJudgeResult:
        started = self._clock()
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=JUDGE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": render_judge_input(summary, case)}],
                output_config={"effort": self._effort, "format": VERDICT_FORMAT},
            )
        except anthropic.AnthropicError as error:
            # Type only: the message may quote the Summary under grading.
            logger.warning("second judge failed: %s", type(error).__name__)
            return build_error_result(self._model, self._clock() - started)
        return classify_response(response, self._model, self._clock() - started)


def build_claude_judge(settings: EvalSettings) -> ClaudeSummaryJudge | None:
    """None without an Anthropic key, so the eval still runs with the first Judge alone."""
    if settings.anthropic_api_key is None:
        return None
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
    return ClaudeSummaryJudge(client, settings.claude_judge_model, settings.claude_judge_effort)
