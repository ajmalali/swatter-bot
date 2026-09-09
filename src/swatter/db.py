"""One SQLite file holds everything: Bindings, Subscriptions, Drafts, the Issue index, and LLM logs.

The Issue index is searchable two ways: an FTS5 table kept in sync by triggers (keyword), and an
embedding BLOB per row (semantic). See ADR 0003.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
-- A channel may bind several repos; the LLM then chooses among them (see DESIGN.md step 2).
CREATE TABLE IF NOT EXISTS bindings (
    channel_id  TEXT NOT NULL,
    repo        TEXT NOT NULL,
    template    TEXT,
    bound_by    TEXT NOT NULL,
    bound_at    TEXT NOT NULL,
    PRIMARY KEY (channel_id, repo)
);

CREATE TABLE IF NOT EXISTS issues (
    repo         TEXT NOT NULL,
    number       INTEGER NOT NULL,
    title        TEXT NOT NULL,
    body         TEXT NOT NULL DEFAULT '',
    state        TEXT NOT NULL,
    state_reason TEXT,
    labels       TEXT NOT NULL DEFAULT '[]',
    updated_at   TEXT NOT NULL,
    closed_at    TEXT,
    html_url     TEXT NOT NULL DEFAULT '',
    embedding    BLOB,
    PRIMARY KEY (repo, number)
);
CREATE INDEX IF NOT EXISTS issues_repo_state ON issues (repo, state, closed_at);

CREATE VIRTUAL TABLE IF NOT EXISTS issues_fts USING fts5(
    title, body, content='issues', content_rowid='rowid'
);
-- Keep FTS in sync. Writers must use INSERT ... ON CONFLICT DO UPDATE, never INSERT OR REPLACE,
-- because REPLACE deletes without firing the delete trigger.
CREATE TRIGGER IF NOT EXISTS issues_ai AFTER INSERT ON issues BEGIN
    INSERT INTO issues_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body);
END;
CREATE TRIGGER IF NOT EXISTS issues_ad AFTER DELETE ON issues BEGIN
    INSERT INTO issues_fts(issues_fts, rowid, title, body)
        VALUES ('delete', old.rowid, old.title, old.body);
END;
CREATE TRIGGER IF NOT EXISTS issues_au AFTER UPDATE ON issues BEGIN
    INSERT INTO issues_fts(issues_fts, rowid, title, body)
        VALUES ('delete', old.rowid, old.title, old.body);
    INSERT INTO issues_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body);
END;

CREATE TABLE IF NOT EXISTS poll_cursors (
    repo   TEXT PRIMARY KEY,
    since  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    id                     TEXT PRIMARY KEY,
    channel_id             TEXT NOT NULL,
    thread_ts              TEXT NOT NULL,
    message_ts             TEXT NOT NULL,
    reporter_id            TEXT NOT NULL,
    repo                   TEXT NOT NULL,
    state                  TEXT NOT NULL,
    fields                 TEXT NOT NULL DEFAULT '{}',
    candidates             TEXT NOT NULL DEFAULT '[]',
    clarification_deadline TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS drafts_thread ON drafts (channel_id, thread_ts);

CREATE TABLE IF NOT EXISTS subscriptions (
    repo           TEXT NOT NULL,
    number         INTEGER NOT NULL,
    slack_user_id  TEXT NOT NULL,
    channel_id     TEXT NOT NULL,
    thread_ts      TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (repo, number, slack_user_id)
);

CREATE TABLE IF NOT EXISTS llm_log (
    id          INTEGER PRIMARY KEY,
    draft_id    TEXT,
    task        TEXT NOT NULL,
    model       TEXT NOT NULL,
    attempt     INTEGER NOT NULL,
    prompt      TEXT NOT NULL,
    response    TEXT,
    valid       INTEGER NOT NULL,
    error       TEXT,
    latency_ms  INTEGER,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clarification_log (
    id          INTEGER PRIMARY KEY,
    draft_id    TEXT NOT NULL,
    field       TEXT NOT NULL,
    question    TEXT NOT NULL,
    answered    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_events (
    event_id    TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL
);
"""


class Database:
    """Thread-safe wrapper around one SQLite connection.

    Slack handlers run on Bolt's thread pool and the poller runs on its own thread, so every
    access goes through the lock.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Bring tables created by older versions up to date. Each step is idempotent."""
        pk = [
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(bindings)").fetchall()
            if r["pk"]
        ]
        if pk != ["channel_id", "repo"]:
            # Before multi-Binding channels, channel_id alone was the primary key.
            self._conn.executescript(
                """
                CREATE TABLE bindings_new (
                    channel_id  TEXT NOT NULL,
                    repo        TEXT NOT NULL,
                    template    TEXT,
                    bound_by    TEXT NOT NULL,
                    bound_at    TEXT NOT NULL,
                    PRIMARY KEY (channel_id, repo)
                );
                INSERT OR IGNORE INTO bindings_new
                    SELECT channel_id, repo, template, bound_by, bound_at
                    FROM bindings;
                DROP TABLE bindings;
                ALTER TABLE bindings_new RENAME TO bindings;
                """
            )
            self._conn.commit()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Run a block inside the lock and one transaction."""
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
