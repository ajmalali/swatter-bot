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

MAX_PRUNE_CHECKS = 200  # one GitHub request each; a healthy index needs none


def run_once(ctx) -> None:  # noqa: ANN001  (AppContext lives in slack.app; avoid the cycle)
    for repo in _repos(ctx):
        try:
            poll_repo(ctx, repo)
        except Exception:  # noqa: BLE001
            log.exception("polling %s failed", repo)
    expire_clarifications(ctx)


def poll_repo(ctx, repo: str, *, full: bool = False) -> int:  # noqa: ANN001
    """Fetch and index; returns how many Issues were touched. `full` re-embeds everything."""
    return _poll(ctx, repo, full=full)[0]


def sync_repo(ctx, repo: str) -> tuple[int, int]:  # noqa: ANN001
    """`swatter sync`: a full re-index, then a reconciliation. Returns (touched, pruned)."""
    return _poll(ctx, repo, full=True)


def _poll(ctx, repo: str, *, full: bool) -> tuple[int, int]:  # noqa: ANN001
    since = None if full else store.get_cursor(ctx.db, repo)
    started = store.now()
    issues = ctx.github.list_issues_since(repo, since)
    if since is None:
        # A first fetch returns only open and recently closed Issues, so the newest updated_at
        # among them is not a safe cursor: it would make the next poll refetch every Issue
        # touched since then. The poll's own start time is.
        store.set_cursor(ctx.db, repo, started)
    if not issues:
        # Still reconcile: a repo whose every indexed Issue was deleted fetches nothing.
        return 0, (reconcile_repo(ctx, repo, set()) if full else 0)
    newest = since if since is not None else started
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
    pruned = reconcile_repo(ctx, repo, {i.number for i in issues}) if full else 0
    return len(issues), pruned


def reconcile_repo(ctx, repo: str, seen: set[int]) -> int:  # noqa: ANN001
    """Delete indexed Issues GitHub no longer has; returns how many went.

    A deletion or a transfer leaves no trace in the polling API — the Issue simply stops being
    listed — so the only way to find one is to ask about the rows the fetch did not return.
    Only rows retrieval could still surface are worth checking, and only a definite answer
    deletes: an error leaves the row alone, because a rate limit is not a deletion.
    """
    scope, cutoff = store.searchable_issue_filter(ctx.settings.swatter_closed_lookback_days)
    rows = ctx.db.query(
        f"SELECT i.number FROM issues i WHERE i.repo = ? AND {scope} ORDER BY i.number",
        (repo, cutoff),
    )
    missing = [r["number"] for r in rows if r["number"] not in seen]
    if len(missing) > MAX_PRUNE_CHECKS:
        log.warning(
            "%s: %d issues unaccounted for; checking the first %d",
            repo,
            len(missing),
            MAX_PRUNE_CHECKS,
        )
        missing = missing[:MAX_PRUNE_CHECKS]
    pruned = 0
    for number in missing:
        try:
            if ctx.github.issue_exists(repo, number):
                continue
        except Exception:  # noqa: BLE001
            log.exception("could not check whether %s#%s still exists", repo, number)
            continue
        store.delete_issue(ctx.db, repo, number)
        pruned += 1
        log.info("%s#%s is gone from GitHub; dropped from the index", repo, number)
    return pruned


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
