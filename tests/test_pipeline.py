import json
from datetime import UTC, datetime, timedelta

import pytest

from swatter import pipeline, store
from swatter.config import Settings
from swatter.db import Database
from swatter.models import DraftState, IssueRecord
from tests.fakes import Ctx, FakeGitHub, FakeLLM, FakeSlack
from tests.test_retrieval import HashEmbedder

STRUCTURED_FULL = {
    "title": "Checkout spinner never stops",
    "labels": ["bug"],
    "summary": "Spinner after paying",
    "steps_to_reproduce": "Open cart, press Pay",
    "expected_behavior": "Order confirmation",
    "actual_behavior": "Spinner forever",
    "environment": "Safari",
}
STRUCTURED_THIN = {**STRUCTURED_FULL, "steps_to_reproduce": "", "expected_behavior": ""}
NOT_SAME = {"same_bug": False, "reason": "different feature"}
SAME = {"same_bug": True, "reason": "same spinner on checkout"}

THREAD = [
    {"ts": "1.0", "user": "UREP", "text": "<@UBOT> checkout spinner never stops after I press Pay"},
    {"ts": "1.1", "user": "UOTHER", "text": "same here on Safari"},
]


@pytest.fixture
def ctx(base_env, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_DEFAULT_REPO", "o/web")
    monkeypatch.setenv("SWATTER_DB_PATH", str(tmp_path / "t.db"))
    settings = Settings()
    db = Database(settings.swatter_db_path)
    return Ctx(settings, db, FakeLLM(), HashEmbedder(), FakeGitHub())


def _seed_issue(ctx, repo, number, title, body, state="open"):
    rec = IssueRecord(
        repo=repo,
        number=number,
        title=title,
        body=body,
        state=state,
        updated_at=datetime.now(UTC),
        closed_at=datetime.now(UTC) if state == "closed" else None,
        html_url=f"https://github.com/{repo}/issues/{number}",
    )
    store.upsert_issue(ctx.db, rec, ctx.embedder.embed([f"{title}\n\n{body}"])[0])
    return rec


def _start(ctx, slack):
    return pipeline.start_draft(
        ctx, slack, channel_id="C1", thread_ts="1.0", message_ts="1.0", actor_id="UREP"
    )


def test_no_binding_and_no_default_posts_help(ctx, monkeypatch):
    ctx.settings = ctx.settings.model_copy(update={"github_default_repo": "owner/repo"})
    slack = FakeSlack(THREAD)
    assert _start(ctx, slack) is None
    assert "/swatter connect" in slack.posted[-1]["text"]


def test_complete_report_goes_straight_to_confirm(ctx):
    ctx.llm = FakeLLM(STRUCTURED_FULL)  # structure only: nothing indexed, so no judge calls
    slack = FakeSlack(THREAD)
    draft = _start(ctx, slack)
    assert draft.state == DraftState.AWAITING_CONFIRM
    assert draft.repo == "o/web"
    assert draft.reporter_id == "UREP"
    assert slack.updated[-1]["text"] == "Ready to file"
    ids = [b["action_id"] for b in slack.last_blocks[-1]["elements"]]
    assert ids == ["open_issue", "cancel"]
    # The bot mention is stripped and the thread reply is passed as context.
    user_prompt = ctx.llm.calls[0]["user"]
    assert "<@UBOT>" not in user_prompt and "same here on Safari" in user_prompt


def test_duplicate_found_offers_append(ctx):
    _seed_issue(ctx, "o/web", 7, "Checkout spinner stuck", "spinner after pay on checkout")
    ctx.llm = FakeLLM(STRUCTURED_FULL, SAME)
    slack = FakeSlack(THREAD)
    draft = _start(ctx, slack)
    assert draft.state == DraftState.AWAITING_CHOICE
    assert [c.number for c in draft.candidates] == [7]
    blocks = slack.last_blocks
    assert blocks[1]["accessory"]["action_id"] == "append:o/web#7"
    assert json.loads(blocks[1]["accessory"]["value"]) == {
        "draft_id": draft.id,
        "repo": "o/web",
        "number": 7,
    }
    assert [b["action_id"] for b in blocks[-1]["elements"]] == ["force_new", "cancel"]


def test_missing_required_asks_then_finishes(ctx):
    ctx.llm = FakeLLM(
        STRUCTURED_THIN,
        {
            "questions": [
                {"field": "steps_to_reproduce", "question": "What did you click?"},
                {"field": "expected_behavior", "question": "What should have happened?"},
            ],
            "wants_screenshot": True,
        },
        STRUCTURED_FULL,  # re-structure after Done
    )
    slack = FakeSlack(THREAD)
    draft = _start(ctx, slack)
    assert draft.state == DraftState.CLARIFYING
    assert draft.clarification_deadline > datetime.now(UTC) + timedelta(minutes=25)
    text = slack.last_blocks[0]["text"]["text"]
    assert "1. What did you click?" in text and "screenshot" in text
    assert "press *Done*" in text and "in 30 minutes anyway" in text
    logged = ctx.db.query("SELECT field, answered FROM clarification_log ORDER BY id")
    assert [(r["field"], r["answered"]) for r in logged] == [
        ("steps_to_reproduce", 0),
        ("expected_behavior", 0),
    ]

    slack.messages.append(
        {"ts": "1.2", "user": "UREP", "text": "I clicked Pay; expected a receipt"}
    )
    pipeline.finish_clarification(ctx, slack, draft, skipped=False)
    assert draft.state == DraftState.AWAITING_CONFIRM
    assert draft.clarification_deadline is None
    logged = ctx.db.query("SELECT field, answered FROM clarification_log ORDER BY id")
    assert all(r["answered"] == 1 for r in logged)
    assert "I clicked Pay" in ctx.llm.calls[-1]["user"]


def test_skip_never_clarifies_twice(ctx):
    ctx.llm = FakeLLM(
        STRUCTURED_THIN,
        {
            "questions": [{"field": "steps_to_reproduce", "question": "How?"}],
            "wants_screenshot": False,
        },
    )
    slack = FakeSlack(THREAD)
    draft = _start(ctx, slack)
    pipeline.finish_clarification(ctx, slack, draft, skipped=True)
    assert draft.state == DraftState.AWAITING_CONFIRM
    assert len(ctx.llm.calls) == 2  # no second structuring, no second clarification
    unanswered = ctx.db.query("SELECT answered FROM clarification_log")
    assert [r["answered"] for r in unanswered] == [0]


def test_multi_binding_routes_first(ctx):
    store.add_binding(ctx.db, "C1", "o/web", None, "U1")
    store.add_binding(ctx.db, "C1", "o/api", None, "U1")
    ctx.llm = FakeLLM({"repo": "o/api", "reason": "API"}, STRUCTURED_FULL)
    slack = FakeSlack(THREAD)
    draft = _start(ctx, slack)
    assert ctx.llm.calls[0]["task"] == "route"
    assert draft.repo == "o/api"


def test_second_trigger_in_active_thread_is_refused(ctx):
    ctx.llm = FakeLLM(STRUCTURED_FULL)
    slack = FakeSlack(THREAD)
    first = _start(ctx, slack)
    again = _start(ctx, slack)
    assert again.id == first.id
    assert "already working" in slack.posted[-1]["text"]


def test_llm_failure_cancels_and_apologises(ctx):
    ctx.llm = FakeLLM(RuntimeError("model down"))
    slack = FakeSlack(THREAD)
    draft = _start(ctx, slack)
    assert draft.state == DraftState.CANCELLED
    assert "model down" in slack.updated[-1]["text"]


def test_file_draft_renders_uploads_subscribes_and_indexes(ctx):
    ctx.llm = FakeLLM(STRUCTURED_FULL)
    thread = [
        {
            **THREAD[0],
            "files": [
                {
                    "name": "shot.png",
                    "mimetype": "image/png",
                    "url_private_download": "https://x/shot.png",
                    "size": 10,
                }
            ],
        },
        THREAD[1],
    ]
    slack = FakeSlack(thread)
    draft = _start(ctx, slack)
    pipeline._download = lambda ctx_, att: b"png"  # no network in tests
    pipeline.file_draft(
        ctx,
        slack,
        draft,
        {"title": "Edited title", "labels": ["ui", "bogus"], "environment": "Safari 17"},
        actor_id="UCONF",
    )
    assert draft.state == DraftState.FILED
    created = ctx.github.created[0]
    assert created["title"] == "Edited title"
    assert created["labels"] == ["ui"]
    body = created["body"]
    assert "## Steps to reproduce\n\nOpen cart, press Pay" in body
    assert "Safari 17" in body
    assert "![shot.png](https://github.com/o/web/blob/swatter-assets/shot.png?raw=true)" in body
    assert "> checkout spinner never stops after I press Pay" in body
    assert "> @UOTHER: same here on Safari" in body
    assert "Reported by **Name of UREP** in #bugs" in body
    assert "[View in Slack](https://slack.example/C1/1.0)" in body
    subs = store.subscriptions_for(ctx.db, "o/web", 101)
    assert [s.slack_user_id for s in subs] == ["UREP"]
    assert store.get_issue(ctx.db, "o/web", 101).title == "Edited title"
    assert "o/web#101" in slack.posted[-1]["text"]


def test_append_to_closed_candidate_reopens(ctx):
    _seed_issue(ctx, "o/web", 7, "Checkout spinner stuck", "spinner after pay", state="closed")
    ctx.llm = FakeLLM(STRUCTURED_FULL, SAME)
    slack = FakeSlack(THREAD)
    draft = _start(ctx, slack)
    assert slack.last_blocks[1]["accessory"]["action_id"] == "reopen:o/web#7"
    pipeline.append_draft(ctx, slack, draft, repo="o/web", number=7, actor_id="UX")
    assert ctx.github.reopened == [("o/web", 7)]
    comment = ctx.github.comments[0]["body"]
    assert "> checkout spinner never stops" in comment and "Name of UREP" in comment
    assert draft.state == DraftState.APPENDED
    assert store.subscriptions_for(ctx.db, "o/web", 7)[0].slack_user_id == "UREP"
    assert "Reopened and added" in slack.posted[-1]["text"]


def test_handled_draft_cannot_be_filed_again(ctx):
    ctx.llm = FakeLLM(STRUCTURED_FULL)
    slack = FakeSlack(THREAD)
    draft = _start(ctx, slack)
    pipeline.cancel_draft(ctx, draft)
    with pytest.raises(pipeline.PipelineError):
        pipeline.file_draft(ctx, slack, draft, {}, actor_id="U")
    assert ctx.github.created == []


def test_clean_text():
    assert pipeline.clean_text(
        "<@UBOT> see <https://a.b|the page> in <#C1|bugs> &amp; <@U2>", "UBOT"
    ) == ("see the page (https://a.b) in #bugs & @U2")
