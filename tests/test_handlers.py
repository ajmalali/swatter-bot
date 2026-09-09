import json
from datetime import UTC, datetime

import pytest
from slack_bolt import App

from swatter import store
from swatter.config import Settings
from swatter.db import Database
from swatter.models import Draft, DraftState
from swatter.slack.blocks import values_from_view
from swatter.slack.handlers import _run_command, register_handlers
from tests.fakes import Ctx, FakeGitHub, FakeLLM, FakeSlack


@pytest.fixture
def ctx(base_env, monkeypatch, tmp_path):
    monkeypatch.setenv("SWATTER_DB_PATH", str(tmp_path / "t.db"))
    settings = Settings()
    return Ctx(settings, Database(settings.swatter_db_path), FakeLLM(), None, FakeGitHub())


def _cmd(text):
    return {"text": text, "channel_id": "C1", "user_id": "U1"}


class _Respond:
    """Stands in for Bolt's Respond, which is the only way a handler edits its own message."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> None:
        self.calls.append(kwargs)


def _listener(ctx, name):
    """The registered handler by name; Bolt keeps the undecorated function on the listener."""
    app = App(token="xoxb-test", token_verification_enabled=False, signing_secret="x")
    register_handlers(app, ctx)
    return next(x.ack_function for x in app._listeners if x.ack_function.__name__ == name)


def _draft(ctx, state=DraftState.AWAITING_CHOICE):
    draft = Draft(
        id="d1",
        channel_id="C1",
        thread_ts="1.0",
        message_ts="1.0",
        reporter_id="UREP",
        repo="o/web",
        state=state,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    store.save_draft(ctx.db, draft)
    return draft


def test_connect_disconnect_list(ctx):
    assert "Run `/swatter connect" in _run_command(ctx, _cmd("list"))
    assert "Usage" in _run_command(ctx, _cmd("connect not-a-repo"))
    out = _run_command(ctx, _cmd("connect o/web"))
    assert "Connected" in out and "pick one" not in out
    out = _run_command(ctx, _cmd("connect o/api bug.yml"))
    assert "2 repos" in out
    assert [b.repo for b in store.bindings_for_channel(ctx.db, "C1")] == ["o/web", "o/api"]
    assert store.bindings_for_channel(ctx.db, "C1")[1].template == "bug.yml"
    assert "o/api" in _run_command(ctx, _cmd("list"))
    assert "Disconnected" in _run_command(ctx, _cmd("disconnect o/api"))
    assert "was not connected" in _run_command(ctx, _cmd("disconnect o/api"))


def test_connect_checks_github_access(ctx):
    def boom(repo):
        raise RuntimeError("404 Not Found")

    ctx.github.describe_repo = boom
    assert "cannot reach" in _run_command(ctx, _cmd("connect o/private"))
    assert store.bindings_for_channel(ctx.db, "C1") == []


def test_values_from_view_flattens_every_element_type():
    view = {
        "state": {
            "values": {
                "title": {"value": {"type": "plain_text_input", "value": " T "}},
                "repo": {"value": {"type": "static_select", "selected_option": {"value": "o/api"}}},
                "labels": {
                    "value": {"type": "multi_static_select", "selected_options": [{"value": "bug"}]}
                },
                "checks": {"value": {"type": "checkboxes", "selected_options": []}},
                "steps": {"value": {"type": "plain_text_input", "value": None}},
            }
        }
    }
    assert values_from_view(view) == {
        "title": "T",
        "repo": "o/api",
        "labels": ["bug"],
        "checks": [],
        "steps": "",
    }


def test_append_button_on_a_deleted_issue_keeps_the_buttons(ctx):
    _draft(ctx)
    ctx.github.deleted.add(("o/web", 7))
    respond = _Respond()
    _listener(ctx, "on_action")(
        ack=lambda: None,
        action={"action_id": "append:o/web#7", "value": json.dumps({"draft_id": "d1"})},
        body={"user": {"id": "UX"}, "message": {"blocks": []}},
        respond=respond,
        client=FakeSlack(),
    )
    # One ephemeral reply, and the original message — buttons and all — is left alone.
    assert [c.get("replace_original") for c in respond.calls] == [False]
    assert "no longer exists" in respond.calls[0]["text"]
    assert ctx.github.comments == []
    assert store.get_draft(ctx.db, "d1").state == DraftState.AWAITING_CHOICE


def test_help_explains_instead_of_filing(ctx):
    store.add_binding(ctx.db, "C1", "o/web", None, "U1")
    slack = FakeSlack()
    listener = _listener(ctx, "on_mention")
    listener(
        event={"channel": "C1", "ts": "1.0", "text": "<@UBOT> help", "user": "UREP"},
        body={"event_id": "Ev1"},
        say=None,
        client=slack,
    )
    posted = slack.posted[-1]["text"]
    assert "File as bug" in posted and "`o/web`" in posted and "/swatter connect" in posted
    assert slack.posted[-1]["thread_ts"] == "1.0"
    assert ctx.db.query("SELECT id FROM drafts") == []
    # The slash command answers with exactly the same text.
    assert _run_command(ctx, _cmd("help")) == posted
    assert _run_command(ctx, _cmd("wat")) == posted


def test_help_without_a_binding_says_how_to_connect(ctx):
    assert "run `/swatter connect owner/repo` first" in _run_command(ctx, _cmd("help"))


def test_a_report_that_starts_with_help_is_still_a_report(ctx):
    store.add_binding(ctx.db, "C1", "o/web", None, "U1")
    slack = FakeSlack([{"ts": "1.0", "user": "UREP", "text": "help, the login page 500s"}])
    listener = _listener(ctx, "on_mention")
    listener(
        event={"channel": "C1", "ts": "1.0", "text": "<@UBOT> help, the login page 500s"},
        body={"event_id": "Ev2"},
        say=None,
        client=slack,
    )
    assert ctx.db.query("SELECT id FROM drafts") != []
