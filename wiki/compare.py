"""Side-by-side comparison of two wiki pages' infoboxes (/wf compare).

Rows are matched by infobox section ("General Information", "Normal Attack",
...) and label ("Critical Chance", ...). Values that differ are marked so the
interesting rows stand out.
"""
from __future__ import annotations

import nextcord

from ui.paginator import Section, SectionView
from ui.text import paginate_lines

from .embeds import EMBED_COLOR, FOOTER_ICON, FOOTER_TEXT
from .parser import InfoboxSection, WikiPage

DIFF, SAME = "◆", "◇"


def _rows(a: InfoboxSection | None, b: InfoboxSection | None) -> list[tuple[str, str, str]]:
    av = dict(a.fields) if a else {}
    bv = dict(b.fields) if b else {}
    labels = list(av) + [label for label in bv if label not in av]
    return [(label, av.get(label, "—"), bv.get(label, "—")) for label in labels]


def compare_lines(a: WikiPage, b: WikiPage, header: str) -> list[str]:
    rows = _rows(a.section(header), b.section(header))
    return [f"{DIFF if va != vb else SAME} **{label}:** {va} │ {vb}" for label, va, vb in rows]


def shared_headers(a: WikiPage, b: WikiPage) -> list[str]:
    b_headers = {s.header.lower() for s in b.sections if s.header and s.fields}
    out = []
    for s in a.sections:
        if s.header and s.fields and s.header.lower() in b_headers and s.header not in out:
            out.append(s.header)
    return out


def _embed(a: WikiPage, b: WikiPage, description: str) -> nextcord.Embed:
    e = nextcord.Embed(title=f"{a.title} vs {b.title}", url=a.url, description=description,
                       color=EMBED_COLOR)
    if a.image_url:
        e.set_thumbnail(url=a.image_url)
    return e


def build_compare_view(a: WikiPage, b: WikiPage) -> SectionView:
    headers = shared_headers(a, b)
    legend = (f"Left: **[{a.title}]({a.url})** │ Right: **[{b.title}]({b.url})**\n"
              f"{DIFF} differs · {SAME} same\n")
    sections = []
    for header in headers:
        lines = compare_lines(a, b, header)
        pages = [_embed(a, b, legend + "\n" + chunk) for chunk in paginate_lines(lines, 3500)]
        if pages:
            diffs = sum(1 for line in lines if line.startswith(DIFF))
            sections.append(Section(header, pages, description=f"{diffs} of {len(lines)} rows differ"))
    if not sections:
        note = ("These pages share no infobox sections to compare. Try two of the same kind "
                "(two weapons, two Warframes, two mods, ...).")
        sections = [Section("Overview", [_embed(a, b, legend + "\n" + note)])]
    return SectionView(sections, footer_text=FOOTER_TEXT, footer_icon=FOOTER_ICON,
                       select_placeholder="Pick a stat block",
                       links=[(a.title, a.url), (b.title, b.url)])
