"""LLM job 3: for each Candidate, is it the same bug as the Draft? Yes or no, with a reason.

One call per Candidate: the prompt stays small enough for local models and a bad reply for
one Candidate never spoils the others.
"""

from __future__ import annotations

import logging

from swatter.db import Database
from swatter.llm import LLMClient, LLMOutputError, load_prompt
from swatter.models import Candidate, Verdict

log = logging.getLogger(__name__)

ISSUE_BODY_LIMIT = 4_000


def judge_candidates(
    llm: LLMClient,
    db: Database,
    *,
    draft_fields: dict,
    candidates: list[Candidate],
    draft_id: str,
) -> list[Candidate]:
    """Returns the Candidates with verdicts attached, keeping only those judged the same bug."""
    report = describe_draft(draft_fields)
    confirmed: list[Candidate] = []
    for candidate in candidates:
        row = db.one(
            "SELECT body, labels FROM issues WHERE repo = ? AND number = ?",
            (candidate.repo, candidate.number),
        )
        body = (row["body"] if row else "")[:ISSUE_BODY_LIMIT]
        user = (
            f"New report:\n{report}\n\n"
            f"Existing issue {candidate.repo}#{candidate.number} ({candidate.state}):\n"
            f"Title: {candidate.title}\n{body or '(no body)'}"
        )
        try:
            verdict = llm.complete_json(
                task="judge",
                system=load_prompt("judge"),
                user=user,
                schema=Verdict,
                draft_id=draft_id,
            )
        except LLMOutputError:
            log.warning("judge gave no valid verdict for %s#%s", candidate.repo, candidate.number)
            continue
        candidate.verdict = verdict
        if verdict.same_bug:
            confirmed.append(candidate)
    return confirmed


def describe_draft(fields: dict) -> str:
    """Flatten the structured fields into the text the judge and the eval both use."""
    lines = []
    for key, value in fields.items():
        if key == "labels" or not value:
            continue
        text = ", ".join(value) if isinstance(value, list) else str(value)
        lines.append(f"{key}: {text}")
    return "\n".join(lines)
