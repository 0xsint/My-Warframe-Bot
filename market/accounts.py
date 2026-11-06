"""Per-Discord-user warframe.market account links (/wm link).

Each user's warframe.market token is stored encrypted (Fernet: AES + HMAC)
in ``data/accounts.json``. Passwords are never stored: email/password sign-in
is used once to obtain a token, and only the token is kept.

The encryption key comes from WFBOT_SECRET_KEY, or is generated once into
``data/secret.key``. Anyone with that key and the accounts file can act on the
linked accounts, so keep both private (data/ is in .gitignore).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("warframebot.market.accounts")



def default_dir() -> Path:
    return Path(os.getenv("WFBOT_DATA_DIR", "data"))


@dataclass
class LinkedAccount:
    discord_id: int
    ingame_name: Optional[str]          # None when warframe.market didn't tell us
    platform: str = "pc"
    linked_at: float = 0.0


def _load_key(data_dir: Path) -> bytes:
    env = os.getenv("WFBOT_SECRET_KEY")
    if env:
        return env.encode()
    path = data_dir / "secret.key"
    if path.exists():
        return path.read_bytes().strip()
    data_dir.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
        path.chmod(0o600)
    except OSError:                                  # e.g. Windows
        pass
    log.warning("Generated a new account encryption key at %s. Back it up: without it, "
                "linked accounts can't be read and users must /wm link again.", path)
    return key


class AccountStore:
    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.dir = Path(data_dir) if data_dir is not None else default_dir()
        self.path = self.dir / "accounts.json"
        self._fernet = Fernet(_load_key(self.dir))
        self._rows: dict[str, dict] = self._read()

    # ------------------------------------------------------------ storage
    def _read(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            log.exception("Could not read %s; starting with no linked accounts", self.path)
            return {}

    def _write(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".accounts.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._rows, fh, indent=1)
            os.replace(tmp, self.path)               # atomic: never a half-written file
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ------------------------------------------------------------- public
    def link(self, discord_id: int, token: str, *, ingame_name: Optional[str], platform: str = "pc") -> LinkedAccount:
        acc = LinkedAccount(discord_id, ingame_name, platform, time.time())
        row = asdict(acc)
        row["token"] = self._fernet.encrypt(token.encode()).decode()
        self._rows[str(discord_id)] = row
        self._write()
        return acc

    def unlink(self, discord_id: int) -> bool:
        if self._rows.pop(str(discord_id), None) is None:
            return False
        self._write()
        return True

    def get(self, discord_id: int) -> Optional[LinkedAccount]:
        row = self._rows.get(str(discord_id))
        if not row:
            return None
        return LinkedAccount(int(row["discord_id"]), row.get("ingame_name"), row.get("platform") or "pc",
                             float(row.get("linked_at") or 0))

    def token(self, discord_id: int) -> Optional[str]:
        row = self._rows.get(str(discord_id))
        if not row:
            return None
        try:
            return self._fernet.decrypt(row["token"].encode()).decode()
        except (InvalidToken, KeyError):
            log.warning("Stored token for a linked account can't be decrypted (key changed?)")
            return None

    def __len__(self) -> int:
        return len(self._rows)
