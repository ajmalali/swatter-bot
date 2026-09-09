"""Test doubles shared across test modules."""

from __future__ import annotations

from collections import deque
from typing import Any

from pydantic import BaseModel


class FakeLLM:
    """Stands in for LLMClient. Queue raw dicts; each complete_json pops one and validates it."""

    def __init__(self, *replies: dict[str, Any]) -> None:
        self.replies = deque(replies)
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, *, task, system, user, schema, draft_id=None, max_attempts=2):
        self.calls.append({"task": task, "user": user, "schema": schema, "draft_id": draft_id})
        if not self.replies:
            raise AssertionError(f"FakeLLM has no reply queued for task {task}")
        reply = self.replies.popleft()
        if isinstance(reply, Exception):
            raise reply
        model: type[BaseModel] = schema
        return model.model_validate(reply)
