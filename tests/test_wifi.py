import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from webdisplay import wifi_helper
from webdisplay.network import internet_status, website_status


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
                patch.object(wifi_helper, "connect", side_effect=RuntimeError("no connection")),
                patch("webdisplay.netmonitor.Supervisor.start_ap", return_value=True) as start_ap,
            ):
                with self.assertRaisesRegex(RuntimeError, "no connection"):
                    wifi_helper.handle(
                        {"operation": "connect", "ssid": "Unavailable", "password": "password123"}
                    )
            start_ap.assert_called_once()

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


class ProbeTests(unittest.TestCase):
    def test_internet_needs_any_endpoint(self):
        self.assertTrue(
            internet_status((("a", 1), ("b", 1)), lambda h, p: h == "b")["online"]
        )
        self.assertFalse(internet_status((("a", 1),), lambda h, p: False)["online"])

    def test_website_failure_is_separate(self):
        result = website_status("https://example.test/path", lambda h, p: (h, p) == ("example.test", 443))
        self.assertTrue(result["reachable"])
        self.assertFalse(website_status("http://example.test", lambda h, p: False)["reachable"])


if __name__ == "__main__":
    unittest.main()
