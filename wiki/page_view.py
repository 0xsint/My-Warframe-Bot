"""Dropdown + pagination for wiki pages (Warframes, weapons, mods, enemies, ...).

Same idea as the ability view: the first embed is the overview you already had,
and the dropdown lists every other block of the page (the infobox groups such as
"General Information" / "Abilities", then the article headings such as
"Acquisition" / "Trivia"). Long sections are split over several pages.
"""
from __future__ import annotations

from typing import Optional

import nextcord

from ui.paginator import Section, SectionView
from ui.text import paginate_lines

from .embeds import (
    EMBED_COLOR,
    FOOTER_ICON,
    FOOTER_TEXT,
    build_generic_embed,
    build_warframe_embed,
    build_weapon_embed,
)
from .parser import InfoboxSection, WikiPage

OVERVIEW = "Overview"

# Infobox groups shown first (in this order) for each page kind.
_PREFERRED = {
    "warframe": ("General Information", "Abilities", "Passive"),
    "weapon": ("General Information", "Utility", "Normal Attack", "Charged Attack", "Heavy Attack"),
    "generic": (),
}
_OVERVIEW_BUILDERS = {
    "warframe": build_warframe_embed,
    "weapon": build_weapon_embed,
    "generic": build_generic_embed,
}
# Article headings that are navigation noise rather than content.
_SKIP_ARTICLE = {"see also", "references", "gallery", "media", "external links", "contents",
                 "navigation"}


def detect_kind(page: WikiPage) -> str:
    """Best guess at what kind of page this is, from its infobox."""
    if page.section("Abilities") and page.section("General Information"):
        return "warframe"
    if page.section("Utility") or page.field("Trigger Type") is not None:
        return "weapon"
    return "generic"


def _section_lines(section: InfoboxSection) -> list[str]:
    lines = []
    for label, value in section.rows:
        lines.append(f"> {value}" if label == "__caption__" else f"**{label}:** {value}")
    return lines


def _page_embeds(page: WikiPage, title: str, lines: list[str]) -> list[nextcord.Embed]:
    embeds = []
    for chunk in paginate_lines(lines):
        e = nextcord.Embed(title=page.title, url=page.url, description=chunk, color=EMBED_COLOR)
        if page.image_url:
            e.set_thumbnail(url=page.image_url)
        embeds.append(e)
    return embeds


def _ordered_infobox(page: WikiPage, kind: str) -> list[InfoboxSection]:
    wanted = [h.lower() for h in _PREFERRED.get(kind, ())]
    first, rest = [], []
    for sec in page.sections:
        if not sec.header or not sec.rows:
            continue
        (first if sec.header.lower() in wanted else rest).append(sec)
    first.sort(key=lambda s: wanted.index(s.header.lower()))
    return first + rest


def build_page_view(page: WikiPage, kind: Optional[str] = None) -> SectionView:
    kind = kind or detect_kind(page)
    sections = [Section(OVERVIEW, [_OVERVIEW_BUILDERS[kind](page)], description="Summary and key stats")]

    taken = {OVERVIEW.lower()}
    for sec in _ordered_infobox(page, kind):
        pages = _page_embeds(page, sec.header, _section_lines(sec))
        if pages and sec.header.lower() not in taken:
            taken.add(sec.header.lower())
            sections.append(Section(sec.header, pages))

    for heading, text in page.article_sections.items():
        if heading.lower() in _SKIP_ARTICLE:
            continue
        label = heading if heading.lower() not in taken else f"{heading} (article)"
        pages = _page_embeds(page, heading, text.split("\n"))
        if pages and label.lower() not in taken:
            taken.add(label.lower())
            sections.append(Section(label, pages))

    return SectionView(sections, footer_text=FOOTER_TEXT, footer_icon=FOOTER_ICON,
                       select_placeholder="Pick a section")
