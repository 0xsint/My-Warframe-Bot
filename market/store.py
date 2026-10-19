"""Per-user warframe.market credentials, kept in MongoDB and encrypted at rest,
plus the allow-list that decides who may link an account and post listings.

Env (.env):
    MONGODB_URI          e.g. mongodb://localhost:27017  (required)
    MONGODB_DB           database name (default "warframebot")
    WFM_ENCRYPT_KEY      Fernet key. Generate one with:
                         python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    ALLOWED_USERS_FILE   path to the allow-list (default allowed_user_ids.json)

Every stored secret (email, password and the JWT cookie) is kept encrypted; the
email and password also get a hash:
  * an argon2id hash (one-way) for checking a value later with verify();
  * a Fernet-encrypted copy, because signing in to warframe.market needs the
    real email and password, which a hash cannot give back.
Anyone who has both the database AND WFM_ENCRYPT_KEY can read the encrypted
copies: keep the key out of the database host and out of git. The Discord id
is the document key and is stored as is, since it is how users are looked up.
"""
from __future__ import annotations

import json
import logging
import os
import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("warframebot.store")

_hasher = PasswordHasher()      # argon2id with the library's recommended parameters

DEFAULT_ALLOWED_FILE = "allowed_user_ids.json"


class StoreError(Exception):
    pass


@dataclass(frozen=True)
class Credentials:
    email: str
    password: str
    jwt: Optional[str] = None       # the warframe.market JWT cookie from the last good sign-in


class AllowList:
    """Discord user ids allowed to sell. Re-read whenever the file changes, so
    adding someone does not need a bot restart. A missing or broken file allows
    nobody (fail closed).

    File format: {"allowed_user_ids": [123, 456]}  or just  [123, 456]
    """

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path or os.getenv("ALLOWED_USERS_FILE") or DEFAULT_ALLOWED_FILE)
        self._ids: frozenset[int] = frozenset()
        self._mtime: float | None = None

    def _reload(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            if self._mtime is not None or self._ids:
                log.warning("Allow-list %s is missing; nobody is allowed", self.path)
            self._ids, self._mtime = frozenset(), None
            return
        if mtime == self._mtime:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("allowed_user_ids", [])
            ids = frozenset(int(x) for x in data)
        except (ValueError, TypeError, OSError) as exc:
            log.error("Could not read allow-list %s (%s); nobody is allowed", self.path, exc)
            ids = frozenset()
        self._ids, self._mtime = ids, mtime

    def is_allowed(self, user_id: int) -> bool:
        self._reload()
        return int(user_id) in self._ids


def make_cipher(key: str | bytes | None = None) -> Fernet:
    key = key or os.getenv("WFM_ENCRYPT_KEY")
    if not key:
        raise StoreError("WFM_ENCRYPT_KEY is not set (see market/store.py for how to generate one)")
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise StoreError("WFM_ENCRYPT_KEY is not a valid Fernet key") from exc


class UserStore:
    """Encrypted warframe.market credentials, one document per Discord user id.

    `collection` is any Motor-style async collection (find_one / update_one /
    delete_one), which is also what the tests inject.
    """

    def __init__(self, collection: Any, cipher: Fernet) -> None:
        self._col = collection
        self._cipher = cipher

    @classmethod
    def from_env(cls) -> "UserStore":
        uri = os.getenv("MONGODB_URI")
        if not uri:
            raise StoreError("MONGODB_URI is not set")
        cipher = make_cipher()
        from motor.motor_asyncio import AsyncIOMotorClient      # imported lazily: optional for the read-only commands
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        return cls(client[os.getenv("MONGODB_DB", "warframebot")]["users"], cipher)

    def _enc(self, value: str) -> str:
        return self._cipher.encrypt(value.encode("utf-8")).decode("ascii")

    def _dec(self, token: str) -> str:
        try:
            return self._cipher.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise StoreError("Stored credentials can't be decrypted (was WFM_ENCRYPT_KEY changed?)") from exc

    @staticmethod
    async def _hash(value: str) -> str:
        return await asyncio.to_thread(_hasher.hash, value)     # argon2 is deliberately slow

    @staticmethod
    async def _check(stored: str, value: str) -> bool:
        def run() -> bool:
            try:
                return _hasher.verify(stored, value)
            except (VerificationError, InvalidHashError):
                return False
        return await asyncio.to_thread(run)

    async def save(self, user_id: int, email: str, password: str, jwt: str | None = None) -> None:
        now = time.time()
        email_hash, password_hash = await asyncio.gather(self._hash(email.strip().lower()),
                                                         self._hash(password))
        await self._col.update_one(
            {"_id": int(user_id)},
            {"$set": {"email": self._enc(email), "password": self._enc(password),
                      "email_hash": email_hash, "password_hash": password_hash, "updated_at": now,
                      **({"jwt": self._enc(jwt)} if jwt else {})},
             "$setOnInsert": {"created_at": now}},
            upsert=True)

    async def get(self, user_id: int) -> Optional[Credentials]:
        doc = await self._col.find_one({"_id": int(user_id)})
        if not doc:
            return None
        return Credentials(self._dec(doc["email"]), self._dec(doc["password"]),
                           self._dec(doc["jwt"]) if doc.get("jwt") else None)

    async def save_token(self, user_id: int, jwt: str) -> None:
        """Keep the newest JWT (encrypted) so the next session can skip signing in."""
        await self._col.update_one({"_id": int(user_id)},
                                   {"$set": {"jwt": self._enc(jwt), "jwt_updated_at": time.time()}})
        return self._enc(jwt)

    async def verify(self, user_id: int, *, email: str | None = None, password: str | None = None) -> bool:
        """True when every given value matches what the user stored (checked against the argon2 hashes)."""
        doc = await self._col.find_one({"_id": int(user_id)})
        if not doc or (email is None and password is None):
            return False
        if email is not None and not await self._check(doc.get("email_hash", ""), email.strip().lower()):
            return False
        if password is not None and not await self._check(doc.get("password_hash", ""), password):
            return False
        return True

    async def delete(self, user_id: int) -> bool:
        res = await self._col.delete_one({"_id": int(user_id)})
        return bool(res.deleted_count)
