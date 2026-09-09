"""The only path to the LLM. One OpenAI-compatible client, structured output enforced by code.

Every call asks for a JSON object, validates it against a Pydantic model, and retries once with
the validation error fed back. Every attempt is logged to llm_log. See ADR 0002 and ADR 0004.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from swatter.config import Settings
from swatter.db import Database

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LLMOutputError(RuntimeError):
    """The model failed to produce valid JSON for the schema after all attempts."""


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def extract_json(text: str) -> str:
    """Pull the first JSON object out of a model response, tolerating fences and chatter."""
    cleaned = _FENCE.sub("", text.strip())
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in response")
    return cleaned[start : end + 1]


class LLMClient:
    def __init__(self, settings: Settings, db: Database) -> None:
        self._model = settings.llm_model
        self._db = db
        self._client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key or "none",
            default_headers=settings.llm_extra_headers or None,
        )

    def complete_json(
        self,
        *,
        task: str,
        system: str,
        user: str,
        schema: type[T],
        draft_id: str | None = None,
        max_attempts: int = 2,
    ) -> T:
        schema_text = json.dumps(schema.model_json_schema(), indent=None)
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": f"{system}\n\nJSON Schema for your reply:\n{schema_text}",
            },
            {"role": "user", "content": user},
        ]
        last_error = "no attempts made"
        for attempt in range(1, max_attempts + 1):
            started = time.monotonic()
            response = self._client.chat.completions.create(
                model=self._model, messages=messages, temperature=0
            )
            latency_ms = int((time.monotonic() - started) * 1000)
            text = response.choices[0].message.content or ""
            try:
                parsed = schema.model_validate_json(extract_json(text))
            except (ValidationError, ValueError) as exc:
                last_error = str(exc)
                self._log(task, attempt, messages, text, False, last_error, latency_ms, draft_id)
                messages.append({"role": "assistant", "content": text})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "That reply was not valid for the schema. Errors:\n"
                            f"{last_error}\n\nReturn only the corrected JSON object."
                        ),
                    }
                )
                continue
            self._log(task, attempt, messages, text, True, None, latency_ms, draft_id)
            return parsed
        raise LLMOutputError(f"{task}: invalid output after {max_attempts} attempts: {last_error}")

    def _log(
        self,
        task: str,
        attempt: int,
        messages: list[dict[str, str]],
        response: str,
        valid: bool,
        error: str | None,
        latency_ms: int,
        draft_id: str | None,
    ) -> None:
        with self._db.tx() as conn:
            conn.execute(
                "INSERT INTO llm_log (draft_id, task, model, attempt, prompt, response, valid,"
                " error, latency_ms, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    draft_id,
                    task,
                    self._model,
                    attempt,
                    json.dumps(messages),
                    response,
                    int(valid),
                    error,
                    latency_ms,
                    datetime.now(UTC).isoformat(),
                ),
            )
