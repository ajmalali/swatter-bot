"""Typed reads and writes over the SQLite tables in db.py. No business logic lives here."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import numpy as np

from swatter.db import Database
from swatter.models import Binding, Candidate, Draft, DraftState, IssueRecord, Subscription


def now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ---- Bindings -----------------------------------------------------------------------------


def bindings_for_channel(db: Database, channel_id: str) -> list[Binding]:
    rows = db.query("SELECT * FROM bindings WHERE channel_id = ? ORDER BY bound_at", (channel_id,))
    return [Binding(**{**dict(r), "bound_at": _dt(r["bound_at"])}) for r in rows]


def all_bound_repos(db: Database) -> list[str]:
    return [r["repo"] for r in db.query("SELECT DISTINCT repo FROM bindings ORDER BY repo")]


def add_binding(db: Database, channel_id: str, repo: str, template: str | None, actor: str) -> None:
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO bindings (channel_id, repo, template, bound_by, bound_at)"
            " VALUES (?,?,?,?,?) ON CONFLICT(channel_id, repo) DO UPDATE SET"
            " template=excluded.template, bound_by=excluded.bound_by, bound_at=excluded.bound_at",
            (channel_id, repo, template, actor, _iso(now())),
        )


def remove_binding(db: Database, channel_id: str, repo: str) -> bool:
    with db.tx() as conn:
        cur = conn.execute(
            "DELETE FROM bindings WHERE channel_id = ? AND repo = ?", (channel_id, repo)
        )
        return cur.rowcount > 0


# ---- Issue index --------------------------------------------------------------------------


def get_issue(db: Database, repo: str, number: int) -> IssueRecord | None:
    row = db.one("SELECT * FROM issues WHERE repo = ? AND number = ?", (repo, number))
    return _issue_from_row(row) if row else None


def upsert_issue(db: Database, issue: IssueRecord, embedding: np.ndarray | None) -> None:
    """Insert or update one row. Never INSERT OR REPLACE: the FTS triggers depend on it."""
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO issues (repo, number, title, body, state, state_reason, labels,"
            " updated_at, closed_at, html_url, embedding) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(repo, number) DO UPDATE SET title=excluded.title, body=excluded.body,"
            " state=excluded.state, state_reason=excluded.state_reason, labels=excluded.labels,"
            " updated_at=excluded.updated_at, closed_at=excluded.closed_at,"
            " html_url=excluded.html_url,"
            " embedding=COALESCE(excluded.embedding, issues.embedding)",
            (
                issue.repo,
                issue.number,
                issue.title,
                issue.body,
                issue.state,
                issue.state_reason,
                json.dumps(issue.labels),
                _iso(issue.updated_at),
                _iso(issue.closed_at),
                issue.html_url,
                embedding.astype(np.float32).tobytes() if embedding is not None else None,
            ),
        )


def delete_issue(db: Database, repo: str, number: int) -> bool:
    """Drop one row from the index. DELETE, not REPLACE, so issues_ad clears FTS with it."""
    with db.tx() as conn:
        cur = conn.execute("DELETE FROM issues WHERE repo = ? AND number = ?", (repo, number))
        return cur.rowcount > 0


def issue_needs_embedding(db: Database, issue: IssueRecord) -> bool:
    """True when the stored text differs or no vector is stored yet."""
    row = db.one(
        "SELECT title, body, embedding IS NULL AS missing FROM issues WHERE repo=? AND number=?",
        (issue.repo, issue.number),
    )
    if row is None:
        return True
    return bool(row["missing"]) or row["title"] != issue.title or row["body"] != issue.body


def searchable_issue_filter(closed_lookback_days: int) -> tuple[str, str]:
    """SQL fragment and cutoff: open Issues plus those closed within the lookback window."""
    cutoff = _iso(now() - timedelta(days=closed_lookback_days)) or ""
    return "(i.state = 'open' OR (i.closed_at IS NOT NULL AND i.closed_at >= ?))", cutoff


def _issue_from_row(row) -> IssueRecord:  # noqa: ANN001
    return IssueRecord(
        repo=row["repo"],
        number=row["number"],
        title=row["title"],
        body=row["body"],
        state=row["state"],
        state_reason=row["state_reason"],
        labels=json.loads(row["labels"] or "[]"),
        updated_at=_dt(row["updated_at"]),
        closed_at=_dt(row["closed_at"]),
        html_url=row["html_url"],
    )


def get_cursor(db: Database, repo: str) -> datetime | None:
    row = db.one("SELECT since FROM poll_cursors WHERE repo = ?", (repo,))
    return _dt(row["since"]) if row else None


def set_cursor(db: Database, repo: str, since: datetime) -> None:
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO poll_cursors (repo, since) VALUES (?, ?)"
            " ON CONFLICT(repo) DO UPDATE SET since=excluded.since",
            (repo, _iso(since)),
        )


# ---- Drafts -------------------------------------------------------------------------------


def save_draft(db: Database, draft: Draft) -> None:
    draft.updated_at = now()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO drafts (id, channel_id, thread_ts, message_ts, reporter_id, repo, state,"
            " fields, candidates, clarification_deadline, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET"
            " repo=excluded.repo, state=excluded.state, fields=excluded.fields,"
            " candidates=excluded.candidates,"
            " clarification_deadline=excluded.clarification_deadline,"
            " updated_at=excluded.updated_at",
            (
                draft.id,
                draft.channel_id,
                draft.thread_ts,
                draft.message_ts,
                draft.reporter_id,
                draft.repo,
                draft.state.value,
                json.dumps(draft.fields),
                json.dumps([c.model_dump(mode="json") for c in draft.candidates]),
                _iso(draft.clarification_deadline),
                _iso(draft.created_at),
                _iso(draft.updated_at),
            ),
        )


def get_draft(db: Database, draft_id: str) -> Draft | None:
    row = db.one("SELECT * FROM drafts WHERE id = ?", (draft_id,))
    return _draft_from_row(row) if row else None


def draft_in_thread(db: Database, channel_id: str, thread_ts: str) -> Draft | None:
    """The most recent Draft for a thread, whatever its state."""
    row = db.one(
        "SELECT * FROM drafts WHERE channel_id = ? AND thread_ts = ?"
        " ORDER BY created_at DESC LIMIT 1",
        (channel_id, thread_ts),
    )
    return _draft_from_row(row) if row else None


def expired_clarifications(db: Database) -> list[Draft]:
    rows = db.query(
        "SELECT * FROM drafts WHERE state = ? AND clarification_deadline IS NOT NULL"
        " AND clarification_deadline <= ?",
        (DraftState.CLARIFYING.value, _iso(now())),
    )
    return [_draft_from_row(r) for r in rows]


def _draft_from_row(row) -> Draft:  # noqa: ANN001
    return Draft(
        id=row["id"],
        channel_id=row["channel_id"],
        thread_ts=row["thread_ts"],
        message_ts=row["message_ts"],
        reporter_id=row["reporter_id"],
        repo=row["repo"],
        state=DraftState(row["state"]),
        fields=json.loads(row["fields"] or "{}"),
        candidates=[Candidate.model_validate(c) for c in json.loads(row["candidates"] or "[]")],
        clarification_deadline=_dt(row["clarification_deadline"]),
        created_at=_dt(row["created_at"]),
        updated_at=_dt(row["updated_at"]),
    )


def log_clarification(db: Database, draft_id: str, field: str, question: str) -> None:
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO clarification_log (draft_id, field, question, answered, created_at)"
            " VALUES (?,?,?,0,?)",
            (draft_id, field, question, _iso(now())),
        )


def mark_clarifications_answered(db: Database, draft_id: str, answered_fields: list[str]) -> None:
    if not answered_fields:
        return
    marks = ",".join("?" for _ in answered_fields)
    with db.tx() as conn:
        conn.execute(
            f"UPDATE clarification_log SET answered = 1 WHERE draft_id = ? AND field IN ({marks})",
            (draft_id, *answered_fields),
        )


# ---- Subscriptions ------------------------------------------------------------------------


def add_subscription(db: Database, sub: Subscription) -> None:
    with db.tx() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO subscriptions (repo, number, slack_user_id, channel_id,"
            " thread_ts, created_at) VALUES (?,?,?,?,?,?)",
            (
                sub.repo,
                sub.number,
                sub.slack_user_id,
                sub.channel_id,
                sub.thread_ts,
                _iso(sub.created_at),
            ),
        )


def subscriptions_for(db: Database, repo: str, number: int) -> list[Subscription]:
    rows = db.query(
        "SELECT * FROM subscriptions WHERE repo = ? AND number = ? ORDER BY created_at",
        (repo, number),
    )
    return [Subscription(**{**dict(r), "created_at": _dt(r["created_at"])}) for r in rows]


# ---- Event de-duplication ---------------------------------------------------------------


def claim_event(db: Database, event_id: str) -> bool:
    """True the first time an event id is seen. Slack retries deliveries, so handlers check."""
    with db.tx() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO processed_events (event_id, created_at) VALUES (?, ?)",
            (event_id, _iso(now())),
        )
        return cur.rowcount > 0
