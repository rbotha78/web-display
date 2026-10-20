import http.client
import json
import re
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from webdisplay.carousel import validate_playlist
from webdisplay.scene_player import SceneRenderer, SceneServer, stop_process


def make_scene(kind="dashboard", **changes):
    return validate_playlist({"enabled": True, "scenes": [
        {"id": "scene", "kind": kind, "url": "https://example.com/stream", **changes}
    ]})["scenes"][0]


class SceneServerTests(unittest.TestCase):
    def test_only_display_data_is_exposed_and_ready_is_revision_bound(self):
        server = SceneServer()
        self.addCleanup(server.close)
        server.show(make_scene(text="<script>not markup</script>"))
        connection = http.client.HTTPConnection("127.0.0.1", server.server.server_port)
        self.addCleanup(connection.close)
        connection.request("GET", "/api/scene")
        response = connection.getresponse()
        payload = json.loads(response.read())
        self.assertEqual(response.status, 200)
        self.assertNotIn("unsafe-inline", response.getheader("Content-Security-Policy"))
        self.assertEqual(payload["text"], "<script>not markup</script>")
        self.assertNotIn("url", payload)
        connection.request("GET", "/scene")
        response = connection.getresponse()
        index = response.read().decode()
        self.assertEqual(response.status, 200)
        assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', index)
        self.assertTrue(assets)
        for path in assets:
            connection.request("GET", path)
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertTrue(response.read())
        for path in ("/api/config", "/api/playlist", "/assets/../app.py"):
            connection.request("GET", path)
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 404)
        connection.request("GET", "/api/scene/ready?revision=stale")
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 409)
        self.assertFalse(server.ready.is_set())
        connection.request("GET", "/api/scene/ready?revision=" + payload["revision"])
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 200)
        self.assertTrue(server.ready.is_set())
        server.show(None)
        self.assertFalse(server.ready.is_set())
        connection.request("GET", "/api/scene", headers={"Host": "untrusted.example"})
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 403)

    def test_probe_helper_reports_reachable_and_missing_pages(self):
        server = SceneServer()
        self.addCleanup(server.close)
        for url, expected in ((server.url, 0), (server.url.replace("/scene", "/missing"), 1)):
            result = subprocess.run(
                [sys.executable, "-m", "webdisplay.scene_player", "--probe", url],
                capture_output=True, timeout=10,
            )
            self.assertEqual(result.returncode, expected, result.stderr)


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.process = Mock(pid=12345)
        self.process.poll.return_value = None
        self.launch = Mock(return_value=self.process)
        self.renderer = SceneRenderer(self.launch, Path(self.directory.name))
        stop = patch("webdisplay.scene_player.stop_process")
        self.stop_process = stop.start()
        self.addCleanup(stop.stop)
        self.addCleanup(self.renderer.close)

    def test_dashboard_waits_for_render_and_browser_crashes_fail(self):
        self.renderer.start(make_scene())
        self.assertEqual(self.renderer.state(), "starting")
        self.renderer.server.ready.set()
        self.assertEqual(self.renderer.state(), "playing")
        self.process.poll.return_value = 0
        self.assertEqual(self.renderer.state(), "failed")

    def test_web_probes_before_launch_and_unreachable_scenes_fail(self):
        probe = Mock(pid=12346)
        probe.poll.return_value = None
        with patch("webdisplay.scene_player.subprocess.Popen", return_value=probe):
            self.renderer.start(make_scene("web"))
        self.assertEqual(self.renderer.state(), "starting")
        self.launch.assert_not_called()
        probe.poll.return_value = 1
        self.assertEqual(self.renderer.state(), "failed")
        self.launch.assert_not_called()
        probe.poll.return_value = 0
        with patch("webdisplay.scene_player.subprocess.Popen", return_value=probe):
            self.renderer.start(make_scene("web"))
        self.assertEqual(self.renderer.state(), "playing")
        self.launch.assert_called_once_with("https://example.com/stream")

    def test_stopping_a_stuck_probe_does_not_wait_for_its_network_timeout(self):
        probe = Mock(pid=12346)
        probe.poll.return_value = None
        with patch("webdisplay.scene_player.subprocess.Popen", return_value=probe):
            self.renderer.start(make_scene("web"))
        self.assertEqual(self.renderer.state(), "starting")
        self.renderer.stop()
        self.stop_process.assert_any_call(probe)
        self.assertIsNone(self.renderer.probe_process)
        self.launch.assert_not_called()

    def test_mpv_missing_fails_and_recovery_restores_browser(self):
        with patch("webdisplay.scene_player.subprocess.Popen", side_effect=FileNotFoundError):
            self.renderer.start(make_scene("video"))
        self.assertEqual(self.renderer.state(), "failed")
        self.renderer.recovery()
        self.launch.assert_called_once_with(self.renderer.server.url)
        self.renderer.recovery()
        self.assertEqual(self.launch.call_count, 1)
        self.renderer.start(make_scene())
        self.renderer.stop()
        self.renderer.recovery()
        self.assertEqual(self.launch.call_count, 3)

    def test_video_progress_eof_stall_and_crash(self):
        self.start_video()
        ipc = Mock()
        self.renderer.ipc = ipc
        ipc.recv.side_effect = [b'{"request_id":1,"data":0.0}\n', socket.timeout()]
        with patch("webdisplay.scene_player.time.monotonic", return_value=100):
            self.assertEqual(self.renderer.state(), "playing")
        ipc.recv.side_effect = socket.timeout()
        with patch("webdisplay.scene_player.time.monotonic", return_value=131):
            self.assertEqual(self.renderer.state(), "failed")
        ipc.recv.side_effect = [b'{"event":"end-file","reason":"eof"}\n']
        self.assertEqual(self.renderer.state(), "finished")
        self.process.poll.return_value = 2
        self.assertEqual(self.renderer.state(), "failed")
        self.process.poll.return_value = 0
        self.assertEqual(self.renderer.state(), "finished")
        self.renderer.start(make_scene())
        ipc.close.assert_called_once()
        self.stop_process.assert_any_call(self.process)

    def test_audio_is_enabled_only_for_scenes_that_opt_in(self):
        for enabled, expected in (
            (False, ("--mute=yes", "--audio=no")),
            (
                True,
                (
                    "--aid=auto", "--mute=no",
                    "--audio-device=alsa/hdmi:CARD=vc4hdmi,DEV=0",
                ),
            ),
        ):
            with self.subTest(audio_enabled=enabled):
                self.renderer.scene = make_scene("video", audio_enabled=enabled)
                with patch("webdisplay.scene_player.subprocess.Popen") as spawn:
                    self.renderer._launch_video("https://example.com/stream")
                arguments = spawn.call_args.args[0]
                self.assertTrue(all(option in arguments for option in expected))
                other_options = ("--mute=yes", "--audio=no") if enabled else (
                    "--aid=auto", "--mute=no",
                    "--audio-device=alsa/hdmi:CARD=vc4hdmi,DEV=0",
                )
                self.assertFalse(any(option in arguments for option in other_options))
                self.assertNotIn("--audio=yes", arguments)

    def test_player_ipc_errors_are_failures(self):
        self.start_video()
        self.renderer.ipc = Mock()
        self.renderer.ipc.recv.return_value = b"not json\n"
        self.assertEqual(self.renderer.state(), "failed")

    def start_video(self):
        resolver = Mock(pid=12346)
        resolver.poll.return_value = 0
        resolver.communicate.return_value = (b'{"url":"https://example.com/720p.m3u8"}', None)
        with patch("webdisplay.scene_player.subprocess.Popen", side_effect=[resolver, self.process]) as spawn:
            self.renderer.start(make_scene("video"))
            self.assertEqual(self.renderer.state(), "starting")
        arguments = spawn.call_args.args[0]
        self.assertEqual(arguments[-1], "https://example.com/720p.m3u8")
        self.assertIn("--hls-bitrate=4000000", arguments)

    def test_failed_resolution_skips_player_and_stop_cancels_pending_resolution(self):
        resolver = Mock(pid=12346)
        resolver.poll.return_value = None
        with patch("webdisplay.scene_player.subprocess.Popen", return_value=resolver):
            self.renderer.start(make_scene("video"))
        self.assertEqual(self.renderer.state(), "starting")
        self.renderer.stop()
        self.stop_process.assert_any_call(resolver)
        resolver.stdout.close.assert_called_once()
        resolver.poll.return_value = 1
        resolver.communicate.return_value = (b"", None)
        with patch("webdisplay.scene_player.subprocess.Popen", return_value=resolver) as spawn:
            self.renderer.start(make_scene("video"))
            self.assertEqual(self.renderer.state(), "failed")
            self.assertEqual(spawn.call_count, 1)

    def test_resolved_video_reports_missing_mpv(self):
        resolver = Mock(pid=12346)
        resolver.poll.return_value = 0
        resolver.communicate.return_value = (b'{"url":"https://example.com/720p.m3u8"}', None)
        with patch("webdisplay.scene_player.subprocess.Popen", side_effect=[resolver, FileNotFoundError]):
            self.renderer.start(make_scene("video"))
            self.assertEqual(self.renderer.state(), "failed")
        self.assertIsNone(self.renderer.process)

    def test_failure_status_is_safe_and_successful_retry_clears_it(self):
        resolver = Mock(pid=12346)
        resolver.poll.return_value = 1
        resolver.communicate.return_value = (b'{"error":"not-hls","url":"https://secret.example/?token=private"}', None)
        with patch("webdisplay.scene_player.subprocess.Popen", return_value=resolver):
            self.renderer.start(make_scene("video"))
            self.assertEqual(self.renderer.state(), "failed")
        self.renderer.failure()
        path = Path(self.directory.name) / "carousel-status.json"
        content = path.read_text()
        self.assertIn("non-HLS", content)
        self.assertNotIn("private", content)
        self.assertNotIn("secret.example", content)
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        self.renderer.start(make_scene())
        self.renderer.server.ready.set()
        self.assertEqual(self.renderer.state(), "playing")
        self.assertEqual(json.loads(path.read_text())["failures"], {})

    def test_player_end_file_error_is_reported_without_raw_error_content(self):
        self.start_video()
        self.renderer.ipc = Mock()
        self.renderer.ipc.recv.side_effect = [
            b'{"event":"end-file","reason":"error","file_error":"loading failed"}\n',
        ]
        self.assertEqual(self.renderer.state(), "failed")
        self.renderer.failure()
        status = json.loads((Path(self.directory.name) / "carousel-status.json").read_text())
        self.assertEqual(status["failures"]["scene"]["message"], "Stream could not be opened or read.")

    def test_shutdown_escalates_for_hung_process(self):
        process = Mock(pid=12345)
        process.wait.side_effect = [subprocess.TimeoutExpired("renderer", 5), 0]
        with patch("webdisplay.scene_player.os.killpg") as kill:
            stop_process(process)
        self.assertEqual(kill.call_count, 2)


if __name__ == "__main__":
    unittest.main()
