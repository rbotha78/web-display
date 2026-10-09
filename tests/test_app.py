import json
import http.client
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from webdisplay import app as webdisplay_app
from webdisplay.app import (
    Application,
    ConfigStore,
    LoginThrottle,
    SessionStore,
    hash_password,
    make_handler,
    static_file,
    validate_url,
    verify_password,
)
from webdisplay.kiosk import screen_size
from http.server import ThreadingHTTPServer


class ConfigTests(unittest.TestCase):
    def test_atomic_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            store = ConfigStore(path)
            store.update(url="https://example.com/display")
            self.assertEqual(store.load()["url"], "https://example.com/display")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text())["url"], "https://example.com/display")

    def test_url_validation(self):
        self.assertEqual(validate_url(""), "")
        self.assertEqual(validate_url(" http://example.com/a "), "http://example.com/a")
        for value in ("example.com", "file:///etc/passwd", "https://user:pass@example.com"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_url(value)


class AuthenticationTests(unittest.TestCase):
    def test_password_hash(self):
        stored = hash_password("a secure password")
        self.assertTrue(verify_password("a secure password", stored))
        self.assertFalse(verify_password("wrong password", stored))
        self.assertNotIn("a secure password", json.dumps(stored))

    def test_short_password_rejected(self):
        with self.assertRaises(ValueError):
            hash_password("short")

    def test_sessions_expire(self):
        sessions = SessionStore()
        token, _ = sessions.create()
        self.assertIsNotNone(sessions.get(token))
        with patch("webdisplay.app.time.time", return_value=10**12):
            self.assertIsNone(sessions.get(token))

    def test_login_throttle(self):
        throttle = LoginThrottle(limit=2)
        self.assertTrue(throttle.allowed("client"))
        throttle.failure("client")
        throttle.failure("client")
        self.assertFalse(throttle.allowed("client"))
        throttle.success("client")
        self.assertTrue(throttle.allowed("client"))


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.app = Application(
            root / "config.json",
            root / "pairing-code",
            root / "target-url",
            Path("/bin/true"),
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=5
        )

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def request(self, method, path, body=None, headers=None):
        encoded = json.dumps(body).encode() if body is not None else None
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        self.connection.request(method, path, body=encoded, headers=request_headers)
        response = self.connection.getresponse()
        payload = json.loads(response.read()) if response.length != 0 else {}
        return response, payload

    def test_wifi_requires_login_and_csrf(self):
        response, _ = self.request("POST", "/api/wifi/scan", {})
        self.assertEqual(response.status, 401)
        response, _ = self.request("GET", "/api/network")
        self.assertEqual(response.status, 401)

        response, payload = self.request(
            "POST",
            "/api/pair",
            {"code": self.app.pairing_code, "password": "a secure password"},
        )
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        response, _ = self.request("POST", "/api/wifi/scan", {}, {"Cookie": cookie})
        self.assertEqual(response.status, 403)

        calls = []
        self.app.run_wifi = lambda request: calls.append(request) or {"ok": True}
        response, _ = self.request(
            "POST",
            "/api/wifi/connect",
            {"ssid": "Home", "password": "secret123", "extra": "ignored"},
            {"Cookie": cookie, "X-CSRF-Token": payload["csrf"]},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(
            calls, [{"operation": "connect", "ssid": "Home", "password": "secret123"}]
        )

    def test_root_serves_ui_and_health_reports_pairing(self):
        response, payload = self.request("GET", "/health")
        self.assertEqual(payload, {"status": "ok", "paired": False})
        self.connection.request("GET", "/")
        response = self.connection.getresponse()
        body = response.read()
        self.assertEqual(response.status, 200)
        self.assertIn(b'<div id="root">', body)
        self.assertNotIn("unsafe-inline", response.getheader("Content-Security-Policy"))
        self.connection.request("GET", "/assets/../app.py")
        response = self.connection.getresponse()
        response.read()
        self.assertEqual(response.status, 404)

    def test_pair_authentication_and_csrf(self):
        response, _ = self.request("GET", "/api/config")
        self.assertEqual(response.status, 401)

        response, payload = self.request(
            "POST",
            "/api/pair",
            {"code": self.app.pairing_code, "password": "a secure password"},
        )
        self.assertEqual(response.status, 200)
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        csrf = payload["csrf"]

        response, _ = self.request(
            "PUT",
            "/api/config",
            {"url": "https://example.com"},
            {"Cookie": cookie},
        )
        self.assertEqual(response.status, 403)

        response, payload = self.request(
            "PUT",
            "/api/config",
            {"url": "https://example.com"},
            {"Cookie": cookie, "X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["url"], "https://example.com")
        self.assertFalse(payload["auto_ap"])

        response, payload = self.request(
            "PUT",
            "/api/config",
            {"auto_ap": True},
            {"Cookie": cookie, "X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status, 200)
        self.assertTrue(payload["auto_ap"])
        self.assertEqual(payload["url"], "https://example.com")
        response, _ = self.request(
            "PUT",
            "/api/config",
            {"auto_ap": "yes"},
            {"Cookie": cookie, "X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status, 400)
        response, payload = self.request(
            "GET", "/api/config", None, {"Cookie": cookie}
        )
        self.assertTrue(payload["auto_ap"])
        self.assertEqual(
            (Path(self.directory.name) / "target-url").read_text().strip(),
            "https://example.com",
        )


class StaticUiTests(unittest.TestCase):
    def test_static_file_serves_index_and_assets_only_inside_the_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "static"
            (root / "assets").mkdir(parents=True)
            (root / "index.html").write_text("<html></html>")
            (root / "assets" / "app.js").write_text("1")
            (Path(directory) / "secret.html").write_text("secret")
            self.assertEqual(static_file("/", root), (b"<html></html>", "text/html"))
            self.assertEqual(static_file("/assets/app.js?x=1", root), (b"1", "text/javascript"))
            self.assertIsNone(static_file("/../secret.html", root))
            self.assertIsNone(static_file("/assets/../../secret.html", root))
            self.assertIsNone(static_file("/%2e%2e/secret.html", root))
            self.assertIsNone(static_file("/assets/missing.js", root))
            self.assertIsNone(static_file("/assets/app.exe", root))

    def test_built_bundle_is_present_and_has_no_inline_scripts(self):
        index = (Path(webdisplay_app.__file__).parent / "static" / "index.html").read_text()
        self.assertIn('src="/assets/', index)
        self.assertNotRegex(index, r"<script(?![^>]*\bsrc=)")


class KioskTests(unittest.TestCase):
    @patch("webdisplay.kiosk.subprocess.run")
    def test_screen_size_uses_xrandr_current_mode(self, run):
        run.return_value.stdout = (
            "Screen 0: minimum 320 x 200, current 1920 x 1080, maximum 2048 x 2048"
        )
        self.assertEqual(screen_size(), "1920,1080")


if __name__ == "__main__":
    unittest.main()
