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


def build_app(ctx: AppContext) -> App:
    app = App(token=ctx.settings.slack_bot_token)
    register_handlers(app, ctx)
    return app


def run(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.swatter_log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db = Database(settings.swatter_db_path)
    ctx = AppContext(settings=settings, db=db)

    from swatter.embeddings import build_embedder
    from swatter.github import GitHubService, build_client
    from swatter.llm import LLMClient

    ctx.llm = LLMClient(settings, db)
    ctx.embedder = build_embedder(settings)
    ctx.github = GitHubService(build_client(settings), settings)

    app = build_app(ctx)
    threading.Thread(
        target=poller.loop,
        args=(ctx, settings.swatter_poll_interval_seconds),
        name="swatter-poller",
        daemon=True,
    ).start()
    log.info("Swatter starting in Socket Mode")
    SocketModeHandler(app, settings.slack_app_token).start()
