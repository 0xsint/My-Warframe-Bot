"""Reusable "dropdown + Prev/Next" embed views.

``SectionView`` is the same interaction the ability page uses, made generic:

    * a dropdown to jump between named sections (hidden when there is only one)
    * Prev / Next buttons to flip through the pages of the current section
      (hidden when everything fits on one page)

Every section is a list of ready-made embeds, so any command (wiki or market)
just builds its pages and hands them over:

    view = SectionView([Section("Overview", [embed]), Section("Tips", tip_pages)],
                       footer_text="data from warframe.market")
    await interaction.send(**view_kwargs(view))
    view.message = await interaction.original_message()   # lets it grey out on timeout

``chain=True`` makes Prev/Next walk through *all* pages of *all* sections in
order (good for a report where each section is one page); the default keeps
Prev/Next inside the current section, like the ability view.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import nextcord

MAX_SELECT_OPTIONS = 25      # Discord limit for one dropdown


@dataclass
class Section:
    label: str                           # dropdown label (max 100 chars)
    pages: list                          # list[nextcord.Embed]
    description: str = ""                # dropdown sub-text; defaults to "N pages"
    emoji: Optional[str] = None


def view_kwargs(view) -> dict:
    """kwargs for send(): the embed, plus the view only when it actually has
    controls (the key is left out entirely otherwise, since send() rejects None)."""
    kwargs = {"embed": view.current_embed()}
    if view.children:
        kwargs["view"] = view
    return kwargs


def _unique_sections(sections: Sequence[Section]) -> list[Section]:
    seen: dict[str, int] = {}
    out: list[Section] = []
    for s in sections:
        if not s.pages:
            continue
        key = s.label[:100]
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:                              # dropdown values must be unique
            s = Section(f"{key[:94]} ({seen[key]})", s.pages, s.description, s.emoji)
        out.append(s)
    return out[:MAX_SELECT_OPTIONS]


class BaseView(nextcord.ui.View):
    """Keeps a handle on the message so the controls grey out on timeout."""

    def __init__(self, *, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.message = None

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except Exception:                          # deleted / ephemeral / expired token
                pass


class SectionView(BaseView):
    def __init__(self, sections: Sequence[Section], *, footer_text: Optional[str] = None,
                 footer_icon: Optional[str] = None, chain: bool = False,
                 select_placeholder: str = "Pick a section", timeout: float = 600):
        super().__init__(timeout=timeout)
        self.sections = _unique_sections(sections)
        if not self.sections:
            raise ValueError("SectionView needs at least one section with a page")
        self.footer_text = footer_text
        self.footer_icon = footer_icon
        self.chain = chain
        self.section_idx = 0
        self.page_idx = 0
        self.select = None
        self.prev_btn = None
        self.next_btn = None

        if len(self.sections) > 1:
            options = []
            for i, s in enumerate(self.sections):
                n = len(s.pages)
                desc = s.description or (f"{n} pages" if n > 1 else "1 page")
                kw = {"emoji": s.emoji} if s.emoji else {}
                options.append(nextcord.SelectOption(label=s.label[:100], value=str(i),
                                                     description=desc[:100], default=(i == 0), **kw))
            self.select = nextcord.ui.Select(placeholder=select_placeholder, options=options)
            self.select.custom_id = "section_select"
            self.select.callback = self._on_select
            self.add_item(self.select)

        if self._total_pages() > 1 and (self.chain or any(len(s.pages) > 1 for s in self.sections)):
            self.prev_btn = nextcord.ui.Button(label="Prev", style=nextcord.ButtonStyle.secondary)
            self.next_btn = nextcord.ui.Button(label="Next", style=nextcord.ButtonStyle.secondary)
            self.prev_btn.custom_id = "section_prev"
            self.next_btn.custom_id = "section_next"
            self.prev_btn.callback = self._on_prev
            self.next_btn.callback = self._on_next
            self.add_item(self.prev_btn)
            self.add_item(self.next_btn)
        self._sync()

    # --------------------------------------------------------------- position
    def _total_pages(self) -> int:
        return sum(len(s.pages) for s in self.sections)

    def _position(self) -> tuple[int, int]:
        """(zero-based position, total) within the range Prev/Next walks."""
        if self.chain:
            before = sum(len(s.pages) for s in self.sections[:self.section_idx])
            return before + self.page_idx, self._total_pages()
        return self.page_idx, len(self.sections[self.section_idx].pages)

    def _step(self, delta: int) -> None:
        pos, total = self._position()
        pos = max(0, min(total - 1, pos + delta))
        if not self.chain:
            self.page_idx = pos
            return
        for i, s in enumerate(self.sections):          # map flat position back to (section, page)
            if pos < len(s.pages):
                self.section_idx, self.page_idx = i, pos
                return
            pos -= len(s.pages)

    # ----------------------------------------------------------------- embeds
    def current_embed(self) -> nextcord.Embed:
        section = self.sections[self.section_idx]
        embed = section.pages[self.page_idx]
        pos, total = self._position()
        parts = []
        if len(self.sections) > 1:
            parts.append(section.label)
        if total > 1:
            parts.append(f"Page {pos + 1}/{total}")
        if self.footer_text:
            parts.append(self.footer_text)
        if parts:
            kw = {"text": " · ".join(parts)}
            if self.footer_icon:
                kw["icon_url"] = self.footer_icon
            embed.set_footer(**kw)
        return embed

    # --------------------------------------------------------------- plumbing
    def _sync(self) -> None:
        pos, total = self._position()
        if self.prev_btn is not None:
            self.prev_btn.disabled = pos <= 0
            self.next_btn.disabled = pos >= total - 1
        if self.select is not None:
            for opt in self.select.options:
                opt.default = opt.value == str(self.section_idx)

    async def _refresh(self, interaction: nextcord.Interaction) -> None:
        self._sync()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _on_select(self, interaction: nextcord.Interaction) -> None:
        self.section_idx = int(self.select.values[0])
        self.page_idx = 0
        await self._refresh(interaction)

    async def _on_prev(self, interaction: nextcord.Interaction) -> None:
        self._step(-1)
        await self._refresh(interaction)

    async def _on_next(self, interaction: nextcord.Interaction) -> None:
        self._step(+1)
        await self._refresh(interaction)
