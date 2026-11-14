import json
import tempfile
import threading
import time
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
        player.user_paused = False
        player.ipc = Mock()
        player.process = Mock()
        player.buffer = b""
        player.request_id = 0
        player.ended = None
        player._now_playing = {"state": "disabled", "title": "", "artist": ""}
        return player

    def test_song_is_announced_only_after_playback_has_started(self):
        player = self.player()
        player.process.poll.return_value = None
        player.shutdown = threading.Event()
        player._stop_player = Mock()
        player._load = Mock(return_value=True)
        player._control = Mock(return_value={"track_id": "1", "title": "Song", "artist": "Artist"})
        player._command = Mock(side_effect=[None, 0.5])
        seen = []

        def wait(_):
            seen.append(player.now_playing())
            if len(seen) == 2:
                player.shutdown.set()

        player.shutdown.wait = wait
        with tempfile.TemporaryDirectory() as directory:
            player.config_path = Path(directory) / "music.json"
            player.config_path.write_text(json.dumps({
                "enabled": True, "station_key": "station", "control_url": self.endpoint, "volume": 30,
            }))
            player._run()
        self.assertEqual(seen, [
            {"state": "waiting", "title": "", "artist": ""},
            {"state": "playing", "title": "Song", "artist": "Artist"},
        ])

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
        player._now_playing = {"state": "playing", "title": "Song", "artist": "Artist"}
        player._command = Mock()
        player.pause_for_video(True)
        self.assertEqual(player.now_playing(), {"state": "paused", "title": "Song", "artist": "Artist"})
        self.assertEqual(player._command.call_args_list[0].args[0], ["set_property", "pause", True])
        self.assertEqual(player._command.call_args_list[1].args[0], ["set_property", "aid", "no"])
        player._command.reset_mock()
        player.pause_for_video(False)
        self.assertEqual(player.now_playing()["state"], "playing")
        self.assertEqual(player._command.call_args_list[0].args[0], ["set_property", "aid", "auto"])
        self.assertEqual(player._command.call_args_list[1].args[0], ["set_property", "pause", False])

    def test_video_handover_cannot_resume_manually_paused_music(self):
        player = self.player()
        player.user_paused = True
        player._now_playing = {"state": "playing", "title": "Song", "artist": "Artist"}
        player._command = Mock()
        player.pause_for_video(True)
        player._command.reset_mock()
        player.pause_for_video(False)
        self.assertEqual(player.now_playing()["state"], "paused")
        player._command.assert_any_call(["set_property", "pause", True])
        player._command.assert_any_call(["set_property", "aid", "no"])
        player._launch = Mock()
        self.assertFalse(player._load({
            "url": self.endpoint.removesuffix("control") + "stream/a", "track_id": "1",
        }, self.endpoint, 30))
        player._launch.assert_not_called()

    def test_runtime_pause_skip_and_resume_preserve_queue_and_skip_once(self):
        player = self.player()
        player.process.poll.return_value = None
        player.shutdown = threading.Event()
        player._stop_player = Mock()
        player._load = Mock(return_value=True)
        player._command = Mock(return_value=1.0)
        iterations = 0
        observed = []
        current = {"track_id": "1", "title": "First", "artist": "Artist"}
        next_track = {"track_id": "2", "title": "Second", "artist": "Artist"}
        player._control = Mock(side_effect=lambda _, action, **kwargs: next_track if action == "next" else current)
        with tempfile.TemporaryDirectory() as directory:
            player.config_path = Path(directory) / "music.json"
            settings = {"enabled": True, "station_key": "station", "control_url": self.endpoint, "volume": 30}
            player.config_path.write_text(json.dumps(settings))

            def wait(_):
                nonlocal iterations
                observed.append(player.now_playing())
                iterations += 1
                if iterations == 1:
                    settings["paused"] = True
                elif iterations == 2:
                    settings["skip"] = {"request_id": "skip-1", "track_id": "1"}
                elif iterations == 3:
                    self.assertEqual(player._load.call_count, 1)
                    settings["paused"] = False
                elif iterations == 4:
                    settings["skip"] = {"request_id": "late-skip", "track_id": "1"}
                elif iterations == 5:
                    player.shutdown.set()
                player.config_path.write_text(json.dumps(settings))

            player.shutdown.wait = wait
            player._run()
        self.assertEqual([call.args[1] for call in player._control.call_args_list].count("next"), 1)
        self.assertEqual(player._load.call_count, 2)
        self.assertEqual(observed[1]["state"], "paused")
        self.assertEqual(observed[-1]["title"], "Second")
        player._command.assert_any_call(["stop"])
        # Only initial setup and final cleanup stop the process, not pause/skip.
        self.assertEqual(player._stop_player.call_count, 2)

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

    def test_connected_speaker_switches_output_live_and_offsets_group_clock(self):
        player = self.player()
        player._command = Mock(return_value=None)
        player.audio_output = "alsa/hdmi:CARD=vc4hdmi,DEV=0"
        speaker = "AA:BB:CC:DD:EE:01"
        with tempfile.TemporaryDirectory() as directory:
            player.config_path = Path(directory) / "music.json"
            (Path(directory) / "bluetooth.json").write_text(
                json.dumps({"address": speaker, "enabled": True, "delay_ms": 200}))
            status = Path(directory) / "bluetooth-status.json"
            status.write_text(json.dumps({"address": speaker, "connected": True, "updated": time.time()}))
            wired, output = player._resolve_output({})
            player._apply_output(output)
            self.assertEqual(player.output_delay, 0.2)
            player._command.assert_called_once_with(["set_property", "audio-device",
                                                     f"alsa/bluealsa:DEV={speaker},PROFILE=a2dp"])
            status.write_text(json.dumps({"address": speaker, "connected": False, "updated": time.time()}))
            _, output = player._resolve_output({})
            player._apply_output(output)
            self.assertEqual((player.audio_output, player.output_delay), (wired, 0.0))
        # Playing ahead of the group by the speaker delay: 10 s into a track heard 0.2 s late.
        player.output_delay = 0.2
        player.sync = {"track": "1", "armed": True, "settle_at": 0.0, "volume": 30}
        player.process.poll.return_value = None
        player._command = Mock(return_value=10.2)
        player.sleep_runtime = Mock()
        with patch("webdisplay.music_player.time.time", return_value=1010.0):
            player._sync_apply({"state": "playing", "url": self.endpoint.removesuffix("control") + "stream/" + "b" * 32,
                                "track_id": "1", "started_at": 1000.0, "duration": 100},
                               self.endpoint, False, False, 30)
        player._command.assert_called_with(["set_property", "speed", 1.0])

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
