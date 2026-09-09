"""Slack entry points. Each one acks within Slack's 3-second window, then does the work.

Triggers:   app_mention, message shortcut `file_bug`
Command:    /swatter connect owner/repo [template] | disconnect owner/repo | list
Actions:    append:<repo>#<n>, reopen:<repo>#<n>, force_new, open_issue, cancel,
            clarify_done, clarify_skip
View:       confirm_issue (the edit modal's submission)
Events:     thread replies are read back when a Clarification ends, so `message` is a no-op.
"""

from __future__ import annotations

import json
import logging
import re

from slack_bolt import Ack, App, Respond, Say
from slack_sdk import WebClient

from swatter import pipeline, store
from swatter.models import DraftState
from swatter.slack.blocks import CONFIRM_VIEW_ID, settled_message, values_from_view

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
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TARGET = re.compile(r"^(?:append|reopen):(?P<repo>[^#]+)#(?P<number>\d+)$")


def register_handlers(app: App, ctx) -> None:  # noqa: ANN001
    @app.event("app_mention")
    def on_mention(event: dict, body: dict, say: Say, client: WebClient) -> None:
        event_id = body.get("event_id") or f"mention:{event.get('channel')}:{event.get('ts')}"
        if not store.claim_event(ctx.db, event_id):
            return
        _start(
            client,
            channel_id=event["channel"],
            thread_ts=event.get("thread_ts") or event["ts"],
            message_ts=event["ts"],
            actor_id=event.get("user", ""),
        )

    @app.shortcut("file_bug")
    def on_file_bug(ack: Ack, shortcut: dict, client: WebClient) -> None:
        ack()
        message = shortcut.get("message") or {}
        channel_id = (shortcut.get("channel") or {}).get("id", "")
        if not channel_id or not message.get("ts"):
            return
        _start(
            client,
            channel_id=channel_id,
            thread_ts=message.get("thread_ts") or message["ts"],
            message_ts=message["ts"],
            actor_id=(shortcut.get("user") or {}).get("id", ""),
        )

    @app.command("/swatter")
    def on_command(ack: Ack, command: dict, respond: Respond) -> None:
        ack()
        respond(_run_command(ctx, command))

    @app.action({"action_id": _any_of(ACTION_IDS)})
    def on_action(ack: Ack, action: dict, body: dict, respond: Respond, client: WebClient) -> None:
        ack()
        action_id = action.get("action_id", "")
        try:
            payload = json.loads(action.get("value") or "{}")
        except json.JSONDecodeError:
            payload = {}
        draft = store.get_draft(ctx.db, payload.get("draft_id", ""))
        if draft is None:
            respond(text="I no longer have that report.", replace_original=False)
            return
        actor = (body.get("user") or {}).get("id", "")
        if draft.state not in pipeline.ACTIVE_STATES:
            respond(text=f"This report was already {draft.state.value}.", replace_original=False)
            return

        def settle(note: str) -> None:
            """Keep the message's text, drop its buttons, and record who decided."""
            respond(
                text=note,
                blocks=settled_message(body.get("message") or {}, note),
                replace_original=True,
            )

        try:
            if action_id == "cancel":
                pipeline.cancel_draft(ctx, draft)
                settle(f"Cancelled by <@{actor}>.")
            elif action_id == "open_issue":
                pipeline.open_confirm_modal(ctx, client, draft, body["trigger_id"])
            elif action_id == "force_new":
                settle(f"<@{actor}> chose to file a new issue.")
                draft.state = DraftState.AWAITING_CONFIRM
                store.save_draft(ctx.db, draft)
                pipeline.open_confirm_modal(ctx, client, draft, body["trigger_id"])
            elif action_id in ("clarify_done", "clarify_skip"):
                settle(f"<@{actor}> pressed {_button_name(action_id)}. One moment...")
                pipeline.finish_clarification(
                    ctx, client, draft, skipped=action_id == "clarify_skip"
                )
            else:
                target = _TARGET.match(action_id)
                if not target:
                    return
                repo, number = target.group("repo"), int(target.group("number"))
                settle(f"<@{actor}> chose to add this to {repo}#{number}.")
                pipeline.append_draft(ctx, client, draft, repo=repo, number=number, actor_id=actor)
        except pipeline.PipelineError as exc:
            respond(text=str(exc), replace_original=False)
        except Exception as exc:  # noqa: BLE001
            log.exception("action %s failed for draft %s", action_id, draft.id)
            respond(text=f"Sorry, that failed: {pipeline._short(exc)}", replace_original=False)

    @app.view(CONFIRM_VIEW_ID)
    def on_confirm(ack: Ack, view: dict, body: dict, client: WebClient) -> None:
        ack()
        draft = store.get_draft(ctx.db, view.get("private_metadata", ""))
        if draft is None:
            return
        actor = (body.get("user") or {}).get("id", "")
        try:
            pipeline.file_draft(ctx, client, draft, values_from_view(view), actor_id=actor)
        except Exception as exc:  # noqa: BLE001
            log.exception("filing draft %s failed", draft.id)
            client.chat_postMessage(
                channel=draft.channel_id,
                thread_ts=draft.thread_ts,
                text=f"Sorry, filing failed: {pipeline._short(exc)}. The buttons above still work.",
            )

    @app.event("message")
    def on_message(event: dict) -> None:
        # Thread replies are collected when a Clarification ends; nothing to do per message.
        if event.get("thread_ts") and event.get("subtype") is None:
            log.debug("thread reply in %s/%s", event.get("channel"), event.get("thread_ts"))

    def _start(client: WebClient, **kwargs) -> None:
        try:
            pipeline.start_draft(ctx, client, **kwargs)
        except pipeline.PipelineError as exc:
            client.chat_postMessage(
                channel=kwargs["channel_id"], thread_ts=kwargs["thread_ts"], text=str(exc)
            )
        except Exception:  # noqa: BLE001
            log.exception("start_draft failed in %s", kwargs.get("channel_id"))


def _run_command(ctx, command: dict) -> str:  # noqa: ANN001
    """`/swatter connect owner/repo [template] | disconnect owner/repo | list`."""
    words = (command.get("text") or "").split()
    channel_id = command.get("channel_id", "")
    actor = command.get("user_id", "")
    verb = words[0].lower() if words else "list"
    if verb == "connect" and len(words) >= 2:
        repo, template = words[1], (words[2] if len(words) > 2 else None)
        if not _REPO.match(repo):
            return "Usage: `/swatter connect owner/repo [template-filename]`"
        try:
            ctx.github.describe_repo(repo)
        except Exception as exc:  # noqa: BLE001
            return f"I cannot reach `{repo}` with my GitHub credentials: {pipeline._short(exc)}"
        store.add_binding(ctx.db, channel_id, repo, template, actor)
        log.info("%s bound %s to %s (template=%s)", actor, channel_id, repo, template)
        return (
            f"Connected this channel to `{repo}`"
            + (f" using template `{template}`." if template else ".")
            + _binding_note(ctx, channel_id)
        )
    if verb == "disconnect" and len(words) >= 2:
        if store.remove_binding(ctx.db, channel_id, words[1]):
            log.info("%s unbound %s from %s", actor, channel_id, words[1])
            return f"Disconnected `{words[1]}`." + _binding_note(ctx, channel_id)
        return f"`{words[1]}` was not connected to this channel."
    if verb == "list":
        bindings = store.bindings_for_channel(ctx.db, channel_id)
        if not bindings:
            default = ctx.settings.github_default_repo
            return (
                f"No Bindings here; reports go to the default repo `{default}`."
                if default and default.lower() != "owner/repo"
                else "No Bindings here. Run `/swatter connect owner/repo`."
            )
        lines = [
            f"- `{b.repo}`" + (f" (template `{b.template}`)" if b.template else "")
            for b in bindings
        ]
        return "This channel files into:\n" + "\n".join(lines)
    return (
        "Usage: `/swatter connect owner/repo [template]`, `/swatter disconnect owner/repo`,"
        " `/swatter list`"
    )


def _binding_note(ctx, channel_id: str) -> str:  # noqa: ANN001
    n = len(store.bindings_for_channel(ctx.db, channel_id))
    if n > 1:
        return (
            f" This channel now has {n} repos; I will pick one per report from their"
            " GitHub descriptions."
        )
    return ""


def _button_name(action_id: str) -> str:
    return "Done" if action_id == "clarify_done" else "Skip"


def _any_of(ids: tuple[str, ...]):
    return re.compile("^(" + "|".join(re.escape(i) for i in ids) + ")(:.*)?$")
