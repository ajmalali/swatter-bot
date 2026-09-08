"""LLM job 2: write up to N questions for the required fields that came back empty.

Code decides *whether* to ask (missing_required) and *how many* (settings). The LLM only
writes the wording and flags whether a screenshot would help.

TODO(scaffold): implement.
"""

from __future__ import annotations

from swatter.llm import LLMClient
from swatter.models import ClarificationPlan


def plan_clarification(
    llm: LLMClient,
    *,
    report_text: str,
    missing_fields: list[str],
    max_questions: int,
    draft_id: str,
) -> ClarificationPlan:
    raise NotImplementedError
