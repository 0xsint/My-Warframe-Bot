"""Typed /wf lookups: which wiki categories feed each command's autocomplete.

Several candidate category names are listed per kind because the wiki has
renamed categories before; missing ones are simply empty. When none of them
has loaded yet, autocomplete falls back to plain wiki title search.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Kind:
    name: str                          # subcommand name
    noun: str                          # "Warframe", "mod", ...
    categories: tuple[str, ...]
    example: str
    view_kind: Optional[str] = None    # page_view overview builder; None = auto-detect


KINDS: dict[str, Kind] = {k.name: k for k in (
    Kind("warframe", "Warframe", ("Warframes",), "Excalibur", "warframe"),
    Kind("weapon", "weapon", ("Weapons", "Primary Weapons", "Secondary Weapons", "Melee Weapons",
                              "Arch-Guns", "Arch-Melee"), "Braton Prime", "weapon"),
    Kind("mod", "mod", ("Mods",), "Serration"),
    Kind("arcane", "arcane", ("Arcane Enhancements", "Arcanes"), "Arcane Energize"),
    Kind("companion", "companion", ("Companions", "Sentinels", "Kubrows", "Kavats", "MOAs"), "Smeeta Kavat"),
    Kind("enemy", "enemy", ("Enemies",), "Corrupted Heavy Gunner"),
    Kind("relic", "relic", ("Void Relics", "Relics"), "Axi A1"),
    Kind("quest", "quest", ("Quests",), "The Second Dream"),
)}

ABILITY_CATEGORIES = ("Warframe Abilities",)


def all_categories() -> list[str]:
    cats: list[str] = list(ABILITY_CATEGORIES)
    for kind in KINDS.values():
        cats.extend(c for c in kind.categories if c not in cats)
    return cats
