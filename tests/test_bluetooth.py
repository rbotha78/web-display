import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from webdisplay import bluetooth

SPEAKER = "AA:BB:CC:DD:EE:01"
INFO = f"""Device {SPEAKER} (public)
\tName: Kitchen Speaker
\tAlias: Kitchen Speaker
\tClass: 0x00240414
\tIcon: audio-card
\tPaired: yes
\tTrusted: yes
\tConnected: no
\tUUID: Audio Sink                (0000110b-0000-1000-8000-00805f9b34fb)
"""


class ParsingTests(unittest.TestCase):
    def test_config_validation_normalizes_and_rejects(self):
        self.assertEqual(bluetooth.validate_bluetooth_config({}),
                         {"address": "", "name": "", "enabled": False, "delay_ms": 0})
        value = bluetooth.validate_bluetooth_config(
            {"address": SPEAKER.lower(), "name": "Kitchen\x07", "enabled": True, "delay_ms": -250})
        self.assertEqual(value, {"address": SPEAKER, "name": "Kitchen", "enabled": True, "delay_ms": -250})
        for invalid in ({"address": "nope"}, {"delay_ms": 1001}, {"delay_ms": True}, {"enabled": "yes"}):
            with self.assertRaises(ValueError):
                bluetooth.validate_bluetooth_config(invalid)
        self.assertEqual(bluetooth.output_for(SPEAKER), f"alsa/bluealsa:DEV={SPEAKER},PROFILE=a2dp")
        self.assertTrue(bluetooth.is_bluetooth_output(bluetooth.output_for(SPEAKER)))
        self.assertFalse(bluetooth.is_bluetooth_output("alsa/bluealsa:DEV=00:11,PROFILE=sco"))

    def test_parses_bluetoothctl_output_with_colours(self):
        info = bluetooth.parse_info(bluetooth._clean("\x1b[0;94m[bluetoothctl]> \x1b[0m\r" + INFO))
        self.assertEqual(info, {"name": "Kitchen Speaker", "paired": True, "trusted": True,
                                "connected": False, "audio": True})
        self.assertIsNone(bluetooth.parse_info(f"Device {SPEAKER} not available\n"))
        self.assertEqual(bluetooth.parse_devices(f"Device {SPEAKER} Kitchen\nDevice 11:22:33:44:55:66 \n"),
                         [(SPEAKER, "Kitchen"), ("11:22:33:44:55:66", "")])


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.desired = Path(self.directory.name) / "bluetooth.json"
        self.status = Path(self.directory.name) / "bluetooth-status.json"

    def tearDown(self):
        self.directory.cleanup()

    def write(self, desired, status):
        self.desired.write_text(json.dumps(desired))
        self.status.write_text(json.dumps(status))

    def test_uses_connected_speaker_with_delay_and_falls_back_otherwise(self):
        wired = "alsa/hdmi:CARD=vc4hdmi,DEV=0"
        now = time.time()
        desired = {"address": SPEAKER, "enabled": True, "delay_ms": 150}
        self.write(desired, {"address": SPEAKER, "connected": True, "updated": now})
        self.assertEqual(bluetooth.active_output(wired, self.desired, self.status, now),
                         (bluetooth.output_for(SPEAKER), 0.15))
        cases = [
            ({**desired, "enabled": False}, {"address": SPEAKER, "connected": True, "updated": now}),
            (desired, {"address": SPEAKER, "connected": False, "updated": now}),
            (desired, {"address": SPEAKER, "connected": True, "updated": now - 120}),
            (desired, {"address": "11:22:33:44:55:66", "connected": True, "updated": now}),
            ({"address": "bad", "enabled": True}, {"address": SPEAKER, "connected": True, "updated": now}),
        ]
        for desired_value, status in cases:
            self.write(desired_value, status)
            self.assertEqual(bluetooth.active_output(wired, self.desired, self.status, now), (wired, 0.0))
        self.status.unlink()
        self.assertEqual(bluetooth.active_output(wired, self.desired, self.status, now), (wired, 0.0))


class SupervisorTests(unittest.TestCase):
    def test_reconnects_paired_speaker_with_backoff_and_publishes_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bluetooth.json").write_text(json.dumps({"address": SPEAKER, "enabled": True}))
            clock = [100.0]
            connected = {"value": False}

            def info(_address):
                return {"name": "Kitchen", "paired": True, "trusted": True,
                        "connected": connected["value"], "audio": True}

            with patch.multiple(bluetooth, DESIRED_PATH=root / "bluetooth.json",
                                STATUS_PATH=root / "status.json", LOCK_PATH=root / "lock"), \
                    patch.object(bluetooth, "adapter_present", return_value=True), \
                    patch.object(bluetooth, "device_info", side_effect=info), \
                    patch.object(bluetooth, "prepare"), \
                    patch.object(bluetooth, "pcm_present", return_value=True), \
                    patch.object(bluetooth, "connect", return_value=False) as connect:
                supervisor = bluetooth.Supervisor(clock=lambda: clock[0])
                self.assertFalse(supervisor.tick()["connected"])
                supervisor.tick()  # Backed off: no second attempt yet.
                self.assertEqual(connect.call_count, 1)
                clock[0] += 15
                supervisor.tick()
                self.assertEqual(connect.call_count, 2)
                connected["value"] = True
                status = supervisor.tick()
                self.assertTrue(status["connected"])
                published = json.loads((root / "status.json").read_text())
                self.assertEqual((published["address"], published["connected"]), (SPEAKER, True))
                self.assertEqual(connect.call_count, 2)


class HelperTests(unittest.TestCase):
    def test_rejects_invalid_requests_with_json_error(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(bluetooth, "LOCK_PATH", Path(directory) / "lock"), \
                patch("sys.stdin.read", return_value=json.dumps({"action": "remove", "address": "x"})), \
                patch("builtins.print") as output:
            self.assertEqual(bluetooth.helper(), 1)
        self.assertEqual(json.loads(output.call_args[0][0]), {"error": "Invalid Bluetooth address"})


if __name__ == "__main__":
    unittest.main()
