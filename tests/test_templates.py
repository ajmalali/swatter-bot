import pytest

from swatter.templates import (
    NOT_PROVIDED,
    default_template,
    parse_issue_form,
    parse_markdown_template,
    parse_template,
    render,
    slugify,
)

ISSUE_FORM = """
name: Bug report
labels: ["bug", "triage"]
body:
  - type: markdown
    attributes:
      value: Thanks for reporting.
  - type: input
    id: version
    attributes:
      label: Version
      description: Which release?
    validations:
      required: true
  - type: textarea
    id: repro
    attributes:
      label: Steps to reproduce
    validations:
      required: true
  - type: dropdown
    id: browser
    attributes:
      label: Browser
      options: [Chrome, Firefox, Safari]
  - type: checkboxes
    id: checks
    attributes:
      label: Checks
      options:
        - label: I searched existing issues
        - label: I can reproduce this
"""

GITHUB_STOCK_MD = """---
name: Bug report
about: Create a report to help us improve
labels: bug
---

**Describe the bug**
A clear and concise description of what the bug is.

**To Reproduce**
Steps to reproduce the behavior:
1. Go to '...'

**Expected behavior**
A clear and concise description of what you expected to happen.

**Screenshots**
If applicable, add screenshots to help explain your problem.

**Additional context**
Add any other context about the problem here.
"""


def test_issue_form_fields_required_and_options():
    t = parse_issue_form(ISSUE_FORM)
    ids = [f.id for f in t.llm_fields]
    assert ids == ["version", "repro", "browser", "checks"]
    by_id = {f.id: f for f in t.fields}
    assert by_id["version"].required and by_id["version"].kind == "input"
    assert by_id["browser"].options == ["Chrome", "Firefox", "Safari"]
    assert by_id["checks"].kind == "checkboxes"
    assert by_id["checks"].options == ["I searched existing issues", "I can reproduce this"]
    assert t.default_labels == ["bug", "triage"]
    assert "{{original_report}}" in t.body and "{{permalink}}" in t.body


def test_markdown_bold_labels_and_aliases():
    t = parse_markdown_template(GITHUB_STOCK_MD)
    ids = [f.id for f in t.llm_fields]
    assert ids == ["summary", "steps_to_reproduce", "expected_behavior", "additional_context"]
    assert t.field("steps_to_reproduce").required
    assert not t.field("additional_context").required
    assert t.default_labels == ["bug"]
    # Screenshots heading is a system field rendered by the footer, so it appears once.
    assert t.body.count("{{attachments}}") == 1


def test_markdown_headings_and_british_spelling():
    t = parse_markdown_template("## Steps\n\n## Expected behaviour\n\n## Actual result\n")
    assert [f.id for f in t.llm_fields] == [
        "steps_to_reproduce",
        "expected_behavior",
        "actual_behavior",
    ]
    assert all(f.required for f in t.llm_fields)


def test_markdown_without_headings_is_rejected():
    with pytest.raises(ValueError):
        parse_markdown_template("just some prose")


def test_parse_template_dispatches_on_extension():
    assert parse_template(ISSUE_FORM, "bug.yml").source == "issue-form"
    assert parse_template(GITHUB_STOCK_MD, "bug_report.md").source == "markdown"


def test_default_template_fields():
    t = default_template()
    assert [f.id for f in t.llm_fields] == [
        "summary",
        "steps_to_reproduce",
        "expected_behavior",
        "actual_behavior",
        "environment",
    ]
    assert t.field("steps_to_reproduce").required
    assert not t.field("environment").required
    assert {f.id for f in t.system_fields} == {
        "attachments",
        "original_report",
        "reporter_name",
        "channel_name",
        "permalink",
    }


def test_render_fills_lists_and_marks_empty():
    t = default_template()
    out = render(
        t,
        {
            "summary": "Checkout hangs",
            "steps_to_reproduce": ["Open cart", "Click pay"],
            "environment": "",
            "original_report": "> it hangs",
            "reporter_name": "Sam",
            "channel_name": "bugs",
            "permalink": "https://slack.example/p1",
        },
    )
    assert "Checkout hangs" in out
    assert "- Open cart\n- Click pay" in out
    assert out.count(NOT_PROVIDED) == 4  # expected, actual, environment, attachments
    assert "[View in Slack](https://slack.example/p1)" in out
    assert "{{" not in out


def test_slugify():
    assert slugify("Expected behaviour") == "expected_behavior"
    assert slugify("To Reproduce") == "steps_to_reproduce"
    assert slugify("  Weird -- Label! ") == "weird_label"
