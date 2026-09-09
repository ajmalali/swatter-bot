"""The deterministic flow for one Report, from trigger to filed Issue. See DESIGN.md.

Handlers and the poller call these functions; nothing here parses Slack payloads. Every LLM
call is one of the narrow jobs in structuring, judge, and clarify. State lives in the Draft row.
"""

from __future__ import annotations

import logging
import re
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from swatter import store
from swatter.clarify import plan_clarification
from swatter.judge import judge_candidates
from swatter.models import Draft, DraftState, Subscription
from swatter.retrieval import find_candidates, index_issue_text
from swatter.slack import blocks
from swatter.structuring import missing_required, route_report, structure_report
from swatter.templates import ParsedTemplate, default_template, parse_template, render

log = logging.getLogger(__name__)

ACTIVE_STATES = frozenset(
    {
        DraftState.STRUCTURING,
        DraftState.AWAITING_CHOICE,
        DraftState.CLARIFYING,
        DraftState.AWAITING_CONFIRM,
    }
)
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS = 6
SHORT_MENTION_CHARS = 20  # "@swatter file this" inside a thread means: the root is the Report

_USER_MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
_LINK = re.compile(r"<(https?://[^|>]+)(?:\|([^>]*))?>")
_CHANNEL = re.compile(r"<#[A-Z0-9]+\|([^>]*)>")


class PipelineError(RuntimeError):
    """A user-facing failure; the message is safe to post in the thread."""


@dataclass
class Attachment:
    name: str
    url: str
    mimetype: str
    size: int


@dataclass
class Report:
    """What a person said in Slack: the message, its thread, and any files."""

    channel_id: str
    thread_ts: str
    message_ts: str
    reporter_id: str
    text: str
    thread_text: str
    attachments: list[Attachment] = field(default_factory=list)


# ---- Slack reading ---------------------------------------------------------------------


def fetch_report(
    ctx, client: WebClient, *, channel_id: str, thread_ts: str, message_ts: str
) -> Report:  # noqa: ANN001
    messages = _thread_messages(client, channel_id, thread_ts)
    if not messages:
        raise PipelineError("I could not read that thread. Am I invited to this channel?")
    bot_id = _bot_user_id(ctx, client)
    by_ts = {m.get("ts"): m for m in messages}
    trigger = by_ts.get(message_ts) or messages[0]
    trigger_text = clean_text(trigger.get("text", ""), bot_id)
    root = messages[0]
    # A short mention inside a thread ("@swatter file this") points at the root message.
    if trigger is not root and len(trigger_text) < SHORT_MENTION_CHARS and not trigger.get("files"):
        report_msg = root
    else:
        report_msg = trigger
    report_text = clean_text(report_msg.get("text", ""), bot_id)
    others = []
    for m in messages:
        if m is report_msg or m.get("user") == bot_id or m.get("bot_id"):
            continue
        text = clean_text(m.get("text", ""), bot_id)
        if text:
            others.append(f"@{m.get('user', 'unknown')}: {text}")
    attachments = []
    for m in messages:
        for f in m.get("files") or []:
            if (f.get("mimetype") or "").startswith("image/") and f.get("url_private_download"):
                attachments.append(
                    Attachment(
                        name=f.get("name") or "screenshot.png",
                        url=f["url_private_download"],
                        mimetype=f["mimetype"],
                        size=int(f.get("size") or 0),
                    )
                )
    return Report(
        channel_id=channel_id,
        thread_ts=thread_ts,
        message_ts=report_msg.get("ts", message_ts),
        reporter_id=report_msg.get("user") or trigger.get("user") or "",
        text=report_text,
        thread_text="\n".join(others),
        attachments=attachments[:MAX_ATTACHMENTS],
    )


def clean_text(text: str, bot_user_id: str | None) -> str:
    if bot_user_id:
        text = text.replace(f"<@{bot_user_id}>", "")
    text = _LINK.sub(lambda m: f"{m.group(2)} ({m.group(1)})" if m.group(2) else m.group(1), text)
    text = _CHANNEL.sub(lambda m: f"#{m.group(1)}", text)
    text = _USER_MENTION.sub(lambda m: f"@{m.group(1)}", text)
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").strip()


# ---- Draft lifecycle ------------------------------------------------------------------


def start_draft(
    ctx, client: WebClient, *, channel_id: str, thread_ts: str, message_ts: str, actor_id: str
) -> Draft | None:  # noqa: ANN001
    """Trigger to first decision: Candidates, Clarification, or ready to confirm."""
    repos = bound_repos(ctx, channel_id)
    if not repos:
        _post(
            client,
            channel_id,
            thread_ts,
            "No repo is bound to this channel. Run `/swatter connect owner/repo` first.",
        )
        return None
    existing = store.draft_in_thread(ctx.db, channel_id, thread_ts)
    if existing and existing.state in ACTIVE_STATES:
        _post(
            client,
            channel_id,
            thread_ts,
            "I am already working on this thread; use the buttons above.",
        )
        return existing

    report = fetch_report(
        ctx, client, channel_id=channel_id, thread_ts=thread_ts, message_ts=message_ts
    )
    if not report.text and not report.attachments:
        _post(client, channel_id, thread_ts, "I could not find any report text in this thread.")
        return None
    draft = Draft(
        id=uuid.uuid4().hex[:12],
        channel_id=channel_id,
        thread_ts=thread_ts,
        message_ts=report.message_ts,
        reporter_id=report.reporter_id or actor_id,
        repo=repos[0],
        created_at=store.now(),
        updated_at=store.now(),
    )
    store.save_draft(ctx.db, draft)
    working = _post(client, channel_id, thread_ts, "Looking at this report...")
    try:
        if len(repos) > 1:
            descriptions = {r: _safe_description(ctx, r) for r in repos}
            draft.repo = route_report(
                ctx.llm,
                repos=descriptions,
                report_text=report.text,
                thread_text=report.thread_text,
                draft_id=draft.id,
            )
        template = template_for(ctx, channel_id, draft.repo)
        labels = _safe_labels(ctx, draft.repo)
        draft.fields = structure_report(
            ctx.llm,
            template=template,
            labels=labels,
            report_text=report.text,
            thread_text=report.thread_text,
            draft_id=draft.id,
        )
        draft.fields["labels"] = _merge_labels(draft.fields, template, labels)
        store.save_draft(ctx.db, draft)
        _decide(
            ctx,
            client,
            draft,
            template,
            report,
            repos,
            allow_clarification=True,
            replace_ts=working,
        )
    except Exception as exc:
        draft.state = DraftState.CANCELLED
        store.save_draft(ctx.db, draft)
        log.exception("draft %s failed", draft.id)
        _update(
            client, channel_id, working, f"Sorry, I could not process this report: {_short(exc)}"
        )
    return draft


def finish_clarification(ctx, client: WebClient, draft: Draft, *, skipped: bool) -> None:  # noqa: ANN001
    """Done, Skip, or timeout: re-structure with the thread's answers, then decide again."""
    if draft.state != DraftState.CLARIFYING:
        return
    repos = bound_repos(ctx, draft.channel_id) or [draft.repo]
    report = fetch_report(
        ctx,
        client,
        channel_id=draft.channel_id,
        thread_ts=draft.thread_ts,
        message_ts=draft.message_ts,
    )
    template = template_for(ctx, draft.channel_id, draft.repo)
    labels = _safe_labels(ctx, draft.repo)
    before = set(missing_required(template, draft.fields))
    if not skipped:
        draft.fields = structure_report(
            ctx.llm,
            template=template,
            labels=labels,
            report_text=report.text,
            thread_text=report.thread_text,
            draft_id=draft.id,
        )
        draft.fields["labels"] = _merge_labels(draft.fields, template, labels)
    after = set(missing_required(template, draft.fields))
    store.mark_clarifications_answered(ctx.db, draft.id, sorted(before - after))
    if after:
        log.info("draft %s: unanswered clarification for %s", draft.id, sorted(after))
    draft.clarification_deadline = None
    store.save_draft(ctx.db, draft)
    _decide(ctx, client, draft, template, report, repos, allow_clarification=False)


def open_confirm_modal(ctx, client: WebClient, draft: Draft, trigger_id: str) -> None:  # noqa: ANN001
    template = template_for(ctx, draft.channel_id, draft.repo)
    labels = _safe_labels(ctx, draft.repo)
    repos = bound_repos(ctx, draft.channel_id) or [draft.repo]
    client.views_open(trigger_id=trigger_id, view=blocks.edit_modal(draft, template, labels, repos))


def file_draft(
    ctx, client: WebClient, draft: Draft, values: dict[str, str | list[str]], actor_id: str
) -> None:  # noqa: ANN001
    """Render the Template, upload Attachments, create the Issue, subscribe, and index it."""
    if draft.state not in ACTIVE_STATES:
        raise PipelineError("This report was already handled.")
    repo = str(values.pop("repo", "") or draft.repo)
    draft.repo = repo
    template = template_for(ctx, draft.channel_id, repo)
    labels = _safe_labels(ctx, repo)
    draft.fields.update(values)
    draft.fields["labels"] = _merge_labels(draft.fields, template, labels)
    report = fetch_report(
        ctx,
        client,
        channel_id=draft.channel_id,
        thread_ts=draft.thread_ts,
        message_ts=draft.message_ts,
    )
    system_values = _system_values(ctx, client, report, repo)
    body = render(template, {**draft.fields, **system_values})
    title = str(draft.fields.get("title") or "").strip() or "Bug report from Slack"
    issue = ctx.github.create_issue(repo, title, body, list(draft.fields["labels"]))
    draft.state = DraftState.FILED
    store.save_draft(ctx.db, draft)
    _subscribe(ctx, draft, repo, issue.number)
    try:
        vec = ctx.embedder.embed([index_issue_text(issue.title, issue.body)])[0]
    except Exception:  # noqa: BLE001
        vec = None
    store.upsert_issue(ctx.db, issue, vec)
    _post(
        client,
        draft.channel_id,
        draft.thread_ts,
        blocks.filed_text(issue.html_url, repo, issue.number),
    )


def append_draft(
    ctx, client: WebClient, draft: Draft, *, repo: str, number: int, actor_id: str
) -> None:  # noqa: ANN001
    """Comment on an existing Issue with the verbatim Report; reopen it first if closed."""
    if draft.state not in ACTIVE_STATES:
        raise PipelineError("This report was already handled.")
    report = fetch_report(
        ctx,
        client,
        channel_id=draft.channel_id,
        thread_ts=draft.thread_ts,
        message_ts=draft.message_ts,
    )
    reporter, channel, permalink = _names(ctx, client, report)
    reopened = False
    indexed = store.get_issue(ctx.db, repo, number)
    if indexed and indexed.state != "open":
        ctx.github.reopen(repo, number)
        reopened = True
    url = ctx.github.comment(
        repo, number, blocks.append_comment(report.text, reporter, channel, permalink)
    )
    draft.repo = repo
    draft.state = DraftState.APPENDED
    store.save_draft(ctx.db, draft)
    _subscribe(ctx, draft, repo, number)
    _post(
        client, draft.channel_id, draft.thread_ts, blocks.appended_text(url, repo, number, reopened)
    )


def cancel_draft(ctx, draft: Draft) -> None:  # noqa: ANN001
    if draft.state in ACTIVE_STATES:
        draft.state = DraftState.CANCELLED
        store.save_draft(ctx.db, draft)


# ---- Decision point --------------------------------------------------------------------


def _decide(
    ctx,
    client: WebClient,
    draft: Draft,
    template: ParsedTemplate,
    report: Report,
    repos: list[str],
    *,
    allow_clarification: bool,
    replace_ts: str | None = None,
) -> None:  # noqa: ANN001
    query = (
        "\n".join(
            str(v) for k, v in draft.fields.items() if k != "labels" and isinstance(v, str) and v
        )
        or report.text
    )
    found = find_candidates(
        ctx.db,
        ctx.embedder,
        repos=repos,
        query_text=query,
        closed_lookback_days=ctx.settings.swatter_closed_lookback_days,
        limit=ctx.settings.swatter_max_candidates,
    )
    confirmed = judge_candidates(
        ctx.llm, ctx.db, draft_fields=draft.fields, candidates=found, draft_id=draft.id
    )
    if confirmed:
        draft.candidates = confirmed
        draft.state = DraftState.AWAITING_CHOICE
        store.save_draft(ctx.db, draft)
        _show(
            client,
            draft,
            blocks.candidates_message(draft, confirmed),
            "Possible duplicates found",
            replace_ts,
        )
        return

    missing = missing_required(template, draft.fields)
    if allow_clarification and missing:
        plan = plan_clarification(
            ctx.llm,
            report_text=report.text,
            missing_fields=missing,
            max_questions=ctx.settings.swatter_max_clarification_questions,
            draft_id=draft.id,
            field_labels={f.id: f.label for f in template.llm_fields},
        )
        if plan.questions:
            for q in plan.questions:
                store.log_clarification(ctx.db, draft.id, q.field, q.question)
            draft.state = DraftState.CLARIFYING
            draft.clarification_deadline = store.now() + timedelta(
                minutes=ctx.settings.swatter_clarification_timeout_minutes
            )
            store.save_draft(ctx.db, draft)
            _show(
                client,
                draft,
                blocks.clarification_message(draft, plan),
                "A few questions",
                replace_ts,
            )
            return

    draft.state = DraftState.AWAITING_CONFIRM
    store.save_draft(ctx.db, draft)
    _show(client, draft, blocks.confirm_message(draft), "Ready to file", replace_ts)


# ---- Lookups -----------------------------------------------------------------------------


def bound_repos(ctx, channel_id: str) -> list[str]:  # noqa: ANN001
    repos = [b.repo for b in store.bindings_for_channel(ctx.db, channel_id)]
    if not repos and ctx.settings.github_default_repo and "/" in ctx.settings.github_default_repo:
        default = ctx.settings.github_default_repo
        if default.lower() != "owner/repo":
            repos = [default]
    return repos


def template_for(ctx, channel_id: str, repo: str) -> ParsedTemplate:  # noqa: ANN001
    name = next(
        (b.template for b in store.bindings_for_channel(ctx.db, channel_id) if b.repo == repo), None
    )
    try:
        found = ctx.github.get_bug_template(repo, name)
        if found:
            return parse_template(found[1], found[0])
    except Exception:  # noqa: BLE001
        log.exception("could not load template for %s; using the default", repo)
    return default_template()


def _safe_labels(ctx, repo: str) -> list[str]:  # noqa: ANN001
    try:
        return ctx.github.list_labels(repo)
    except Exception:  # noqa: BLE001
        log.exception("could not list labels for %s", repo)
        return []


def _safe_description(ctx, repo: str) -> str:  # noqa: ANN001
    try:
        return ctx.github.describe_repo(repo)
    except Exception:  # noqa: BLE001
        log.exception("could not describe %s", repo)
        return ""


def _merge_labels(fields: dict, template: ParsedTemplate, labels: list[str]) -> list[str]:
    """LLM-chosen labels plus the template's own, restricted to labels the repo really has."""
    allowed = set(labels)
    chosen = [x for x in (fields.get("labels") or []) if x in allowed]
    return list(dict.fromkeys(chosen + [x for x in template.default_labels if x in allowed]))


def _system_values(ctx, client: WebClient, report: Report, repo: str) -> dict[str, str]:  # noqa: ANN001
    reporter, channel, permalink = _names(ctx, client, report)
    quote = "\n".join(f"> {line}" for line in report.text.splitlines()) or "> (no text)"
    if report.thread_text:
        quote += "\n>\n> Thread:\n" + "\n".join(
            f"> {line}" for line in report.thread_text.splitlines()
        )
    urls = []
    for att in report.attachments:
        try:
            data = _download(ctx, att)
            urls.append(f"![{att.name}]({ctx.github.upload_attachment(repo, att.name, data)})")
        except Exception:  # noqa: BLE001
            log.exception("could not copy attachment %s", att.name)
    return {
        "original_report": quote,
        "reporter_name": reporter,
        "channel_name": channel,
        "permalink": permalink,
        "attachments": "\n\n".join(urls),
    }


def _names(ctx, client: WebClient, report: Report) -> tuple[str, str, str]:  # noqa: ANN001
    reporter = report.reporter_id
    try:
        user = client.users_info(user=report.reporter_id)["user"]
        reporter = user.get("profile", {}).get("display_name") or user.get("real_name") or reporter
    except SlackApiError:
        pass
    channel = report.channel_id
    try:
        channel = client.conversations_info(channel=report.channel_id)["channel"].get(
            "name", channel
        )
    except SlackApiError:
        pass
    permalink = ""
    try:
        permalink = client.chat_getPermalink(
            channel=report.channel_id, message_ts=report.message_ts
        )["permalink"]
    except SlackApiError:
        pass
    return reporter, channel, permalink


def _download(ctx, att: Attachment) -> bytes:  # noqa: ANN001
    if att.size > MAX_ATTACHMENT_BYTES:
        raise PipelineError(f"{att.name} is larger than {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB")
    request = urllib.request.Request(
        att.url, headers={"Authorization": f"Bearer {ctx.settings.slack_bot_token}"}
    )
    with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310
        return resp.read(MAX_ATTACHMENT_BYTES + 1)[:MAX_ATTACHMENT_BYTES]


def _subscribe(ctx, draft: Draft, repo: str, number: int) -> None:  # noqa: ANN001
    store.add_subscription(
        ctx.db,
        Subscription(
            repo=repo,
            number=number,
            slack_user_id=draft.reporter_id,
            channel_id=draft.channel_id,
            thread_ts=draft.thread_ts,
            created_at=store.now(),
        ),
    )


def _thread_messages(client: WebClient, channel_id: str, thread_ts: str) -> list[dict]:
    messages: list[dict] = []
    cursor = None
    while True:
        resp = client.conversations_replies(
            channel=channel_id, ts=thread_ts, limit=200, cursor=cursor
        )
        messages.extend(resp.get("messages") or [])
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return messages


def _bot_user_id(ctx, client: WebClient) -> str | None:  # noqa: ANN001
    if getattr(ctx, "bot_user_id", None) is None:
        try:
            ctx.bot_user_id = client.auth_test()["user_id"]
        except SlackApiError:
            return None
    return ctx.bot_user_id


# ---- Posting -----------------------------------------------------------------------------


def _post(
    client: WebClient, channel_id: str, thread_ts: str, text: str, blocks_: list[dict] | None = None
) -> str:
    resp = client.chat_postMessage(
        channel=channel_id, thread_ts=thread_ts, text=text, blocks=blocks_
    )
    return resp.get("ts", "")


def _update(
    client: WebClient, channel_id: str, ts: str, text: str, blocks_: list[dict] | None = None
) -> None:
    try:
        client.chat_update(channel=channel_id, ts=ts, text=text, blocks=blocks_ or [])
    except SlackApiError:
        _post(client, channel_id, ts, text, blocks_)


def _show(
    client: WebClient, draft: Draft, blocks_: list[dict], fallback: str, replace_ts: str | None
) -> None:
    if replace_ts:
        _update(client, draft.channel_id, replace_ts, fallback, blocks_)
    else:
        _post(client, draft.channel_id, draft.thread_ts, fallback, blocks_)


def _short(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    return text if len(text) < 300 else text[:300] + "..."
