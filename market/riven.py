"""Riven contracts (warframe.market riven auctions): filtering and sorting.

Pure functions only: no network and no Discord code, so they are easy to test.

Stat names are matched word by word, each typed word must start a word of the
stat name. So "crit damage" finds `critical_damage`, and "base damage" finds
`base_damage_/_melee_damage`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .client import LichAuction, RivenAuction, _norm

POLARITIES = ("madurai", "vazarin", "naramon")
ELEMENTS = ("heat", "cold", "electricity", "toxin", "impact", "puncture", "slash", "radiation")
MAX_RANK = 8


@dataclass(frozen=True)
class RivenFilter:
    polarity: Optional[str] = None      # one of POLARITIES
    rank: Optional[int] = None          # exact mod rank, 0-8
    positives: tuple[str, ...] = ()     # every one of these must be a positive stat
    negative: Optional[str] = None      # must have this stat as its negative (curse)
    max_price: Optional[int] = None     # highest buyout (or bid) to keep
    online_only: bool = True            # keep sellers who are in game or online


def asking_price(auction: "RivenAuction | LichAuction") -> Optional[int]:
    """The number a buyer pays: the buyout when set, else the current top bid
    (or the starting bid when nobody has bid yet)."""
    if auction.buyout_price:
        return auction.buyout_price
    return auction.top_bid or auction.starting_price or None


def _stat_matches(wanted: str, url_name: str) -> bool:
    """True when every word typed by the user starts some word of the stat name."""
    want = _norm(wanted).split()
    have = _norm(url_name).split()
    return bool(want) and all(any(h.startswith(w) for h in have) for w in want)


def stat_label(url_name: str) -> str:
    """Display name for a stat slug: 'critical_damage' -> 'Critical Damage'."""
    return " ".join(url_name.replace("/", " / ").replace("_", " ").split()).title()


def has_positive(auction: RivenAuction, wanted: str) -> bool:
    return any(a.positive and _stat_matches(wanted, a.url_name) for a in auction.attributes)


def has_negative(auction: RivenAuction, wanted: str) -> bool:
    """True when the riven's negative stat (the curse) matches `wanted`."""
    return any(not a.positive and _stat_matches(wanted, a.url_name) for a in auction.attributes)


def stat_suggestions(query: str, slugs: Iterable[str], limit: int = 25) -> list[str]:
    """Stat display names for autocomplete. Word-prefix matches come first, then
    substring matches. An empty query lists the first `limit` names alphabetically."""
    pairs = sorted({(stat_label(s), s) for s in slugs})
    q = _norm(query)
    if not q:
        return [label for label, _ in pairs][:limit]
    hits = [label for label, slug in pairs if _stat_matches(query, slug)]
    hits += [label for label, _ in pairs if q in _norm(label) and label not in hits]
    return hits[:limit]


def unknown_stats(auctions: Sequence[RivenAuction], wanted: Iterable[str]) -> list[str]:
    """Wanted stat names that no riven in `auctions` has, so the user can be told
    about a typo instead of getting an empty list. Empty when there is nothing to compare."""
    if not auctions:
        return []
    known = {a.url_name for auction in auctions for a in auction.attributes}
    return [w for w in wanted if not any(_stat_matches(w, k) for k in known)]


def _sort_key(auction: RivenAuction):
    price = asking_price(auction)
    return (price is None, price or 0, -auction.mod_rank)


def sort_rivens(auctions: Iterable[RivenAuction]) -> list[RivenAuction]:
    """Cheapest first; auctions without a price go last."""
    return sorted(auctions, key=_sort_key)


def filter_rivens(auctions: Iterable[RivenAuction], flt: RivenFilter) -> list[RivenAuction]:
    kept: list[RivenAuction] = []
    for a in auctions:
        if flt.online_only and not a.is_online:
            continue
        if flt.polarity and a.polarity != flt.polarity.lower():
            continue
        if flt.rank is not None and a.mod_rank != flt.rank:
            continue
        if flt.max_price is not None:
            price = asking_price(a)
            if price is None or price > flt.max_price:
                continue
        if flt.positives and not all(has_positive(a, w) for w in flt.positives):
            continue
        if flt.negative and not has_negative(a, flt.negative):
            continue
        kept.append(a)
    return sort_rivens(kept)


@dataclass(frozen=True)
class LichFilter:
    element: Optional[str] = None
    min_damage: Optional[int] = None
    ephemera: Optional[bool] = None     # True = must have one, False = must not
    max_price: Optional[int] = None
    online_only: bool = True


def filter_liches(auctions: Iterable[LichAuction], flt: LichFilter) -> list[LichAuction]:
    """Matching Kuva Lich / Sister auctions: cheapest first, then highest damage."""
    kept = []
    for a in auctions:
        if flt.online_only and not a.is_online:
            continue
        if flt.element and a.element.lower() != flt.element.lower():
            continue
        if flt.min_damage is not None and a.damage < flt.min_damage:
            continue
        if flt.ephemera is not None and a.ephemera != flt.ephemera:
            continue
        price = asking_price(a)
        if flt.max_price is not None and (price is None or price > flt.max_price):
            continue
        kept.append(a)
    return sorted(kept, key=lambda a: (asking_price(a) is None, asking_price(a) or 0, -a.damage))
