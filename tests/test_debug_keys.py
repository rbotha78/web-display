import base64
import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from webdisplay import debug_keys_helper as helper
from webdisplay.app import Application, make_handler


def make_key(seed=b"x", comment="laptop"):
    blob = len(b"ssh-ed25519").to_bytes(4, "big") + b"ssh-ed25519" + len(seed * 32).to_bytes(4, "big") + seed * 32
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} {comment}".strip()


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.keys = root / "authorized_keys"
        self.marker = root / "marker"
        self.marker.write_text("")
        patches = [
            mock.patch.object(helper, "AUTHORIZED_KEYS", self.keys),
            mock.patch.object(helper, "ENABLED_MARKER", self.marker),
            mock.patch.object(helper, "write_lines", self.write_lines),
            mock.patch.object(helper.pwd, "getpwnam", return_value=object()),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.directory.cleanup)

    def write_lines(self, lines):
        self.keys.write_text("".join(line + "\n" for line in lines))

    def test_parse_and_validate(self):
        parsed = helper.validate_public_key(make_key())
        self.assertEqual(parsed["type"], "ssh-ed25519")
        self.assertTrue(parsed["fingerprint"].startswith("SHA256:"))
        self.assertEqual(len(parsed["id"]), 12)
        for bad in ['command="id" ' + make_key(), make_key() + "\n" + make_key(), "ssh-ed25519 !!!", "", 5]:
            with self.assertRaises(ValueError):
                helper.validate_public_key(bad)

    def test_add_list_remove(self):
        added = helper.apply({"operation": "add", "key": make_key(), "name": "desk"})
        self.assertTrue(added["enabled"])
        self.assertEqual(added["keys"][0]["comment"], "desk")
        with self.assertRaises(ValueError):
            helper.apply({"operation": "add", "key": make_key(), "name": "again"})
        helper.apply({"operation": "add", "key": make_key(b"y"), "name": "other"})
        key_id = added["keys"][0]["id"]
        remaining = helper.apply({"operation": "remove", "id": key_id})
        self.assertEqual([k["comment"] for k in remaining["keys"]], ["other"])
        with self.assertRaises(ValueError):
            helper.apply({"operation": "remove", "id": key_id})

    def test_unparseable_lines_are_preserved(self):
        self.keys.write_text("# note\n")
        helper.apply({"operation": "add", "key": make_key(), "name": ""})
        self.assertIn("# note", self.keys.read_text())

    def test_limit(self):
        for index in range(helper.MAX_KEYS):
            helper.apply({"operation": "add", "key": make_key(bytes([65 + index])), "name": ""})
        with self.assertRaises(ValueError):
            helper.apply({"operation": "add", "key": make_key(b"z"), "name": ""})

    def test_disabled(self):
        self.marker.unlink()
        self.assertEqual(helper.apply({"operation": "list"}), {"enabled": False, "keys": []})
        with self.assertRaises(ValueError):
            helper.apply({"operation": "add", "key": make_key(), "name": ""})


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.helper_path = root / "debug-keys-action"
        self.app = Application(
            root / "config.json", root / "pairing", root / "target", Path("/bin/true"),
            debug_keys_helper=self.helper_path,
        )
        self.requests = []
        self.app.run_debug_keys = self.fake
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

    def fake(self, request):
        self.requests.append(request)
        if request["operation"] == "add" and request["key"] == "bad":
            raise ValueError("nope")
        return {"enabled": True, "keys": []}

    def call(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        data = json.dumps(body).encode() if body is not None else None
        connection.request(method, path, body=data, headers={"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, json.loads(raw) if raw else {}, dict(response.getheaders())

    def test_requires_authentication(self):
        self.assertEqual(self.call("GET", "/api/debug-keys")[0], 401)
        self.assertEqual(self.call("POST", "/api/debug-keys", {"key": "k"})[0], 401)

    def test_manager_api_keys_are_accepted_without_csrf(self):
        _, minted, _ = self.call("POST", "/api/keys", {"name": "pc"}, self.session)
        auth = {"Authorization": "Bearer " + minted["key"]}
        self.assertEqual(self.call("GET", "/api/debug-keys", headers=auth)[0], 200)
        self.assertEqual(self.call("POST", "/api/debug-keys", {"key": "k"}, auth)[0], 201)
        self.assertEqual(self.call("DELETE", "/api/debug-keys/aaaaaaaaaaaa", headers=auth)[0], 200)

    def test_csrf_required_for_writes(self):
        cookie = {"Cookie": self.session["Cookie"]}
        self.assertEqual(self.call("POST", "/api/debug-keys", {"key": "k"}, cookie)[0], 403)
        self.assertEqual(self.call("DELETE", "/api/debug-keys/aaaaaaaaaaaa", headers=cookie)[0], 403)

    def test_list_add_remove(self):
        self.assertEqual(self.call("GET", "/api/debug-keys", headers=self.session)[0], 200)
        status, _, _ = self.call("POST", "/api/debug-keys", {"key": "k", "name": "n"}, self.session)
        self.assertEqual(status, 201)
        self.assertEqual(self.call("DELETE", "/api/debug-keys/aaaaaaaaaaaa", headers=self.session)[0], 200)
        self.assertEqual([r["operation"] for r in self.requests], ["list", "add", "remove"])

    def test_helper_errors_are_client_errors(self):
        status, payload, _ = self.call("POST", "/api/debug-keys", {"key": "bad"}, self.session)
        self.assertEqual(status, 400)
        self.assertIn("nope", payload["error"])

    def test_unavailable_on_release_devices(self):
        del self.app.run_debug_keys
        status, payload, _ = self.call("GET", "/api/debug-keys", headers=self.session)
        self.assertEqual((status, payload), (200, {"enabled": False, "keys": []}))
        status, _, _ = self.call("POST", "/api/debug-keys", {"key": "k"}, self.session)
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
