"""Discord pop-up forms (modals) for /wm link.

The form contents are private to the user who opened it. Nothing typed into
these forms is ever logged; a password is used once to get a token and then
dropped.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import nextcord

from .accounts import AccountStore, LinkedAccount
from .client import WarframeMarketClient
from .embeds import GREEN, RED, _embed
from .seller import MarketSeller, SellerError

log = logging.getLogger("warframebot.market.link")

TOKEN_HELP = ("Sign in at warframe.market in your browser, open the developer tools "
              "(F12) → Application/Storage → Cookies → warframe.market, "
              "and copy the value of the **JWT** cookie.")

OnLinked = Callable[[int], Awaitable[None]]


def clean_token(raw: str) -> str:
    """Accept the bare token, or what people tend to paste around it:
    'JWT=eyJ...', 'JWT eyJ...', 'Bearer eyJ...', quotes, a trailing ';'."""
    t = raw.strip().strip('"\'').strip()
    t = re.sub(r"^(?:JWT\s*[=:]?|Bearer)\s*", "", t, flags=re.I)
    return t.split(";")[0].strip().strip('"\'')


async def verify(seller: MarketSeller) -> dict:
    """Prove the credentials work and return what we know about the account."""
    try:
        return await seller.whoami()
    except SellerError as exc:
        if not str(exc).startswith("404"):          # /me missing: check the token another way
            raise
    await seller.get_my_orders()
    return {}


def linked_embed(acc: LinkedAccount) -> nextcord.Embed:
    who = f"**{acc.ingame_name}**" if acc.ingame_name else "your account"
    e = _embed("warframe.market account linked", color=GREEN,
               description=(f"Linked {who} ({acc.platform.upper()}). You can now use `/wm list`, "
                            "`/wm my_orders`, `/wm update_order`, `/wm sold`, `/wm unlist` and "
                            "`/wm status` on it.\n\nYour token is stored encrypted and only used for "
                            "your own commands. `/wm unlink` removes it."))
    return e


def failed_embed(reason: str) -> nextcord.Embed:
    return _embed("Couldn't link that account", color=RED,
                  description=f"{reason}\n\n{TOKEN_HELP}")


class _LinkModal(nextcord.ui.Modal):
    def __init__(self, title: str, store: AccountStore, market: WarframeMarketClient,
                 on_linked: Optional[OnLinked] = None):
        super().__init__(title=title, timeout=600)
        self.store, self.market, self.on_linked = store, market, on_linked

    def make_seller(self) -> MarketSeller:
        raise NotImplementedError

    async def callback(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        seller = self.make_seller()
        try:
            if not seller.token:
                seller._jwt = await seller.login()
            me = await verify(seller)
            acc = self.store.link(interaction.user.id, seller.token,
                                  ingame_name=me.get("ingameName"),
                                  platform=str(me.get("platform") or "pc"))
        except SellerError as exc:
            await interaction.followup.send(embed=failed_embed(str(exc)), ephemeral=True)
            return
        except Exception:
            log.exception("Unexpected error while linking an account")
            await interaction.followup.send(embed=failed_embed("Unexpected error. Try again later."),
                                            ephemeral=True)
            return
        finally:
            await seller.close()
        log.info("Discord user %s linked a warframe.market account", interaction.user.id)
        if self.on_linked is not None:
            await self.on_linked(interaction.user.id)
        await interaction.followup.send(embed=linked_embed(acc), ephemeral=True)


class TokenModal(_LinkModal):
    def __init__(self, store, market, on_linked=None):
        super().__init__("Link warframe.market (token)", store, market, on_linked)
        self.token_input = nextcord.ui.TextInput(
            label="Your warframe.market JWT cookie", style=nextcord.TextInputStyle.paragraph,
            placeholder="eyJ0eXAiOiJKV1Qi...", min_length=20, max_length=4000, required=True)
        self.add_item(self.token_input)

    def make_seller(self) -> MarketSeller:
        return MarketSeller(self.market, token=clean_token(self.token_input.value or ""))


class PasswordModal(_LinkModal):
    def __init__(self, store, market, on_linked=None):
        super().__init__("Link warframe.market (sign in)", store, market, on_linked)
        self.email_input = nextcord.ui.TextInput(label="warframe.market email", max_length=200,
                                                 placeholder="you@example.com", required=True)
        self.password_input = nextcord.ui.TextInput(
            label="Password (used once, never stored)", max_length=200, required=True)
        self.add_item(self.email_input)
        self.add_item(self.password_input)

    def make_seller(self) -> MarketSeller:
        return MarketSeller(self.market, email=(self.email_input.value or "").strip(),
                            password=self.password_input.value or "")


def account_embed(acc: Optional[LinkedAccount], *, owner_fallback: bool) -> nextcord.Embed:
    if acc is None:
        text = "No warframe.market account is linked to you. Use `/wm link`."
        if owner_fallback:
            text += "\nAs the bot owner, your commands use the account from the bot's `.env`."
        return _embed("Your warframe.market account", description=text)
    when = datetime.fromtimestamp(acc.linked_at, tz=timezone.utc)
    e = _embed("Your warframe.market account", color=GREEN,
               description=f"Linked: **{acc.ingame_name or 'name unknown'}** ({acc.platform.upper()})")
    if acc.ingame_name:
        e.url = f"https://warframe.market/profile/{acc.ingame_name.lower()}"
    e.add_field(name="Linked", value=f"<t:{int(when.timestamp())}:R>", inline=True)
    e.set_footer(text="/wm unlink removes the stored token")
    return e
