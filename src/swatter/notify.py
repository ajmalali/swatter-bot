"""Resolution notices: DM plus thread reply on close, thread reply on reopen.

TODO(scaffold): implement. Wording differs for state_reason completed vs not_planned.
"""

from __future__ import annotations

from slack_sdk import WebClient

from swatter.models import IssueRecord, Subscription


def notify_closed(client: WebClient, issue: IssueRecord, subs: list[Subscription]) -> None:
    raise NotImplementedError


def notify_reopened(client: WebClient, issue: IssueRecord, subs: list[Subscription]) -> None:
    raise NotImplementedError
