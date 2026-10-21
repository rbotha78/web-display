import http.client
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from webdisplay.app import Application, make_handler


class ApiKeyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.app = Application(root / "config.json", root / "pairing", root / "target", Path("/bin/true"))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        _, payload, headers = self.call("POST", "/api/pair", {"code": self.app.pairing_code, "password": "a secure password"})
        self.session = {"Cookie": headers["Set-Cookie"].split(";", 1)[0], "X-CSRF-Token": payload["csrf"]}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def call(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        data = json.dumps(body).encode() if body is not None else None
        connection.request(method, path, body=data, headers={"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, json.loads(raw) if raw else {}, dict(response.getheaders())

    def mint(self, name="My PC"):
        status, payload, _ = self.call("POST", "/api/keys", {"name": name}, self.session)
        self.assertEqual(status, 201)
        return payload

    def test_key_authenticates_without_cookie_or_csrf(self):
        key = self.mint()["key"]
        status, payload, _ = self.call("GET", "/api/status", headers={"Authorization": f"Bearer {key}"})
        self.assertEqual(status, 200)
        self.assertIn("hostname", payload)
        self.app.run_action = lambda action: None
        status, _, _ = self.call("POST", "/api/reboot", {}, {"Authorization": f"Bearer {key}"})
        self.assertEqual(status, 202)

    def test_only_a_hash_is_stored(self):
        key = self.mint()["key"]
        stored = Path(self.directory.name, "config.json").read_text()
        self.assertNotIn(key, stored)

    def test_bad_key_is_rejected_and_throttled(self):
        for _ in range(5):
            status, _, _ = self.call("GET", "/api/status", headers={"Authorization": "Bearer wdk_wrong"})
            self.assertEqual(status, 401)
        status, _, _ = self.call("GET", "/api/status", headers={"Authorization": "Bearer wdk_wrong"})
        self.assertEqual(status, 429)

    def test_keys_cannot_manage_keys(self):
        key = self.mint()["key"]
        bearer = {"Authorization": f"Bearer {key}"}
        self.assertEqual(self.call("POST", "/api/keys", {"name": "x"}, bearer)[0], 401)
        self.assertEqual(self.call("GET", "/api/keys", headers=bearer)[0], 401)
        self.assertEqual(self.call("DELETE", "/api/keys/abcd", headers=bearer)[0], 401)

    def test_minting_requires_csrf(self):
        headers = {"Cookie": self.session["Cookie"]}
        self.assertEqual(self.call("POST", "/api/keys", {"name": "x"}, headers)[0], 403)

    def test_list_and_revoke(self):
        minted = self.mint()
        status, payload, _ = self.call("GET", "/api/keys", headers=self.session)
        self.assertEqual([k["name"] for k in payload["keys"]], ["My PC"])
        self.assertNotIn("hash", payload["keys"][0])
        bearer = {"Authorization": f"Bearer {minted['key']}"}
        self.assertEqual(self.call("GET", "/api/status", headers=bearer)[0], 200)
        status, _, _ = self.call("DELETE", f"/api/keys/{minted['id']}", headers=self.session)
        self.assertEqual(status, 200)
        self.assertEqual(self.call("GET", "/api/status", headers=bearer)[0], 401)
        self.assertEqual(self.call("DELETE", f"/api/keys/{minted['id']}", headers=self.session)[0], 400)

    def test_name_and_count_limits(self):
        self.assertEqual(self.call("POST", "/api/keys", {"name": "<script>"}, self.session)[0], 400)
        for number in range(10):
            self.mint(f"pc {number}")
        self.assertEqual(self.call("POST", "/api/keys", {"name": "one more"}, self.session)[0], 400)

    def test_logout_clears_the_session(self):
        status, _, headers = self.call("POST", "/api/logout", {}, self.session)
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(self.call("GET", "/api/status", headers=self.session)[0], 401)

    def test_keys_survive_other_config_changes(self):
        key = self.mint()["key"]
        self.call("PUT", "/api/config", {"url": "https://example.com"}, self.session)
        self.assertEqual(self.call("GET", "/api/status", headers={"Authorization": f"Bearer {key}"})[0], 200)

    def login_token(self, key):
        status, payload, _ = self.call(
            "POST", "/api/admin-login-token", {}, {"Authorization": f"Bearer {key}"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["expires_in"], 60)
        return payload["token"]

    def test_manager_login_is_single_use_and_keeps_key_permissions_and_csrf(self):
        minted = self.mint()
        token = self.login_token(minted["key"])
        status, payload, headers = self.call("POST", "/api/admin-login", {"token": token})
        self.assertEqual(status, 200)
        self.assertFalse(payload["can_manage_keys"])
        self.assertIn("Secure; HttpOnly; SameSite=Strict", headers["Set-Cookie"])
        cookie = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
        self.assertEqual(self.call("POST", "/api/admin-login", {"token": token})[0], 401)
        status, session, _ = self.call("GET", "/api/session", headers=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(session, payload)
        self.assertEqual(self.call("PUT", "/api/config", {"url": ""}, cookie)[0], 403)
        authenticated = {**cookie, "X-CSRF-Token": payload["csrf"]}
        self.assertEqual(self.call("PUT", "/api/config", {"url": "https://example.com/"}, authenticated)[0], 200)
        for method, path, body in (
            ("GET", "/api/keys", None),
            ("POST", "/api/keys", {"name": "extra"}),
            ("DELETE", f"/api/keys/{minted['id']}", None),
        ):
            self.assertEqual(self.call(method, path, body, authenticated)[0], 403)
        self.app.revoke_key(minted["id"])
        self.assertEqual(self.call("GET", "/api/session", headers=cookie)[0], 401)
        self.assertEqual(self.call("GET", "/api/config", headers=cookie)[0], 401)

    def test_login_tokens_expire_and_revocation_prevents_exchange(self):
        minted = self.mint()
        expired = self.login_token(minted["key"])
        with patch("webdisplay.app.time.time", return_value=time.time() + 61):
            self.assertEqual(self.call("POST", "/api/admin-login", {"token": expired})[0], 401)
        revoked = self.login_token(minted["key"])
        self.app.revoke_key(minted["id"])
        self.assertEqual(self.call("POST", "/api/admin-login", {"token": revoked})[0], 401)

    def test_login_token_creation_requires_bearer_and_exchange_is_throttled(self):
        self.assertEqual(self.call("POST", "/api/admin-login-token", {})[0], 401)
        self.assertEqual(self.call("POST", "/api/admin-login-token", {}, self.session)[0], 401)
        for _ in range(5):
            self.assertEqual(self.call("POST", "/api/admin-login", {"token": "invalid"})[0], 401)
        self.assertEqual(self.call("POST", "/api/admin-login", {"token": "invalid"})[0], 429)


if __name__ == "__main__":
    unittest.main()
