import pytest

from swatter.llm import extract_json, load_prompt


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        'Sure! Here you go:\n```json\n{"a": 1}\n```',
        'Result: {"a": 1} hope that helps',
    ],
)
def test_extract_json_tolerates_chatter(text):
    assert extract_json(text) == '{"a": 1}'


def test_extract_json_rejects_no_object():
    with pytest.raises(ValueError):
        extract_json("nothing here")


def test_prompts_exist():
    for name in ("structure", "clarify", "judge"):
        assert "JSON" in load_prompt(name)
