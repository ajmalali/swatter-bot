"""Everything that talks to GitHub: auth, Issues, labels, templates, and the assets branch.

TODO(scaffold): implement. Signatures are the contract the rest of the code is written against.
"""

from __future__ import annotations

from datetime import datetime

from github import Auth, Github

from swatter.config import Settings
from swatter.models import IssueRecord


def build_client(settings: Settings) -> Github:
    """PAT or GitHub App; the caller never knows which."""
    if settings.github_token:
        return Github(auth=Auth.Token(settings.github_token))
    assert settings.github_app_private_key_path and settings.github_app_id
    assert settings.github_app_installation_id
    app_auth = Auth.AppAuth(
        settings.github_app_id, settings.github_app_private_key_path.read_text(encoding="utf-8")
    )
    return Github(auth=app_auth.get_installation_auth(settings.github_app_installation_id))


class GitHubService:
    def __init__(self, client: Github, settings: Settings) -> None:
        self._gh = client
        self._settings = settings

    def list_issues_since(self, repo: str, since: datetime | None) -> list[IssueRecord]:
        """Open and closed Issues updated since the cursor. Pull requests are filtered out."""
        raise NotImplementedError

    def list_labels(self, repo: str) -> list[str]:
        raise NotImplementedError

    def get_bug_template(self, repo: str, name: str | None) -> str | None:
        """Raw text of the repo's issue template, by name or by 'bug' in the filename."""
        raise NotImplementedError

    def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> IssueRecord:
        raise NotImplementedError

    def comment(self, repo: str, number: int, body: str) -> str:
        """Post a comment; returns its html_url."""
        raise NotImplementedError

    def reopen(self, repo: str, number: int) -> None:
        raise NotImplementedError

    def upload_attachment(self, repo: str, filename: str, data: bytes) -> str:
        """Commit a file to the assets branch and return a URL GitHub will render inline."""
        raise NotImplementedError
