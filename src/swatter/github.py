"""Everything that talks to GitHub: auth, Issues, labels, templates, and the assets branch.

Signatures are the contract the rest of the code is written against. PyGithub does the HTTP.
"""

from __future__ import annotations

import base64
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

from github import Auth, Github, GithubException, InputGitTreeElement, UnknownObjectException
from github.Issue import Issue
from github.Repository import Repository

from swatter.config import Settings
from swatter.models import IssueRecord

log = logging.getLogger(__name__)

TEMPLATE_DIR = ".github/ISSUE_TEMPLATE"
LEGACY_TEMPLATE_PATHS = (".github/ISSUE_TEMPLATE.md", "ISSUE_TEMPLATE.md")
TEMPLATE_EXTENSIONS = (".yml", ".yaml", ".md")
MAX_ISSUES_PER_POLL = 500

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


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
        self._repos: dict[str, Repository] = {}

    # ---- reading ---------------------------------------------------------------------

    def describe_repo(self, repo: str) -> str:
        """The repo's GitHub description, the LLM's only routing hint."""
        return self._repo(repo).description or ""

    def list_issues_since(self, repo: str, since: datetime | None) -> list[IssueRecord]:
        """Issues updated since the cursor, oldest first. Pull requests are filtered out.

        With no cursor: every open Issue plus those closed within the lookback window, which
        is exactly what retrieval can use. Later polls pass the cursor and get both states.
        """
        gh_repo = self._repo(repo)
        if since is None:
            cutoff = datetime.now(UTC) - timedelta(days=self._settings.swatter_closed_lookback_days)
            pages = [
                gh_repo.get_issues(state="open", sort="updated", direction="asc"),
                gh_repo.get_issues(state="closed", since=cutoff, sort="updated", direction="asc"),
            ]
        else:
            pages = [gh_repo.get_issues(state="all", since=since, sort="updated", direction="asc")]
        records: dict[int, IssueRecord] = {}
        for page in pages:
            for issue in page:
                if issue.pull_request is not None:
                    continue
                records[issue.number] = _to_record(repo, issue)
                if len(records) >= MAX_ISSUES_PER_POLL and since is not None:
                    break
        return sorted(records.values(), key=lambda r: r.updated_at)

    def list_labels(self, repo: str) -> list[str]:
        return [label.name for label in self._repo(repo).get_labels()]

    def get_bug_template(self, repo: str, name: str | None) -> tuple[str, str] | None:
        """(filename, raw text) of the repo's issue template, by name or 'bug' in the filename."""
        gh_repo = self._repo(repo)
        try:
            entries = gh_repo.get_contents(TEMPLATE_DIR)
        except (UnknownObjectException, GithubException):
            entries = []
        if not isinstance(entries, list):
            entries = [entries]
        files = [
            e for e in entries if e.type == "file" and e.name.lower().endswith(TEMPLATE_EXTENSIONS)
        ]
        chosen = None
        if name:
            wanted = name.lower()
            chosen = next(
                (
                    e
                    for e in files
                    if e.name.lower() in (wanted, *[wanted + x for x in TEMPLATE_EXTENSIONS])
                ),
                None,
            )
            if chosen is None:
                log.warning("template %r not found in %s/%s", name, repo, TEMPLATE_DIR)
        if chosen is None:
            chosen = next((e for e in files if "bug" in e.name.lower()), None)
        if chosen is not None:
            return chosen.name, chosen.decoded_content.decode("utf-8")
        for path in LEGACY_TEMPLATE_PATHS:
            try:
                entry = gh_repo.get_contents(path)
            except (UnknownObjectException, GithubException):
                continue
            if not isinstance(entry, list):
                return entry.name, entry.decoded_content.decode("utf-8")
        return None

    # ---- writing ---------------------------------------------------------------------

    def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> IssueRecord:
        issue = self._repo(repo).create_issue(title=title, body=body, labels=labels)
        return _to_record(repo, issue)

    def comment(self, repo: str, number: int, body: str) -> str:
        """Post a comment; returns its html_url."""
        return self._repo(repo).get_issue(number).create_comment(body).html_url

    def reopen(self, repo: str, number: int) -> None:
        self._repo(repo).get_issue(number).edit(state="open")

    def upload_attachment(self, repo: str, filename: str, data: bytes) -> str:
        """Commit a file to the assets branch and return a URL GitHub will render inline."""
        gh_repo = self._repo(repo)
        branch = self._settings.swatter_assets_branch
        safe = _SAFE_FILENAME.sub("-", filename).strip("-") or "attachment"
        path = f"{datetime.now(UTC):%Y/%m}/{uuid.uuid4().hex[:8]}-{safe}"
        if self._branch_exists(gh_repo, branch):
            gh_repo.create_file(path, f"Add {safe} from Slack", data, branch=branch)
        else:
            self._create_orphan_branch(gh_repo, branch, path, data, f"Add {safe} from Slack")
        return f"{gh_repo.html_url}/blob/{branch}/{path}?raw=true"

    # ---- helpers ---------------------------------------------------------------------

    def _repo(self, full_name: str) -> Repository:
        if full_name not in self._repos:
            self._repos[full_name] = self._gh.get_repo(full_name)
        return self._repos[full_name]

    @staticmethod
    def _branch_exists(gh_repo: Repository, branch: str) -> bool:
        try:
            gh_repo.get_branch(branch)
            return True
        except GithubException:
            return False

    @staticmethod
    def _create_orphan_branch(
        gh_repo: Repository, branch: str, path: str, data: bytes, message: str
    ) -> None:
        """A commit with no parents, so the assets never mix with the code history."""
        blob = gh_repo.create_git_blob(base64.b64encode(data).decode("ascii"), "base64")
        tree = gh_repo.create_git_tree(
            [InputGitTreeElement(path=path, mode="100644", type="blob", sha=blob.sha)]
        )
        commit = gh_repo.create_git_commit(message, tree, [])
        gh_repo.create_git_ref(f"refs/heads/{branch}", commit.sha)


def _to_record(repo: str, issue: Issue) -> IssueRecord:
    return IssueRecord(
        repo=repo,
        number=issue.number,
        title=issue.title or "",
        body=issue.body or "",
        state=issue.state,
        state_reason=getattr(issue, "state_reason", None),
        labels=[label.name for label in issue.labels],
        updated_at=_aware(issue.updated_at),
        closed_at=_aware(issue.closed_at) if issue.closed_at else None,
        html_url=issue.html_url,
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
