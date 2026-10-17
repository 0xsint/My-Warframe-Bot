"""Pure text helpers shared by the wiki and market views (no Discord imports,
so they can be unit-tested and imported without nextcord installed)."""
from __future__ import annotations

from typing import Sequence, TypeVar

T = TypeVar("T")

PAGE_CHARS = 3800  # Discord embed description hard limit is 4096


def paginate_lines(lines: Sequence[str], limit: int = PAGE_CHARS) -> list[str]:
    """Pack markdown lines into chunks <= limit chars without splitting a line
    (unless a single line is longer than the limit)."""
    pages: list[str] = []
    buf = ""
    for line in lines:
        while len(line) > limit:                       # pathological long line
            cut = line.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            if buf:
                pages.append(buf)
                buf = ""
            pages.append(line[:cut])
            line = line[cut:].lstrip()
        if len(buf) + len(line) + 1 > limit and buf:
            pages.append(buf)
            buf = ""
        buf += line + "\n"
    if buf.strip():
        pages.append(buf)
    return [p.rstrip() for p in pages]


def chunk(items: Sequence[T], size: int) -> list[list[T]]:
    """Split ``items`` into consecutive lists of at most ``size``."""
    if size < 1:
        raise ValueError("size must be >= 1")
    return [list(items[i:i + size]) for i in range(0, len(items), size)]
