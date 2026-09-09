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
        sys.exit(_sync(settings, args.repo))
    elif args.command == "eval":
        sys.exit(_eval(settings))


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


def _context(settings, with_slack: bool = False):  # noqa: ANN001
    import logging

    from swatter.db import Database
    from swatter.slack.app import AppContext, build_services, quiet_http_logs

    logging.basicConfig(
        level=settings.swatter_log_level, format="%(levelname)s %(name)s: %(message)s"
    )
    quiet_http_logs()
    ctx = build_services(AppContext(settings=settings, db=Database(settings.swatter_db_path)))
    if not with_slack:
        ctx.slack = None  # a sync never sends notifications
    return ctx


def _sync(settings, repo: str | None) -> int:  # noqa: ANN001
    from swatter import poller, store

    ctx = _context(settings)
    repos = [repo] if repo else poller._repos(ctx)
    if not repos:
        print("No bound repos. Run /swatter connect in Slack or set GITHUB_DEFAULT_REPO.")
        return 1
    for name in repos:
        n = poller.poll_repo(ctx, name, full=True)
        total = ctx.db.one("SELECT COUNT(*) AS n FROM issues WHERE repo = ?", (name,))["n"]
        cursor = store.get_cursor(ctx.db, name)
        print(
            f"{name}: fetched {n}, index holds {total}, "
            f"cursor {cursor.isoformat() if cursor else 'none'}"
        )
    return 0


def _eval(settings) -> int:  # noqa: ANN001
    """Replay eval/golden.jsonl through structuring, retrieval, and the judge."""
    import json
    from pathlib import Path

    from swatter.judge import judge_candidates
    from swatter.retrieval import find_candidates
    from swatter.structuring import structure_report
    from swatter.templates import default_template

    path = Path("eval/golden.jsonl")
    if not path.exists():
        print(f"{path} not found. See eval/README.md and eval/golden.example.jsonl.")
        return 1
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    ctx = _context(settings)
    template = default_template()
    tp = fp = fn = 0
    for i, row in enumerate(rows, start=1):
        repo = row["repo"]
        labels = sorted(
            {
                label
                for r in ctx.db.query("SELECT labels FROM issues WHERE repo = ?", (repo,))
                for label in json.loads(r["labels"] or "[]")
            }
        )
        draft_id = f"eval-{i}"
        fields = structure_report(
            ctx.llm,
            template=template,
            labels=labels,
            report_text=row["report"],
            thread_text=row.get("thread", ""),
            draft_id=draft_id,
        )
        query = "\n".join(
            str(v) for k, v in fields.items() if k != "labels" and isinstance(v, str) and v
        )
        found = find_candidates(
            ctx.db,
            ctx.embedder,
            repos=[repo],
            query_text=query or row["report"],
            closed_lookback_days=settings.swatter_closed_lookback_days,
            limit=settings.swatter_max_candidates,
        )
        confirmed = judge_candidates(
            ctx.llm, ctx.db, draft_fields=fields, candidates=found, draft_id=draft_id
        )
        predicted = confirmed[0].number if confirmed else None
        expected = row.get("expected")
        if predicted is not None and predicted == expected:
            tp += 1
            mark = "ok  "
        elif predicted is not None:
            fp += 1
            mark = "FP  "
            if expected is not None:
                fn += 1
        elif expected is not None:
            fn += 1
            mark = "FN  "
        else:
            mark = "ok  "
        retrieved = [c.number for c in found]
        print(f"{mark} #{i} expected={expected} predicted={predicted} retrieved={retrieved}")
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    print(
        f"\n{len(rows)} reports: precision {precision:.2f}, recall {recall:.2f}"
        f" (tp={tp} fp={fp} fn={fn})"
    )
    return 0
