"""Lightweight client + parser for scraping the official WARFRAME Wiki
(https://wiki.warframe.com), which runs on MediaWiki."""

from .client import PageNotFoundError, WarframeWikiClient, WikiError
from .parser import InfoboxSection, WikiPage, parse_page

__all__ = [
    "WarframeWikiClient",
    "WikiError",
    "PageNotFoundError",
    "WikiPage",
    "InfoboxSection",
    "parse_page",
]
