from __future__ import annotations

from app.services.entity_resolver import Candidate, resolve_name


def test_resolve_name_exact_match() -> None:
    candidates = [
        Candidate(id="1", display="Mercato Downtown", normalized="mercato downtown"),
        Candidate(id="2", display="KTM", normalized="ktm"),
    ]
    res = resolve_name(
        kind="restaurant",
        query="KTM",
        candidates=candidates,
        min_score=0.9,
        min_gap=0.1,
    )
    assert res.status == "resolved"
    assert res.id == "2"


def test_resolve_name_shortform_containment() -> None:
    candidates = [
        Candidate(id="1", display="Joyful banquet", normalized="joyful banquet"),
        Candidate(id="2", display="Mercato Downtown", normalized="mercato downtown"),
    ]
    res = resolve_name(
        kind="restaurant",
        query="joyful",
        candidates=candidates,
        min_score=0.9,
        min_gap=0.1,
    )
    assert res.status == "resolved"
    assert res.id == "1"


def test_resolve_name_ambiguous_when_close() -> None:
    candidates = [
        Candidate(id="1", display="Cheong Hing Company", normalized="cheong hing"),
        Candidate(id="2", display="Cheong Hing Foods", normalized="cheong hing foods"),
    ]
    res = resolve_name(
        kind="supplier",
        query="cheong hing",
        candidates=candidates,
        min_score=0.8,
        min_gap=0.2,
    )
    assert res.status == "ambiguous"
    assert res.candidates is not None
    assert len(res.candidates) >= 2


def test_resolve_name_not_found() -> None:
    candidates = [
        Candidate(id="1", display="Mercato Downtown", normalized="mercato downtown"),
    ]
    res = resolve_name(
        kind="restaurant",
        query="zzzz",
        candidates=candidates,
        min_score=0.9,
        min_gap=0.1,
    )
    assert res.status == "not_found"

