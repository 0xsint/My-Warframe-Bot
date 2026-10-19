import asyncio
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from cryptography.fernet import Fernet

from market.store import AllowList, StoreError, UserStore, make_cipher


class FakeCollection:
    def __init__(self):
        self.docs = {}

    async def find_one(self, q):
        return self.docs.get(q["_id"])

    async def update_one(self, q, update, upsert=False):
        doc = self.docs.setdefault(q["_id"], {"_id": q["_id"], **update.get("$setOnInsert", {})})
        doc.update(update["$set"])

    async def delete_one(self, q):
        return SimpleNamespace(deleted_count=1 if self.docs.pop(q["_id"], None) else 0)


class UserStoreTests(unittest.TestCase):
    def setUp(self):
        self.col = FakeCollection()
        self.store = UserStore(self.col, Fernet(Fernet.generate_key()))

    def test_round_trip_and_encrypted_at_rest(self):
        async def go():
            await self.store.save(1, "a@b.com", "hunter2")
            raw = self.col.docs[1]
            self.assertNotIn("a@b.com", str(raw))
            self.assertNotIn("hunter2", str(raw))
            creds = await self.store.get(1)
            self.assertEqual((creds.email, creds.password), ("a@b.com", "hunter2"))
        asyncio.run(go())

    def test_argon2_hashes_stored_and_verify(self):
        async def go():
            await self.store.save(1, "A@b.com", "hunter2")
            doc = self.col.docs[1]
            self.assertTrue(doc["password_hash"].startswith("$argon2id$"))
            self.assertTrue(doc["email_hash"].startswith("$argon2id$"))
            self.assertNotIn("hunter2", doc["password_hash"])
            self.assertTrue(await self.store.verify(1, email="a@B.com", password="hunter2"))
            self.assertFalse(await self.store.verify(1, password="wrong"))
            self.assertFalse(await self.store.verify(1, email="x@y.com"))
            self.assertFalse(await self.store.verify(2, password="hunter2"))
            self.assertFalse(await self.store.verify(1))
        asyncio.run(go())

    def test_jwt_encrypted_and_replaceable(self):
        async def go():
            await self.store.save(1, "a@b.com", "pw", jwt="tok1")
            self.assertNotIn("tok1", str(self.col.docs[1]))
            self.assertEqual((await self.store.get(1)).jwt, "tok1")
            await self.store.save_token(1, "tok2")
            creds = await self.store.get(1)
            self.assertEqual((creds.jwt, creds.password), ("tok2", "pw"))
        asyncio.run(go())

    def test_update_keeps_created_at(self):
        async def go():
            await self.store.save(1, "a@b.com", "x")
            created = self.col.docs[1]["created_at"]
            await self.store.save(1, "a@b.com", "y")
            self.assertEqual(self.col.docs[1]["created_at"], created)
            self.assertEqual((await self.store.get(1)).password, "y")
        asyncio.run(go())

    def test_missing_and_delete(self):
        async def go():
            self.assertIsNone(await self.store.get(5))
            await self.store.save(5, "a", "b")
            self.assertTrue(await self.store.delete(5))
            self.assertFalse(await self.store.delete(5))
        asyncio.run(go())

    def test_wrong_key_raises(self):
        async def go():
            await self.store.save(1, "a@b.com", "pw")
            other = UserStore(self.col, Fernet(Fernet.generate_key()))
            with self.assertRaises(StoreError):
                await other.get(1)
        asyncio.run(go())

    def test_missing_key(self):
        os.environ.pop("WFM_ENCRYPT_KEY", None)
        with self.assertRaises(StoreError):
            make_cipher()
        with self.assertRaises(StoreError):
            make_cipher("not-a-key")


class SellerTokenTests(unittest.TestCase):
    def _seller(self, statuses, saved):
        from market.seller import MarketSeller

        async def on_token(t):
            saved.append(t)

        s = MarketSeller("a@b.com", "pw", jwt="old", on_token=on_token)
        calls = []

        async def fake_send(method, path, **kw):
            calls.append(path)
            return statuses.pop(0), {}, {"ok": 1}

        async def fake_signin(*a, **k):
            s._jwt = "new"
            await on_token("new")
            return "new"

        s._send_Data = fake_send
        s._refresh_csrf = lambda: asyncio.sleep(0)
        s._csrf = "x"
        s.login = fake_signin
        return s, calls

    def test_saved_token_used_without_login(self):
        saved = []
        s, calls = self._seller([200], saved)
        asyncio.run(s._call("GET", "/profile/orders"))
        self.assertEqual(saved, [])
        self.assertEqual(s.jwt, "old")

    def test_login_only_after_401(self):
        saved = []
        s, calls = self._seller([401, 200], saved)
        asyncio.run(s._call("GET", "/profile/orders"))
        self.assertEqual(saved, ["new"])
        self.assertEqual(s.jwt, "new")
        self.assertEqual(len(calls), 2)


class AllowListTests(unittest.TestCase):
    def _file(self, content):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        f.write(content)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_dict_and_list_formats(self):
        self.assertTrue(AllowList(self._file(json.dumps({"allowed_user_ids": [1, "2"]}))).is_allowed(2))
        self.assertTrue(AllowList(self._file("[7]")).is_allowed(7))
        self.assertFalse(AllowList(self._file("[7]")).is_allowed(8))

    def test_fails_closed(self):
        self.assertFalse(AllowList("/nonexistent/allowed.json").is_allowed(1))
        self.assertFalse(AllowList(self._file("not json")).is_allowed(1))

    def test_reloads_on_change(self):
        path = self._file("[1]")
        allow = AllowList(path)
        self.assertFalse(allow.is_allowed(2))
        with open(path, "w") as f:
            f.write("[1, 2]")
        os.utime(path, (1, 1_000_000_000))      # force a distinct mtime
        self.assertTrue(allow.is_allowed(2))


if __name__ == "__main__":
    unittest.main()
