import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from webdisplay.music_player import MusicPlayer, control_url


class MusicPlayerTests(unittest.TestCase):
    endpoint = "http://127.0.0.1:4321/music/" + "a" * 32 + "/control"

    def player(self):
        player = MusicPlayer.__new__(MusicPlayer)
        player.lock = threading.RLock()
        player.paused = False
        player.ipc = Mock()
        player.process = Mock()
        player.buffer = b""
        player.request_id = 0
        player.ended = None
        return player

    def test_sleep_stops_instead_of_pausing_and_wake_requests_a_fresh_current_track(self):
        player = self.player()
        player.shutdown = threading.Event()
        player._stop_player = Mock()
        player._control = Mock(return_value={"track_id": "1"})
        player._load = Mock(return_value=False)
        iterations = 0

        def wait(_):
            nonlocal iterations
            iterations += 1
            if iterations == 4:
                player.shutdown.set()

        player.shutdown.wait = wait
        with tempfile.TemporaryDirectory(dir=".") as directory:
            player.config_path = Path(directory) / "music.json"
            player.config_path.write_text(json.dumps({
                "enabled": True, "station_key": "station", "control_url": self.endpoint, "volume": 30,
            }))
            with patch("webdisplay.music_player.SleepRuntime.sleeping", side_effect=[False, True, True, False]):
                player._run()
        self.assertEqual([call.args[1] for call in player._control.call_args_list],
                         ["current", "stop", "current", "stop"])
        self.assertEqual(player._load.call_count, 2)
        self.assertEqual(player._stop_player.call_count, 4)
        self.assertFalse(player.paused)

    def test_sleep_during_preparation_does_not_load_the_returned_audio(self):
        player = self.player()
        player.sleep_runtime = Mock()
        player.sleep_runtime.sleeping.return_value = True
        player._launch = Mock()
        player._command = Mock()
        track = {"url": self.endpoint.removesuffix("control") + "stream/a", "track_id": "1"}
        self.assertFalse(player._load(track, self.endpoint, 30))
        player._launch.assert_not_called()
        player._command.assert_not_called()

    def test_sleep_watcher_interrupts_mpv_without_waiting_for_the_queue_worker(self):
        player = self.player()
        process = player.process
        player.shutdown = Mock()
        player.shutdown.wait.side_effect = [False, False, True]
        player.sleep_runtime = Mock()
        player.sleep_runtime.sleeping.return_value = True
        player._stop_player = Mock(side_effect=lambda: setattr(player, "process", None))
        with patch("webdisplay.music_player.stop_process") as stop:
            player._watch_sleep()
        stop.assert_called_once_with(process)
        player._stop_player.assert_called_once()

    def test_corrupt_sleep_runtime_stops_music_even_without_carousel(self):
        player = self.player()
        player.shutdown = threading.Event()
        player.shutdown.wait = lambda _: player.shutdown.set()
        player._stop_player = Mock()
        player._control = Mock()
        with tempfile.TemporaryDirectory(dir=".") as directory:
            player.config_path = Path(directory) / "music.json"
            player.config_path.write_text(json.dumps({
                "enabled": True, "station_key": "station", "control_url": self.endpoint, "volume": 30,
            }))
            player.config_path.with_name("sleep.json").write_text("invalid")
            with self.assertLogs("webdisplay.sleep", level="ERROR"):
                player._run()
        player._control.assert_not_called()
        player._stop_player.assert_called()

    def test_pause_releases_audio_device_and_resume_preserves_file(self):
        player = self.player()
        player._command = Mock()
        player.pause_for_video(True)
        self.assertEqual(player._command.call_args_list[0].args[0], ["set_property", "pause", True])
        self.assertEqual(player._command.call_args_list[1].args[0], ["set_property", "aid", "no"])
        player._command.reset_mock()
        player.pause_for_video(False)
        self.assertEqual(player._command.call_args_list[0].args[0], ["set_property", "aid", "auto"])
        self.assertEqual(player._command.call_args_list[1].args[0], ["set_property", "pause", False])

    def test_output_change_restarts_music_with_selected_device(self):
        player = self.player()
        player.shutdown = threading.Event()
        player._stop_player = Mock()
        player._control = Mock(return_value={"track_id": "1"})
        player._load = Mock(return_value=False)
        with tempfile.TemporaryDirectory() as directory:
            player.config_path = Path(directory) / "music.json"
            settings = {"enabled": True, "station_key": "station", "control_url": self.endpoint,
                        "volume": 30}
            player.config_path.write_text(json.dumps(settings))

            def wait(_):
                if player.audio_output == "alsa/plughw:CARD=USB,DEV=0":
                    player.shutdown.set()
                else:
                    settings["audio_output"] = "alsa/plughw:CARD=USB,DEV=0"
                    player.config_path.write_text(json.dumps(settings))

            player.shutdown.wait = wait
            player._run()
        self.assertEqual(player.audio_output, "alsa/plughw:CARD=USB,DEV=0")
        self.assertEqual(player._load.call_count, 2)
        self.assertEqual(player._stop_player.call_count, 3)

    def test_next_track_reuses_process_and_defers_loading_while_video_paused(self):
        player = self.player()
        process = player.process
        player.paused = True
        player._command = Mock()
        player._launch = Mock()
        track = {"url": self.endpoint.removesuffix("control") + "stream/b", "track_id": "2"}
        self.assertFalse(player._load(track, self.endpoint, 20))
        player._command.assert_not_called()
        player.paused = False
        self.assertTrue(player._load(track, self.endpoint, 20))
        player._launch.assert_not_called()
        self.assertIs(player.process, process)
        player._command.assert_any_call(["loadfile", track["url"], "replace"])
        with self.assertRaises(ValueError):
            player._load({"url": "http://example.invalid/audio"}, self.endpoint, 20)

    def test_eof_event_is_preserved_with_unavailable_time_position(self):
        player = self.player()
        player.ipc.recv.return_value = (
            b'{"event":"end-file","reason":"eof"}\n'
            b'{"request_id":1,"error":"property unavailable"}\n')
        self.assertIsNone(player._command(["get_property", "time-pos"]))
        self.assertEqual(player.ended, "eof")

    def test_network_retries_are_bounded_and_stop_player(self):
        player = self.player()
        player.shutdown = threading.Event()
        waits = []
        clock = [0.0]

        def wait(seconds):
            waits.append(seconds)
            clock[0] += seconds
            if sum(call.args[1] == "current" for call in player._control.call_args_list) >= 4:
                player.shutdown.set()
            return player.shutdown.is_set()

        player.shutdown.wait = wait
        player._stop_player = Mock()
        player._control = Mock(side_effect=OSError("private upstream error"))
        with tempfile.TemporaryDirectory() as directory:
            player.config_path = Path(directory) / "music.json"
            player.config_path.write_text(json.dumps({"enabled": True, "station_key": "station",
                                                     "control_url": self.endpoint, "volume": 30}))
            with patch("webdisplay.music_player.time.monotonic", side_effect=lambda: clock[0]):
                player._run()
        actions = [call.args[1] for call in player._control.call_args_list]
        self.assertEqual(actions.count("current"), 4)
        self.assertIn("error", actions)
        player._stop_player.assert_called()

    def test_enabling_music_during_audible_video_does_not_load_audio(self):
        player = self.player()
        player.paused = True
        player.shutdown = threading.Event()
        player.shutdown.wait = lambda _: player.shutdown.set()
        player._stop_player = Mock()
        player._control = Mock()
        with tempfile.TemporaryDirectory() as directory:
            player.config_path = Path(directory) / "music.json"
            player.config_path.write_text(json.dumps({"enabled": True, "station_key": "station",
                                                     "control_url": self.endpoint, "volume": 30}))
            player._run()
        self.assertNotIn("current", [call.args[1] for call in player._control.call_args_list])

    def test_server_recovers_after_three_failures_without_changing_settings(self):
        player = self.player()
        player.process.poll.return_value = None
        player.shutdown = threading.Event()
        player._stop_player = Mock()
        player._load = Mock(return_value=True)
        player._command = Mock(return_value=1.0)
        clock = [0.0]
        attempts = []
        track = {"track_id": "fresh", "url": self.endpoint.removesuffix("control") + "stream/fresh"}

        def control(endpoint, action, **kwargs):
            if action == "current":
                attempts.append(clock[0])
                if len(attempts) <= 3:
                    raise OSError("Plex is restarting")
                return track
            return {}

        def wait(seconds):
            clock[0] += seconds
            if player._load.called:
                player.shutdown.set()
            self.assertLess(clock[0], 100)
            return player.shutdown.is_set()

        player.shutdown.wait = wait
        player._control = Mock(side_effect=control)
        with tempfile.TemporaryDirectory() as directory:
            player.config_path = Path(directory) / "music.json"
            player.config_path.write_text(json.dumps({
                "enabled": True, "station_key": "station", "control_url": self.endpoint, "volume": 30,
            }))
            with patch("webdisplay.music_player.time.monotonic", side_effect=lambda: clock[0]):
                player._run()
        self.assertEqual(attempts, [0, 5, 20, 80])
        player._load.assert_called_once_with(track, self.endpoint, 30)
        self.assertIn("error", [call.args[1] for call in player._control.call_args_list])

    def test_control_endpoint_rejects_remote_hosts_credentials_and_queries(self):
        self.assertEqual(control_url(self.endpoint), self.endpoint)
        for value in (self.endpoint.replace("127.0.0.1", "example.org"), self.endpoint + "?token=x",
                      self.endpoint.replace("127.0.0.1", "user@127.0.0.1"),
                      self.endpoint.replace("http:", "https:")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                control_url(value)
