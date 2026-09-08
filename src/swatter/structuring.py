"""LLM job 1: fill the Template's fields from a Report and its thread.

TODO(scaffold): implement. The schema is built dynamically from the ParsedTemplate so any
repo template works; labels are an enum of the repo's real labels.
"""

from __future__ import annotations

from swatter.llm import LLMClient
from swatter.templates import ParsedTemplate


def structure_report(
    llm: LLMClient,
    *,
    template: ParsedTemplate,
    labels: list[str],
    report_text: str,
    thread_text: str,
    draft_id: str,
) -> dict[str, str | list[str]]:
    raise NotImplementedError


def missing_required(template: ParsedTemplate, values: dict[str, str | list[str]]) -> list[str]:
    """Deterministic check of which required fields came back empty."""
    return [f.id for f in template.llm_fields if f.required and not values.get(f.id)]
