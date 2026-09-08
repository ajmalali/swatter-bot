"""`swatter run | health | sync | eval`."""

from __future__ import annotations

import argparse
import sys

from swatter import __version__


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="swatter", description="Slack bug reports to GitHub.")
    parser.add_argument("--version", action="version", version=f"swatter {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="start the bot (Socket Mode plus GitHub poller)")
    health = sub.add_parser("health", help="check Slack, GitHub, and the database")
    health.add_argument("--llm", action="store_true", help="also make one tiny LLM call")
    sync = sub.add_parser("sync", help="full re-index of a bound repo's Issues")
    sync.add_argument("repo", nargs="?", help="owner/repo; defaults to every bound repo")
    sub.add_parser("eval", help="replay eval/golden.jsonl and report precision and recall")

    args = parser.parse_args(argv)
    from swatter.config import load_settings

    settings = load_settings()

    if args.command == "run":
        from swatter.slack.app import run

        run(settings)
    elif args.command == "health":
        sys.exit(_health(settings, check_llm=args.llm))
    elif args.command == "sync":
        raise SystemExit("sync is not implemented yet")
    elif args.command == "eval":
        raise SystemExit("eval is not implemented yet")


def _health(settings, check_llm: bool) -> int:  # noqa: ANN001
    from swatter.db import Database

    failures = 0

    def report(name: str, ok: bool, detail: str) -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'} {name}: {detail}")

    try:
        db = Database(settings.swatter_db_path)
        n = db.one("SELECT COUNT(*) AS n FROM bindings")
        report("database", True, f"{settings.swatter_db_path} ({n['n']} bindings)")
    except Exception as exc:  # noqa: BLE001
        report("database", False, str(exc))

    try:
        from slack_sdk import WebClient

        me = WebClient(token=settings.slack_bot_token).auth_test()
        report("slack", True, f"bot @{me['user']} in {me['team']}")
    except Exception as exc:  # noqa: BLE001
        report("slack", False, str(exc))

    try:
        from swatter.github import build_client

        gh = build_client(settings)
        limit = gh.get_rate_limit()
        core = getattr(limit, "core", None) or limit.resources.core
        mode = "GitHub App" if settings.uses_github_app else "PAT"
        report("github", True, f"{mode}, {core.remaining}/{core.limit} requests left")
    except Exception as exc:  # noqa: BLE001
        report("github", False, str(exc))

    if check_llm:
        try:
            from pydantic import BaseModel

            from swatter.llm import LLMClient

            class Pong(BaseModel):
                pong: bool

            out = LLMClient(settings, Database(settings.swatter_db_path)).complete_json(
                task="health",
                system="Reply with JSON.",
                user='Return {"pong": true}',
                schema=Pong,
            )
            report("llm", out.pong, f"{settings.llm_model} via {settings.llm_base_url}")
        except Exception as exc:  # noqa: BLE001
            report("llm", False, str(exc))

    return 1 if failures else 0
