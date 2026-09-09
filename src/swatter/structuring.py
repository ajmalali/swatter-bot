"""LLM jobs 0 and 1: pick the repo when a channel binds several, then fill the Template's fields.

The field schema is built dynamically from the ParsedTemplate so any repo template works;
labels, dropdowns, and checkboxes are enums of the real options. Code post-checks everything.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

from swatter.llm import LLMClient, load_prompt
from swatter.templates import ParsedTemplate, TemplateField

MAX_LABELS_IN_SCHEMA = 60
THREAD_CHAR_LIMIT = 12_000


class RouteDecision(BaseModel):
    repo: str
    reason: str


def route_report(
    llm: LLMClient,
    *,
    repos: dict[str, str],
    report_text: str,
    thread_text: str,
    draft_id: str,
) -> str:
    """Return one of the keys of `repos` (repo full name -> GitHub description)."""
    names = list(repos)
    if len(names) == 1:
        return names[0]
    schema = create_model(
        "RouteDecision",
        repo=(Literal[tuple(names)], ...),  # type: ignore[valid-type]
        reason=(str, ...),
        __base__=BaseModel,
    )
    listing = "\n".join(f"- {name}: {desc or '(no description)'}" for name, desc in repos.items())
    user = (
        f"Repositories:\n{listing}\n\n"
        f"Report:\n{report_text}\n\n"
        f"Thread:\n{_clip(thread_text) or '(no replies)'}"
    )
    out = llm.complete_json(
        task="route", system=load_prompt("route"), user=user, schema=schema, draft_id=draft_id
    )
    return out.repo  # type: ignore[attr-defined]


def structure_report(
    llm: LLMClient,
    *,
    template: ParsedTemplate,
    labels: list[str],
    report_text: str,
    thread_text: str,
    draft_id: str,
) -> dict[str, str | list[str]]:
    schema = build_schema(template, labels)
    user = (
        f"Available labels: {', '.join(labels) if labels else '(none)'}\n\n"
        f"Fields to fill:\n{_describe_fields(template.llm_fields)}\n\n"
        f"Report:\n{report_text}\n\n"
        f"Thread:\n{_clip(thread_text) or '(no replies)'}"
    )
    out = llm.complete_json(
        task="structure",
        system=load_prompt("structure"),
        user=user,
        schema=schema,
        draft_id=draft_id,
    )
    return clean_values(template, labels, out.model_dump())


def build_schema(template: ParsedTemplate, labels: list[str]) -> type[BaseModel]:
    """A Pydantic model with title, labels, and one attribute per LLM-filled field."""
    fields: dict[str, Any] = {
        "title": (str, Field(..., description="One line, under 80 characters, the symptom.")),
        "labels": (_labels_type(labels), Field(default_factory=list)),
    }
    for f in template.llm_fields:
        fields[f.id] = _field_type(f)
    return create_model("StructuredReport", __base__=BaseModel, **fields)


def clean_values(
    template: ParsedTemplate, labels: list[str], raw: dict[str, Any]
) -> dict[str, str | list[str]]:
    """Deterministic pass after validation: strip, drop unknown labels, keep field order."""
    values: dict[str, str | list[str]] = {"title": str(raw.get("title", "")).strip()[:200]}
    allowed = set(labels)
    values["labels"] = [
        label for label in dict.fromkeys(raw.get("labels") or []) if label in allowed
    ]
    for f in template.llm_fields:
        value = raw.get(f.id)
        if f.kind == "checkboxes":
            chosen = [str(v) for v in (value or []) if str(v) in f.options]
            values[f.id] = chosen
        elif f.kind == "dropdown":
            values[f.id] = str(value) if value in f.options else ""
        else:
            values[f.id] = str(value or "").strip()
    return values


def missing_required(template: ParsedTemplate, values: dict[str, str | list[str]]) -> list[str]:
    """Deterministic check of which required fields came back empty."""
    return [f.id for f in template.llm_fields if f.required and not values.get(f.id)]


def _labels_type(labels: list[str]) -> Any:
    if not labels:
        return list[str]
    return list[Literal[tuple(labels[:MAX_LABELS_IN_SCHEMA])]]  # type: ignore[valid-type]


def _field_type(f: TemplateField) -> tuple[Any, Any]:
    desc = f.label if not f.description else f"{f.label}. {f.description}"
    if f.kind == "dropdown" and f.options:
        return (Literal[tuple([*f.options, ""])], Field("", description=desc))  # type: ignore[valid-type]
    if f.kind == "checkboxes" and f.options:
        return (list[Literal[tuple(f.options)]], Field(default_factory=list, description=desc))  # type: ignore[valid-type]
    return (str, Field("", description=desc))


def _describe_fields(fields: list[TemplateField]) -> str:
    lines = []
    for f in fields:
        bits = [f.id, f"({f.label})"]
        if f.required:
            bits.append("[required]")
        if f.options:
            bits.append("options: " + ", ".join(f.options))
        if f.description:
            bits.append("- " + f.description)
        lines.append("- " + " ".join(bits))
    return "\n".join(lines) or "(none)"


def _clip(text: str) -> str:
    text = text.strip()
    if len(text) <= THREAD_CHAR_LIMIT:
        return text
    return text[:THREAD_CHAR_LIMIT] + "\n[thread truncated]"
