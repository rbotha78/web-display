import http.client
import json
import ssl
import struct
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from http.server import ThreadingHTTPServer
from pathlib import Path

from webdisplay.app import Application, make_handler
from webdisplay_manager import client, discovery, version
from webdisplay_manager.client import CertificateMismatch, DeviceClient, DeviceError
from webdisplay_manager.server import make_handler as manager_handler
from webdisplay_manager.service import Manager
from webdisplay_manager.store import DeviceStore


def make_certificate(directory: Path, name: str) -> tuple[Path, Path]:
    certificate, key = directory / f"{name}.pem", directory / f"{name}.key"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
         "-keyout", str(key), "-out", str(certificate), "-days", "2", "-subj", f"/CN={name}"],
        check=True, capture_output=True,
    )
    return certificate, key


class FakeDevice:
    """A real Application served over TLS with a throwaway certificate."""

    def __init__(self, directory: Path, name: str = "device"):
        root = directory / name
        root.mkdir()
        self.certificate, key = make_certificate(root, name)
        self.app = Application(root / "config.json", root / "pairing", root / "target", Path("/bin/true"))
        self.app.run_action = lambda action: self.actions.append(action)
        self.actions: list[str] = []
        self.app.updater.running_version = lambda: "0.5.7"
        self.app.config.update(password=__import__("webdisplay.app", fromlist=["x"]).hash_password("device password"))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.certificate, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port
        der = ssl.PEM_cert_to_DER_cert(self.certificate.read_text())
        self.fingerprint = client.format_fingerprint(der)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class ManagerCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.device = FakeDevice(self.root)
        self.store = DeviceStore(self.root / "manager" / "devices.json")
        self.manager = Manager(self.store, discover_fn=lambda: [{"name": "device", "host": "127.0.0.1", "port": self.device.port}])
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.device.close)

    def enroll(self):
        return self.manager.enroll({
            "host": "127.0.0.1", "port": self.device.port, "fingerprint": self.device.fingerprint,
            "password": "device password", "name": "Lobby",
        })["id"]


class ClientTests(ManagerCase):
    def test_fetches_the_fingerprint_shown_on_the_device(self):
        self.assertEqual(client.fetch_fingerprint("127.0.0.1", self.device.port), self.device.fingerprint)

    def test_pinned_client_rejects_a_different_certificate(self):
        wrong = DeviceClient("127.0.0.1", self.device.port, "AA:" * 31 + "AA")
        with self.assertRaises(CertificateMismatch):
            wrong.call("GET", "/health")

    def test_fingerprint_normalisation(self):
        plain = self.device.fingerprint.replace(":", "").lower()
        self.assertEqual(client.normalise_fingerprint(plain), self.device.fingerprint)
        with self.assertRaises(ValueError):
            client.normalise_fingerprint("abc")

    def test_unreachable_device(self):
        with self.assertRaises(DeviceError):
            client.fetch_fingerprint("127.0.0.1", 1, timeout=1)


class EnrollmentTests(ManagerCase):
    def test_enrol_stores_key_and_not_the_password(self):
        identifier = self.enroll()
        stored = self.store.path.read_text()
        self.assertNotIn("device password", stored)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        entry = self.store.get(identifier)
        self.assertTrue(str(entry["key"]).startswith("wdk_"))
        self.assertEqual(len(self.device.app.list_keys()), 1)

    def test_wrong_password_is_reported(self):
        with self.assertRaisesRegex(DeviceError, "invalid credentials"):
            self.manager.enroll({
                "host": "127.0.0.1", "port": self.device.port, "fingerprint": self.device.fingerprint,
                "password": "nope nope nope",
            })
        self.assertEqual(self.store.all(), {})

    def test_enrolment_aborts_if_certificate_differs_from_confirmed(self):
        with self.assertRaises(CertificateMismatch):
            self.manager.enroll({
                "host": "127.0.0.1", "port": self.device.port, "fingerprint": "00:" * 31 + "00",
                "password": "device password",
            })
        self.assertEqual(self.device.app.list_keys(), [])

    def test_validation(self):
        for body in ({"host": "bad host!"}, {"host": "127.0.0.1", "port": 0}, {"host": "127.0.0.1", "port": "x"}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.manager.enroll({**body, "fingerprint": self.device.fingerprint, "password": "x"})


class OperationTests(ManagerCase):
    def test_status_summary(self):
        self.enroll()
        self.device.app.network_status = lambda: {
            "internet": {"online": True}, "default_route": "wlan0",
            "wifi": {"devices": [{"device": "wlan0", "type": "wifi", "state": "connected", "connection": "x"}]},
        }
        self.device.app.update_status = lambda: {"installed_version": "0.5.6", "running_version": "0.5.6"}
        [summary] = self.manager.devices()
        self.assertTrue(summary["online"])
        self.assertEqual(summary["name"], "Lobby")
        self.assertTrue(summary["internet"])
        self.assertTrue(summary["wifi_connected"])
        self.assertFalse(summary["restart_needed"])
        self.assertNotIn("key", summary)

    def test_offline_device_is_reported_not_raised(self):
        identifier = self.enroll()
        self.device.close()
        self.device = FakeDevice(self.root, "other")
        [summary] = self.manager.devices()
        self.assertFalse(summary["online"])
        self.assertIn("error", summary)
        self.assertEqual(summary["id"], identifier)

    def test_changed_certificate_is_flagged(self):
        self.enroll()
        self.store.save("127.0.0.1", self.device.port, "11:" * 31 + "11", "wdk_x", "Lobby")
        summaries = {s["id"]: s for s in self.manager.devices()}
        self.assertTrue(any(s.get("certificate_changed") for s in summaries.values()))

    def test_revoked_key_is_flagged(self):
        identifier = self.enroll()
        self.device.app.revoke_key(self.device.app.list_keys()[0]["id"])
        summary = self.manager.devices()[0]
        self.assertTrue(summary["key_rejected"])
        self.assertEqual(summary["id"], identifier)

    def test_url_and_reboot_actions(self):
        identifier = self.enroll()
        self.manager.action(identifier, "url", {"url": "https://example.com/"})
        self.assertEqual(self.device.app.config.load()["url"], "https://example.com/")
        with self.assertRaises(DeviceError):
            self.manager.action(identifier, "url", {"url": "not a url"})
        self.manager.action(identifier, "reboot", {})
        self.assertEqual(self.device.actions, ["reboot"])

    def test_debug_keys_are_managed_with_the_manager_key(self):
        identifier = self.enroll()
        requests = []

        def fake(request):
            requests.append(request)
            return {"enabled": True, "keys": []}

        self.device.app.run_debug_keys = fake
        self.manager.debug_keys(identifier, "list", {})
        self.manager.debug_keys(identifier, "add", {"key": "ssh-ed25519 AAAA", "name": "pc"})
        self.manager.debug_keys(identifier, "remove", {}, "aaaaaaaaaaaa")
        self.assertEqual(
            requests,
            [
                {"operation": "list"},
                {"operation": "add", "key": "ssh-ed25519 AAAA", "name": "pc"},
                {"operation": "remove", "id": "aaaaaaaaaaaa"},
            ],
        )

    def test_update_all_reports_each_device(self):
        self.enroll()
        self.device.app.check_update = lambda: {"available": True, "latest": {"version": "9.9.9", "size": 1}}
        self.device.app.install_release_update = lambda: {"version": "9.9.9", "restart_required": True}
        [result] = self.manager.update_all()
        self.assertEqual((result["outcome"], result["version"]), ("installed", "9.9.9"))
        self.device.app.check_update = lambda: {"available": False, "latest": None}
        self.assertEqual(self.manager.update_all()[0]["outcome"], "up to date")

    def test_discovery_marks_enrolled_devices(self):
        self.assertFalse(self.manager.discovered()[0]["enrolled"])
        self.enroll()
        self.assertTrue(self.manager.discovered()[0]["enrolled"])

    def test_forget(self):
        identifier = self.enroll()
        self.manager.forget(identifier)
        self.assertEqual(self.manager.devices(), [])
        with self.assertRaises(LookupError):
            self.manager.forget(identifier)


class ServerTests(ManagerCase):
    TOKEN = "t" * 20

    def setUp(self):
        super().setUp()
        holder = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), manager_handler(self.manager, self.TOKEN, lambda: holder["port"]))
        holder["port"] = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def call(self, method, path, body=None, headers=None, host=None, token=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        send = {"Host": host or f"127.0.0.1:{self.server.server_port}", **(headers or {})}
        if token:
            send["X-Manager-Token"] = self.TOKEN
        data = json.dumps(body).encode() if body is not None else None
        connection.request(method, path, body=data, headers=send)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, (json.loads(raw) if raw and raw.startswith(b"{") else raw)

    def test_static_page_needs_no_token_but_api_does(self):
        self.assertEqual(self.call("GET", "/", token=False)[0], 200)
        self.assertEqual(self.call("GET", "/api/devices", token=False)[0], 401)
        self.assertEqual(self.call("GET", "/api/devices")[0], 200)

    def test_rebinding_hosts_and_foreign_origins_are_refused(self):
        self.assertEqual(self.call("GET", "/api/devices", host="evil.example:80")[0], 403)
        self.assertEqual(self.call("POST", "/api/fingerprint", {"host": "127.0.0.1"}, {"Origin": "https://evil.example"})[0], 403)

    def test_traversal_is_not_served(self):
        self.assertEqual(self.call("GET", "/../server.py", token=False)[0], 404)
        self.assertEqual(self.call("GET", "/%2e%2e/server.py", token=False)[0], 404)

    def test_enrol_list_act_forget_over_http(self):
        status, payload = self.call("POST", "/api/fingerprint", {"host": "127.0.0.1", "port": self.device.port})
        self.assertEqual(payload["fingerprint"], self.device.fingerprint)
        status, payload = self.call("POST", "/api/enroll", {
            "host": "127.0.0.1", "port": self.device.port, "fingerprint": payload["fingerprint"],
            "password": "device password", "name": "Lobby"})
        self.assertEqual(status, 201)
        identifier = payload["id"]
        status, payload = self.call("GET", "/api/devices")
        self.assertEqual(payload["devices"][0]["name"], "Lobby")
        self.assertNotIn("key", json.dumps(payload))
        self.assertEqual(self.call("POST", f"/api/devices/{identifier}/reboot", {})[0], 200)
        self.assertEqual(self.call("POST", f"/api/devices/{identifier}/format-disk", {})[0], 404)
        self.device.app.run_debug_keys = lambda request: {"enabled": True, "keys": []}
        keys = f"/api/devices/{identifier}/debug-keys"
        self.assertEqual(self.call("GET", keys)[0], 200)
        self.assertEqual(self.call("POST", keys, {"key": "k"})[0], 201)
        self.assertEqual(self.call("DELETE", f"{keys}/aaaaaaaaaaaa")[0], 200)
        self.assertEqual(self.call("DELETE", f"{keys}/not-an-id")[0], 404)
        self.assertEqual(self.call("DELETE", f"/api/devices/{identifier}")[0], 200)
        self.assertEqual(self.call("DELETE", f"/api/devices/{identifier}")[0], 404)

    def test_bad_enrol_is_a_client_error(self):
        status, _ = self.call("POST", "/api/enroll", {"host": "bad host"})
        self.assertEqual(status, 400)

    def test_admin_login_over_http_uses_pinned_device_and_single_use_token(self):
        identifier = self.enroll()
        path = f"/api/devices/{identifier}/admin-login"
        self.assertEqual(self.call("POST", path, {}, token=False)[0], 401)
        self.assertEqual(self.call("POST", path, {}, {"Origin": "https://evil.example"})[0], 403)
        status, result = self.call("POST", path, {})
        self.assertEqual(status, 200)
        url = urlsplit(result["url"])
        self.assertEqual((url.scheme, url.hostname, url.port, url.path, url.query),
                         ("https", "127.0.0.1", self.device.port, "/", ""))
        token = url.fragment.removeprefix("admin-login=")
        browser = DeviceClient("127.0.0.1", self.device.port, self.device.fingerprint)
        status, session, headers = browser.request("POST", "/api/admin-login", {"token": token})
        self.assertEqual(status, 200)
        self.assertFalse(session["can_manage_keys"])
        cookie = {"Cookie": headers["set-cookie"].split(";", 1)[0]}
        self.assertEqual(browser.request("GET", "/api/config", headers=cookie)[0], 200)
        self.assertEqual(browser.request("POST", "/api/admin-login", {"token": token})[0], 401)
        self.assertNotIn(str(self.store.get(identifier)["key"]), result["url"])
        self.store.save("127.0.0.1", self.device.port, "11:" * 31 + "11", "wdk_x", "Wrong")
        wrong = next(i for i in self.store.all() if i != identifier)
        self.assertEqual(self.call("POST", f"/api/devices/{wrong}/admin-login", {})[0], 502)

    def test_admin_login_reports_old_device_software(self):
        identifier = self.enroll()
        with patch.object(DeviceClient, "request", return_value=(404, {"error": "not found"}, {})):
            with patch.object(self.manager, "client", return_value=DeviceClient("device.example", 8443, "")):
                status, payload = self.call("POST", f"/api/devices/{identifier}/admin-login", {})
        self.assertEqual(status, 502)
        self.assertIn("newer device software", payload["error"])


class VersionTests(ManagerCase):
    def test_old_firmware_cannot_be_enrolled(self):
        self.device.app.updater.running_version = lambda: "0.5.6"
        with self.assertRaisesRegex(DeviceError, "needs 0.5.7"):
            self.enroll()
        self.assertEqual(self.store.all(), {})

    def test_old_firmware_is_flagged_for_enrolled_device(self):
        self.enroll()
        self.device.app.updater.running_version = lambda: "0.5.6"
        [summary] = self.manager.devices()
        self.assertTrue(summary["firmware_outdated"])
        self.assertEqual(summary["min_firmware"], version.MIN_FIRMWARE)

    def test_current_firmware_is_not_flagged(self):
        self.enroll()
        [summary] = self.manager.devices()
        self.assertNotIn("firmware_outdated", summary)

    def test_manager_version_follows_release_file_and_package(self):
        release = version.manager_version()
        self.assertRegex(release, r"^\d+\.\d+\.\d+$")
        pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
        self.assertIn(f'version = "{release}"', pyproject)
        self.assertFalse(version.firmware_too_old(release))


class MultiAddressTests(ManagerCase):
    def test_falls_back_to_another_address_and_promotes_it(self):
        identifier = self.enroll()
        self.store.learn(identifier, [], self.device.port, None)
        entry = self.store.get(identifier)
        entry_addresses = [{"host": "127.0.0.2", "port": 1}, {"host": "127.0.0.1", "port": self.device.port}]
        data = self.store.all()
        data[identifier]["addresses"] = entry_addresses
        data[identifier].update(host="127.0.0.2", port=1)
        self.store._write(data)
        [summary] = self.manager.devices()
        self.assertTrue(summary["online"])
        self.assertEqual(summary["host"], "127.0.0.1")
        self.assertEqual(self.store.get(identifier)["host"], "127.0.0.1")
        self.assertIsNotNone(entry)

    def test_enrolling_a_second_address_merges_into_one_device(self):
        first = self.enroll()
        second = self.manager.enroll({
            "host": "localhost", "port": self.device.port, "fingerprint": self.device.fingerprint,
            "password": "device password", "name": "Lobby",
        })["id"]
        self.assertEqual(first, second)
        self.assertEqual(len(self.manager.devices()), 1)
        hosts = [item["host"] for item in self.store.get(first)["addresses"]]
        self.assertEqual(hosts[:2], ["localhost", "127.0.0.1"])

    def test_discovery_matches_any_known_address_or_hostname(self):
        identifier = self.enroll()
        self.store.learn(identifier, ["10.0.0.9"], self.device.port, "pi-one")
        self.manager.discover_fn = lambda: [
            {"name": "pi-one", "host": "10.9.9.9", "port": self.device.port},
            {"name": "pi-one", "host": "10.0.0.9", "port": self.device.port},
            {"name": "other", "host": "10.0.0.50", "port": 8443},
        ]
        found = self.manager.discovered()
        self.assertEqual([(d["name"], d["enrolled"]) for d in found], [("pi-one", True), ("other", False)])

    def test_scan_learns_extra_address_of_enrolled_device(self):
        identifier = self.enroll()
        self.store.learn(identifier, [], self.device.port, "pi-one")
        self.manager.discover_fn = lambda: [
            {"name": "pi-one", "host": "10.0.0.9", "port": self.device.port},
        ]
        self.manager.discovered()
        self.assertIn("10.0.0.9", [a["host"] for a in self.store.get(identifier)["addresses"]])

    def test_device_reported_addresses_are_learned(self):
        identifier = self.enroll()
        device = self.manager.client(identifier)
        self.manager.learn_addresses(
            identifier, device,
            {"hostname": "pi-one", "addresses": ["192.168.0.5", "::1", "127.0.0.1", "169.254.1.1"]},
        )
        entry = self.store.get(identifier)
        self.assertEqual(entry["hostname"], "pi-one")
        self.assertIn("192.168.0.5", [a["host"] for a in entry["addresses"]])
        self.assertNotIn("169.254.1.1", [a["host"] for a in entry["addresses"]])


class DiscoveryTests(unittest.TestCase):
    def packet(self, flags=0x8400, port=8443):
        service = discovery.encode_name(discovery.SERVICE)
        instance = bytes([11]) + b"web-display" + service
        header = struct.pack(">HHHHHH", 0, flags, 0, 2, 0, 0)
        ptr = service + struct.pack(">HHIH", 12, 1, 120, len(instance)) + instance
        srv_data = struct.pack(">HHH", 0, 0, port) + discovery.encode_name("web-display.local")
        srv = instance + struct.pack(">HHIH", 33, 1, 120, len(srv_data)) + srv_data
        return header + ptr + srv

    def test_parses_instance_and_port(self):
        self.assertEqual(discovery.parse_response(self.packet(port=9000)), [{"name": "web-display", "port": 9000}])

    def test_ignores_queries_and_garbage(self):
        self.assertEqual(discovery.parse_response(self.packet(flags=0)), [])
        self.assertEqual(discovery.parse_response(b"\x00" * 5), [])
        self.assertEqual(discovery.parse_response(self.packet()[:30]), [])


class StoreTests(unittest.TestCase):
    def test_corrupt_file_reads_as_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.json"
            path.write_text("{not json")
            self.assertEqual(DeviceStore(path).all(), {})


if __name__ == "__main__":
    unittest.main()
