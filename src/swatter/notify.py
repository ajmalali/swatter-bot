"""Resolution notices: DM plus thread reply on close, thread reply on reopen.

Wording differs for state_reason completed versus not_planned. One thread reply per thread,
one DM per subscriber, however many Subscriptions point at the Issue.
"""

from __future__ import annotations

import logging

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from swatter.models import IssueRecord, Subscription

log = logging.getLogger(__name__)


def notify_closed(client: WebClient, issue: IssueRecord, subs: list[Subscription]) -> None:
    link = f"<{issue.html_url}|{issue.repo}#{issue.number} {_esc(issue.title)}>"
    if issue.state_reason == "not_planned":
        headline = f"{link} was closed as *not planned*."
        detail = "The maintainers decided not to fix this. The issue has the details."
    elif issue.state_reason == "duplicate":
        headline = f"{link} was closed as a *duplicate*."
        detail = "It is tracked under another issue; follow the link for the reference."
    else:
        headline = f"{link} has been *fixed* and closed."
        detail = "Check the issue for which release carries the fix."
    _fan_out(client, subs, f"{headline}\n{detail}", "closed")


def notify_reopened(client: WebClient, issue: IssueRecord, subs: list[Subscription]) -> None:
    link = f"<{issue.html_url}|{issue.repo}#{issue.number} {_esc(issue.title)}>"
    text = f"{link} was *reopened*. I will let you know again when it is closed."
    _post_threads(client, subs, text)


def _fan_out(client: WebClient, subs: list[Subscription], text: str, what: str) -> None:
    _post_threads(client, subs, text)
    for user_id in dict.fromkeys(s.slack_user_id for s in subs):
        try:
            channel = client.conversations_open(users=user_id)["channel"]["id"]
            client.chat_postMessage(channel=channel, text=text)
        except SlackApiError as exc:
            log.warning("could not DM %s about %s issue: %s", user_id, what, exc)


def _post_threads(client: WebClient, subs: list[Subscription], text: str) -> None:
    threads = dict.fromkeys((s.channel_id, s.thread_ts) for s in subs)
    for channel_id, thread_ts in threads:
        mentions = " ".join(
            f"<@{uid}>"
            for uid in dict.fromkeys(
                s.slack_user_id
                for s in subs
                if (s.channel_id, s.thread_ts) == (channel_id, thread_ts)
            )
        )
        try:
            client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts, text=f"{mentions} {text}"
            )
        except SlackApiError as exc:
            log.warning("could not post in %s/%s: %s", channel_id, thread_ts, exc)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
