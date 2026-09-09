"""Test doubles shared across test modules."""

from __future__ import annotations

from collections import deque
from typing import Any

from pydantic import BaseModel


class FakeLLM:
    """Stands in for LLMClient. Queue raw dicts; each complete_json pops one and validates it."""

    def __init__(self, *replies: dict[str, Any]) -> None:
        self.replies = deque(replies)
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, *, task, system, user, schema, draft_id=None, max_attempts=2):
        self.calls.append({"task": task, "user": user, "schema": schema, "draft_id": draft_id})
        if not self.replies:
            raise AssertionError(f"FakeLLM has no reply queued for task {task}")
        reply = self.replies.popleft()
        if isinstance(reply, Exception):
            raise reply
        model: type[BaseModel] = schema
        return model.model_validate(reply)


class FakeSlack:
    """Records every call. Thread messages and user names are seeded by tests."""

    def __init__(self, messages: list[dict] | None = None, bot_user_id: str = "UBOT") -> None:
        self.messages = messages or []
        self.bot_user_id = bot_user_id
        self.posted: list[dict] = []
        self.updated: list[dict] = []
        self.dms: list[dict] = []
        self.views: list[dict] = []
        self._ts = 100

    def auth_test(self):
        return {"user_id": self.bot_user_id}

    def conversations_replies(self, channel, ts, limit=200, cursor=None):
        return {"messages": self.messages, "response_metadata": {"next_cursor": ""}}

    def chat_postMessage(self, channel, text, thread_ts=None, blocks=None):
        self._ts += 1
        entry = {
            "channel": channel,
            "thread_ts": thread_ts,
            "text": text,
            "blocks": blocks,
            "ts": str(self._ts),
        }
        if channel.startswith("D"):
            self.dms.append(entry)
        else:
            self.posted.append(entry)
        return {"ts": entry["ts"]}

    def chat_update(self, channel, ts, text, blocks=None):
        self.updated.append({"channel": channel, "ts": ts, "text": text, "blocks": blocks})
        return {"ok": True}

    def conversations_open(self, users):
        return {"channel": {"id": f"D{users}"}}

    def users_info(self, user):
        return {"user": {"real_name": f"Name of {user}", "profile": {"display_name": ""}}}

    def conversations_info(self, channel):
        return {"channel": {"name": "bugs"}}

    def chat_getPermalink(self, channel, message_ts):
        return {"permalink": f"https://slack.example/{channel}/{message_ts}"}

    def views_open(self, trigger_id, view):
        self.views.append({"trigger_id": trigger_id, "view": view})
        return {"ok": True}

    @property
    def last_blocks(self):
        source = self.updated[-1] if self.updated else self.posted[-1]
        return source["blocks"] or []


class FakeGitHub:
    """In-memory GitHub with just enough behaviour for the pipeline and the poller."""

    def __init__(self, labels=("bug", "ui"), template=None, description="The web app") -> None:
        self.labels = list(labels)
        self.template = template  # (filename, text) or None
        self.description = description
        self.created: list[dict] = []
        self.comments: list[dict] = []
        self.reopened: list[tuple[str, int]] = []
        self.uploads: list[tuple[str, str]] = []
        self.issues: dict[str, list] = {}  # repo -> IssueRecords returned by list_issues_since
        self.deleted: set[tuple[str, int]] = set()  # gone from GitHub; issue_exists says so
        self.exists_error: Exception | None = None
        self.next_number = 100

    def describe_repo(self, repo):
        return self.description

    def list_labels(self, repo):
        return self.labels

    def get_bug_template(self, repo, name):
        return self.template

    def create_issue(self, repo, title, body, labels):
        from datetime import UTC, datetime

        from swatter.models import IssueRecord

        self.next_number += 1
        self.created.append({"repo": repo, "title": title, "body": body, "labels": labels})
        return IssueRecord(
            repo=repo,
            number=self.next_number,
            title=title,
            body=body,
            state="open",
            labels=labels,
            updated_at=datetime.now(UTC),
            html_url=f"https://github.com/{repo}/issues/{self.next_number}",
        )

    def comment(self, repo, number, body):
        self.comments.append({"repo": repo, "number": number, "body": body})
        return f"https://github.com/{repo}/issues/{number}#issuecomment-1"

    def reopen(self, repo, number):
        self.reopened.append((repo, number))

    def upload_attachment(self, repo, filename, data):
        self.uploads.append((repo, filename))
        return f"https://github.com/{repo}/blob/swatter-assets/{filename}?raw=true"

    def list_issues_since(self, repo, since):
        return [
            i
            for i in self.issues.get(repo, [])
            if (repo, i.number) not in self.deleted and (since is None or i.updated_at > since)
        ]

    def issue_exists(self, repo, number):
        if self.exists_error is not None:
            raise self.exists_error
        return (repo, number) not in self.deleted


class Ctx:
    """A stand-in for slack.app.AppContext."""

    def __init__(self, settings, db, llm, embedder, github, slack=None) -> None:
        self.settings = settings
        self.db = db
        self.llm = llm
        self.embedder = embedder
        self.github = github
        self.slack = slack
        self.bot_user_id = None
