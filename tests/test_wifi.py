import unittest
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from webdisplay import wifi_helper
from webdisplay import network
from webdisplay.network import gateway_status, internet_status, website_status


class ValidationTests(unittest.TestCase):
    def test_country(self):
        self.assertEqual(wifi_helper.validate_country("GB"), "GB")
        for bad in ("gb", "GBR", "G", "", None, "G;"):
            with self.assertRaises(ValueError):
                wifi_helper.validate_country(bad)

    def test_ssid(self):
        self.assertEqual(wifi_helper.validate_ssid("Home WiFi"), "Home WiFi")
        for bad in ("", "x" * 33, "a\nb", None):
            with self.assertRaises(ValueError):
                wifi_helper.validate_ssid(bad)

    def test_psk(self):
        self.assertEqual(wifi_helper.validate_psk(""), "")
        self.assertEqual(wifi_helper.validate_psk("longenough"), "longenough")
        for bad in ("short", "x" * 64, "pass\nword!", "pässword1"):
            with self.assertRaises(ValueError):
                wifi_helper.validate_psk(bad)

    def test_keyfile(self):
        text = wifi_helper.render_keyfile("Home", "secret123", "wlan0")
        self.assertIn("ssid=Home", text)
        self.assertIn("psk=secret123", text)
        self.assertIn("autoconnect-retries=0", text)
        self.assertNotIn("wifi-security", wifi_helper.render_keyfile("Open", "", "wlan0"))

    def test_unknown_operation_rejected(self):
        with self.assertRaises(ValueError):
            wifi_helper.handle({"operation": "shell"})

    def test_failed_client_connect_restores_existing_ap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ap_info = root / "setup-ap.json"
            ap_info.write_text("{}", encoding="utf-8")
            with (
                patch.object(wifi_helper, "LOCK_PATH", root / "network.lock"),
                patch("webdisplay.netmonitor.AP_INFO", ap_info),
                patch.object(wifi_helper, "run", return_value=Mock(returncode=0)),
                patch("webdisplay.netmonitor.write_private") as write,
                patch.object(wifi_helper, "connect", side_effect=RuntimeError("no connection")),
                patch("webdisplay.netmonitor.Supervisor.start_ap", return_value=True) as start_ap,
            ):
                with self.assertRaisesRegex(RuntimeError, "no connection"):
                    wifi_helper.handle(
                        {"operation": "connect", "ssid": "Unavailable", "password": "password123"}
                    )
            start_ap.assert_called_once()
            self.assertEqual(json.loads(write.call_args.args[1]), {"error": "no connection"})

    def test_successful_client_connect_stops_ap_first_and_clears_stale_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ap_info = root / "ap.json"
            ap_info.write_text("{}")
            error_file = root / "error.json"
            error_file.write_text('{"error":"previous failure"}')
            events = []
            with (
                patch.object(wifi_helper, "LOCK_PATH", root / "lock"),
                patch.object(wifi_helper, "CONNECTION_ERROR", error_file),
                patch("webdisplay.netmonitor.AP_INFO", ap_info),
                patch.object(wifi_helper, "run",
                             side_effect=lambda *args: events.append("down") or Mock(returncode=0)),
                patch.object(wifi_helper, "connect", side_effect=lambda *args: events.append("connect")),
                patch("webdisplay.netmonitor.Supervisor.remove_ap_profile") as remove,
            ):
                self.assertEqual(wifi_helper.handle(
                    {"operation": "connect", "ssid": "Home", "password": "password123"}
                ), {"connected": True})
            self.assertEqual(events, ["down", "connect"])
            self.assertFalse(error_file.exists())
            remove.assert_called_once_with()

    def test_timed_out_client_activation_rolls_back_profile(self):
        for previous in (None, b"previous profile"):
            with self.subTest(previous=previous), tempfile.TemporaryDirectory() as directory:
                keyfile = Path(directory) / "wifi.nmconnection"
                if previous is not None:
                    keyfile.write_bytes(previous)
                def run(command, *args):
                    if "--wait" in command:
                        raise subprocess.TimeoutExpired(command, 45)
                    return Mock(returncode=0, stdout="", stderr="")
                with patch.object(wifi_helper, "KEYFILE", keyfile), \
                     patch.object(wifi_helper, "wifi_device", return_value="wlan0"), \
                     patch.object(wifi_helper, "run", side_effect=run):
                    with self.assertRaisesRegex(RuntimeError, "Could not connect"):
                        wifi_helper.connect("New", "password123")
                self.assertEqual(keyfile.read_bytes() if keyfile.exists() else None, previous)
                if previous is not None:
                    self.assertEqual(keyfile.stat().st_mode & 0o777, 0o600)

    def test_connection_error_remains_available_after_rejoining_ap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "error.json"
            with patch.object(wifi_helper, "CONNECTION_ERROR", path):
                self.assertEqual(wifi_helper.connection_error(), "")
                path.write_text('{"error":"Authentication failed"}')
                self.assertEqual(wifi_helper.connection_error(), "Authentication failed")

    @patch.object(wifi_helper, "run")
    def test_scan_parses_escaped_ssids_and_dedupes(self, run):
        run.return_value.stdout = "Home\\:5G:70:WPA2\nHome\\:5G:40:WPA2\nOpen:55:\n:20:WPA2\n"
        networks = wifi_helper.scan()
        self.assertEqual(
            networks,
            [
                {"ssid": "Home:5G", "signal": 70, "secured": True},
                {"ssid": "Open", "signal": 55, "secured": False},
            ],
        )


    def test_status_reports_saved_ssid_with_escapes(self):
        def run(command, timeout=60):
            stdout = {"device": "wlan0:wifi:connected:web-display-wifi\n",
                      "rfkill": "0: phy0: Wireless LAN\n\tSoft blocked: no\n",
                      "iw": "country GB: DFS-ETSI\n"}.get(command[0] if command[0] != "nmcli" else command[-1])
            if command[-2:] == ["show", wifi_helper.CONNECTION_ID]:
                stdout = "Home\\:5G\n"
            return Mock(returncode=0, stdout=stdout)

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(wifi_helper, "KEYFILE", Path(directory) / "wifi.nmconnection") as keyfile, \
                patch.object(wifi_helper, "run", side_effect=run):
            self.assertEqual(wifi_helper.status()["saved_ssid"], "")
            keyfile.write_text("[wifi]\n")
            status = wifi_helper.status()
        self.assertTrue(status["saved_network"])
        self.assertEqual(status["saved_ssid"], "Home:5G")

class ProbeTests(unittest.TestCase):
    def test_internet_needs_any_endpoint(self):
        self.assertTrue(
            internet_status((("a", 1), ("b", 1)), lambda h, p: h == "b")["online"]
        )
        self.assertFalse(internet_status((("a", 1),), lambda h, p: False)["online"])

    def test_gateway_needs_any_default_route_answering(self):
        routes = [("192.168.0.1", "eth0"), ("192.168.0.1", "wlan0")]
        self.assertTrue(gateway_status(routes, lambda gw, dev: dev == "wlan0")["online"])
        self.assertFalse(gateway_status(routes, lambda gw, dev: False)["online"])
        self.assertFalse(gateway_status([], lambda gw, dev: True)["online"])

    def test_gateway_routes_and_arp_fallback(self):
        routes = Mock(stdout="default via 192.168.0.254 dev eth0 proto dhcp metric 100\n"
                             "default via 192.168.0.254 dev wlan0 proto dhcp metric 600\n")
        with patch.object(network.subprocess, "run", return_value=routes):
            self.assertEqual(network.default_gateways(),
                             [("192.168.0.254", "eth0"), ("192.168.0.254", "wlan0")])
        for neighbour, expected in (("192.168.0.254 lladdr aa:bb REACHABLE", True),
                                    ("192.168.0.254 lladdr aa:bb STALE", False)):
            with patch.object(network.subprocess, "run",
                              side_effect=[Mock(returncode=1), Mock(stdout=neighbour)]) as run:
                self.assertEqual(network.gateway_probe("192.168.0.254", "eth0"), expected)
            self.assertIn("-I", run.call_args_list[0].args[0])
        with patch.object(network.subprocess, "run", return_value=Mock(returncode=0)) as run:
            self.assertTrue(network.gateway_probe("192.168.0.254", "eth0"))
        run.assert_called_once()

    def test_website_failure_is_separate(self):
        result = website_status("https://example.test/path", lambda h, p: (h, p) == ("example.test", 443))
        self.assertTrue(result["reachable"])
        self.assertFalse(website_status("http://example.test", lambda h, p: False)["reachable"])


if __name__ == "__main__":
    unittest.main()
