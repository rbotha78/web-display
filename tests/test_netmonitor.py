import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from webdisplay import kiosk, netmonitor, wifi_helper
from webdisplay.netmonitor import OUTAGE_GRACE, RETRY_INTERVAL, Supervisor


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.supervisor = Supervisor(self.clock, auto_ap=lambda: True)
        self.events = []
        self.supervisor.start_ap = lambda: self._start()
        self.supervisor.stop_ap = lambda: self._stop()
        self.supervisor.ap_is_up = lambda: True
        self.supervisor.restore_client = lambda: self.events.append("restore")
        self.start_result = True

    def _start(self):
        self.events.append("start")
        if self.start_result:
            self.supervisor.ap_active = True
            self.supervisor.last_retry = self.clock()
        return self.start_result

    def _stop(self):
        self.events.append("stop")
        self.supervisor.ap_active = False

    def test_no_ap_when_automatic_ap_is_disabled(self):
        self.supervisor.auto_ap = lambda: False
        self.supervisor.tick(online=False)
        self.clock.now += OUTAGE_GRACE * 10
        self.supervisor.tick(online=False)
        self.assertEqual(self.events, [])

    def test_disabling_automatic_ap_stops_a_running_ap(self):
        self.supervisor.tick(online=False)
        self.clock.now += OUTAGE_GRACE
        self.supervisor.tick(online=False)
        self.supervisor.auto_ap = lambda: False
        self.supervisor.tick(online=False)
        self.assertEqual(self.events, ["start", "stop", "restore"])

    def test_automatic_ap_setting_defaults_to_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            with patch.object(netmonitor, "CONFIG_PATH", path):
                self.assertFalse(netmonitor.auto_ap_enabled())
                path.write_text(json.dumps({"url": ""}))
                self.assertFalse(netmonitor.auto_ap_enabled())
                path.write_text(json.dumps({"auto_ap": True}))
                self.assertTrue(netmonitor.auto_ap_enabled())
                path.write_text("not json")
                self.assertFalse(netmonitor.auto_ap_enabled())

    def test_no_ap_during_grace_period(self):
        self.supervisor.tick(online=False)
        self.clock.now += OUTAGE_GRACE - 1
        self.supervisor.tick(online=False)
        self.assertEqual(self.events, [])

    def test_ap_after_grace_and_stops_when_online(self):
        self.supervisor.tick(online=False)
        self.clock.now += OUTAGE_GRACE
        self.supervisor.tick(online=False)
        self.assertEqual(self.events, ["start"])
        self.supervisor.tick(online=True)
        self.assertEqual(self.events, ["start", "stop", "restore"])

    def test_brief_recovery_resets_grace(self):
        self.supervisor.tick(online=False)
        self.clock.now += OUTAGE_GRACE - 5
        self.supervisor.tick(online=True)
        self.clock.now += 10
        self.supervisor.tick(online=False)
        self.clock.now += OUTAGE_GRACE - 5
        self.supervisor.tick(online=False)
        self.assertEqual(self.events, [])

    def test_repeated_outages_each_wait_for_grace_period(self):
        for _ in range(2):
            self.supervisor.tick(online=False)
            self.clock.now += OUTAGE_GRACE - 1
            self.supervisor.tick(online=False)
            self.assertNotIn("start", self.events)
            self.supervisor.tick(online=True)
        self.supervisor.tick(online=False)
        self.clock.now += OUTAGE_GRACE
        self.supervisor.tick(online=False)
        self.assertEqual(self.events, ["start"])

    def test_failed_retry_restores_ap(self):
        self.supervisor.tick(online=False)
        self.clock.now += OUTAGE_GRACE
        self.supervisor.tick(online=False)
        self.supervisor.retry_client = lambda: self.events.append("retry") or False
        self.clock.now += RETRY_INTERVAL
        self.supervisor.tick(online=False)
        self.assertEqual(self.events, ["start", "retry", "start"])

    def test_displaced_ap_is_forgotten(self):
        self.supervisor.tick(online=False)
        self.clock.now += OUTAGE_GRACE
        self.supervisor.tick(online=False)
        self.supervisor.ap_is_up = lambda: False
        self.supervisor.tick(online=True)
        self.assertEqual(self.events, ["start", "stop", "restore"])

    def test_ap_start_failure_backs_off(self):
        self.start_result = False
        self.supervisor.tick(online=False)
        self.clock.now += OUTAGE_GRACE
        self.supervisor.tick(online=False)
        self.clock.now += 10
        self.supervisor.tick(online=False)
        self.assertEqual(self.events, ["start"])


class ApConfigTests(unittest.TestCase):
    def test_passwords_rotate_and_are_wpa2_length(self):
        passwords = {netmonitor.new_password() for _ in range(20)}
        self.assertEqual(len(passwords), 20)
        self.assertTrue(all(len(p) == 12 for p in passwords))

    def test_keyfile_is_wpa2_shared(self):
        text = netmonitor.render_ap_keyfile("WebDisplay-AB12", "pw", "wlan0")
        for expected in ("mode=ap", "key-mgmt=wpa-psk", "pairwise=ccmp", "method=shared"):
            self.assertIn(expected, text)
        self.assertIn("autoconnect=false", text)

    def test_client_profile_yields_to_ethernet_route(self):
        text = wifi_helper.render_keyfile("Home", "secret123")
        self.assertIn("route-metric=600", text)


class FallbackTests(unittest.TestCase):
    def test_fallback_shows_setup_ap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            info = root / "ap.json"
            info.write_text(
                json.dumps({"ssid": "WebDisplay-AB12", "password": "pw<>", "address": "10.42.0.1"})
            )
            with patch.object(kiosk, "local_addresses", return_value=["192.168.0.5"]):
                page = kiosk.fallback_html(root / "none", root / "none", 8443, info)
                plain = kiosk.fallback_html(root / "none", root / "none", 8443, root / "missing")
        self.assertIn("WebDisplay-AB12", page)
        self.assertIn("pw&lt;&gt;", page)
        self.assertIn("https://10.42.0.1:8443/", page)
        self.assertNotIn("WebDisplay-AB12", plain)


class StartFailureTests(unittest.TestCase):
    def test_failed_ap_start_restores_saved_client(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(netmonitor, "wifi_device", return_value="wlan0"),
                patch.object(netmonitor, "ap_capable", return_value=True),
                patch.object(netmonitor, "current_country", return_value="GB"),
                patch.object(netmonitor, "run") as run,
                patch.object(netmonitor, "AP_KEYFILE", root / "ap.nmconnection"),
                patch.object(netmonitor, "AP_INFO", root / "ap.json"),
                patch.object(netmonitor, "CLIENT_KEYFILE", root / "client.nmconnection"),
                patch.object(netmonitor.Supervisor, "restore_client") as restore,
            ):
                run.return_value.returncode = 1
                run.return_value.stderr = ""
                result = Supervisor().start_ap()
        self.assertFalse(result)
        restore.assert_called_once()


class DestinationTests(unittest.TestCase):
    ARGS = ("file:///fallback", "file:///offline")

    def choose(self, target, now, failures, ap, current):
        return kiosk.choose_destination(target, now, failures, ap, current, *self.ARGS)

    def test_unconfigured_shows_setup_page(self):
        self.assertEqual(self.choose("", False, 5, False, ""), ("file:///fallback", False))

    def test_ap_always_shows_fallback_without_icon(self):
        self.assertEqual(
            self.choose("http://site", False, 5, True, "http://site"), ("file:///fallback", False)
        )

    def test_configured_site_stays_up_and_icon_is_debounced(self):
        self.assertEqual(self.choose("http://site", False, 1, False, "http://site"), ("http://site", False))
        self.assertEqual(self.choose("http://site", False, 2, False, "http://site"), ("http://site", True))
        self.assertEqual(self.choose("http://site", True, 0, False, "http://site"), ("http://site", False))

    def test_unreachable_at_boot_shows_minimal_offline_page(self):
        self.assertEqual(self.choose("http://site", False, 1, False, ""), ("file:///offline", True))
        self.assertEqual(self.choose("http://site", True, 0, False, "file:///offline"), ("http://site", False))


if __name__ == "__main__":
    unittest.main()
