"""Slack entry points. Each one acks within Slack's 3-second window, then does the work.

Triggers:   app_mention, message shortcut `file_bug`
Command:    /swatter connect owner/repo [template] | disconnect owner/repo | list
Actions:    append:<n>, force_new, open_issue, cancel, clarify_done, clarify_skip, reopen:<n>
Events:     thread replies while a Draft is clarifying (message.channels / message.groups)

TODO(scaffold): each handler currently acks and logs. Wire them to the pipeline.
"""

from __future__ import annotations

import logging

from slack_bolt import Ack, App, BoltContext, Say

log = logging.getLogger(__name__)

ACTION_IDS = (
    "append",
    "force_new",
    "open_issue",
    "cancel",
    "clarify_done",
    "clarify_skip",
    "reopen",
)


def register_handlers(app: App, ctx) -> None:  # noqa: ANN001
    @app.event("app_mention")
    def on_mention(event: dict, say: Say) -> None:
        log.info("app_mention in %s by %s", event.get("channel"), event.get("user"))
        say(text="Swatter is scaffolded but not wired up yet.", thread_ts=_thread_ts(event))

    @app.shortcut("file_bug")
    def on_file_bug(ack: Ack, shortcut: dict) -> None:
        ack()
        log.info("file_bug shortcut on %s", shortcut.get("message", {}).get("ts"))

    @app.command("/swatter")
    def on_command(ack: Ack, command: dict) -> None:
        ack(f"`/swatter {command.get('text', '')}` received. Bindings are not implemented yet.")

    @app.action({"action_id": _any_of(ACTION_IDS)})
    def on_action(ack: Ack, action: dict, context: BoltContext) -> None:
        ack()
        log.info("action %s value=%s", action.get("action_id"), action.get("value"))

    @app.event("message")
    def on_message(event: dict) -> None:
        # Only thread replies matter, and only while a Draft in that thread is clarifying.
        if event.get("thread_ts") and event.get("subtype") is None:
            log.debug("thread reply in %s/%s", event.get("channel"), event.get("thread_ts"))


def _thread_ts(event: dict) -> str:
    return event.get("thread_ts") or event["ts"]


def _any_of(ids: tuple[str, ...]):
    import re

    return re.compile("^(" + "|".join(re.escape(i) for i in ids) + ")(:.*)?$")
