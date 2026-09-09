"""Find Candidates for a Draft: FTS5 keyword search plus cosine over embeddings, fused by RRF.

Searches every repo it is given, so a duplicate filed in a sibling repo is still caught.
Returns Candidates without verdicts; the judge fills those in. See ADR 0003.
"""

from __future__ import annotations

import re

import numpy as np

from swatter.db import Database
from swatter.embeddings import Embedder
from swatter.models import Candidate
from swatter.store import _dt, searchable_issue_filter

RRF_K = 60  # standard constant for reciprocal rank fusion
PER_METHOD_LIMIT = 10
MAX_QUERY_TERMS = 40

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,}")
_STOPWORDS = frozenset(
    """a an the and or but if then of to in on at for with by from as is are was were be been
    being it its this that these those there here i we you he she they them my our your his her
    their me us when while where which who what why how do does did done have has had having
    not no yes can could would should will just also very so than too into out up down over
    under again about after before between during through""".split()
)


def find_candidates(
    db: Database,
    embedder: Embedder,
    *,
    repos: list[str],
    query_text: str,
    closed_lookback_days: int,
    limit: int,
) -> list[Candidate]:
    if not repos or not query_text.strip():
        return []
    keyword_hits = _keyword_search(db, repos, query_text, closed_lookback_days)
    semantic_hits = _semantic_search(db, embedder, repos, query_text, closed_lookback_days)

    fused: dict[tuple[str, int], float] = {}
    for ranking in (keyword_hits, semantic_hits):
        for rank, key in enumerate(ranking, start=1):
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + rank)
    ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    if not ordered:
        return []

    candidates = []
    for (repo, number), score in ordered:
        row = db.one(
            "SELECT title, state, closed_at, html_url FROM issues WHERE repo=? AND number=?",
            (repo, number),
        )
        if row is None:
            continue
        candidates.append(
            Candidate(
                repo=repo,
                number=number,
                title=row["title"],
                state=row["state"],
                closed_at=_dt(row["closed_at"]),
                html_url=row["html_url"],
                retrieval_score=round(score, 6),
            )
        )
    return candidates


def index_issue_text(title: str, body: str) -> str:
    """The exact text that gets embedded, so index and query stay consistent."""
    return f"{title}\n\n{body}".strip()


def fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 query: quoted terms joined with OR."""
    terms: list[str] = []
    for word in _WORD.findall(text):
        lowered = word.lower().strip("._-")
        if len(lowered) < 2 or lowered in _STOPWORDS or lowered in terms:
            continue
        terms.append(lowered)
        if len(terms) >= MAX_QUERY_TERMS:
            break
    return " OR ".join(f'"{t}"' for t in terms)


def _keyword_search(
    db: Database, repos: list[str], text: str, lookback_days: int
) -> list[tuple[str, int]]:
    query = fts_query(text)
    if not query:
        return []
    scope, cutoff = searchable_issue_filter(lookback_days)
    marks = ",".join("?" for _ in repos)
    rows = db.query(
        "SELECT i.repo, i.number FROM issues_fts f JOIN issues i ON i.rowid = f.rowid"
        f" WHERE issues_fts MATCH ? AND i.repo IN ({marks}) AND {scope}"
        " ORDER BY f.rank LIMIT ?",
        (query, *repos, cutoff, PER_METHOD_LIMIT),
    )
    return [(r["repo"], r["number"]) for r in rows]


def _semantic_search(
    db: Database, embedder: Embedder, repos: list[str], text: str, lookback_days: int
) -> list[tuple[str, int]]:
    scope, cutoff = searchable_issue_filter(lookback_days)
    marks = ",".join("?" for _ in repos)
    rows = db.query(
        "SELECT i.repo, i.number, i.embedding FROM issues i"
        f" WHERE i.repo IN ({marks}) AND i.embedding IS NOT NULL AND {scope}",
        (*repos, cutoff),
    )
    if not rows:
        return []
    query_vec = embedder.embed([text])[0]
    keys: list[tuple[str, int]] = []
    vectors: list[np.ndarray] = []
    for r in rows:
        vec = np.frombuffer(r["embedding"], dtype=np.float32)
        if vec.shape[0] != query_vec.shape[0]:
            continue  # indexed under a different embedding model; `swatter sync` fixes it
        keys.append((r["repo"], r["number"]))
        vectors.append(vec)
    if not vectors:
        return []
    scores = np.stack(vectors) @ query_vec
    order = np.argsort(-scores)[:PER_METHOD_LIMIT]
    return [keys[i] for i in order]
