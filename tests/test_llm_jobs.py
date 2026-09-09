from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from swatter.clarify import plan_clarification
from swatter.db import Database
from swatter.judge import judge_candidates
from swatter.llm import LLMOutputError
from swatter.models import Candidate, IssueRecord
from swatter.store import upsert_issue
from swatter.structuring import (
    build_schema,
    missing_required,
    route_report,
    structure_report,
)
from swatter.templates import default_template, parse_issue_form
from tests.fakes import FakeLLM

FORM = """
body:
  - type: textarea
    id: steps
    attributes: {label: Steps}
    validations: {required: true}
  - type: dropdown
    id: browser
    attributes: {label: Browser, options: [Chrome, Firefox]}
  - type: checkboxes
    id: checks
    attributes:
      label: Checks
      options: [{label: Searched}, {label: Reproduced}]
"""


def test_schema_constrains_labels_dropdowns_and_checkboxes():
    schema = build_schema(parse_issue_form(FORM), ["bug", "ui"])
    ok = schema.model_validate(
        {"title": "t", "labels": ["bug"], "steps": "1", "browser": "Chrome", "checks": ["Searched"]}
    )
    assert ok.browser == "Chrome"
    with pytest.raises(ValidationError):
        schema.model_validate({"title": "t", "labels": ["nope"]})
    with pytest.raises(ValidationError):
        schema.model_validate({"title": "t", "browser": "Edge"})
    with pytest.raises(ValidationError):
        schema.model_validate({"title": "t", "checks": ["Other"]})


def test_structure_report_cleans_and_orders_values():
    template = default_template()
    llm = FakeLLM(
        {
            "title": "  Checkout hangs  ",
            "labels": ["bug", "bug"],
            "summary": "hangs",
            "steps_to_reproduce": "",
            "expected_behavior": "completes",
            "actual_behavior": "spinner",
            "environment": "",
        }
    )
    values = structure_report(
        llm,
        template=template,
        labels=["bug"],
        report_text="it hangs",
        thread_text="",
        draft_id="d1",
    )
    assert values["title"] == "Checkout hangs"
    assert values["labels"] == ["bug"]
    assert list(values) == [
        "title",
        "labels",
        "summary",
        "steps_to_reproduce",
        "expected_behavior",
        "actual_behavior",
        "environment",
    ]
    assert missing_required(template, values) == ["steps_to_reproduce"]
    assert "Available labels: bug" in llm.calls[0]["user"]
    assert llm.calls[0]["task"] == "structure"


def test_route_report_is_an_enum_over_bound_repos():
    llm = FakeLLM({"repo": "o/api", "reason": "it is the API"})
    chosen = route_report(
        llm,
        repos={"o/web": "The web app", "o/api": "The REST API"},
        report_text="POST /login 500s",
        thread_text="",
        draft_id="d1",
    )
    assert chosen == "o/api"
    schema = llm.calls[0]["schema"]
    with pytest.raises(ValidationError):
        schema.model_validate({"repo": "o/other", "reason": "x"})


def test_route_report_single_repo_skips_the_llm():
    llm = FakeLLM()
    assert (
        route_report(llm, repos={"o/web": ""}, report_text="", thread_text="", draft_id="d")
        == "o/web"
    )
    assert llm.calls == []


def _seed_issue(db, repo, number, body):
    upsert_issue(
        db,
        IssueRecord(
            repo=repo,
            number=number,
            title=f"issue {number}",
            body=body,
            state="open",
            updated_at=datetime.now(UTC),
        ),
        None,
    )


def test_judge_keeps_only_same_bug_and_survives_bad_output(tmp_path):
    db = Database(tmp_path / "t.db")
    _seed_issue(db, "o/r", 1, "spinner on checkout")
    _seed_issue(db, "o/r", 2, "export csv empty")
    _seed_issue(db, "o/r", 3, "login crash")
    cands = [
        Candidate(repo="o/r", number=n, title=f"issue {n}", state="open", retrieval_score=0.1)
        for n in (1, 2, 3)
    ]
    llm = FakeLLM(
        {"same_bug": True, "reason": "same spinner"},
        LLMOutputError("judge: invalid output"),
        {"same_bug": False, "reason": "different"},
    )
    kept = judge_candidates(
        llm,
        db,
        draft_fields={"title": "Checkout spinner", "labels": ["bug"]},
        candidates=cands,
        draft_id="d",
    )
    assert [c.number for c in kept] == [1]
    assert kept[0].verdict.reason == "same spinner"
    assert cands[2].verdict is not None and not cands[2].verdict.same_bug
    assert "spinner on checkout" in llm.calls[0]["user"]
    assert "labels" not in llm.calls[0]["user"].split("Existing issue")[0]


def test_clarification_limits_are_enforced_by_code():
    llm = FakeLLM(
        {
            "questions": [
                {"field": "expected_behavior", "question": "What should happen?"},
                {"field": "steps_to_reproduce", "question": "How do you trigger it?"},
                {"field": "steps_to_reproduce", "question": "duplicate"},
                {"field": "environment", "question": "not asked for"},
            ],
            "wants_screenshot": True,
        }
    )
    plan = plan_clarification(
        llm,
        report_text="it broke",
        missing_fields=["steps_to_reproduce", "expected_behavior", "actual_behavior"],
        max_questions=2,
        draft_id="d",
    )
    assert [q.field for q in plan.questions] == ["steps_to_reproduce", "expected_behavior"]
    assert plan.wants_screenshot
    assert "Allowed number of questions: 2" in llm.calls[0]["user"]


def test_clarification_with_nothing_missing_skips_the_llm():
    llm = FakeLLM()
    plan = plan_clarification(
        llm, report_text="x", missing_fields=[], max_questions=3, draft_id="d"
    )
    assert plan.questions == [] and llm.calls == []
