"""Shared doubles for vision parser tests: a recording chat model and image builders."""

import io
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from PIL import Image


def png_bytes(width: int = 40, height: int = 30) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class RecordingChatModel:
    """Stands in for ChatOpenAI; records each call and returns scripted replies."""

    def __init__(self, reply: str = "Cláusula Primera. Objeto.", error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.calls: list[tuple[list[BaseMessage], RunnableConfig]] = []
        self.runnable: RunnableLambda[Any, AIMessage] = RunnableLambda(self._respond)

    def _respond(self, messages: list[BaseMessage], config: RunnableConfig) -> AIMessage:
        self.calls.append((messages, config))
        if self.error:
            raise self.error
        return AIMessage(content=self.reply)
