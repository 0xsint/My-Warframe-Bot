"""Parser + fetcher for Warframe *ability* pages on wiki.warframe.com.

Ability pages (e.g. https://wiki.warframe.com/w/Nurinarim) share one layout:

    description paragraph
    stat block            (Strength / Duration / Range / Misc)
    **Info**              -> bullet list of mechanics
    **Kit Interactions**  -> bullet list of synergies with the rest of the kit
    **Tips**, **Notes**, **Bugs** ... (only when the page has them)
    **Maximization**      -> calculator widget (skipped, empty without JS)
    **Localization**      -> translation table (skipped)

The parser is deliberately heuristic: it keys off the bold section labels and
the "Warframe Abilities" category rather than exact CSS classes, so small
markup changes on the wiki are less likely to break it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup, NavigableString, Tag

WIKI_BASE = "https://wiki.warframe.com"
API_URL = f"{WIKI_BASE}/api.php"
USER_AGENT = "WarframeBOT/1.0 (Discord bot; https://github.com/0xsint/My-Warframe-Bot)"

# label on the page (lower-case) -> label we show
SHOWN_SECTIONS = {
    "info": "Info",
    "kit interactions": "Kit Interactions",
    "synergies": "Synergies",
    "tips": "Tips",
    "notes": "Notes",
    "trivia": "Trivia",
    "bugs": "Bugs",
}
# recognised headings we parse *past* but never display
SKIPPED_SECTIONS = {"maximization", "localization", "see also", "media", "patch history"}
STAT_LABELS = ("Strength", "Duration", "Range", "Misc")
_ALL_HEADINGS = set(SHOWN_SECTIONS) | SKIPPED_SECTIONS


@dataclass
class AbilityPage:
    title: str
    url: str
    description: str = ""
    introduced: Optional[str] = None
    energy_cost: Optional[str] = None
    stats: dict[str, list[str]] = field(default_factory=dict)
    sections: dict[str, list[str]] = field(default_factory=dict)  # name -> markdown lines
    image_url: Optional[str] = None
    categories: list[str] = field(default_factory=list)

    @property
    def is_ability(self) -> bool:
        return any("warframe abilities" in c.lower().replace("_", " ") for c in self.categories)


# --------------------------------------------------------------------------- #
# HTML -> Discord markdown
# --------------------------------------------------------------------------- #
_SKIP_TAGS = {"img", "style", "script", "sup", "table", "figure"}


def _inline_md(node: Tag) -> str:
    out: list[str] = []
    for child in node.children:
        if isinstance(child, NavigableString):
            out.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        classes = " ".join(child.get("class", []))
        if child.name in _SKIP_TAGS or "mw-editsection" in classes:
            continue
        if child.name in ("ul", "ol", "dl"):  # nested lists are rendered separately
            continue
        if child.name == "br":
            out.append("\n")
        elif child.name in ("b", "strong"):
            inner = _inline_md(child)
            stripped = inner.strip()
            if stripped:
                lead = inner[: len(inner) - len(inner.lstrip())]
                trail = inner[len(inner.rstrip()):]
                out.append(f"{lead}**{stripped}**{trail}")
        elif child.name in ("i", "em"):
            inner = _inline_md(child)
            stripped = inner.strip()
            if stripped:
                out.append(f"*{stripped}*")
        else:
            out.append(_inline_md(child))
    return "".join(out)


def clean_text(text: str) -> str:
    text = re.sub(r"<DT_[A-Z_]+>\s*", "", text)          # unresolved in-game tokens
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([,.;:%)!?])", r"\1", text)        # "Cold ," -> "Cold,"
    text = re.sub(r"\*\*\s*\*\*", "", text)               # empty bold
    text = re.sub(r"\(\s+", "(", text)
    return text.strip()


def _list_md(lst: Tag, depth: int = 0) -> list[str]:
    lines: list[str] = []
    for li in lst.find_all("li", recursive=False):
        text = clean_text(_inline_md(li).replace("\n", " "))
        if text:
            lines.append(f"{'  ' * depth}- {text}")
        for sub in li.find_all(["ul", "ol"], recursive=False):
            lines.extend(_list_md(sub, depth + 1))
    return lines


def _heading_name(tag: Tag) -> Optional[str]:
    """If `tag` is a section heading, return its lower-case name."""
    if tag.name in ("h2", "h3", "h4"):
        text = tag.get_text(" ", strip=True)
        text = re.sub(r"\[\s*edit.*?\]", "", text).strip()
    elif tag.name in ("b", "strong"):
        text = tag.get_text(" ", strip=True)
        # only a heading if the bold text is (nearly) the whole paragraph
        parent = tag.parent
        if parent is not None and parent.name in ("p", "div"):
            if len(parent.get_text(" ", strip=True)) > len(text) + 3:
                return None
    else:
        return None
    key = text.rstrip(":").strip().lower()
    return key if key in _ALL_HEADINGS else None


# --------------------------------------------------------------------------- #
# Page parsing
# --------------------------------------------------------------------------- #
def _text_lines(soup: Tag) -> list[str]:
    work = BeautifulSoup(str(soup), "html.parser")
    for t in work.find_all(["style", "script"]):
        t.decompose()
    for br in work.find_all("br"):
        br.replace_with("\n")
    for t in work.find_all(["p", "div", "li", "tr", "h2", "h3", "h4", "ul", "ol"]):
        t.insert_before("\n")
        t.insert_after("\n")
    raw = work.get_text("")
    lines = [clean_text(line.replace("*", "")) for line in raw.splitlines()]
    return [l for l in lines if l]


def _parse_stats(pre_lines: list[str]) -> dict[str, list[str]]:
    blob = "\n".join(pre_lines)
    pattern = re.compile(r"(%s)\s*:" % "|".join(STAT_LABELS))
    matches = list(pattern.finditer(blob))
    stats: dict[str, list[str]] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(blob)
        values = [clean_text(v) for v in blob[m.end():end].splitlines() if clean_text(v)]
        if values and m.group(1) not in stats:
            stats[m.group(1)] = values
    return stats


def parse_ability_html(title: str, html: str, categories: Optional[list[str]] = None,
                       image_url: Optional[str] = None) -> AbilityPage:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".mw-parser-output") or soup
    page = AbilityPage(
        title=title,
        url=f"{WIKI_BASE}/w/{title.replace(' ', '_')}",
        categories=categories or [],
        image_url=image_url or _find_image_in_html(title, soup),
    )

    # ---- sections (DOM walk so nested bullets keep their hierarchy) ---------
    current: Optional[str] = None
    seen_first_heading = False
    for el in root.descendants:
        if not isinstance(el, Tag):
            continue
        name = _heading_name(el)
        if name is not None:
            seen_first_heading = True
            current = name if name in SHOWN_SECTIONS else None
            continue
        if el.name in ("ul", "ol") and seen_first_heading and current:
            if el.find_parent(["ul", "ol"]) is not None:
                continue  # nested: rendered by its parent
            lines = _list_md(el)
            if lines:
                page.sections.setdefault(SHOWN_SECTIONS[current], []).extend(lines)

    # ---- description / stats from the text before the first heading --------
    pre: list[str] = []
    for line in _text_lines(root):
        if line.rstrip(":").lower() in _ALL_HEADINGS:
            break
        pre.append(line)

    page.stats = _parse_stats(pre)

    stat_re = re.compile(r"^(%s)\b" % "|".join(STAT_LABELS))
    for line in pre:
        if line.startswith("Introduced") or stat_re.match(line):
            continue
        if len(line) > 40 and not re.fullmatch(r"[\d\s/%.,]+", line):
            page.description = line
            break

    intro = re.search(r"Introduced in\s+(.+?)\s*\((\d{4}-\d{2}-\d{2})\)", "\n".join(pre))
    if intro:
        page.introduced = f"{intro.group(1).strip()} ({intro.group(2)})"

    # the two bare-number lines are normally [keybind, energy cost]
    bare = [l for l in pre if re.fullmatch(r"\d{1,3}", l)]
    if len(bare) == 2:
        page.energy_cost = bare[1]
    return page


def _find_image_in_html(title: str, soup: BeautifulSoup) -> Optional[str]:
    """Fallback: the ability icon is usually '<Title>(xWhite).png'."""
    needle = title.replace(" ", "_").lower()
    for img in soup.find_all("img"):
        src = img.get("src", "")
        norm = src.lower().replace("%28", "(").replace("%29", ")")
        if needle in norm:
            src = re.sub(r"/thumb/(.+?)/\d+px-[^/]+$", r"/\1", src)  # thumb -> original
            return src if src.startswith("http") else WIKI_BASE + src
    return None


# kept under the old names so existing imports (ability_view) keep working
from ui.text import PAGE_CHARS, paginate_lines as paginate  # noqa: E402,F401


# --------------------------------------------------------------------------- #
# Fetching (aiohttp session is passed in; nothing heavy imported at module load)
# --------------------------------------------------------------------------- #
async def fetch_ability(session, title: str) -> Optional[AbilityPage]:
    """Fetch + parse a page. Returns None if the page does not exist."""
    parse_params = {
        "action": "parse", "page": title, "prop": "text|categories",
        "redirects": 1, "format": "json", "formatversion": 2,
        "disablelimitreport": 1, "disableeditsection": 1,
    }
    headers = {"User-Agent": USER_AGENT}
    async with session.get(API_URL, params=parse_params, headers=headers) as resp:
        if resp.status == 403:
            raise RuntimeError("WARFRAME Wiki blocked this request (Cloudflare 403).")
        resp.raise_for_status()
        data = await resp.json()
    if "error" in data:
        return None
    parsed = data["parse"]
    cats = [c.get("category", "") for c in parsed.get("categories", [])]

    image = None
    img_params = {
        "action": "query", "titles": parsed["title"], "prop": "pageimages",
        "piprop": "original", "redirects": 1, "format": "json", "formatversion": 2,
    }
    try:
        async with session.get(API_URL, params=img_params, headers=headers) as resp:
            if resp.status == 200:
                q = await resp.json()
                pages = q.get("query", {}).get("pages", [])
                if pages:
                    image = pages[0].get("original", {}).get("source")
    except Exception:
        image = None  # fall back to pulling the <img> out of the HTML

    return parse_ability_html(parsed["title"], parsed["text"], cats, image)


async def search_titles(session, query: str, limit: int = 10) -> list[str]:
    params = {"action": "opensearch", "search": query, "limit": limit,
              "namespace": 0, "format": "json"}
    async with session.get(API_URL, params=params, headers={"User-Agent": USER_AGENT}) as r:
        if r.status != 200:
            return []
        data = await r.json()
    return data[1] if len(data) > 1 else []


async def is_ability_title(session, title: str) -> Optional[bool]:
    """Cheap check (one tiny API call) for 'is this page in Category:Warframe Abilities?'

    Returns True / False, or None when the page doesn't exist (so the caller can
    try a fuzzy title match). Lets /wf page avoid a second full fetch for
    pages that are not abilities."""
    params = {
        "action": "query", "titles": title, "prop": "categories",
        "clcategories": "Category:Warframe Abilities", "redirects": 1,
        "format": "json", "formatversion": 2,
    }
    async with session.get(API_URL, params=params, headers={"User-Agent": USER_AGENT}) as r:
        if r.status != 200:
            return False
        data = await r.json()
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing") or pages[0].get("invalid"):
        return None
    return bool(pages[0].get("categories"))
