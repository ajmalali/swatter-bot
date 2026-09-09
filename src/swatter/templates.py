"""Issue Templates: derive the field list from a repo's template, render a Draft into it.

Supports GitHub issue forms (YAML, with ids and required flags) and Markdown templates
(headings or bold paragraph labels become fields). Falls back to templates/default_issue.md.
Every rendered Issue ends with templates/footer.md, which carries the verbatim Report,
Attachments, reporter, channel, and permalink. See ADR 0004.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
DEFAULT_TEMPLATE_PATH = TEMPLATES_DIR / "default_issue.md"
FOOTER_PATH = TEMPLATES_DIR / "footer.md"

# Fields Swatter always provides itself; the LLM never fills these.
SYSTEM_FIELDS = frozenset(
    {"original_report", "reporter_name", "channel_name", "permalink", "attachments"}
)

# Markdown templates carry no required flags, so these headings count as required when present.
REQUIRED_BY_NAME = frozenset({"steps_to_reproduce", "expected_behavior", "actual_behavior"})

# Common heading wordings mapped onto the canonical ids above, so the required rule and the
# default template line up across repos. Keys are slugs after British spellings are normalised.
_ALIASES = {
    "to_reproduce": "steps_to_reproduce",
    "reproduction_steps": "steps_to_reproduce",
    "how_to_reproduce": "steps_to_reproduce",
    "steps": "steps_to_reproduce",
    "expected": "expected_behavior",
    "expected_result": "expected_behavior",
    "expected_results": "expected_behavior",
    "actual": "actual_behavior",
    "actual_result": "actual_behavior",
    "actual_results": "actual_behavior",
    "current_behavior": "actual_behavior",
    "describe_the_bug": "summary",
    "description": "summary",
    "bug_description": "summary",
    "screenshot": "attachments",
    "screenshots": "attachments",
}

NOT_PROVIDED = "_Not provided_"

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")
_HEADING = re.compile(r"^(?:#{1,6}\s+(?P<h>.+?)\s*#*|\*\*(?P<b>[^*]+?)\*\*:?)\s*$")
_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class TemplateField(BaseModel):
    id: str
    label: str
    description: str = ""
    required: bool = False
    kind: str = "textarea"  # textarea | input | dropdown | checkboxes
    options: list[str] = Field(default_factory=list)  # dropdown and checkboxes only


class ParsedTemplate(BaseModel):
    source: str  # "issue-form" | "markdown" | "default"
    fields: list[TemplateField]
    body: str  # the text to render into, with {{field_id}} placeholders
    default_labels: list[str] = Field(default_factory=list)  # labels the template itself sets

    @property
    def llm_fields(self) -> list[TemplateField]:
        return [f for f in self.fields if f.id not in SYSTEM_FIELDS]

    @property
    def system_fields(self) -> list[TemplateField]:
        return [f for f in self.fields if f.id in SYSTEM_FIELDS]

    def field(self, field_id: str) -> TemplateField | None:
        return next((f for f in self.fields if f.id == field_id), None)


def slugify(label: str) -> str:
    """'Expected behaviour' -> 'expected_behavior'. Applies the alias table."""
    slug = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    slug = slug.replace("behaviour", "behavior")
    return _ALIASES.get(slug, slug)


def parse_template(text: str, filename: str) -> ParsedTemplate:
    """Dispatch on the template's filename, the way GitHub does."""
    if filename.lower().endswith((".yml", ".yaml")):
        return parse_issue_form(text)
    return parse_markdown_template(text)


def parse_issue_form(yaml_text: str) -> ParsedTemplate:
    data = yaml.safe_load(yaml_text) or {}
    if not isinstance(data, dict) or not isinstance(data.get("body"), list):
        raise ValueError("issue form has no body list")
    fields: list[TemplateField] = []
    seen: set[str] = set()
    for i, element in enumerate(data["body"]):
        if not isinstance(element, dict):
            continue
        kind = element.get("type")
        if kind not in ("input", "textarea", "dropdown", "checkboxes"):
            continue  # "markdown" elements are display only
        attrs = element.get("attributes") or {}
        label = str(attrs.get("label") or element.get("id") or f"field_{i}")
        field_id = slugify(str(element.get("id") or label))
        if field_id in seen:
            field_id = f"{field_id}_{i}"
        seen.add(field_id)
        options: list[str]
        if kind == "dropdown":
            options = [str(o) for o in attrs.get("options") or []]
        elif kind == "checkboxes":
            options = [
                str(o.get("label", "")) if isinstance(o, dict) else str(o)
                for o in attrs.get("options") or []
            ]
        else:
            options = []
        fields.append(
            TemplateField(
                id=field_id,
                label=label,
                description=str(attrs.get("description") or ""),
                required=bool((element.get("validations") or {}).get("required", False)),
                kind=kind,
                options=options,
            )
        )
    labels = data.get("labels") or []
    if isinstance(labels, str):
        labels = [labels]
    return _assemble("issue-form", fields, [str(label) for label in labels])


def parse_markdown_template(md_text: str) -> ParsedTemplate:
    """Each heading (or bold-only line) becomes a field; the prose under it is dropped."""
    default_labels: list[str] = []
    match = _FRONT_MATTER.match(md_text)
    if match:
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            meta = {}
        raw = meta.get("labels") if isinstance(meta, dict) else None
        if isinstance(raw, str):
            default_labels = [s.strip() for s in raw.split(",") if s.strip()]
        elif isinstance(raw, list):
            default_labels = [str(s) for s in raw]
        md_text = md_text[match.end() :]

    fields: list[TemplateField] = []
    seen: set[str] = set()
    for line in md_text.splitlines():
        heading = _HEADING.match(line)
        if not heading:
            continue
        label = (heading.group("h") or heading.group("b")).strip().rstrip(":")
        field_id = slugify(label)
        if not field_id or field_id in seen:
            continue
        seen.add(field_id)
        fields.append(
            TemplateField(id=field_id, label=label, required=field_id in REQUIRED_BY_NAME)
        )
    if not fields:
        raise ValueError("markdown template has no headings to use as fields")
    return _assemble("markdown", fields, default_labels)


def default_template() -> ParsedTemplate:
    text = DEFAULT_TEMPLATE_PATH.read_text(encoding="utf-8")
    fields: list[TemplateField] = []
    label = ""
    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading:
            label = (heading.group("h") or heading.group("b")).strip()
            continue
        for field_id in _PLACEHOLDER.findall(line):
            fields.append(
                TemplateField(
                    id=field_id, label=label or field_id, required=field_id in REQUIRED_BY_NAME
                )
            )
    return ParsedTemplate(
        source="default", fields=fields + _footer_fields(), body=text.rstrip() + "\n\n" + _footer()
    )


def render(template: ParsedTemplate, values: dict[str, str | list[str]]) -> str:
    """Fill placeholders. Empty values render as '_Not provided_'."""

    def fill(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        if isinstance(value, list):
            value = "\n".join(f"- {item}" for item in value if str(item).strip())
        text = (value or "").strip() if isinstance(value, str) else ""
        return text or NOT_PROVIDED

    return _PLACEHOLDER.sub(fill, template.body).rstrip() + "\n"


def _assemble(source: str, fields: list[TemplateField], labels: list[str]) -> ParsedTemplate:
    # System fields a repo template mentions (for example a Screenshots heading) are rendered
    # by the footer, so they are dropped from the body to avoid appearing twice.
    body_fields = [f for f in fields if f.id not in SYSTEM_FIELDS]
    sections = [f"## {f.label}\n\n{{{{{f.id}}}}}" for f in body_fields]
    body = "\n\n".join(sections) + "\n\n" + _footer()
    return ParsedTemplate(
        source=source, fields=body_fields + _footer_fields(), body=body, default_labels=labels
    )


def _footer() -> str:
    return FOOTER_PATH.read_text(encoding="utf-8").rstrip() + "\n"


def _footer_fields() -> list[TemplateField]:
    fields = []
    for field_id in dict.fromkeys(_PLACEHOLDER.findall(_footer())):
        fields.append(TemplateField(id=field_id, label=field_id.replace("_", " ").title()))
    return fields
