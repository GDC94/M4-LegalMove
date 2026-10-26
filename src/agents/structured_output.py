"""Shared structured-output plumbing for both agents: include_raw replies and the retry blocks."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from pydantic_core import ErrorDetails

from errors import RetryFeedback
from prompt_blocks import serialize_block

StructuredReply = Mapping[str, object]
StructuredModel = Runnable[LanguageModelInput, StructuredReply]


@dataclass(frozen=True)
class StructuredModels:
    """The first attempt and the semantic retry, which always runs at temperature 0."""

    attempt: StructuredModel
    retry: StructuredModel


def build_structured_models(
    chat_model: BaseChatModel, response_format: dict[str, object]
) -> StructuredModels:
    """The injection seam sits above this call: LangChain fakes cannot do structured output."""
    retry_chat_model = chat_model.model_copy(update={"temperature": 0})
    return StructuredModels(
        attempt=build_structured_model(chat_model, response_format),
        retry=build_structured_model(retry_chat_model, response_format),
    )


def build_structured_model(
    chat_model: BaseChatModel, response_format: dict[str, object]
) -> StructuredModel:
    # A plain JSON schema, not the Pydantic class: given the class, the OpenAI SDK validates
    # inside the call and a validator failure would raise past include_raw instead of feeding
    # the retry.
    structured = chat_model.with_structured_output(
        response_format, method="json_schema", strict=True, include_raw=True
    )
    # include_raw=True always yields the raw/parsed/parsing_error mapping; the stub is wider.
    return cast(StructuredModel, structured)


def read_raw_output(reply: StructuredReply) -> str:
    raw = reply.get("raw")
    return raw.text if isinstance(raw, BaseMessage) else ""


def format_error_path(error: ErrorDetails) -> str:
    """Pydantic loc in the bracket notation of checks.py: sections[0].section_id."""
    loc = list(error["loc"])
    if error["type"] == "extra_forbidden":
        loc = loc[:-1]  # the last part is a key the model invented, so it may be any text
    path = "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in loc)
    return path.removeprefix(".") or "$"


def render_retry_blocks(
    raw_output: str, feedback: Sequence[RetryFeedback], instruction: str
) -> str:
    """Same prompt plus the raw previous output and one error per line (plan §4.4)."""
    return (
        f"\n\n{serialize_block('previous_attempt', raw_output)}"
        f"\n\n{serialize_block('validation_errors', render_feedback_lines(feedback))}"
        f"\n\n{instruction}"
    )


def render_feedback_lines(feedback: Sequence[RetryFeedback]) -> str:
    return "\n".join(f"- {describe_feedback(item)}" for item in feedback)


def describe_feedback(item: RetryFeedback) -> str:
    return f"{item.issue.code} at {item.issue.path}: {item.detail}"
