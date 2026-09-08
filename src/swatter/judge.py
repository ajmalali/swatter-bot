"""LLM job 3: for each Candidate, is it the same bug as the Draft? Yes or no, with a reason.

TODO(scaffold): implement.
"""

from __future__ import annotations

from swatter.llm import LLMClient
from swatter.models import Candidate


def judge_candidates(
    llm: LLMClient, *, draft_fields: dict, candidates: list[Candidate], draft_id: str
) -> list[Candidate]:
    """Returns the Candidates with verdicts attached, keeping only those judged the same bug."""
    raise NotImplementedError
