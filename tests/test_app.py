from slack_bolt import App

from swatter.slack.handlers import register_handlers


def test_handlers_register_without_slack(base_env):
    """Bolt validates decorator arguments at registration time; catch mistakes here."""
    app = App(token="xoxb-test", token_verification_enabled=False, signing_secret="x")
    register_handlers(app, ctx=None)
    kinds = [type(listener).__name__ for listener in app._listeners]
    assert len(app._listeners) == 6
    assert all(k == "CustomListener" for k in kinds)
