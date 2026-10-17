"""Offline tests for the dropdown + pagination views (wiki and market).

Run from the project root:  python -m unittest tests.test_views -v
"""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _stubs  # noqa: E402

_stubs.install()

import nextcord  # noqa: E402

from market import embeds as M  # noqa: E402
from market.analysis import build_report  # noqa: E402
from market.client import Item, Order  # noqa: E402
from ui.paginator import Section, SectionView, view_kwargs  # noqa: E402
from ui.text import chunk, paginate_lines  # noqa: E402
from wiki.page_view import build_page_view, detect_kind  # noqa: E402
from wiki.parser import parse_page  # noqa: E402
from wiki.search_view import SearchView  # noqa: E402


class FakeInteraction:
    def __init__(self):
        self.edits = []
        self.response = SimpleNamespace(edit_message=self._edit)

    async def _edit(self, **kw):
        self.edits.append(kw)


def run(coro):
    return asyncio.run(coro)


def emb(title):
    return nextcord.Embed(title=title, description=title)


def click(view, button, ix=None):
    ix = ix or FakeInteraction()
    run(button.callback(ix))
    return ix


def pick(view, index):
    view.select._selected_values = [str(index)]
    return run(view.select.callback(FakeInteraction()))


class TextTests(unittest.TestCase):
    def test_chunk(self):
        self.assertEqual(chunk([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]])
        self.assertEqual(chunk([], 3), [])

    def test_paginate_lines_respects_limit_and_never_splits_short_lines(self):
        lines = [f"line number {i} " + "x" * 40 for i in range(200)]
        pages = paginate_lines(lines, 500)
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(p) <= 500 for p in pages))
        self.assertEqual("\n".join(pages).split("\n"), lines)

    def test_paginate_lines_splits_one_huge_line(self):
        pages = paginate_lines(["word " * 400], 300)
        self.assertTrue(all(len(p) <= 300 for p in pages))


class SectionViewTests(unittest.TestCase):
    def make(self, **kw):
        return SectionView([Section("Overview", [emb("o")]),
                            Section("Info", [emb("i1"), emb("i2"), emb("i3")]),
                            Section("Tips", [emb("t1")])], footer_text="foot", **kw)

    def test_select_and_buttons_present_and_default_disabled_states(self):
        v = self.make()
        self.assertIsNotNone(v.select)
        self.assertEqual([o.label for o in v.select.options], ["Overview", "Info", "Tips"])
        self.assertEqual(v.select.options[1].description, "3 pages")
        self.assertTrue(v.prev_btn.disabled and v.next_btn.disabled)   # overview is one page

    def test_select_resets_page_and_enables_next(self):
        v = self.make()
        pick(v, 1)
        self.assertEqual(v.current_embed().title, "i1")
        self.assertFalse(v.next_btn.disabled)
        self.assertTrue(v.prev_btn.disabled)
        self.assertEqual([o.default for o in v.select.options], [False, True, False])
        self.assertEqual(v.current_embed().footer.text, "Info · Page 1/3 · foot")

    def test_next_prev_stay_inside_section(self):
        v = self.make()
        pick(v, 1)
        click(v, v.next_btn); click(v, v.next_btn); click(v, v.next_btn)    # extra click is clamped
        self.assertEqual(v.current_embed().title, "i3")
        self.assertTrue(v.next_btn.disabled)
        ix = click(v, v.prev_btn)
        self.assertEqual(v.current_embed().title, "i2")
        self.assertIs(ix.edits[-1]["view"], v)

    def test_chain_walks_across_sections(self):
        v = self.make(chain=True)
        seen = [v.current_embed().title]
        while not v.next_btn.disabled:
            click(v, v.next_btn)
            seen.append(v.current_embed().title)
        self.assertEqual(seen, ["o", "i1", "i2", "i3", "t1"])
        self.assertEqual(v.select.options[2].default, True)                # dropdown follows along
        self.assertEqual(v.current_embed().footer.text, "Tips · Page 5/5 · foot")
        click(v, v.prev_btn)
        self.assertEqual(v.current_embed().title, "i3")

    def test_single_page_has_no_controls(self):
        v = SectionView([Section("Only", [emb("x")])])
        self.assertEqual(v.children, [])
        self.assertNotIn("view", view_kwargs(v))

    def test_single_section_paginated_has_buttons_but_no_dropdown(self):
        v = SectionView([Section("Orders", [emb("a"), emb("b")])], footer_text="f")
        self.assertIsNone(v.select)
        self.assertEqual(len(v.children), 2)
        self.assertEqual(v.current_embed().footer.text, "Page 1/2 · f")

    def test_duplicate_labels_and_empty_sections(self):
        v = SectionView([Section("A", [emb("1")]), Section("A", [emb("2")]), Section("Empty", [])])
        self.assertEqual([o.label for o in v.select.options], ["A", "A (2)"])

    def test_dropdown_capped_at_25(self):
        v = SectionView([Section(f"S{i}", [emb(str(i))]) for i in range(40)])
        self.assertEqual(len(v.select.options), 25)

    def test_no_sections_is_an_error(self):
        with self.assertRaises(ValueError):
            SectionView([Section("x", [])])


WEAPON_HTML = """
<div class="infobox"><div class="title">Braton</div><span class="main-image"><img src="/images/Braton.png"></span></div>
<div class="group mw-collapsible"><div class="header">General Information</div>
  <div class="row"><div class="label left">Type</div><div class="value right">Rifle</div></div>
  <div class="row"><div class="label left">Trigger Type</div><div class="value right">Auto</div></div></div>
<div class="group mw-collapsible"><div class="header">Normal Attack</div>
  <div class="row"><div class="label left">Total Damage</div><div class="value right">18</div></div></div>
<p>The Braton is a rifle that fires in bursts and is the standard starter weapon.</p>
<div class="mw-heading mw-heading2"><h2>Acquisition</h2></div>
<p>Given to every Tenno at the start.</p>
<div class="mw-heading mw-heading3"><h3>Blueprint</h3></div>
<ul>%s</ul>
<div class="mw-heading mw-heading2"><h2>See also</h2></div>
<ul><li>Something</li></ul>
<div class="mw-heading mw-heading2"><h2>Trivia</h2></div>
<ul><li>First trivia fact.</li></ul>
"""


class WikiPageViewTests(unittest.TestCase):
    def setUp(self):
        bullets = "".join(f"<li>Acquisition detail number {i} " + "word " * 20 + "</li>" for i in range(40))
        self.page = parse_page("Braton", "https://wiki.warframe.com/w/Braton", WEAPON_HTML % bullets)

    def test_parser_keeps_long_article_sections_and_subheadings(self):
        text = self.page.article("Acquisition")
        self.assertIn("**Blueprint**", text)
        self.assertGreater(len(text.split("\n")), 8)           # used to be cut at 8 lines

    def test_detect_kind(self):
        self.assertEqual(detect_kind(self.page), "weapon")

    def test_sections_in_dropdown_and_noise_skipped(self):
        v = build_page_view(self.page)
        labels = [o.label for o in v.select.options]
        self.assertEqual(labels[0], "Overview")
        self.assertEqual(labels[1:3], ["General Information", "Normal Attack"])   # preferred weapon order
        self.assertIn("Acquisition", labels)
        self.assertIn("Trivia", labels)
        self.assertNotIn("See also", labels)

    def test_long_section_paginates_within_embed_limit(self):
        v = build_page_view(self.page)
        pick(v, [o.label for o in v.select.options].index("Acquisition"))
        n = len(v.sections[v.section_idx].pages)
        self.assertGreater(n, 1)
        self.assertFalse(v.next_btn.disabled)
        for s in v.sections:
            for e in s.pages:
                self.assertLessEqual(len(e.description or ""), 4096)
        self.assertIn("Acquisition · Page 1/", v.current_embed().footer.text)

    def test_page_with_only_overview_has_no_controls(self):
        page = parse_page("Stub", "u", "<p>Just a short page with a lead paragraph that is long enough.</p>")
        v = build_page_view(page)
        self.assertEqual(v.children, [])


class SearchViewTests(unittest.TestCase):
    def setUp(self):
        self.results = [(f"Page {i}", f"https://wiki.warframe.com/w/Page_{i}") for i in range(1, 26)]
        self.picked = []

        async def on_pick(interaction, title):
            self.picked.append(title)

        self.view = SearchView("page", self.results, on_pick)

    def test_dropdown_lists_current_page_only(self):
        self.assertEqual(len(self.view.select.options), 10)
        click(self.view, self.view.next_btn); click(self.view, self.view.next_btn)
        self.assertEqual(len(self.view.select.options), 5)
        self.assertTrue(self.view.next_btn.disabled)
        self.assertEqual(self.view.select.options[0].label, "21. Page 21")
        self.assertIn("Page 3/3 · 25 results", self.view.current_embed().footer.text)
        self.assertIn("21. [Page 21]", self.view.current_embed().description)

    def test_pick_calls_back_and_stops_view(self):
        self.view.select._selected_values = ["Page 4"]
        run(self.view.select.callback(FakeInteraction()))
        self.assertEqual(self.picked, ["Page 4"])
        self.assertTrue(self.view.stopped)

    def test_short_result_list_has_no_buttons(self):
        v = SearchView("x", self.results[:4], self.view.on_pick)
        self.assertEqual(len(v.children), 1)


def order(i, side="buy", rank=None, status="ingame", plat=None):
    return Order(id=str(i), type=side, platinum=plat or 10 + i, quantity=1 + i % 3, rank=rank,
                 seller=f"Tenno{i}", status=status, reputation=i)


ITEM = Item(slug="mirage_prime_set", name="Mirage Prime Set", icon="x/y.png")


class MarketViewTests(unittest.TestCase):
    def test_price_report_sections_chain(self):
        orders = [order(i, "sell") for i in range(10)] + [order(i, "buy") for i in range(4)]
        report = build_report(ITEM.name, ITEM.slug, orders, history={"volume_48h": 5, "avg_price_48h": 20.0,
                                                                      "median_48h": 19.0}, ladder_size=12)
        sections = M.report_sections(report, ITEM, platform="pc", my_price=15)
        labels = [s.label for s in sections]
        self.assertEqual(labels[:2], ["Overview", "Competition"])
        self.assertIn("Last 48h sales", labels)
        self.assertEqual(sections[0].pages[0].image, ITEM.image_url)
        self.assertTrue(any("Your price" in f.name for f in sections[0].pages[0].fields))
        v = SectionView(sections, footer_text="f", chain=True)
        steps = 0
        while not v.next_btn.disabled:
            click(v, v.next_btn); steps += 1
        self.assertEqual(steps, len(sections) - 1)

    def test_price_sections_skip_empty_parts(self):
        report = build_report(ITEM.name, ITEM.slug, [], history=None)
        labels = [s.label for s in M.report_sections(report, ITEM, platform="pc")]
        self.assertNotIn("By mod rank", labels)
        self.assertNotIn("Last 48h sales", labels)

    def test_order_pages_paginate_with_whispers(self):
        orders = [order(i, "buy") for i in range(20)]
        pages = M.order_pages(ITEM, orders, side="buy", rank=None)
        self.assertEqual(len(pages), 4)                                   # 6 per page
        for p in pages:
            self.assertIn("/w Tenno", p.description)
            self.assertLessEqual(len(p.description), 4096)
        self.assertIn("` 7.`", pages[1].description)                      # numbering continues
        self.assertIn("**20** online buyers", pages[0].description)

    def test_order_pages_sell_side_is_cheapest_first_and_empty_case(self):
        pages = M.order_pages(ITEM, [order(i, "sell") for i in (5, 1, 3)], side="sell", rank=None)
        self.assertLess(pages[0].description.index("Tenno1"), pages[0].description.index("Tenno5"))
        empty = M.order_pages(ITEM, [], side="buy", rank=None)
        self.assertEqual(len(empty), 1)
        self.assertIn("No online orders", empty[0].description)

    def test_order_pages_respects_rank_and_online_filters(self):
        orders = [order(1, "buy", rank=0), order(2, "buy", rank=10), order(3, "buy", rank=10, status="offline")]
        pages = M.order_pages(ITEM, orders, side="buy", rank=10)
        self.assertIn("Tenno2", pages[0].description)
        self.assertNotIn("Tenno1", pages[0].description)
        self.assertNotIn("Tenno3", pages[0].description)

    def test_trend_pages(self):
        trends = {f"Item {i}": {"count": 50 - i, "average_platinum": i, "total_quantity": 60 - i} for i in range(23)}
        pages = M.trend_pages(trends)
        self.assertEqual(len(pages), 3)
        self.assertIn("\U0001F947", pages[0].description)
        self.assertIn("`11.`", pages[1].description)
        self.assertIn("not enough", M.trend_pages({})[0].description.lower())

    def test_my_orders_sections(self):
        def o(i, t):
            return {"item": f"Item {i}", "order_data": {"id": f"id{i}", "type": t, "platinum": 10 * i,
                                                        "quantity": 2, "rank": 3 if i % 2 else None,
                                                        "visible": i % 3 != 0}}
        orders = [o(i, "sell" if i % 2 else "buy") for i in range(1, 13)]
        secs = M.my_orders_sections(orders)
        self.assertEqual([s.label for s in secs], ["All orders (12)", "Selling (6)", "Buying (6)"])
        self.assertEqual(len(secs[0].pages), 3)                           # 5 per page
        self.assertIn("ID `id1`", secs[0].pages[0].description)
        self.assertIn("hidden", secs[0].pages[0].description)
        self.assertEqual(len(M.my_orders_sections([o(1, "sell")])), 1)    # no filter needed
        self.assertIn("no orders", M.my_orders_sections([])[0].pages[0].description)

    def test_ducats_embed(self):
        e = M.ducats_embed({"name": "Mirage Prime Set", "ducats": 100, "average_platinum": 5.0}, ITEM)
        verdict = [f.value for f in e.fields if f.name == "Verdict"][0]
        self.assertIn("Ducats look", verdict)
        e = M.ducats_embed({"name": "X", "ducats": 15, "average_platinum": 45.0})
        self.assertIn("Platinum", [f.value for f in e.fields if f.name == "Verdict"][0])
        e = M.ducats_embed({"name": "X", "ducats": 0, "average_platinum": 45.0})
        self.assertIn("can't be sold", [f.value for f in e.fields if f.name == "Verdict"][0])

    def test_listing_embeds(self):
        o = SimpleNamespace(id="abc", item_name="Serration", order_type="sell", platinum=30, quantity=2,
                            rank=10, perTrade=None, mod_charges=None, subtype=None, amberStars=None,
                            cyanStars=None, visible=False)
        e = M.listed_embed(o)
        names = [f.name for f in e.fields]
        self.assertIn("Rank", names)
        self.assertNotIn("Charges", names)
        self.assertIn("Hidden", [f.value for f in e.fields if f.name == "Visibility"][0])
        u = M.updated_embed(o, frozenset({"platinum"}))
        self.assertTrue([f.value for f in u.fields if f.name == "Price"][0].startswith("\u270F"))
        self.assertFalse([f.value for f in u.fields if f.name == "Quantity"][0].startswith("\u270F"))
        self.assertIn("abc", M.removed_embed("abc").description)


if __name__ == "__main__":
    unittest.main()
