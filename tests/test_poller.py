from datetime import UTC, datetime, timedelta

import pytest

from swatter import poller, store
from swatter.config import Settings
from swatter.db import Database
from swatter.models import Draft, DraftState, IssueRecord, Subscription
from tests.fakes import Ctx, FakeGitHub, FakeLLM, FakeSlack
from tests.test_retrieval import HashEmbedder


@pytest.fixture
def ctx(base_env, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_DEFAULT_REPO", "o/web")
    monkeypatch.setenv("SWATTER_DB_PATH", str(tmp_path / "t.db"))
    settings = Settings()
    return Ctx(
        settings,
        Database(settings.swatter_db_path),
        FakeLLM(),
        HashEmbedder(),
        FakeGitHub(),
        FakeSlack(),
    )


def _rec(number, state, updated, reason=None, title="Checkout spinner"):
    return IssueRecord(
        repo="o/web",
        number=number,
        title=title,
        body="spinner",
        state=state,
        state_reason=reason,
        updated_at=updated,
        closed_at=updated if state == "closed" else None,
        html_url=f"https://github.com/o/web/issues/{number}",
    )


def test_poll_indexes_embeds_and_advances_cursor(ctx):
    t0 = datetime.now(UTC) - timedelta(hours=1)
    ctx.github.issues["o/web"] = [_rec(1, "open", t0), _rec(2, "open", t0 + timedelta(minutes=1))]
    assert poller.poll_repo(ctx, "o/web") == 2
    assert store.get_cursor(ctx.db, "o/web") == t0 + timedelta(minutes=1)
    row = ctx.db.one("SELECT embedding FROM issues WHERE number = 1")
    assert row["embedding"] is not None
    # Nothing newer: the next poll is a no-op.
    assert poller.poll_repo(ctx, "o/web") == 0


def test_close_notifies_dm_and_thread_with_wording(ctx):
    t0 = datetime.now(UTC) - timedelta(hours=1)
    ctx.github.issues["o/web"] = [_rec(1, "open", t0)]
    poller.poll_repo(ctx, "o/web")
    store.add_subscription(
        ctx.db,
        Subscription(
            repo="o/web",
            number=1,
            slack_user_id="UREP",
            channel_id="C1",
            thread_ts="1.0",
            created_at=t0,
        ),
    )
    store.add_subscription(
        ctx.db,
        Subscription(
            repo="o/web",
            number=1,
            slack_user_id="UTWO",
            channel_id="C1",
            thread_ts="1.0",
            created_at=t0,
        ),
    )
    ctx.github.issues["o/web"] = [
        _rec(1, "closed", t0 + timedelta(minutes=5), reason="not_planned")
    ]
    poller.poll_repo(ctx, "o/web")
    assert len(ctx.slack.dms) == 2 and "not planned" in ctx.slack.dms[0]["text"]
    assert len(ctx.slack.posted) == 1  # one thread reply for the shared thread
    assert "<@UREP> <@UTWO>" in ctx.slack.posted[0]["text"]

    ctx.github.issues["o/web"] = [_rec(1, "open", t0 + timedelta(minutes=9))]
    poller.poll_repo(ctx, "o/web")
    assert "reopened" in ctx.slack.posted[-1]["text"] and len(ctx.slack.dms) == 2

    ctx.github.issues["o/web"] = [_rec(1, "closed", t0 + timedelta(minutes=12), reason="completed")]
    poller.poll_repo(ctx, "o/web")
    assert "fixed" in ctx.slack.dms[-1]["text"]


def test_edit_without_state_change_is_silent(ctx):
    t0 = datetime.now(UTC) - timedelta(hours=1)
    ctx.github.issues["o/web"] = [_rec(1, "open", t0)]
    poller.poll_repo(ctx, "o/web")
    store.add_subscription(
        ctx.db,
        Subscription(
            repo="o/web",
            number=1,
            slack_user_id="U",
            channel_id="C1",
            thread_ts="1.0",
            created_at=t0,
        ),
    )
    ctx.github.issues["o/web"] = [_rec(1, "open", t0 + timedelta(minutes=1), title="Retitled")]
    poller.poll_repo(ctx, "o/web")
    assert ctx.slack.posted == [] and ctx.slack.dms == []
    assert store.get_issue(ctx.db, "o/web", 1).title == "Retitled"


def test_expired_clarification_is_finished(ctx, monkeypatch):
    now = datetime.now(UTC)
    draft = Draft(
        id="d1",
        channel_id="C1",
        thread_ts="1.0",
        message_ts="1.0",
        reporter_id="U",
        repo="o/web",
        state=DraftState.CLARIFYING,
        fields={"title": "t"},
        clarification_deadline=now - timedelta(minutes=1),
        created_at=now,
        updated_at=now,
    )
    store.save_draft(ctx.db, draft)
    finished = []
    monkeypatch.setattr(
        "swatter.pipeline.finish_clarification",
        lambda ctx_, client, d, skipped: finished.append((d.id, skipped)),
    )
    poller.run_once(ctx)
    assert finished == [("d1", False)]
