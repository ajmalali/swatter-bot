import pytest

from swatter import store
from swatter.config import Settings
from swatter.db import Database
from swatter.slack.blocks import values_from_view
from swatter.slack.handlers import _run_command
from tests.fakes import Ctx, FakeGitHub, FakeLLM


@pytest.fixture
def ctx(base_env, monkeypatch, tmp_path):
    monkeypatch.setenv("SWATTER_DB_PATH", str(tmp_path / "t.db"))
    settings = Settings()
    return Ctx(settings, Database(settings.swatter_db_path), FakeLLM(), None, FakeGitHub())


def _cmd(text):
    return {"text": text, "channel_id": "C1", "user_id": "U1"}


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
