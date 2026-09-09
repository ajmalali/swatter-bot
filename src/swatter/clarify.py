"""LLM job 2: write up to N questions for the required fields that came back empty.

Code decides *whether* to ask (missing_required) and *how many* (settings). The LLM only
writes the wording and flags whether a screenshot would help.
"""

from __future__ import annotations

from swatter.llm import LLMClient, load_prompt
from swatter.models import ClarificationPlan


def plan_clarification(
    llm: LLMClient,
    *,
    report_text: str,
    missing_fields: list[str],
    max_questions: int,
    draft_id: str,
    field_labels: dict[str, str] | None = None,
) -> ClarificationPlan:
    asked_for = missing_fields[:max_questions]
    if not asked_for:
        return ClarificationPlan()
    labels = field_labels or {}
    listing = "\n".join(f"- {f}: {labels.get(f, f.replace('_', ' '))}" for f in asked_for)
    user = (
        f"Allowed number of questions: {len(asked_for)}\n\n"
        f"Missing fields (id: meaning):\n{listing}\n\n"
        f"Report:\n{report_text}"
    )
    plan = llm.complete_json(
        task="clarify",
        system=load_prompt("clarify"),
        user=user,
        schema=ClarificationPlan,
        draft_id=draft_id,
    )
    return enforce_limits(plan, asked_for)


def enforce_limits(plan: ClarificationPlan, asked_for: list[str]) -> ClarificationPlan:
    """One question per missing field, in the order asked, never more than allowed."""
    by_field = {}
    for q in plan.questions:
        if q.field in asked_for and q.field not in by_field and q.question.strip():
            by_field[q.field] = q
    ordered = [by_field[f] for f in asked_for if f in by_field]
    return ClarificationPlan(questions=ordered, wants_screenshot=plan.wants_screenshot)
