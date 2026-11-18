import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from webdisplay import kiosk, netmonitor, wifi_helper
from webdisplay.netmonitor import CLIENT_RETRY_INTERVAL, OUTAGE_GRACE, RETRY_INTERVAL, Supervisor


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.pending = self.root / "setup-pending"
        for name, path in (("FIRST_BOOT_AP", self.pending), ("CLIENT_KEYFILE", self.root / "client")):
            patcher = patch.object(netmonitor, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(netmonitor, "Portal")
        self.portal = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(netmonitor, "AP_INFO", self.root / "ap.json")
        patcher.start()
        self.addCleanup(patcher.stop)
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

    def test_fresh_image_starts_secured_setup_without_enabling_automatic_ap(self):
        self.pending.touch()
        automatic = Mock(return_value=False)
        self.supervisor.auto_ap = automatic
        self.supervisor.tick(online=False)
        self.clock.now += netmonitor.FIRST_BOOT_GRACE - 1
        self.supervisor.tick(online=False)
        self.assertEqual(self.events, [])
        self.clock.now += 1
        self.supervisor.tick(online=False)
        self.assertEqual(self.events, ["start"])
        self.clock.now += RETRY_INTERVAL
        self.supervisor.tick(online=False)
        self.assertEqual(self.events, ["start"])
        self.assertTrue(self.pending.exists())
        automatic.assert_not_called()

    def test_initial_setup_ends_on_network_or_saved_wifi_and_stays_finished(self):
        for online, saved in ((True, False), (False, True)):
            with self.subTest(online=online, saved=saved):
                self.events.clear()
                self.supervisor.auto_ap = lambda: False
                self.supervisor.offline_since = None
                self.pending.touch()
                self.supervisor.tick(online=False)
                self.clock.now += netmonitor.FIRST_BOOT_GRACE
                self.supervisor.tick(online=False)
                if saved:
                    netmonitor.CLIENT_KEYFILE.touch()
                self.supervisor.tick(online=online)
                self.assertFalse(self.pending.exists())
                self.assertEqual(self.events, ["start", "stop", "restore"])
                self.supervisor = Supervisor(self.clock, auto_ap=lambda: False)
                self.supervisor.ap_is_up = lambda: True
                self.supervisor.start_ap = Mock()
                self.supervisor.tick(online=False)
                self.clock.now += OUTAGE_GRACE * 2
                self.supervisor.tick(online=False)
                self.supervisor.start_ap.assert_not_called()
                self.supervisor.start_ap = lambda: self._start()
                self.supervisor.stop_ap = lambda: self._stop()
                self.supervisor.restore_client = lambda: self.events.append("restore")
                netmonitor.CLIENT_KEYFILE.unlink(missing_ok=True)

    def test_fresh_setup_start_failure_retries_after_backoff(self):
        self.pending.touch()
        self.supervisor.auto_ap = lambda: False
        self.start_result = False
        self.supervisor.tick(online=False)
        self.clock.now += netmonitor.FIRST_BOOT_GRACE
        self.supervisor.tick(online=False)
        self.clock.now += 59
        self.supervisor.tick(online=False)
        self.assertEqual(self.events, ["start"])
        self.clock.now += 1
        self.supervisor.tick(online=False)
        self.assertEqual(self.events, ["start", "start"])

    def test_initial_setup_request_survives_supervisor_restart(self):
        self.pending.touch()
        self.supervisor.auto_ap = lambda: False
        self.supervisor.tick(online=False)
        self.clock.now += 10
        self.supervisor = Supervisor(self.clock, auto_ap=lambda: False)
        self.supervisor.ap_is_up = lambda: True
        self.supervisor.start_ap = lambda: self._start()
        self.supervisor.tick(online=False)
        self.clock.now += netmonitor.FIRST_BOOT_GRACE
        self.supervisor.tick(online=False)
        self.assertEqual(self.events, ["start"])
        self.assertTrue(self.pending.exists())

    def test_restored_initial_ap_is_adopted_without_rotating_credentials(self):
        self.pending.touch()
        netmonitor.AP_INFO.write_text("{}")
        self.supervisor.auto_ap = lambda: False
        self.supervisor.tick(online=False)
        self.clock.now += RETRY_INTERVAL
        self.supervisor.tick(online=False)
        self.assertTrue(self.supervisor.ap_active)
        self.assertEqual(self.events, [])
        self.portal.assert_called_once_with(netmonitor.AP_ADDRESS)

    def test_retries_saved_client_without_ap_even_when_ethernet_is_online(self):
        self.supervisor.ap_is_up = lambda: False
        self.supervisor.restore_client = lambda: self.events.append("restore")
        self.supervisor.tick(online=True)
        self.assertEqual(self.events, ["restore"])
        self.clock.now += CLIENT_RETRY_INTERVAL - 1
        self.supervisor.tick(online=True)
        self.assertEqual(self.events, ["restore"])
        self.clock.now += 1
        self.supervisor.tick(online=True)
        self.assertEqual(self.events, ["restore", "restore"])

    def test_does_not_retry_client_while_ap_is_active(self):
        self.supervisor.ap_is_up = lambda: True
        self.supervisor.restore_client = lambda: self.events.append("restore")
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

    def test_connectivity_check_defaults_to_gateway_and_selects_the_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            with patch.object(netmonitor, "CONFIG_PATH", path):
                self.assertEqual(netmonitor.ap_check_mode(), "gateway")
                for stored, expected in (({"auto_ap": True}, "gateway"), ({"ap_check": "internet"}, "internet"),
                                         ({"ap_check": "bogus"}, "gateway")):
                    path.write_text(json.dumps(stored))
                    self.assertEqual(netmonitor.ap_check_mode(), expected)
        with (patch.object(netmonitor, "gateway_status", return_value={"online": True}) as gateway,
              patch.object(netmonitor, "internet_status", return_value={"online": False}) as internet):
            self.supervisor.ap_check = lambda: "gateway"
            self.supervisor.tick()
            self.assertEqual(self.events, [])
            self.supervisor.ap_check = lambda: "internet"
            self.supervisor.tick()
            self.clock.now += OUTAGE_GRACE
            self.supervisor.tick()
        self.assertEqual(self.events, ["start"])
        gateway.assert_called_once_with()
        self.assertEqual(internet.call_count, 2)

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
    def test_stopping_ap_closes_the_captive_portal(self):
        supervisor = Supervisor()
        portal = supervisor.portal = Mock()
        with patch.object(supervisor, "remove_ap_profile"):
            supervisor.stop_ap()
        portal.close.assert_called_once_with()
        self.assertIsNone(supervisor.portal)

    def test_generated_passwords_are_distinct_and_wpa2_length(self):
        passwords = {netmonitor.new_password() for _ in range(20)}
        self.assertEqual(len(passwords), 20)
        self.assertTrue(all(len(p) == 12 for p in passwords))

    def test_keyfile_is_wpa2_shared(self):
        text = netmonitor.render_ap_keyfile("WebDisplay-AB12", "pw", "wlan0")
        for expected in (
            "mode=ap",
            "key-mgmt=wpa-psk",
            "pairwise=ccmp",
            "pmf=1",
            "method=shared",
        ):
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
                json.dumps({"ssid": "WebDisplay-AB12", "password": "pw<>", "address": "10.42.0.1",
                            "error": "Association failed <check router>"})
            )
            with patch.object(kiosk, "local_addresses", return_value=["192.168.0.5"]):
                page = kiosk.fallback_html(root / "none", root / "none", 8443, info)
                plain = kiosk.fallback_html(root / "none", root / "none", 8443, root / "missing")
        self.assertIn("WebDisplay-AB12", page)
        self.assertIn("pw&lt;&gt;", page)
        self.assertIn("https://10.42.0.1:8443/", page)
        self.assertIn("Association failed &lt;check router&gt;", page)
        self.assertNotIn("WebDisplay-AB12", plain)
        self.assertNotIn('class="qr"', plain)

    def test_wifi_qr_payload_escapes_specials(self):
        self.assertEqual(
            kiosk.wifi_qr_payload("a;b", 'p:w\\"x,'),
            'WIFI:T:WPA;S:a\\;b;P:p\\:w\\\\\\"x\\,;;',
        )

    def test_setup_page_embeds_offline_qr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            info = root / "ap.json"
            info.write_text(json.dumps({"ssid": "WebDisplay-AB12", "password": "pw12", "address": "10.42.0.1"}))
            with patch.object(kiosk, "local_addresses", return_value=[]):
                page = kiosk.fallback_html(root / "none", root / "none", 8443, info)
        self.assertIn('<div class="qr"><svg', page)
        self.assertNotIn("http://", page.replace("http://www.w3.org/2000/svg", ""))


class ApCredentialTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.serial = self.root / "serial"
        self.serial.write_text("00000000aabbccdd\x00", encoding="ascii")
        self.machine_id = self.root / "machine-id"
        self.machine_id.write_text("0123456789abcdef0123456789abcdef\n")
        self.password = self.root / "state" / "setup-ap-password"
        for name, path in (("BOARD_SERIAL", self.serial), ("MACHINE_ID", self.machine_id),
                           ("AP_PASSWORD", self.password)):
            patcher = patch.object(netmonitor, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_ssid_uses_board_serial_instead_of_machine_id(self):
        self.assertEqual(netmonitor.setup_ssid(), "WebDisplay-CCDD")
        self.machine_id.write_text("f" * 32)
        self.assertEqual(netmonitor.setup_ssid(), "WebDisplay-CCDD")

    def test_missing_board_serial_uses_machine_id_and_invalid_identity_is_rejected(self):
        self.serial.unlink()
        with self.assertLogs(netmonitor.LOG, level="WARNING"):
            self.assertEqual(netmonitor.setup_ssid(), "WebDisplay-CDEF")
        self.serial.write_text("0000000000000000")
        with self.assertRaisesRegex(ValueError, "identifier"):
            netmonitor.setup_ssid()

    def test_password_is_persisted_privately_and_not_regenerated(self):
        with patch.object(netmonitor, "new_password", wraps=netmonitor.new_password) as generate:
            first = netmonitor.ap_credentials()
            second = netmonitor.ap_credentials()
        self.assertEqual(first, second)
        self.assertEqual(first[0], "WebDisplay-CCDD")
        self.assertEqual(self.password.read_text(), first[1] + "\n")
        self.assertEqual(self.password.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.password.parent.stat().st_mode & 0o777, 0o700)
        generate.assert_called_once_with()

    def test_invalid_saved_password_fails_instead_of_changing_credentials(self):
        self.password.parent.mkdir()
        for content in ("", "short\n", "a" * 64, "invalid\npassword\n"):
            with self.subTest(content=content):
                self.password.write_text(content)
                with patch.object(netmonitor, "new_password") as generate:
                    with self.assertRaises(ValueError):
                        netmonitor.ap_credentials()
                generate.assert_not_called()
                self.assertEqual(self.password.read_text(), content)

    def test_stopping_and_restarting_ap_on_another_adapter_keeps_credentials(self):
        info = self.root / "ap.json"
        keyfile = self.root / "ap.nmconnection"
        with (
            patch.object(netmonitor, "ap_device", side_effect=["wlan0", "wlan1"]),
            patch.object(netmonitor, "current_country", return_value="GB"),
            patch.object(netmonitor, "connection_error", return_value=""),
            patch.object(netmonitor, "run", return_value=Mock(returncode=0)),
            patch.object(netmonitor, "AP_KEYFILE", keyfile),
            patch.object(netmonitor, "AP_INFO", info),
            patch("grp.getgrnam", return_value=Mock(gr_gid=123)),
            patch.object(netmonitor.os, "chown"),
        ):
            supervisor = Supervisor()
            self.assertTrue(supervisor.start_ap())
            first = json.loads(info.read_text())
            supervisor.stop_ap()
            self.assertFalse(info.exists())
            self.assertTrue(self.password.exists())
            self.assertTrue(Supervisor().start_ap())
            self.assertEqual(json.loads(info.read_text()), first)
            self.assertIn("interface-name=wlan1", keyfile.read_text())
            self.assertIn(f"psk={first['password']}", keyfile.read_text())


class StartFailureTests(unittest.TestCase):
    def test_failed_ap_start_restores_saved_client(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(netmonitor, "ap_device", return_value="wlan0"),
                patch.object(netmonitor, "current_country", return_value="GB"),
                patch.object(netmonitor, "ap_credentials",
                             return_value=("WebDisplay-CCDD", "abcdefgh2345")),
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


class ApDeviceTests(unittest.TestCase):
    def pick(self, usb):
        with (
            patch.object(netmonitor, "wifi_devices", return_value=["wlan0", "wlan1"]),
            patch.object(netmonitor, "device_phy", side_effect=lambda d: d),
            patch.object(netmonitor, "ap_capable", return_value=True),
            patch.object(netmonitor, "is_usb", side_effect=lambda d: d in usb),
        ):
            return netmonitor.ap_device()

    def test_prefers_usb_adapter(self):
        self.assertEqual(self.pick({"wlan1"}), "wlan1")

    def test_falls_back_to_first_radio(self):
        self.assertEqual(self.pick(set()), "wlan0")


class AdapterRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = [10_000.0]
        self.supervisor = netmonitor.Supervisor(clock=lambda: self.now[0], auto_ap=lambda: False)

    def recover(self, failures, usb="1-1.3", sees=False, reset=True):
        self.supervisor.client_failures = failures
        with (
            patch.object(netmonitor, "usb_device_id", return_value=usb),
            patch.object(netmonitor, "sees_networks", return_value=sees),
            patch.object(netmonitor, "reset_usb_adapter", return_value=reset) as resetter,
        ):
            self.supervisor.recover_adapter("wlan0")
        return resetter

    def test_resets_blind_usb_adapter_after_repeated_failures(self):
        resetter = self.recover(5)
        resetter.assert_called_once_with("1-1.3")
        self.assertEqual(self.supervisor.client_failures, 0)

    def test_waits_for_repeated_failures(self):
        self.recover(4).assert_not_called()

    def test_leaves_adapter_that_still_sees_networks(self):
        self.recover(9, sees=True).assert_not_called()

    def test_ignores_non_usb_radio(self):
        self.recover(9, usb=None).assert_not_called()

    def test_respects_cooldown(self):
        self.recover(5)
        self.now[0] += 60
        self.recover(5).assert_not_called()
        self.now[0] += netmonitor.ADAPTER_RESET_COOLDOWN
        self.recover(5).assert_called_once()

    def test_usb_id_only_for_usb_interfaces(self):
        with patch.object(netmonitor.os.path, "realpath", return_value="/sys/devices/platform/soc/mmc/net/wlan0"):
            self.assertIsNone(netmonitor.usb_device_id("wlan0"))
        path = "/sys/devices/platform/usb/usb1/1-1/1-1.3/1-1.3:1.0"
        with patch.object(netmonitor.os.path, "realpath", return_value=path):
            self.assertEqual(netmonitor.usb_device_id("wlan0"), "1-1.3")


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



class MissingRadioTests(unittest.TestCase):
    def setUp(self):
        self.now = [10_000.0]
        self.supervisor = netmonitor.Supervisor(clock=lambda: self.now[0], auto_ap=lambda: False)

    def restore(self, device=None, present=True):
        with (
            patch.object(netmonitor, "CLIENT_KEYFILE", Mock(exists=lambda: True)),
            patch.object(netmonitor, "wifi_device", return_value=device),
            patch.object(netmonitor, "builtin_radio_present", return_value=present),
            patch.object(netmonitor, "reload_builtin_driver", return_value=True) as reload,
            patch.object(netmonitor, "run", return_value=Mock(stdout="web-display-wifi\n", returncode=0)),
        ):
            self.supervisor.restore_client()
        return reload.call_count

    def test_reloads_builtin_driver_after_grace_with_cooldown_and_cap(self):
        self.assertEqual(self.restore(), 0)
        self.now[0] += netmonitor.MISSING_RADIO_GRACE
        self.assertEqual(self.restore(), 1)
        self.now[0] += 60
        self.assertEqual(self.restore(), 0)
        for _ in range(netmonitor.MISSING_RADIO_RELOADS + 1):
            self.now[0] += netmonitor.ADAPTER_RESET_COOLDOWN
            self.restore()
        self.assertEqual(self.supervisor.radio_reloads, netmonitor.MISSING_RADIO_RELOADS)

    def test_no_reload_without_builtin_chip_and_reset_when_adapter_returns(self):
        self.restore(present=False)
        self.now[0] += netmonitor.MISSING_RADIO_GRACE
        self.assertEqual(self.restore(present=False), 0)
        self.assertEqual(self.restore(), 1)
        self.restore(device="wlan0")
        self.assertIsNone(self.supervisor.radio_missing_since)
        self.assertEqual(self.supervisor.radio_reloads, 0)

if __name__ == "__main__":
    unittest.main()
