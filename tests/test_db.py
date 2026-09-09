from swatter.db import Database


def _insert(db, number, title, body):
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO issues (repo, number, title, body, state, updated_at)"
            " VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(repo, number) DO UPDATE SET title=excluded.title, body=excluded.body,"
            " updated_at=excluded.updated_at",
            ("o/r", number, title, body, "open", "2026-01-01T00:00:00Z"),
        )


def test_schema_creates_and_fts_tracks_updates(tmp_path):
    db = Database(tmp_path / "t.db")
    _insert(db, 1, "Payment submit hangs indefinitely", "spinner never stops on checkout")
    hits = db.query("SELECT rowid FROM issues_fts WHERE issues_fts MATCH 'checkout'")
    assert len(hits) == 1

    _insert(db, 1, "Payment submit hangs", "export button hangs")
    assert db.query("SELECT rowid FROM issues_fts WHERE issues_fts MATCH 'checkout'") == []
    assert len(db.query("SELECT rowid FROM issues_fts WHERE issues_fts MATCH 'export'")) == 1


def test_reopening_same_file_keeps_data(tmp_path):
    path = tmp_path / "t.db"
    _insert(Database(path), 7, "t", "b")
    again = Database(path)
    assert again.one("SELECT number FROM issues")["number"] == 7


def test_old_single_binding_schema_is_migrated(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE bindings (channel_id TEXT PRIMARY KEY, repo TEXT NOT NULL, template TEXT,"
        " bound_by TEXT NOT NULL, bound_at TEXT NOT NULL);"
        " INSERT INTO bindings VALUES ('C1', 'o/web', NULL, 'U1', '2026-01-01T00:00:00Z');"
    )
    old.commit()
    old.close()

    from swatter.store import add_binding, bindings_for_channel

    db = Database(path)
    add_binding(db, "C1", "o/api", None, "U2")
    assert [b.repo for b in bindings_for_channel(db, "C1")] == ["o/web", "o/api"]
    # Idempotent: reopening does not lose rows.
    assert len(bindings_for_channel(Database(path), "C1")) == 2
