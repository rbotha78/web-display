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

        def wait(seconds):
            waits.append(seconds)
            if len(waits) >= 4:
                player.shutdown.set()
            return player.shutdown.is_set()

        player.shutdown.wait = wait
        player._stop_player = Mock()
        player._control = Mock(side_effect=OSError("private upstream error"))
        with tempfile.TemporaryDirectory() as directory:
            player.config_path = Path(directory) / "music.json"
            player.config_path.write_text(json.dumps({"enabled": True, "station_key": "station",
                                                     "control_url": self.endpoint, "volume": 30}))
            with patch("webdisplay.music_player.time.monotonic", side_effect=range(0, 100000, 100)):
                player._run()
        actions = [call.args[1] for call in player._control.call_args_list]
        self.assertEqual(actions.count("current"), 3)
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

    def test_control_endpoint_rejects_remote_hosts_credentials_and_queries(self):
        self.assertEqual(control_url(self.endpoint), self.endpoint)
        for value in (self.endpoint.replace("127.0.0.1", "example.org"), self.endpoint + "?token=x",
                      self.endpoint.replace("127.0.0.1", "user@127.0.0.1"),
                      self.endpoint.replace("http:", "https:")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                control_url(value)
