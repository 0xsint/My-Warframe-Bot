"""/wm link: encrypted per-user storage, token clean-up, the link form, and
that every user's commands act only on their own account."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import nextcord  # noqa: E402
from nextcord.ext import commands  # noqa: E402

from fakes import ITEMS, RouteSession  # noqa: E402
from market.accounts import AccountStore  # noqa: E402
from market.client import WarframeMarketClient  # noqa: E402
from market.link_ui import TokenModal, clean_token, verify  # noqa: E402
from market.seller import MarketSeller, SellerError  # noqa: E402

LOOP = asyncio.new_event_loop()


def run(coro):
    return LOOP.run_until_complete(coro)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("WFBOT_SECRET_KEY", raising=False)
    return AccountStore(tmp_path)


# ------------------------------------------------------------------- store
def test_tokens_are_encrypted_at_rest_and_survive_restart(store, tmp_path):
    store.link(42, "eyJsecret-token", ingame_name="Tenno", platform="pc")
    raw = (tmp_path / "accounts.json").read_text()
    assert "eyJsecret-token" not in raw and json.loads(raw)["42"]["ingame_name"] == "Tenno"
    again = AccountStore(tmp_path)                       # same key file -> readable
    assert again.token(42) == "eyJsecret-token" and again.get(42).ingame_name == "Tenno"
    assert again.token(7) is None and again.get(7) is None


def test_unlink_and_wrong_key(store, tmp_path, monkeypatch):
    store.link(1, "tok", ingame_name=None)
    assert store.unlink(1) and not store.unlink(1) and len(AccountStore(tmp_path)) == 0
    store.link(2, "tok", ingame_name=None)
    from cryptography.fernet import Fernet
    monkeypatch.setenv("WFBOT_SECRET_KEY", Fernet.generate_key().decode())
    assert AccountStore(tmp_path).token(2) is None       # can't decrypt -> treated as not linked


@pytest.mark.parametrize("raw", ["eyJabc.def", " JWT=eyJabc.def; Path=/", "JWT eyJabc.def",
                                 "Bearer eyJabc.def", '"eyJabc.def"'])
def test_clean_token(raw):
    assert clean_token(raw) == "eyJabc.def"


# -------------------------------------------------------------- link form
class FakeSeller(MarketSeller):
    answers: list = []

    async def _send(self, method, url, *, body=None, platform="pc", signin=False):
        self.seen = getattr(self, "seen", []) + [(method, url, self._jwt)]
        status, data = FakeSeller.answers.pop(0)
        return status, {}, data


class Sink:
    def __init__(self):
        self.sent = []
        self.user = SimpleNamespace(id=42)
        self.response = SimpleNamespace(defer=self._noop)
        self.followup = SimpleNamespace(send=self._send)

    async def _noop(self, **kw):
        pass

    async def _send(self, content=None, **kw):
        self.sent.append({"content": content, **kw})


def _modal(store, token):
    async def make():
        m = TokenModal(store, WarframeMarketClient(RouteSession({"/v2/items": ITEMS})))
        m.token_input._inputed_value = token
        return m
    return run(make())


def test_link_form_verifies_and_stores(store, monkeypatch):
    monkeypatch.setattr("market.link_ui.MarketSeller", FakeSeller)
    FakeSeller.answers = [(200, {"data": {"ingameName": "Tenno", "platform": "ps4"}})]
    modal, ix = _modal(store, "JWT=eyJgood"), Sink()
    run(modal.callback(ix))
    assert store.token(42) == "eyJgood" and store.get(42).platform == "ps4"
    assert "Tenno" in ix.sent[-1]["embed"].description and ix.sent[-1]["ephemeral"]


def test_link_form_rejects_bad_token(store, monkeypatch):
    monkeypatch.setattr("market.link_ui.MarketSeller", FakeSeller)
    FakeSeller.answers = [(401, {"error": "nope"})]
    modal, ix = _modal(store, "eyJexpired-token"), Sink()
    run(modal.callback(ix))
    assert store.token(42) is None and "Couldn't link" in ix.sent[-1]["embed"].title


def test_verify_falls_back_when_me_is_missing(monkeypatch):
    FakeSeller.answers = [(404, {}), (200, {"data": []})]
    s = FakeSeller(WarframeMarketClient(RouteSession({"/v2/items": ITEMS})), token="t")
    assert run(verify(s)) == {}
    FakeSeller.answers = [(500, {})]
    with pytest.raises(SellerError):
        run(verify(s))


# ----------------------------------------------------- per-user isolation
def test_each_user_gets_their_own_account(store, monkeypatch):
    monkeypatch.setenv("OWNER_ID", "1")
    monkeypatch.setenv("WFM_JWT", "owner-token")

    async def make():
        bot = commands.Bot(intents=nextcord.Intents.default())
        bot.market = WarframeMarketClient(RouteSession({"/v2/items": ITEMS}))
        bot.load_extension("cogs.market_commands")
        return bot.get_cog("MarketCommands")
    cog = run(make())
    cog._accounts = store

    async def scenario():
        ix = lambda uid: SimpleNamespace(user=SimpleNamespace(id=uid))

        assert cog.seller_for(ix(2)) is None                         # stranger, not linked
        assert cog.seller_for(ix(1)).token == "owner-token"          # owner falls back to .env
        store.link(2, "user2-token", ingame_name="Two")
        store.link(1, "owner-linked", ingame_name="One")
        assert cog.seller_for(ix(2)).token == "user2-token"
        assert cog.seller_for(ix(1)).token == "owner-linked"         # a linked account wins over .env
        first = cog.seller_for(ix(2))
        assert cog.seller_for(ix(2)) is first                        # cached per user
        store.link(2, "user2-new", ingame_name="Two")
        assert cog.seller_for(ix(2)).token == "user2-new"            # relinking swaps the session
        store.unlink(2)
        assert cog.seller_for(ix(2)) is None
    run(scenario())
