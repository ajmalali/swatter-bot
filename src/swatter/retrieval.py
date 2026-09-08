"""Find Candidates for a Draft: FTS5 keyword search plus cosine over embeddings, fused by RRF.

TODO(scaffold): implement. Returns Candidates without verdicts; the judge fills those in.
"""

from __future__ import annotations

from swatter.db import Database
from swatter.embeddings import Embedder
from swatter.models import Candidate

RRF_K = 60  # standard constant for reciprocal rank fusion
PER_METHOD_LIMIT = 10


def find_candidates(
    db: Database,
    embedder: Embedder,
    *,
    repo: str,
    query_text: str,
    closed_lookback_days: int,
    limit: int,
) -> list[Candidate]:
    raise NotImplementedError


def index_issue_text(title: str, body: str) -> str:
    """The exact text that gets embedded, so index and query stay consistent."""
    return f"{title}\n\n{body}".strip()
