"""Domain objects. Names follow CONTEXT.md."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Binding(BaseModel):
    """A Slack channel may file into this repo, optionally using a named template.

    One row per (channel, repo). A channel with several rows lets the LLM choose the repo.
    """

    channel_id: str
    repo: str
    template: str | None = None
    bound_by: str
    bound_at: datetime


class IssueRecord(BaseModel):
    """One row of the Issue index for a repo."""

    repo: str
    number: int
    title: str
    body: str = ""
    state: str
    state_reason: str | None = None
    labels: list[str] = Field(default_factory=list)
    updated_at: datetime
    closed_at: datetime | None = None
    html_url: str = ""


class DraftState(StrEnum):
    STRUCTURING = "structuring"
    AWAITING_CHOICE = "awaiting_choice"  # Candidates shown, reporter choosing
    CLARIFYING = "clarifying"
    AWAITING_CONFIRM = "awaiting_confirm"
    FILED = "filed"
    APPENDED = "appended"
    CANCELLED = "cancelled"


class Draft(BaseModel):
    """The structured form of a Report, before anything is filed."""

    id: str
    channel_id: str
    thread_ts: str
    message_ts: str
    reporter_id: str
    repo: str
    state: DraftState = DraftState.STRUCTURING
    fields: dict[str, str | list[str]] = Field(default_factory=dict)
    candidates: list[Candidate] = Field(default_factory=list)
    clarification_deadline: datetime | None = None
    created_at: datetime
    updated_at: datetime


class Candidate(BaseModel):
    """An existing Issue that retrieval surfaced as a possible duplicate."""

    repo: str
    number: int
    title: str
    state: str
    closed_at: datetime | None = None
    html_url: str = ""
    retrieval_score: float
    verdict: Verdict | None = None


class Verdict(BaseModel):
    """The judge's answer for one Candidate."""

    same_bug: bool
    reason: str


class ClarificationQuestion(BaseModel):
    field: str
    question: str


class ClarificationPlan(BaseModel):
    """What the LLM proposes to ask. Code enforces the maximum count."""

    questions: list[ClarificationQuestion] = Field(default_factory=list)
    wants_screenshot: bool = False


class Subscription(BaseModel):
    """A Slack user is told when this Issue closes or reopens."""

    repo: str
    number: int
    slack_user_id: str
    channel_id: str
    thread_ts: str
    created_at: datetime
