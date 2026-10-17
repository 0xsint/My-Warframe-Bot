"""Minimal stand-ins for aiohttp / nextcord, installed only when the real
packages are missing, so the view logic can be tested offline."""
import sys
import types


def install() -> None:
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        aio = types.ModuleType("aiohttp")

        class ClientError(Exception): ...

        class ClientTimeout:
            def __init__(self, total=None): self.total = total

        aio.ClientError, aio.ClientTimeout, aio.ClientSession = ClientError, ClientTimeout, object
        sys.modules["aiohttp"] = aio

    try:
        import nextcord  # noqa: F401
        if hasattr(nextcord, "ui"):
            return
    except ImportError:
        pass

    nc = types.ModuleType("nextcord")

    class _Obj:
        def __init__(self, **kw): self.__dict__.update(kw)

    class Embed:
        def __init__(self, title=None, url=None, color=None, description=None):
            self.title, self.url, self.color, self.description = title, url, color, description
            self.fields, self.footer, self.image, self.thumbnail = [], _Obj(text=None, icon_url=None), None, None

        def add_field(self, *, name, value, inline=True):
            self.fields.append(_Obj(name=name, value=value, inline=inline)); return self

        def set_footer(self, *, text=None, icon_url=None):
            self.footer = _Obj(text=text, icon_url=icon_url); return self

        def set_image(self, *, url): self.image = url; return self
        def set_thumbnail(self, *, url): self.thumbnail = url; return self

    class SelectOption:
        def __init__(self, *, label, value=None, description=None, emoji=None, default=False):
            self.label, self.value, self.description, self.emoji, self.default = label, value, description, emoji, default

    class Select:
        def __init__(self, *, placeholder=None, options=None, **kw):
            self.placeholder, self.options, self.disabled, self._selected_values = placeholder, list(options or []), False, []
            self.callback = None

        @property
        def values(self): return self._selected_values

    class Button:
        def __init__(self, *, label=None, style=None, **kw):
            self.label, self.style, self.disabled, self.callback = label, style, False, None

    class View:
        def __init__(self, *, timeout=180):
            self.timeout, self.children, self.stopped = timeout, [], False

        def add_item(self, item): self.children.append(item)
        def stop(self): self.stopped = True

    nc.Embed, nc.SelectOption, nc.Interaction = Embed, SelectOption, object
    nc.ButtonStyle = _Obj(secondary="secondary")
    nc.ui = _Obj(View=View, Select=Select, Button=Button)
    sys.modules["nextcord"] = nc
