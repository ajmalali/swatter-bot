import pytest


@pytest.fixture
def base_env(monkeypatch):
    """Minimal valid environment: Slack tokens and a PAT. Blocks any real .env file."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_test")
    monkeypatch.chdir("/")  # no .env here
