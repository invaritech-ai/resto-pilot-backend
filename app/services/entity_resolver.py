from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, Literal


_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]")

_SUPPLIER_STOPWORDS = {
    "ltd",
    "limited",
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "llc",
    "pte",
    "plc",
}


def normalize_name(text: str, *, kind: Literal["restaurant", "supplier", "generic"] = "generic") -> str:
    """
    Deterministically normalize human-entered names for matching.

    - Lowercase
    - Remove punctuation
    - Collapse whitespace
    - Remove common suffix/stopwords for suppliers
    """
    raw = (text or "").strip().lower()
    if not raw:
        return ""
    raw = _PUNCT_RE.sub(" ", raw)
    raw = _WS_RE.sub(" ", raw).strip()
    if kind == "supplier":
        tokens = [t for t in raw.split(" ") if t and t not in _SUPPLIER_STOPWORDS]
        raw = " ".join(tokens)
    return raw


def _token_jaccard(a: str, b: str) -> float:
    a_set = set(a.split())
    b_set = set(b.split())
    if not a_set or not b_set:
        return 0.0
    inter = a_set.intersection(b_set)
    union = a_set.union(b_set)
    return len(inter) / max(1, len(union))


def _sequence_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def similarity_score(query: str, candidate: str) -> float:
    """
    Deterministic similarity in [0, 1].

    Prioritizes:
    - Exact match
    - Substring containment (shortform)
    - Otherwise max(token jaccard, sequence ratio)
    """
    if not query or not candidate:
        return 0.0
    if query == candidate:
        return 1.0
    if query in candidate or candidate in query:
        # Shortform/containment match: strong but not perfect.
        return 0.95
    return max(_token_jaccard(query, candidate), _sequence_ratio(query, candidate))


@dataclass(frozen=True)
class Candidate:
    id: str
    display: str
    normalized: str


@dataclass(frozen=True)
class ScoredCandidate:
    id: str
    display: str
    score: float


@dataclass(frozen=True)
class ResolutionResult:
    status: Literal["resolved", "ambiguous", "not_found"]
    id: str | None = None
    display: str | None = None
    candidates: list[ScoredCandidate] | None = None


def resolve_name(
    *,
    kind: Literal["restaurant", "supplier", "generic"],
    query: str,
    candidates: Iterable[Candidate],
    min_score: float,
    min_gap: float,
    max_candidates: int = 5,
) -> ResolutionResult:
    """
    Resolve a query to a single candidate deterministically.

    Rules:
    - If best_score < min_score => not_found (include top suggestions if any)
    - If best_score - second_best < min_gap => ambiguous (include top N)
    - Else => resolved
    """
    q = normalize_name(query, kind=kind)
    if not q:
        return ResolutionResult(status="not_found")

    scored: list[ScoredCandidate] = []
    for c in candidates:
        score = similarity_score(q, c.normalized)
        scored.append(ScoredCandidate(id=c.id, display=c.display, score=score))

    scored.sort(key=lambda s: s.score, reverse=True)
    if not scored:
        return ResolutionResult(status="not_found")

    best = scored[0]
    second = scored[1] if len(scored) > 1 else None

    if best.score < min_score:
        suggestions = [s for s in scored[:max_candidates] if s.score >= 0.6]
        return ResolutionResult(status="not_found", candidates=suggestions or None)

    if second is not None and (best.score - second.score) < min_gap:
        return ResolutionResult(status="ambiguous", candidates=scored[:max_candidates])

    return ResolutionResult(status="resolved", id=best.id, display=best.display)

