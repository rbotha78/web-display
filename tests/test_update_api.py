import http.client
import io
import json
import os
import stat
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from webdisplay import app as webdisplay_app
from webdisplay import releases
from webdisplay.app import Application, make_handler
from webdisplay.updater import MAX_BUNDLE_BYTES, UpdateError

REAL_POPEN = subprocess.Popen


def without_sudo(command, **kwargs):
    return REAL_POPEN(command[2:], **kwargs)


class UpdateApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.helper = root / "update-action"
        self.write_helper('n=$(wc -c); echo "{\\"ok\\": true, \\"bytes\\": $n, \\"restart_required\\": true}"')
        self.updater = MagicMock()
        self.updater.status.return_value = {
            "installed_version": "1.0.0", "floor": None, "failed": [], "pending": None,
        }
        self.app = Application(
            root / "config.json", root / "pairing", root / "target", Path("/bin/true"),
            update_helper=self.helper, updater=self.updater,
        )
        popen = patch.object(webdisplay_app.subprocess, "Popen", side_effect=without_sudo)
        popen.start()
        self.addCleanup(popen.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        response, payload = self.request("POST", "/api/pair", {"code": self.app.pairing_code, "password": "a secure password"})
        self.cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        self.csrf = payload["csrf"]

    def write_helper(self, body):
        self.helper.write_text(f"#!/bin/sh\n{body}\n")
        self.helper.chmod(self.helper.stat().st_mode | stat.S_IXUSR)

    def request(self, method, path, body=None, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        encoded = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        content_type = "application/octet-stream" if raw is not None else "application/json"
        connection.request(method, path, body=encoded, headers={"Content-Type": content_type, **(headers or {})})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response, json.loads(data) if data else {}

    def authed(self, csrf=True):
        headers = {"Cookie": self.cookie}
        if csrf:
            headers["X-CSRF-Token"] = self.csrf
        return headers

    def test_endpoints_require_login_and_csrf(self):
        for method, path in (("GET", "/api/update/status"), ("POST", "/api/update/check"),
                             ("POST", "/api/update/install"), ("POST", "/api/update/upload")):
            response, _ = self.request(method, path, {} if method == "POST" else None, raw=b"x" if path.endswith("upload") else None)
            self.assertEqual(response.status, 401, path)
        for path in ("/api/update/check", "/api/update/install"):
            response, _ = self.request("POST", path, {}, self.authed(csrf=False))
            self.assertEqual(response.status, 403, path)
        response, _ = self.request("POST", "/api/update/upload", headers=self.authed(csrf=False), raw=b"x")
        self.assertEqual(response.status, 403)

    def test_status_includes_repository(self):
        self.updater.status.return_value = {"installed_version": "1.0.0"}
        response, payload = self.request("GET", "/api/update/status", headers=self.authed(csrf=False))
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["repository"], releases.REPOSITORY)

    def test_upload_streams_exact_bytes_to_helper(self):
        data = os.urandom(3 * 1024 * 1024 + 17)
        response, payload = self.request("POST", "/api/update/upload", headers=self.authed(), raw=data)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["bytes"], len(data))
        self.assertTrue(payload["restart_required"])

    def test_upload_rejects_oversize_and_empty_requests(self):
        response, payload = self.request(
            "POST", "/api/update/upload", headers={**self.authed(), "Content-Length-Hint": "x"}, raw=b""
        )
        self.assertEqual(response.status, 400)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        connection.putrequest("POST", "/api/update/upload")
        for name, value in {**self.authed(), "Content-Length": str(MAX_BUNDLE_BYTES + 1)}.items():
            connection.putheader(name, value)
        connection.endheaders()
        self.assertEqual(connection.getresponse().status, 400)
        connection.close()

    def test_helper_rejection_is_reported_to_the_administrator(self):
        self.write_helper('cat >/dev/null; echo \'{"error": "Signature verification failed"}\'; exit 1')
        response, payload = self.request("POST", "/api/update/upload", headers=self.authed(), raw=b"data")
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"], "Signature verification failed")

    def test_helper_exiting_early_does_not_hang_large_upload(self):
        self.write_helper('echo \'{"error": "Update file is not a valid bundle"}\'; exit 1')
        response, payload = self.request("POST", "/api/update/upload", headers=self.authed(), raw=os.urandom(8 * 1024 * 1024))
        self.assertEqual(response.status, 400)
        self.assertIn("not a valid bundle", payload["error"])

    def test_malformed_helper_output_is_a_generic_failure(self):
        self.write_helper("cat >/dev/null; echo garbage")
        response, payload = self.request("POST", "/api/update/upload", headers=self.authed(), raw=b"data")
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"], "Update helper failed")

    def test_concurrent_installs_are_refused(self):
        self.app.update_lock.acquire()
        response, payload = self.request("POST", "/api/update/upload", headers=self.authed(), raw=b"data")
        self.assertEqual(response.status, 400)
        self.assertIn("already in progress", payload["error"])

    def test_check_reports_newer_release_only(self):
        newer = {"version": "1.1.0", "url": "https://x", "size": 10}
        with patch.object(releases, "latest_bundle", return_value=newer):
            _, payload = self.request("POST", "/api/update/check", {}, self.authed())
        self.assertTrue(payload["available"])
        self.assertEqual(payload["latest"], {"version": "1.1.0", "size": 10})
        for version in ("1.0.0", "0.9.0"):
            with patch.object(releases, "latest_bundle", return_value={**newer, "version": version}):
                _, payload = self.request("POST", "/api/update/check", {}, self.authed())
            self.assertFalse(payload["available"], version)
        with patch.object(releases, "latest_bundle", return_value=None):
            _, payload = self.request("POST", "/api/update/check", {}, self.authed())
        self.assertFalse(payload["available"])

    def test_check_ignores_failed_versions_and_floor(self):
        self.updater.status.return_value = {"installed_version": "1.0.0", "floor": "1.2.0", "failed": ["1.3.0"]}
        for version, expected in (("1.1.0", False), ("1.3.0", False), ("1.4.0", True)):
            with patch.object(releases, "latest_bundle", return_value={"version": version, "url": "u", "size": 1}):
                _, payload = self.request("POST", "/api/update/check", {}, self.authed())
            self.assertEqual(payload["available"], expected, version)

    def test_check_reports_unreachable_release_server(self):
        with patch.object(releases, "latest_bundle", side_effect=UpdateError("Could not reach the release server")):
            response, payload = self.request("POST", "/api/update/check", {}, self.authed())
        self.assertEqual(response.status, 400)
        self.assertIn("release server", payload["error"])

    def test_install_downloads_latest_release_and_pipes_it_to_helper(self):
        bundle = b"b" * 5000
        latest = {"version": "1.1.0", "url": "https://github.com/x/y/releases/download/v1.1.0/a.wdupdate", "size": len(bundle)}
        with (
            patch.object(releases, "latest_bundle", return_value=latest),
            patch.object(releases, "open_download", return_value=io.BytesIO(bundle)),
        ):
            response, payload = self.request("POST", "/api/update/install", {}, self.authed())
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["bytes"], len(bundle))

    def test_install_refuses_when_nothing_newer(self):
        with patch.object(releases, "latest_bundle", return_value={"version": "1.0.0", "url": "u", "size": 1}):
            response, payload = self.request("POST", "/api/update/install", {}, self.authed())
        self.assertEqual(response.status, 400)
        self.assertIn("No newer update", payload["error"])

    def test_download_larger_than_advertised_is_cut_off(self):
        latest = {"version": "1.1.0", "url": "u", "size": 100}
        with (
            patch.object(releases, "latest_bundle", return_value=latest),
            patch.object(releases, "open_download", return_value=io.BytesIO(b"x" * 5000)),
        ):
            response, payload = self.request("POST", "/api/update/install", {}, self.authed())
        self.assertEqual(response.status, 400)
        self.assertIn("too large", payload["error"])


class ReleaseClientTests(unittest.TestCase):
    def opener(self, release):
        reply = MagicMock()
        reply.__enter__.return_value = reply
        reply.read.return_value = json.dumps(release).encode()
        return lambda *args, **kwargs: reply

    def asset(self, name="web-display-update-1.2.3.wdupdate", url=None, size=1000):
        return {
            "name": name, "size": size,
            "browser_download_url": url or f"https://github.com/{releases.REPOSITORY}/releases/download/v1.2.3/{name}",
        }

    def test_finds_bundle_asset(self):
        found = releases.latest_bundle(opener=self.opener({"assets": [self.asset("a.img.xz"), self.asset()]}))
        self.assertEqual(found["version"], "1.2.3")
        self.assertEqual(found["size"], 1000)

    def test_ignores_wrong_names_hosts_and_sizes(self):
        assets = [
            self.asset("web-display-update-latest.wdupdate"),
            self.asset(url="https://evil.example/web-display-update-1.2.3.wdupdate"),
            self.asset(size=MAX_BUNDLE_BYTES + 1),
            self.asset(size=0),
        ]
        self.assertIsNone(releases.latest_bundle(opener=self.opener({"assets": assets})))

    def test_no_release_assets(self):
        self.assertIsNone(releases.latest_bundle(opener=self.opener({"assets": []})))

    def test_network_failure_and_bad_json_are_update_errors(self):
        def failing(*args, **kwargs):
            raise OSError("down")

        with self.assertRaises(UpdateError):
            releases.latest_bundle(opener=failing)
        reply = MagicMock()
        reply.__enter__.return_value = reply
        reply.read.return_value = b"not json"
        with self.assertRaises(UpdateError):
            releases.latest_bundle(opener=lambda *a, **k: reply)

    def test_download_only_from_the_release_repository(self):
        with self.assertRaises(UpdateError):
            releases.open_download("https://evil.example/x")
        with self.assertRaises(UpdateError):
            releases.open_download("https://github.com/other/repo/releases/download/v1/x")


if __name__ == "__main__":
    unittest.main()
