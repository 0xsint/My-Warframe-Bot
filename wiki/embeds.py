"""Builds Discord embeds from parsed WARFRAME Wiki pages."""

from __future__ import annotations

from typing import Iterable

import nextcord

from .parser import InfoboxSection, WikiPage

EMBED_COLOR = 0xC9A227  # Orokin gold, matches the wiki's accent color
FIELD_VALUE_LIMIT = 1024
DESCRIPTION_LIMIT = 2048

FOOTER_TEXT = "WARFRAME Wiki \u2022 wiki.warframe.com"
FOOTER_ICON = "https://wiki.warframe.com/images/Favicon.ico"

# Preferred stat order for Warframe "General Information" sections. Anything
# else present in the section is appended afterwards, so this is a nice-to-
# have ordering rather than a strict allowlist.
_WARFRAME_STAT_ORDER = (
    "Mastery Rank Requirement",
    "Health",
    "Shields",
    "Armor",
    "Energy",
    "Starting Energy",
    "Sprint Speed",
    "Aura Polarity",
    "Exilus Polarity",
    "Introduced",
)

_WEAPON_STAT_ORDER = (
    "Type",
    "Mastery Rank Requirement",
    "Slot",
    "Trigger Type",
    "Accuracy",
    "Magazine Size",
    "Reload Time",
    "Noise Level",
)


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "\u2026"


def _ordered_fields(section: InfoboxSection, order: Iterable[str]) -> list[tuple[str, str]]:
    ordered: list[tuple[str, str]] = []
    used = set()
    for label in order:
        for actual_label, value in section.fields:
            if actual_label.lower() == label.lower() and actual_label not in used:
                ordered.append((actual_label, value))
                used.add(actual_label)
                break
    for label, value in section.fields:
        if label not in used:
            ordered.append((label, value))
            used.add(label)
    return ordered


def _lines(pairs: Iterable[tuple[str, str]]) -> str:
    return "\n".join(f"**{label}:** {value}" for label, value in pairs)


def _base_embed(page: WikiPage) -> nextcord.Embed:
    embed = nextcord.Embed(title=page.title, url=page.url, color=EMBED_COLOR)
    embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
    if page.image_url:
        embed.set_thumbnail(url=page.image_url)
    return embed


def _collect_captions(page: WikiPage, *, exclude_headers: set[str] = frozenset()) -> list[str]:
    captions: list[str] = []
    for section in page.sections:
        if section.header.lower() in exclude_headers:
            continue
        captions.extend(section.captions)
    return captions


def build_warframe_embed(page: WikiPage) -> nextcord.Embed:
    embed = _base_embed(page)

    description_parts: list[str] = []
    desc_section = page.section("Description")
    passive_section = page.section("Passive")
    if desc_section and desc_section.captions:
        description_parts.append(desc_section.captions[0])
    if passive_section and passive_section.captions:
        description_parts.append(f"**Passive:** {passive_section.captions[0]}")
    if description_parts:
        embed.description = _truncate("\n\n".join(description_parts), DESCRIPTION_LIMIT)

    general = page.section("General Information")
    if general:
        value = _lines(_ordered_fields(general, _WARFRAME_STAT_ORDER)[:10])
        embed.add_field(name="Stats", value=_truncate(value, FIELD_VALUE_LIMIT), inline=False)

    abilities = page.section("Abilities")
    if abilities:
        value = _lines(abilities.fields)
        embed.add_field(name="Abilities", value=_truncate(value, FIELD_VALUE_LIMIT), inline=False)

    return embed


def build_weapon_embed(page: WikiPage) -> nextcord.Embed:
    embed = _base_embed(page)

    captions = _collect_captions(page)
    if captions:
        embed.description = _truncate(" \u2022 ".join(captions), DESCRIPTION_LIMIT)
    elif page.summary:
        embed.description = _truncate(page.summary, DESCRIPTION_LIMIT)

    used_headers = {"description"}

    general = page.section("General Information")
    if general:
        value = _lines(_ordered_fields(general, _WEAPON_STAT_ORDER)[:10])
        embed.add_field(name="General Information", value=_truncate(value, FIELD_VALUE_LIMIT), inline=False)
        used_headers.add("general information")

    utility = page.section("Utility")
    if utility:
        value = _lines(utility.fields)
        embed.add_field(name="Utility", value=_truncate(value, FIELD_VALUE_LIMIT), inline=True)
        used_headers.add("utility")

    # Any remaining sections are typically per-attack-mode damage blocks
    # (e.g. "Normal Attack", "Charged Attack", "Heavy Attack", "Incarnon
    # Form"), which vary a lot between weapon types. Show as many as fit.
    remaining_budget = 25 - len(embed.fields)
    for section in page.sections:
        if remaining_budget <= 0:
            break
        if section.header.lower() in used_headers or not section.header:
            continue
        if not section.fields:
            continue
        value = _lines(section.fields[:10])
        embed.add_field(name=section.header, value=_truncate(value, FIELD_VALUE_LIMIT), inline=True)
        remaining_budget -= 1

    return embed


def build_generic_embed(page: WikiPage) -> nextcord.Embed:
    embed = _base_embed(page)

    if page.summary:
        embed.description = _truncate(page.summary, DESCRIPTION_LIMIT)
    else:
        captions = _collect_captions(page)
        if captions:
            embed.description = _truncate(" \u2022 ".join(captions[:3]), DESCRIPTION_LIMIT)

    shown = 0
    for section in page.sections:
        if shown >= 4:
            break
        if not section.header or not section.fields:
            continue
        value = _lines(section.fields[:8])
        embed.add_field(name=section.header, value=_truncate(value, FIELD_VALUE_LIMIT), inline=False)
        shown += 1

    return embed


def build_search_embed(query: str, results: list[tuple[str, str]]) -> nextcord.Embed:
    embed = nextcord.Embed(
        title=f'Search results for "{query}"',
        color=EMBED_COLOR,
    )
    embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
    if not results:
        embed.description = "No matching pages were found on the WARFRAME Wiki."
        return embed

    lines = [f"{i}. [{title}]({url})" for i, (title, url) in enumerate(results, start=1)]
    embed.description = _truncate("\n".join(lines), DESCRIPTION_LIMIT)
    return embed


def build_error_embed(title: str, message: str) -> nextcord.Embed:
    embed = nextcord.Embed(title=title, description=message, color=0xE74C3C)
    embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
    return embed
