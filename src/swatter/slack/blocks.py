"""Block Kit builders. Pure functions: data in, blocks out. No Slack calls here.

TODO(scaffold): implement.
"""

from __future__ import annotations

from swatter.models import Candidate, ClarificationPlan, Draft


def candidates_message(draft: Draft, candidates: list[Candidate]) -> list[dict]:
    raise NotImplementedError


def clarification_message(draft: Draft, plan: ClarificationPlan) -> list[dict]:
    raise NotImplementedError


def confirm_message(draft: Draft) -> list[dict]:
    raise NotImplementedError


def edit_modal(draft: Draft, labels: list[str]) -> dict:
    raise NotImplementedError
