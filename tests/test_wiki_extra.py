"""Offline tests for the wiki additions: tables, drops, compare, categories, links."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import RouteSession  # noqa: E402
from ui.paginator import Section, SectionView  # noqa: E402
from wiki.client import WarframeWikiClient  # noqa: E402
from wiki.compare import DIFF, SAME, build_compare_view, compare_lines, shared_headers  # noqa: E402
from wiki.kinds import KINDS, all_categories  # noqa: E402
from wiki.page_view import build_page_view, drops_section  # noqa: E402
from wiki.parser import parse_page  # noqa: E402

LOOP = asyncio.new_event_loop()


def run(coro):
    return LOOP.run_until_complete(coro)


def build(factory, *a, **kw):
    async def make():
        return factory(*a, **kw)
    return run(make())


def infobox(title, rows):
    body = "".join(f'<div class="row"><div class="label left">{k}</div><div class="value right">{v}</div></div>'
                   for k, v in rows)
    return (f'<div class="infobox"><div class="title">{title}</div></div>'
            f'<div class="group mw-collapsible"><div class="header">General Information</div>{body}</div>')


RELIC = (infobox("Axi A1", [("Type", "Axi")]) + "<p>A Void Relic that holds Prime parts for you.</p>"
         '<h2>Rewards</h2><table class="wikitable"><tr><th>Reward</th><th>Rarity</th></tr>'
         "<tr><td>Akstiletto Prime Barrel</td><td>Rare</td></tr>"
         "<tr><td>Forma Blueprint</td><td>Common</td></tr></table>"
         '<h2>Acquisition</h2><div class="tabber"><table><tr><td>Hydron</td><td>Rotation C</td></tr></table></div>'
         '<h2>See Also</h2><div class="navbox"><table><tr><td>Lith</td></tr></table></div>')


def test_tables_become_lines_and_navboxes_are_skipped():
    page = parse_page("Axi A1", "u", RELIC)
    assert page.article_sections["Rewards"].splitlines() == [
        "**Reward · Rarity**", "▫ Akstiletto Prime Barrel · Rare", "▫ Forma Blueprint · Common"]
    assert "Hydron · Rotation C" in page.article_sections["Acquisition"]
    assert "See Also" not in page.article_sections


def test_long_tables_are_capped():
    rows = "".join(f"<tr><td>Row {i}</td><td>x</td></tr>" for i in range(60))
    page = parse_page("T", "u", f"<p>Lead paragraph long enough to count.</p><h2>Drops</h2><table>{rows}</table>")
    lines = page.article_sections["Drops"].splitlines()
    assert len(lines) == 41 and "20 more rows" in lines[-1]


def test_drops_section_and_view_starts_there():
    page = parse_page("Axi A1", "u", RELIC)
    assert drops_section(page) == "Acquisition"
    view = build(build_page_view, page, start=drops_section(page))
    assert view.sections[view.section_idx].label == "Acquisition"
    assert drops_section(parse_page("X", "u", "<p>Nothing here but a long lead paragraph.</p>")) is None


def test_link_buttons_survive_timeout():
    view = build(SectionView, [Section("A", ["e1"]), Section("B", ["e2"])], links=[("warframe.market", "https://x")])
    link = [c for c in view.children if getattr(c, "url", None)]
    assert len(link) == 1 and link[0].label == "warframe.market"
    run(view.on_timeout())
    assert not link[0].disabled and view.select.disabled


def test_compare_marks_differences():
    a = parse_page("Braton", "ua", infobox("Braton", [("Mastery Rank Requirement", "0"), ("Slot", "Primary")]))
    b = parse_page("Boltor", "ub", infobox("Boltor", [("Mastery Rank Requirement", "2"), ("Slot", "Primary"),
                                                       ("Trigger Type", "Auto")]))
    assert shared_headers(a, b) == ["General Information"]
    lines = compare_lines(a, b, "General Information")
    assert lines[0].startswith(DIFF) and "0 │ 2" in lines[0]
    assert lines[1].startswith(SAME)
    assert "— │ Auto" in lines[2]
    view = build(build_compare_view, a, b)
    assert view.sections[0].description == "2 of 3 rows differ"
    assert len([c for c in view.children if getattr(c, "url", None)]) == 2


def test_compare_with_nothing_shared():
    a = parse_page("A", "ua", "<p>Plain page with a lead paragraph that is long.</p>")
    view = build(build_compare_view, a, a)
    assert "share no infobox" in view.current_embed().description


def test_category_members_follow_continuation_and_cache():
    pages = [
        {"query": {"categorymembers": [{"title": "Excalibur"}, {"title": "Mag"}]},
         "continue": {"cmcontinue": "page|2"}},
        {"query": {"categorymembers": [{"title": "Volt"}]}},
    ]
    session = RouteSession({"/api.php": [(200, p) for p in pages]})
    wiki = WarframeWikiClient(session)
    assert wiki.cached_category("Warframes") is None
    assert run(wiki.category_members("Warframes")) == ["Excalibur", "Mag", "Volt"]
    assert session.calls[1][1]["cmcontinue"] == "page|2"
    assert wiki.cached_category("Warframes") == ["Excalibur", "Mag", "Volt"]
    run(wiki.category_members("Warframes"))
    assert len(session.calls) == 2


def test_categories_union_and_random():
    session = RouteSession({"/api.php": (200, {"query": {"categorymembers": [{"title": "Braton"}],
                                                         "random": [{"title": "Lotus"}]}})})
    wiki = WarframeWikiClient(session)
    assert run(wiki.categories_members(("Weapons", "Primary Weapons"))) == ["Braton"]
    assert run(wiki.random_title()) == "Lotus"


def test_kinds_cover_every_typed_command():
    assert set(KINDS) == {"warframe", "weapon", "mod", "arcane", "companion", "enemy", "relic", "quest"}
    cats = all_categories()
    assert "Warframe Abilities" in cats and "Mods" in cats and len(cats) == len(set(cats))
