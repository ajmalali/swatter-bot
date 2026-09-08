"""Issue Templates: derive the field list from a repo's template, render a Draft into it.

Supports GitHub issue forms (YAML, with ids and required flags) and Markdown templates
(headings become fields). Falls back to templates/default_issue.md. See ADR 0004.

TODO(scaffold): implement parse_* and render.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

DEFAULT_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "default_issue.md"

# Fields Swatter always provides itself; the LLM never fills these.
SYSTEM_FIELDS = frozenset(
    {"original_report", "reporter_name", "channel_name", "permalink", "attachments"}
)

# Markdown templates carry no required flags, so these headings count as required when present.
REQUIRED_BY_NAME = frozenset({"steps_to_reproduce", "expected_behavior", "actual_behavior"})


class TemplateField(BaseModel):
    id: str
    label: str
    required: bool = False
    kind: str = "textarea"  # textarea | input | dropdown | checkboxes


class ParsedTemplate(BaseModel):
    source: str  # "issue-form" | "markdown" | "default"
    fields: list[TemplateField]
    body: str  # the text to render into, with {{field_id}} placeholders

    @property
    def llm_fields(self) -> list[TemplateField]:
        return [f for f in self.fields if f.id not in SYSTEM_FIELDS]


def parse_issue_form(yaml_text: str) -> ParsedTemplate:
    raise NotImplementedError


def parse_markdown_template(md_text: str) -> ParsedTemplate:
    raise NotImplementedError


def default_template() -> ParsedTemplate:
    raise NotImplementedError


def render(template: ParsedTemplate, values: dict[str, str | list[str]]) -> str:
    """Fill placeholders. Empty values render as '_Not provided_'."""
    raise NotImplementedError
