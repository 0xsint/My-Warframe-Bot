"""Forgiving name search shared by the market and wiki lookups."""
from __future__ import annotations

import difflib
import re
from typing import Callable, Sequence, TypeVar

T = TypeVar("T")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _rank_items(items: Sequence[T], query: str, limit: int,
                name: Callable[[T], str] = lambda i: i.name,
                slug: Callable[[T], str] = lambda i: i.slug) -> list[T]:
    """Name search: exact > prefix > substring > all words > typo tolerance."""
    q = _norm(query)
    if not q:
        return list(items[:limit])
    scored: list[tuple[float, T]] = []
    for it in items:
        n = _norm(name(it))
        if n == q or _norm(slug(it)) == q:
            score = 100.0
        elif n.startswith(q):
            score = 80 - min(len(n) - len(q), 40) / 2
        elif q in n:
            score = 60 - min(n.index(q), 30) / 2
        elif all(tok in n for tok in q.split()):
            score = 50.0
        else:
            continue
        scored.append((score, it))
    scored.sort(key=lambda s: (-s[0], len(name(s[1]))))
    results = [it for _, it in scored[:limit]]
    if not results:                              # typo tolerance
        names = {_norm(name(it)): it for it in items}
        for m in difflib.get_close_matches(q, list(names), n=limit, cutoff=0.6):
            results.append(names[m])
    return results


def norm(text: str) -> str:
    return _norm(text)


def rank_by_name(names: Sequence[str], query: str, limit: int) -> list[str]:
    """_rank_items for plain strings (e.g. wiki page titles)."""
    return _rank_items(list(names), query, limit, name=lambda s: s, slug=lambda s: s)
