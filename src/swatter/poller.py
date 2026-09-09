"""Every N seconds, for each bound repo: fetch Issues updated since the cursor, update the index,
re-embed changed Issues, and fire notifications for closes and reopens. Also ends Clarifications
whose timer ran out. See ADR 0001.
"""

from __future__ import annotations

import logging
import time

from swatter import notify, pipeline, store
from swatter.retrieval import index_issue_text

log = logging.getLogger(__name__)


def run_once(ctx) -> None:  # noqa: ANN001  (AppContext lives in slack.app; avoid the cycle)
    for repo in _repos(ctx):
        try:
            poll_repo(ctx, repo)
        except Exception:  # noqa: BLE001
            log.exception("polling %s failed", repo)
    expire_clarifications(ctx)


def poll_repo(ctx, repo: str, *, full: bool = False) -> int:  # noqa: ANN001
    """Fetch and index; returns how many Issues were touched. `full` re-embeds everything."""
    since = None if full else store.get_cursor(ctx.db, repo)
    issues = ctx.github.list_issues_since(repo, since)
    if not issues:
        return 0
    newest = since
    for issue in issues:
        prior = store.get_issue(ctx.db, issue.repo, issue.number)
        vector = None
        if full or store.issue_needs_embedding(ctx.db, issue):
            try:
                vector = ctx.embedder.embed([index_issue_text(issue.title, issue.body)])[0]
            except Exception:  # noqa: BLE001
                log.exception("embedding %s#%s failed", issue.repo, issue.number)
        store.upsert_issue(ctx.db, issue, vector)
        if prior is not None and ctx.slack is not None:
            _notify_transition(ctx, prior.state, issue)
        if newest is None or issue.updated_at > newest:
            newest = issue.updated_at
    if newest is not None:
        store.set_cursor(ctx.db, repo, newest)
    log.info("%s: indexed %d issue(s)", repo, len(issues))
    return len(issues)


def expire_clarifications(ctx) -> None:  # noqa: ANN001
    if ctx.slack is None:
        return
    for draft in store.expired_clarifications(ctx.db):
        log.info("draft %s: clarification timed out", draft.id)
        try:
            pipeline.finish_clarification(ctx, ctx.slack, draft, skipped=False)
        except Exception:  # noqa: BLE001
            log.exception("finishing clarification for %s failed", draft.id)
            pipeline.cancel_draft(ctx, draft)


def _notify_transition(ctx, prior_state: str, issue) -> None:  # noqa: ANN001
    if prior_state == issue.state:
        return
    subs = store.subscriptions_for(ctx.db, issue.repo, issue.number)
    if not subs:
        return
    if issue.state == "closed":
        notify.notify_closed(ctx.slack, issue, subs)
    elif issue.state == "open":
        notify.notify_reopened(ctx.slack, issue, subs)


def _repos(ctx) -> list[str]:  # noqa: ANN001
    repos = store.all_bound_repos(ctx.db)
    default = ctx.settings.github_default_repo
    if default and default.lower() != "owner/repo" and default not in repos:
        repos.append(default)
    return repos


def loop(ctx, interval_seconds: int) -> None:  # noqa: ANN001
    while True:
        try:
            run_once(ctx)
        except Exception:  # noqa: BLE001
            log.exception("poll cycle failed")
        time.sleep(interval_seconds)
