"""HTML parsing for WARFRAME Wiki pages.

The wiki's pages (Warframes, weapons, mods, enemies, etc.) are all rendered
with a shared "infobox" widget that looks like this once rendered to HTML::

    <div class="infobox tabber-borderless">
        <div class="title">Braton</div>
        <div class="tabber">
            <div class="tabbertab" data-title="Normal">
                <span class="main-image"><img src="..."></span>
            </div>
            ...
        </div>
    </div>
    <div class="group mw-collapsible" id="...">
        <div class="header">General Information</div>
        <div class="row">
            <div class="label left">Mastery Rank Requirement</div>
            <div class="value right">0</div>
        </div>
        ...
    </div>
    <div class="group mw-collapsible" id="...">
        <div class="header">Utility</div>
        ...
    </div>
    ... more "group mw-collapsible" sections ...
    <p>The lead paragraph of the article...</p>
    <h2>Characteristics</h2>
    ...

Every "info box" section shares the same ``div.group.mw-collapsible`` /
``div.row`` / ``div.label.left`` / ``div.value.right`` markup regardless of
whether the page is a Warframe, weapon, mod, enemy, etc. so a single generic
parser can handle all of them without page-type-specific scraping code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

BASE_URL = "https://wiki.warframe.com"

# Caption-only rows (a single centered line of text, no label) are stored
# under this pseudo-label so callers can tell them apart from label/value
# pairs while still keeping everything in one ordered list.
CAPTION_LABEL = "__caption__"

# Headers for "infobox-styled" boxes that aren't actually useful game data
# (wiki housekeeping widgets reusing the same collapsible-box styling).
_IGNORED_HEADERS = {
    "maintenance",
    "article categories",
}


@dataclass(slots=True)
class InfoboxSection:
    """One collapsible block from an infobox, e.g. "General Information"."""

    header: str
    rows: list[tuple[str, str]] = field(default_factory=list)

    @property
    def captions(self) -> list[str]:
        return [value for label, value in self.rows if label == CAPTION_LABEL]

    @property
    def fields(self) -> list[tuple[str, str]]:
        return [(label, value) for label, value in self.rows if label != CAPTION_LABEL]

    def get(self, *labels: str) -> Optional[str]:
        wanted = {label.lower() for label in labels}
        for label, value in self.fields:
            if label.lower() in wanted:
                return value
        return None


@dataclass(slots=True)
class WikiPage:
    """A parsed WARFRAME Wiki page."""

    title: str
    url: str
    image_url: Optional[str]
    summary: Optional[str]
    sections: list[InfoboxSection]
    lead_paragraphs: list[str] = field(default_factory=list)
    article_sections: dict[str, str] = field(default_factory=dict)

    def section(self, *names: str) -> Optional[InfoboxSection]:
        wanted = {name.lower() for name in names}
        for sec in self.sections:
            if sec.header.lower() in wanted:
                return sec
        return None

    def field(self, *labels: str) -> Optional[str]:
        for sec in self.sections:
            value = sec.get(*labels)
            if value is not None:
                return value
        return None

    def article(self, *names: str) -> Optional[str]:
        """Body text of an ``==Article Heading==`` section (e.g. "Trivia")."""
        wanted = {name.lower() for name in names}
        for heading, text in self.article_sections.items():
            if heading.lower() in wanted:
                return text
        return None

    @property
    def lead_text(self) -> Optional[str]:
        """The lead paragraph(s) joined together, for a fuller description
        than just the first sentence (e.g. a Warframe's flavor line *and*
        its ability-overview paragraph)."""
        if not self.lead_paragraphs:
            return None
        return "\n\n".join(self.lead_paragraphs[:2])


def _clean_text(tag: Tag) -> str:
    text = tag.get_text(separator=" ", strip=True)
    return " ".join(text.split())


def _absolute_url(src: str) -> str:
    return urljoin(BASE_URL, src)


def _is_noise_row(label: str, value: str) -> bool:
    """Filter out rows that are editorial/noise rather than real data."""
    if not value:
        return True
    if label.startswith("<") and label.endswith(">"):
        # Redundant per-element damage breakdown rows nested under e.g.
        # "Normal Attack" (labels literally render as "<impact>" etc.) -
        # the sibling "Total Damage" row already summarizes this.
        return True
    if "Update Infobox Data" in value:
        return True
    return False


def _is_lead_paragraph_candidate(p: Tag) -> bool:
    if p.find_parent("div", class_="infobox") is not None:
        return False
    if p.find_parent("div", class_=lambda c: c and "group" in c) is not None:
        return False
    if p.find_parent("div", class_=lambda c: c and "WarframeNavBox" in c) is not None:
        return False
    return True


def _extract_sections(soup: BeautifulSoup) -> tuple[list[InfoboxSection], list[str]]:
    """Single document-order pass that collects both the infobox sections and
    the lead paragraph(s) that precede the first ``==Heading==``."""
    sections: list[InfoboxSection] = []
    lead_paragraphs: list[str] = []
    seen_heading = False

    for el in soup.find_all(True):
        if seen_heading:
            break
        if el.name in ("h2", "h3"):
            seen_heading = True
            continue

        if el.name == "p" and _is_lead_paragraph_candidate(el):
            text = _clean_text(el)
            if len(text) >= 20:
                lead_paragraphs.append(text)
            continue

        if el.name != "div":
            continue
        classes = el.get("class") or []
        if "group" not in classes or "mw-collapsible" not in classes:
            continue

        header_tag = el.find("div", class_="header")
        header = _clean_text(header_tag) if header_tag else ""
        if header.lower() in _IGNORED_HEADERS:
            continue

        section = InfoboxSection(header=header)
        for row in el.find_all("div", class_="row"):
            value_tag = row.find("div", class_="value")
            if value_tag is None:
                continue
            value = _clean_text(value_tag)
            label_tag = row.find("div", class_="label")
            label = _clean_text(label_tag) if label_tag else CAPTION_LABEL
            if _is_noise_row(label, value):
                continue
            section.rows.append((label, value))

        if section.rows:
            sections.append(section)

    return sections, lead_paragraphs


def _extract_image(soup: BeautifulSoup) -> Optional[str]:
    infobox = soup.find("div", class_="infobox")
    if infobox is None:
        return None
    img = infobox.find("img")
    if img is None or not img.get("src"):
        return None
    return _absolute_url(img["src"])


def _is_heading_boundary(tag: Tag) -> bool:
    if tag.name == "h2":
        return True
    if tag.name == "div":
        classes = tag.get("class") or []
        return "mw-heading" in classes and tag.find("h2") is not None
    return False


def _heading_container(h2: Tag) -> Tag:
    """MediaWiki wraps headings in ``<div class="mw-heading mw-heading2">``
    on newer versions; older markup has a bare ``<h2>``. Return whichever
    element's *siblings* are the actual section body."""
    parent = h2.parent
    if parent is not None and "mw-heading" in (parent.get("class") or []):
        return parent
    return h2


def _extract_article_sections(soup: BeautifulSoup, *, max_sections: int = 12) -> dict[str, str]:
    """Pull plain-text body content for each ``==Heading==`` in the article
    (e.g. "Acquisition", "Trivia", "Characteristics", "Notes"). Tables,
    images, and nested widgets are skipped in favor of paragraphs and
    top-level bullet points, which read well inside a Discord embed."""
    result: dict[str, str] = {}

    for h2 in soup.find_all("h2"):
        title = _clean_text(h2)
        if not title or title.lower() == "contents":
            continue
        if len(result) >= max_sections:
            break

        lines: list[str] = []
        node = _heading_container(h2).find_next_sibling()
        while node is not None and not _is_heading_boundary(node):
            if node.name == "p":
                text = _clean_text(node)
                if text:
                    lines.append(text)
            elif node.name in ("ul", "ol"):
                for li in node.find_all("li", recursive=False):
                    text = _clean_text(li)
                    if text:
                        lines.append(f"\u2022 {text}")
            node = node.find_next_sibling()

        if lines:
            result[title] = "\n".join(lines[:8])

    return result


def parse_page(title: str, url: str, html: str) -> WikiPage:
    """Parse the rendered HTML (``action=parse`` output) of a wiki page."""
    soup = BeautifulSoup(html, "lxml")
    fp = open("error.log", "w", encoding="utf-8")
    fp.write(soup.select("body")[0].get_text(separator="\n"))
    fp.close()
    # Strip hidden tooltip/search metadata blobs (raw JSON embedded for a JS
    # gadget) that would otherwise leak into extracted paragraph text, since
    # BeautifulSoup has no concept of CSS `display:none`.
    for hidden in soup.select("span.tooltip-metadata"):
        hidden.decompose()

    sections, lead_paragraphs = _extract_sections(soup)
    summary = lead_paragraphs[0] if lead_paragraphs else None

    return WikiPage(
        title=title,
        url=url,
        image_url=_extract_image(soup),
        summary=summary,
        sections=sections,
        lead_paragraphs=lead_paragraphs,
        article_sections=_extract_article_sections(soup),
    )
