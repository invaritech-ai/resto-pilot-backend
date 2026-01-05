from __future__ import annotations

import re


def _count_questions(text: str) -> int:
    return text.count("?")


def _count_sentences(text: str) -> int:
    # Approximate: split on ., !, ? (keep it simple).
    chunks = re.split(r"[.!?]+", text.strip())
    return len([c for c in chunks if c.strip()])


def is_disallowed_employee_output(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    if not normalized:
        return True

    # Claims of capability / offering menus/options.
    disallowed_phrases = [
        r"\bi can\b",
        r"\bi could\b",
        r"\bi will\b",
        r"\bi(?:'| a)m able\b",
        r"\bi don't have\b",
        r"\bi do not have\b",
        r"\bhere are\b",
        r"\boptions\b",
        r"\bchoose\b",
        r"\beither\b",
    ]
    if any(re.search(p, normalized) for p in disallowed_phrases):
        return True

    # No account/business/email identity flow in v1.
    if any(word in normalized for word in {"email", "account", "business name"}):
        return True

    # Overlong / too many questions.
    if _count_questions(text) > 1:
        return True
    if _count_sentences(text) > 2:
        return True

    return False


def enforce_employee_reply(*, text: str, fallback: str) -> str:
    cleaned = " ".join((text or "").split()).strip()
    if is_disallowed_employee_output(cleaned):
        return fallback
    return cleaned

