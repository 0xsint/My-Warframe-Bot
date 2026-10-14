"""Discord UI for AbilityPage: an overview embed + a section dropdown.

Every embed calls set_image(), which Discord renders as the big picture
*below* the text/fields (set_thumbnail would put it in the top-right corner).
"""
from __future__ import annotations

import nextcord

from .ability import AbilityPage, paginate

EMBED_COLOR = 0x4FC3F7
FIELD_CHARS = 1000         # embed field hard limit is 1024
OVERVIEW = "Overview"

# Show the tip-like sections first, bugs last.
_SECTION_ORDER = ["Info", "Kit Interactions", "Synergies", "Tips", "Notes", "Trivia", "Bugs"]


def _short(text: str, limit: int = FIELD_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class AbilityView(nextcord.ui.View):
    def __init__(self, page: AbilityPage, *, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.page = page
        self.message = None
        self.section = OVERVIEW
        self.index = 0
        self.chunks: dict[str, list[str]] = {
            name: paginate(page.sections[name])
            for name in _SECTION_ORDER if page.sections.get(name)
        }

        options = [nextcord.SelectOption(label=OVERVIEW, value=OVERVIEW, default=True,
                                         description="Stats and summary")]
        for name, chunks in self.chunks.items():
            suffix = f"{len(chunks)} pages" if len(chunks) > 1 else "1 page"
            options.append(nextcord.SelectOption(label=name, value=name, description=suffix))
        self.select = nextcord.ui.Select(placeholder="Pick a section", options=options)
        self.select.callback = self._on_select
        self.select.custom_id = "select_option"
        self.prev_btn = nextcord.ui.Button(label="Prev", style=nextcord.ButtonStyle.secondary)
        self.next_btn = nextcord.ui.Button(label="Next", style=nextcord.ButtonStyle.secondary)
        self.prev_btn.callback = self._on_prev
        self.next_btn.custom_id = "next_btn"
        self.prev_btn.custom_id = "prev_btn"
        self.next_btn.callback = self._on_next
        self.add_item(self.select)
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        self._sync_buttons()

    # ------------------------------------------------------------------ embeds
    def _base(self) -> nextcord.Embed:
        e = nextcord.Embed(title=self.page.title, url=self.page.url, color=EMBED_COLOR)
        if self.page.image_url:
            e.set_image(url=self.page.image_url)       # <- picture below the data
        return e

    def overview_embed(self) -> nextcord.Embed:
        p = self.page
        e = self._base()
        e.description = p.description or "Warframe ability"
        meta = []
        if p.energy_cost:
            meta.append(f"**Energy:** {p.energy_cost}")
        if p.introduced:
            meta.append(f"**Introduced:** {p.introduced}")
        if meta:
            e.add_field(name="Details", value=" · ".join(meta), inline=False)
        for label, values in p.stats.items():
            e.add_field(name=label, value=_short("\n".join(values)), inline=False)
        if self.chunks:
            e.set_footer(text="Use the dropdown for: " + ", ".join(self.chunks))
        return e

    def section_embed(self) -> nextcord.Embed:
        chunks = self.chunks[self.section]
        e = self._base()
        e.description = chunks[self.index]
        footer = f"{self.section}"
        if len(chunks) > 1:
            footer += f" · page {self.index + 1}/{len(chunks)}"
        e.set_footer(text=footer)
        return e

    def current_embed(self) -> nextcord.Embed:
        return self.overview_embed() if self.section == OVERVIEW else self.section_embed()

    # ---------------------------------------------------------------- plumbing
    def _sync_buttons(self) -> None:
        pages = 1 if self.section == OVERVIEW else len(self.chunks[self.section])
        self.prev_btn.disabled = self.index <= 0
        self.next_btn.disabled = self.index >= pages - 1
        for opt in self.select.options:
            opt.default = opt.value == self.section

    async def _refresh(self, interaction: nextcord.Interaction) -> None:
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _on_select(self, interaction: nextcord.Interaction) -> None:
        self.section = self.select.values[0]
        self.index = 0
        await self._refresh(interaction)

    async def _on_prev(self, interaction: nextcord.Interaction) -> None:
        self.index = max(0, self.index - 1)
        await self._refresh(interaction)

    async def _on_next(self, interaction: nextcord.Interaction) -> None:
        if self.section != OVERVIEW:
            self.index = min(len(self.chunks[self.section]) - 1, self.index + 1)
        await self._refresh(interaction)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass
