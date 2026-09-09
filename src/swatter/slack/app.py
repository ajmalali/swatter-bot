"""Builds the Bolt app and runs it over Socket Mode alongside the poller thread."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from swatter import poller
from swatter.config import Settings
from swatter.db import Database
from swatter.slack.handlers import register_handlers

log = logging.getLogger(__name__)


@dataclass
class AppContext:
    """Everything a handler or the poller needs. Built once, shared read-only."""

    settings: Settings
    db: Database
    # Filled lazily by run(): llm, embedder, github. Kept as Any-typed attrs to avoid heavy imports
    # (fastembed) in contexts like `swatter health`.
    llm: object | None = None
    embedder: object | None = None
    github: object | None = None
    slack: object | None = None  # a WebClient for the poller's notifications
    bot_user_id: str | None = None


def build_app(ctx: AppContext) -> App:
    app = App(token=ctx.settings.slack_bot_token)
    register_handlers(app, ctx)
    return app


def quiet_http_logs() -> None:
    """httpx and fastembed log every request at INFO; that drowns Swatter's own lines."""
    for name in ("httpx", "httpcore", "urllib3", "fastembed", "huggingface_hub"):
        logging.getLogger(name).setLevel(logging.WARNING)


def build_services(ctx: AppContext) -> AppContext:
    """Attach the LLM, embedder, GitHub, and Slack clients. Imports fastembed lazily."""
    from slack_sdk import WebClient

    from swatter.embeddings import build_embedder
    from swatter.github import GitHubService, build_client
    from swatter.llm import LLMClient

    ctx.llm = LLMClient(ctx.settings, ctx.db)
    ctx.embedder = build_embedder(ctx.settings)
    ctx.github = GitHubService(build_client(ctx.settings), ctx.settings)
    ctx.slack = WebClient(token=ctx.settings.slack_bot_token)
    return ctx


def run(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.swatter_log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    quiet_http_logs()
    db = Database(settings.swatter_db_path)
    ctx = AppContext(settings=settings, db=db)

    build_services(ctx)

    app = build_app(ctx)
    threading.Thread(
        target=poller.loop,
        args=(ctx, settings.swatter_poll_interval_seconds),
        name="swatter-poller",
        daemon=True,
    ).start()
    log.info("Swatter starting in Socket Mode")
    SocketModeHandler(app, settings.slack_app_token).start()
