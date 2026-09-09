from datetime import UTC, datetime, timedelta

import numpy as np

from swatter.db import Database
from swatter.models import IssueRecord
from swatter.retrieval import find_candidates, fts_query
from swatter.store import upsert_issue


class HashEmbedder:
    """Deterministic stand-in: one dimension per keyword, so similarity is word overlap."""

    words = ["checkout", "spinner", "export", "csv", "login", "crash"]

    def embed(self, texts):
        out = np.zeros((len(texts), len(self.words)), dtype=np.float32)
        for i, t in enumerate(texts):
            for j, w in enumerate(self.words):
                if w in t.lower():
                    out[i, j] = 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return out / norms


def _issue(repo, number, title, body, state="open", closed_days_ago=None):
    now = datetime.now(UTC)
    return IssueRecord(
        repo=repo,
        number=number,
        title=title,
        body=body,
        state=state,
        updated_at=now,
        closed_at=now - timedelta(days=closed_days_ago) if closed_days_ago is not None else None,
        html_url=f"https://github.com/{repo}/issues/{number}",
    )


def _seed(db):
    emb = HashEmbedder()
    rows = [
        _issue("o/web", 1, "Checkout spinner never stops", "payment hangs on submit"),
        _issue("o/web", 2, "CSV export empty", "export produces empty file"),
        _issue("o/api", 3, "Login crash on bad token", "500 on /login", "closed", 5),
        _issue("o/api", 4, "Old checkout bug", "checkout spinner", "closed", 90),
    ]
    for r in rows:
        upsert_issue(db, r, emb.embed([f"{r.title}\n\n{r.body}"])[0])
    return emb


def test_hybrid_search_fuses_and_spans_repos(tmp_path):
    db = Database(tmp_path / "t.db")
    emb = _seed(db)
    found = find_candidates(
        db,
        emb,
        repos=["o/web", "o/api"],
        query_text="the checkout spinner keeps spinning after I pay",
        closed_lookback_days=30,
        limit=3,
    )
    numbers = [c.number for c in found]
    assert numbers[0] == 1
    assert 4 not in numbers  # closed 90 days ago, outside the window
    assert all(c.verdict is None for c in found)
    assert found[0].retrieval_score > 0


def test_recently_closed_issue_is_a_candidate(tmp_path):
    db = Database(tmp_path / "t.db")
    emb = _seed(db)
    found = find_candidates(
        db, emb, repos=["o/api"], query_text="login crash", closed_lookback_days=30, limit=3
    )
    assert [c.number for c in found] == [3]
    assert found[0].state == "closed" and found[0].closed_at is not None


def test_repo_scope_is_respected(tmp_path):
    db = Database(tmp_path / "t.db")
    emb = _seed(db)
    found = find_candidates(
        db, emb, repos=["o/api"], query_text="checkout spinner", closed_lookback_days=30, limit=3
    )
    assert all(c.repo == "o/api" for c in found)


def test_fts_query_is_safe_and_deduplicated():
    q = fts_query('The "export" button (CSV) hangs! hangs AND crashes OR')
    assert q == '"export" OR "button" OR "csv" OR "hangs" OR "crashes"'
    assert fts_query("a the of") == ""
