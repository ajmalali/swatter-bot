"""Block Kit builders. Pure functions: data in, blocks out. No Slack calls here.

Button values carry JSON so a click can be handled without any in-memory state.
"""

from __future__ import annotations

import json

from swatter.models import Binding, Candidate, ClarificationPlan, Draft
from swatter.templates import ParsedTemplate

CONFIRM_VIEW_ID = "confirm_issue"
MAX_SELECT_OPTIONS = 100
SLACK_TEXT_LIMIT = 2_900  # section text hard limit is 3000


def candidates_message(draft: Draft, candidates: list[Candidate]) -> list[dict]:
    many_repos = len({c.repo for c in candidates}) > 1 or any(
        c.repo != draft.repo for c in candidates
    )
    blocks: list[dict] = [
        _section(
            f"*{_esc(_title(draft))}*\nThis looks like it may already be reported. "
            "Add your report to one of these, or file it as a new issue."
        )
    ]
    for c in candidates:
        label = f"{c.repo}#{c.number}" if many_repos else f"#{c.number}"
        state = (
            "open"
            if c.state == "open"
            else f"closed {c.closed_at:%Y-%m-%d}"
            if c.closed_at
            else "closed"
        )
        reason = f"\n_{_esc(c.verdict.reason)}_" if c.verdict else ""
        text = f"<{c.html_url}|{_esc(label)} {_esc(c.title)}> ({state}){reason}"
        if c.state == "open":
            button = _button(f"Append to {label}", f"append:{c.repo}#{c.number}", _val(draft, c))
        else:
            button = _button(
                f"Reopen and append to {label}", f"reopen:{c.repo}#{c.number}", _val(draft, c)
            )
        blocks.append({**_section(text), "accessory": button})
    blocks.append(
        {
            "type": "actions",
            "elements": [
                _button("File as new issue", "force_new", _val(draft), style="primary"),
                _button("Cancel", "cancel", _val(draft)),
            ],
        }
    )
    return blocks


def clarification_message(
    draft: Draft, plan: ClarificationPlan, timeout_minutes: int
) -> list[dict]:
    lines = [f"{i}. {_esc(q.question)}" for i, q in enumerate(plan.questions, start=1)]
    if plan.wants_screenshot:
        lines.append("A screenshot would help too, if you have one. Attach it in this thread.")
    text = (
        f"*{_esc(_title(draft))}*\nBefore I file this, could anyone here answer:\n"
        + "\n".join(lines)
        + "\n\nReply in this thread. Once you have answered, press *Done* and I will read the"
        f" replies. Or keep answering: I will read everything here in {timeout_minutes} minutes"
        " anyway. *Skip* files it as it is."
    )
    return [
        _section(text),
        {
            "type": "actions",
            "elements": [
                _button("Done", "clarify_done", _val(draft), style="primary"),
                _button("Skip", "clarify_skip", _val(draft)),
            ],
        },
    ]


def confirm_message(draft: Draft) -> list[dict]:
    labels = draft.fields.get("labels") or []
    label_text = ", ".join(f"`{_esc(x)}`" for x in labels) if labels else "none"
    text = (
        f"*{_esc(_title(draft))}*\nReady to file in `{_esc(draft.repo)}` with labels {label_text}. "
        "Review the fields, then file it."
    )
    return [
        _section(text),
        {
            "type": "actions",
            "elements": [
                _button("Review and file", "open_issue", _val(draft), style="primary"),
                _button("Cancel", "cancel", _val(draft)),
            ],
        },
    ]


def edit_modal(draft: Draft, template: ParsedTemplate, labels: list[str], repos: list[str]) -> dict:
    """The confirmation modal. Block ids equal field ids so submissions map back directly."""
    blocks: list[dict] = []
    if len(repos) > 1:
        options = [_option(r) for r in repos[:MAX_SELECT_OPTIONS]]
        initial = next((o for o in options if o["value"] == draft.repo), options[0])
        blocks.append(
            _input(
                "repo",
                "Repository",
                {
                    "type": "static_select",
                    "action_id": "value",
                    "options": options,
                    "initial_option": initial,
                },
            )
        )
    blocks.append(
        _input(
            "title",
            "Title",
            {
                "type": "plain_text_input",
                "action_id": "value",
                "initial_value": _title(draft)[:150],
                "max_length": 150,
            },
        )
    )
    if labels:
        options = [_option(x) for x in labels[:MAX_SELECT_OPTIONS]]
        chosen = [o for o in options if o["value"] in (draft.fields.get("labels") or [])]
        element = {"type": "multi_static_select", "action_id": "value", "options": options}
        if chosen:
            element["initial_options"] = chosen
        blocks.append(_input("labels", "Labels", element, optional=True))
    for f in template.llm_fields:
        value = draft.fields.get(f.id)
        if f.kind == "dropdown" and f.options:
            options = [_option(o) for o in f.options[:MAX_SELECT_OPTIONS]]
            element = {"type": "static_select", "action_id": "value", "options": options}
            initial = next((o for o in options if o["value"] == value), None)
            if initial:
                element["initial_option"] = initial
            blocks.append(_input(f.id, f.label, element, optional=not f.required))
        elif f.kind == "checkboxes" and f.options:
            options = [_option(o) for o in f.options[:10]]
            element = {"type": "checkboxes", "action_id": "value", "options": options}
            chosen = [o for o in options if o["value"] in (value or [])]
            if chosen:
                element["initial_options"] = chosen
            blocks.append(_input(f.id, f.label, element, optional=not f.required))
        else:
            text = "\n".join(value) if isinstance(value, list) else (value or "")
            element = {
                "type": "plain_text_input",
                "action_id": "value",
                "multiline": f.kind == "textarea",
            }
            if text:
                element["initial_value"] = text[:3000]
            blocks.append(
                _input(f.id, f.label, element, optional=not f.required, hint=f.description)
            )
    return {
        "type": "modal",
        "callback_id": CONFIRM_VIEW_ID,
        "private_metadata": draft.id,
        "title": {"type": "plain_text", "text": "File GitHub issue"},
        "submit": {"type": "plain_text", "text": "File issue"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": blocks[:100],
    }


def settled_message(original: dict, note: str) -> list[dict]:
    """The same message with its buttons removed and a line saying who pressed what.

    Questions and Candidates stay readable in the thread after the decision.
    """
    kept: list[dict] = []
    for block in original.get("blocks") or []:
        if block.get("type") == "actions":
            continue
        kept.append({k: v for k, v in block.items() if k != "accessory"})
    kept.append({"type": "context", "elements": [{"type": "mrkdwn", "text": note[:2000]}]})
    return kept


def values_from_view(view: dict) -> dict[str, str | list[str]]:
    """Flatten a view_submission's state into {block_id: value}."""
    out: dict[str, str | list[str]] = {}
    for block_id, actions in (view.get("state", {}).get("values") or {}).items():
        element = actions.get("value") or next(iter(actions.values()), {})
        kind = element.get("type")
        if kind == "plain_text_input":
            out[block_id] = (element.get("value") or "").strip()
        elif kind == "static_select":
            out[block_id] = (element.get("selected_option") or {}).get("value", "")
        elif kind in ("multi_static_select", "checkboxes"):
            out[block_id] = [o["value"] for o in element.get("selected_options") or []]
    return out


def filed_text(url: str, repo: str, number: int) -> str:
    return f"Filed as <{url}|{repo}#{number}>. I will let you know here when it is closed."


def appended_text(url: str, repo: str, number: int, reopened: bool) -> str:
    verb = "Reopened and added" if reopened else "Added"
    return (
        f"{verb} your report to <{url}|{repo}#{number}>. "
        "I will let you know here when it is closed."
    )


def help_text(bindings: list[Binding], default_repo: str | None, max_questions: int) -> str:
    """What Swatter does and what the reader has to do, for `@swatter help` and `/swatter help`."""
    if bindings:
        where = "\n".join(
            f"- `{b.repo}`" + (f" (template `{b.template}`)" if b.template else "")
            for b in bindings
        )
    elif default_repo and default_repo.lower() != "owner/repo":
        where = f"- `{default_repo}` (the default; this channel has no Binding of its own)"
    else:
        where = "- nothing yet — run `/swatter connect owner/repo` first"
    return (
        "*I turn bug reports in this channel into GitHub issues,"
        " and tell you when they are fixed.*\n\n"
        "*Reporting a bug*\n"
        "- Describe the bug in the channel, then reply `@swatter` in its thread.\n"
        "- Or use the *File as bug* message shortcut on the message itself.\n"
        "- Either way I read the whole thread, so screenshots and replies come along.\n\n"
        "*What I do next*\n"
        "1. I look for issues that already describe it. If I find one, I offer to add your"
        " report to it instead of filing a duplicate.\n"
        f"2. If something important is missing I ask up to {max_questions} questions in one"
        " message. Answer them in the thread, then press *Done*.\n"
        "3. I show you the issue before it is filed. Every field is editable, and nothing is"
        " filed until someone presses *File issue*.\n"
        "4. When the issue closes or reopens, I say so here and send you a DM.\n\n"
        "*This channel files into*\n"
        f"{where}\n\n"
        "*Commands*\n"
        "`/swatter list` · `/swatter connect owner/repo [template]` ·"
        " `/swatter disconnect owner/repo` · `/swatter help`"
    )


def append_comment(quote: str, reporter_name: str, channel_name: str, permalink: str) -> str:
    """The templated comment Append posts on an existing Issue."""
    quoted = "\n".join(f"> {line}" for line in quote.strip().splitlines()) or "> (no text)"
    return (
        f"Another report of this, from **{reporter_name}** in #{channel_name} via Swatter:\n\n"
        f"{quoted}\n\n[View in Slack]({permalink})"
    )


# ---- small helpers ---------------------------------------------------------------------


def _title(draft: Draft) -> str:
    title = draft.fields.get("title")
    return str(title) if title else "Untitled report"


def _val(draft: Draft, candidate: Candidate | None = None) -> str:
    payload: dict = {"draft_id": draft.id}
    if candidate:
        payload["repo"] = candidate.repo
        payload["number"] = candidate.number
    return json.dumps(payload)


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:SLACK_TEXT_LIMIT]}}


def _button(text: str, action_id: str, value: str, style: str | None = None) -> dict:
    button = {
        "type": "button",
        "text": {"type": "plain_text", "text": text[:75]},
        "action_id": action_id,
        "value": value,
    }
    if style:
        button["style"] = style
    return button


def _option(value: str) -> dict:
    return {"text": {"type": "plain_text", "text": value[:75]}, "value": value[:150]}


def _input(
    block_id: str, label: str, element: dict, optional: bool = False, hint: str = ""
) -> dict:
    block = {
        "type": "input",
        "block_id": block_id,
        "label": {"type": "plain_text", "text": label[:2000]},
        "element": element,
        "optional": optional,
    }
    if hint:
        block["hint"] = {"type": "plain_text", "text": hint[:2000]}
    return block


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
