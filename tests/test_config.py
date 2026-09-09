import pytest

from swatter.config import Settings


def test_pat_is_enough(base_env):
    s = Settings()
    assert s.github_token == "github_pat_test"
    assert not s.uses_github_app
    assert s.swatter_poll_interval_seconds == 120


def test_github_app_needs_all_three(base_env, monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_TOKEN")
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    with pytest.raises(ValueError, match="GITHUB_APP_ID"):
        Settings()
    key = tmp_path / "key.pem"
    key.write_text("x")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", str(key))
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "2")
    assert Settings().uses_github_app


def test_endpoint_embeddings_need_url(base_env, monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "endpoint")
    with pytest.raises(ValueError, match="EMBEDDING_BASE_URL"):
        Settings()


def test_extra_headers_parse_from_json(base_env, monkeypatch):
    monkeypatch.setenv("LLM_EXTRA_HEADERS", '{"anthropic-workspace-id": "wrkspc_1"}')
    assert Settings().llm_extra_headers == {"anthropic-workspace-id": "wrkspc_1"}
