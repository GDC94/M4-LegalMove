"""Doubles for agent tests: a recording structured model and scripted include_raw replies."""

import json
from typing import Any

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.prompt_values import PromptValue
from langchain_core.runnables import RunnableConfig, RunnableLambda

Reply = dict[str, Any]


def structured_reply(payload: dict[str, Any]) -> Reply:
    """What `with_structured_output(..., include_raw=True)` returns when the JSON parses."""
    return {
        "raw": AIMessage(content=json.dumps(payload, ensure_ascii=False)),
        "parsed": payload,
        "parsing_error": None,
    }


def unparseable_reply(raw_text: str) -> Reply:
    """What the same chain returns when the output parser fails: no exception, an error key."""
    return {
        "raw": AIMessage(content=raw_text),
        "parsed": None,
        "parsing_error": OutputParserException(f"Invalid json output: {raw_text}"),
    }


class RecordingStructuredModel:
    """Stands in above `with_structured_output`; records each prompt and plays scripted replies."""

    def __init__(self, *replies: Reply | Exception) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[list[BaseMessage], RunnableConfig]] = []
        self.runnable: RunnableLambda[Any, Reply] = RunnableLambda(self._respond)

    def _respond(self, prompt: PromptValue, config: RunnableConfig) -> Reply:
        self.calls.append((prompt.to_messages(), config))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def human_text(self, call: int) -> str:
        return str(self.calls[call][0][-1].content)
