from datetime import UTC, datetime

from swatter.models import Draft
from swatter.slack.blocks import edit_modal, settled_message
from swatter.templates import parse_issue_form
from tests.test_llm_jobs import FORM


def test_edit_modal_matches_template_kinds():
    now = datetime.now(UTC)
    draft = Draft(
        id="d",
        channel_id="C",
        thread_ts="1",
        message_ts="1",
        reporter_id="U",
        repo="o/api",
        fields={
            "title": "T",
            "labels": ["bug"],
            "steps_to_reproduce": "1. go",
            "browser": "Firefox",
            "checks": ["Searched"],
        },
        created_at=now,
        updated_at=now,
    )
    view = edit_modal(draft, parse_issue_form(FORM), ["bug", "ui"], ["o/web", "o/api"])
    assert view["callback_id"] == "confirm_issue" and view["private_metadata"] == "d"
    by_id = {b["block_id"]: b for b in view["blocks"]}
    assert by_id["repo"]["element"]["initial_option"]["value"] == "o/api"
    assert by_id["title"]["element"]["initial_value"] == "T"
    assert by_id["labels"]["element"]["initial_options"][0]["value"] == "bug"
    assert (
        by_id["steps_to_reproduce"]["element"]["multiline"]
        and not by_id["steps_to_reproduce"]["optional"]
    )
    assert by_id["browser"]["element"]["initial_option"]["value"] == "Firefox"
    assert by_id["checks"]["element"]["type"] == "checkboxes"
    assert by_id["checks"]["optional"]


def test_settled_message_keeps_text_drops_buttons():
    original = {
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": "1. What did you click?"}},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "#7"},
                "accessory": {"type": "button"},
            },
            {"type": "actions", "elements": [{"type": "button"}]},
        ]
    }
    out = settled_message(original, "<@U1> pressed Done.")
    assert [b["type"] for b in out] == ["section", "section", "context"]
    assert "accessory" not in out[1]
    assert out[-1]["elements"][0]["text"] == "<@U1> pressed Done."
