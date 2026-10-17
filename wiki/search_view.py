"""/wf search results: paginated list + a dropdown to open any result."""
from __future__ import annotations

from typing import Awaitable, Callable

import nextcord

from ui.paginator import BaseView
from ui.text import chunk

from .embeds import FOOTER_ICON, FOOTER_TEXT, build_search_embed

PER_PAGE = 10   # the dropdown always lists the results of the page you are looking at


class SearchView(BaseView):
    """``on_pick(interaction, title)`` is awaited when the user chooses a result."""

    def __init__(self, query: str, results: list[tuple[str, str]],
                 on_pick: Callable[[nextcord.Interaction, str], Awaitable[None]],
                 *, per_page: int = PER_PAGE, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.query = query
        self.total = len(results)
        self.pages = chunk(results, per_page)
        self.per_page = per_page
        self.on_pick = on_pick
        self.index = 0

        self.select = nextcord.ui.Select(placeholder="Open a result", options=self._options())
        self.select.custom_id = "search_select"
        self.select.callback = self._on_select
        self.add_item(self.select)
        self.prev_btn = self.next_btn = None
        if len(self.pages) > 1:
            self.prev_btn = nextcord.ui.Button(label="Prev", style=nextcord.ButtonStyle.secondary)
            self.next_btn = nextcord.ui.Button(label="Next", style=nextcord.ButtonStyle.secondary)
            self.prev_btn.callback = self._on_prev
            self.prev_btn.custom_id = "search_prev"
            self.next_btn.custom_id = "search_next"
            self.next_btn.callback = self._on_next
            self.add_item(self.prev_btn)
            self.add_item(self.next_btn)
        self._sync()

    def _options(self) -> list[nextcord.SelectOption]:
        start = self.index * self.per_page
        return [
            nextcord.SelectOption(label=f"{start + i}. {title}"[:100], value=title[:100])
            for i, (title, _url) in enumerate(self.pages[self.index], start=1)
        ]

    def current_embed(self) -> nextcord.Embed:
        e = build_search_embed(self.query, self.pages[self.index], start=self.index * self.per_page + 1)
        text = f"{self.total} results"
        if len(self.pages) > 1:
            text = f"Page {self.index + 1}/{len(self.pages)} · {text}"
        e.set_footer(text=f"{text} · {FOOTER_TEXT}", icon_url=FOOTER_ICON)
        return e

    def _sync(self) -> None:
        self.select.options = self._options()
        if self.prev_btn is not None:
            self.prev_btn.disabled = self.index <= 0
            self.next_btn.disabled = self.index >= len(self.pages) - 1

    async def _refresh(self, interaction: nextcord.Interaction) -> None:
        self._sync()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _on_prev(self, interaction: nextcord.Interaction) -> None:
        self.index = max(0, self.index - 1)
        await self._refresh(interaction)

    async def _on_next(self, interaction: nextcord.Interaction) -> None:
        self.index = min(len(self.pages) - 1, self.index + 1)
        await self._refresh(interaction)

    async def _on_select(self, interaction: nextcord.Interaction) -> None:
        title = self.select.values[0]
        self.stop()                      # this list is replaced by the page the user picked
        await self.on_pick(interaction, title)
