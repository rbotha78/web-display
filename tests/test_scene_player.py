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
from webdisplay.scene_player import RendererStopError, SceneRenderer, SceneServer, stop_process
from webdisplay.browser import BrowserError


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
        with patch("webdisplay.scene_player.PersistentBrowser") as browser, \
             patch("webdisplay.scene_player.SceneWindows"):
            self.renderer = SceneRenderer(self.launch, Path(self.directory.name))
        self.browser = browser.return_value
        self.browser.process = Mock(pid=12346)
        self.browser.process.poll.return_value = None
        self.browser.ready.return_value = True
        stop = patch("webdisplay.scene_player.stop_process")
        self.stop_process = stop.start()
        self.addCleanup(stop.stop)
        self.addCleanup(self.renderer.close)

    def test_dashboard_waits_for_render_and_browser_crashes_fail(self):
        self.renderer.start(make_scene())
        self.assertEqual(self.renderer.state(), "starting")
        self.renderer.server.ready.set()
        self.assertEqual(self.renderer.state(), "playing")
        self.browser.process.poll.return_value = 0
        self.browser.ready.side_effect = BrowserError("exited")
        self.assertEqual(self.renderer.state(), "failed")

    def test_web_waits_for_browser_readiness_and_keeps_previous_scene_visible(self):
        self.renderer.start(make_scene())
        self.renderer.server.ready.set()
        self.assertEqual(self.renderer.state(), "playing")
        self.browser.activate.reset_mock()
        self.browser.ready.return_value = False
        self.renderer.start(make_scene("web"))
        self.assertEqual(self.renderer.state(), "starting")
        self.browser.activate.assert_not_called()
        self.browser.close.assert_not_called()
        self.browser.ready.return_value = True
        self.assertEqual(self.renderer.state(), "playing")
        self.browser.activate.assert_called_once()

    def test_stopping_closes_browser_and_pending_tabs(self):
        self.renderer.start(make_scene("web"))
        pending = self.renderer.pending_tab
        self.renderer.stop()
        self.browser.discard.assert_any_call(pending)
        self.browser.close.assert_called_once()
        self.renderer.windows.close.assert_called_once()

    def test_mpv_missing_fails_and_recovery_restores_browser(self):
        with patch("webdisplay.scene_player.subprocess.Popen", side_effect=FileNotFoundError):
            self.renderer.start(make_scene("video"))
        self.assertEqual(self.renderer.state(), "failed")
        self.renderer.recovery()
        self.browser.prepare.assert_called_once_with(None, self.renderer.server.url)
        self.renderer.recovery()
        self.assertEqual(self.browser.prepare.call_count, 1)
        self.renderer.start(make_scene())
        self.renderer.stop()
        self.renderer.recovery()
        self.assertEqual(self.browser.prepare.call_count, 3)

    def test_video_progress_eof_stall_and_crash(self):
        self.start_video()
        ipc = Mock()
        self.renderer.ipc = ipc
        self.renderer._command = Mock(
            side_effect=lambda command: {"w": 1280, "h": 720}
            if command == ["get_property", "video-out-params"] else False,
        )
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
        self.assertEqual(self.renderer.state(), "starting")
        self.renderer.server.ready.set()
        self.assertEqual(self.renderer.state(), "playing")
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
                )
                self.assertFalse(any(option in arguments for option in other_options))
                self.assertNotIn("--audio=yes", arguments)
                self.assertIn("--audio-device=alsa/hdmi:CARD=vc4hdmi,DEV=0", arguments)

    def test_player_ipc_errors_are_failures(self):
        self.start_video()
        self.renderer.ipc = Mock()
        self.renderer.ipc.recv.return_value = b"not json\n"
        self.assertEqual(self.renderer.state(), "failed")

    def test_plex_bitrate_reaches_resolver_and_player_without_changing_direct_streams(self):
        for source, expected in (("plex", 8_000_000), ("url", 4_000_000)):
            with self.subTest(source=source):
                scene = make_scene(
                    "video", source=source, plex_bitrate_kbps=8000,
                    plex_rating_key="42", url="" if source == "plex" else "https://example.com/live",
                )
                scene["plex_playback_url"] = "http://127.0.0.1:4321/play/cap/start.m3u8"
                resolver = Mock(pid=12346)
                resolver.poll.return_value = 0
                resolver.communicate.return_value = (
                    b'{"url":"https://example.com/720p.m3u8"}', None,
                )
                with patch(
                    "webdisplay.scene_player.subprocess.Popen",
                    side_effect=[resolver, self.process],
                ) as spawn:
                    self.renderer.start(scene)
                    args = spawn.call_args.args[0]
                    self.assertEqual(args[-2:], ["--max-video-bitrate", str(expected)])
                    self.assertEqual(self.renderer.state(), "starting")
                    self.assertIn(f"--hls-bitrate={expected}", spawn.call_args.args[0])
                self.renderer.scene = None
                self.renderer.process = None

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

    def test_shutdown_reports_process_still_alive_after_kill(self):
        process = Mock(pid=12345)
        process.wait.side_effect = subprocess.TimeoutExpired("renderer", 5)
        with patch("webdisplay.scene_player.os.killpg") as kill:
            with self.assertRaisesRegex(RendererStopError, "not exited"):
                stop_process(process)
        self.assertEqual(kill.call_count, 2)

    def test_failed_browser_shutdown_is_contained_and_backs_off_without_replacement(self):
        self.renderer.browser_failed = True
        self.browser.close.side_effect = BrowserError("Browser has not exited after SIGKILL")
        with patch("webdisplay.scene_player.time.monotonic", return_value=100):
            self.renderer.start(make_scene("web"))
            self.assertEqual(self.renderer.state(), "failed")
            self.renderer.start(make_scene("web"))
            self.renderer.recovery()
        self.browser.close.assert_called_once()
        self.browser.prepare.assert_not_called()
        self.assertTrue(self.renderer.browser_failed)
        self.assertEqual(self.browser.process.pid, 12346)
        self.browser.close.side_effect = None

    def test_failed_video_shutdown_retains_process_and_prevents_replacement(self):
        self.renderer.process = self.process
        def stop(process):
            if process is self.process:
                raise RendererStopError("Renderer has not exited after SIGKILL")

        self.stop_process.side_effect = stop
        self.renderer.stop()
        self.assertIs(self.renderer.process, self.process)
        with patch("webdisplay.scene_player.subprocess.Popen") as spawn:
            self.renderer.start(make_scene("video"))
        self.assertEqual(self.renderer.state(), "failed")
        spawn.assert_not_called()
        self.stop_process.side_effect = None

    def test_recovery_does_not_prepare_browser_after_shutdown_failure(self):
        self.browser.close.side_effect = BrowserError("Browser has not exited after SIGKILL")
        self.renderer.recovery()
        self.browser.prepare.assert_not_called()
        self.assertTrue(self.renderer.browser_failed)
        self.browser.close.side_effect = None

    def test_recovery_control_failure_is_not_polled_repeatedly_during_cooldown(self):
        self.renderer.recovering = True
        self.renderer.recovery_retry_at = 200
        self.browser.ready.side_effect = BrowserError("Browser control disconnected")
        with patch("webdisplay.scene_player.time.monotonic", return_value=100):
            self.renderer.recovery()
            self.renderer.recovery()
        self.browser.ready.assert_called_once()
        self.assertTrue(self.renderer.browser_failed)
        self.assertFalse(self.renderer.recovering)

    def test_active_browser_checks_are_bounded_but_exit_is_detected_immediately(self):
        with patch("webdisplay.scene_player.time.monotonic", return_value=100):
            self.renderer.start(make_scene("web"))
            self.assertEqual(self.renderer.state(), "playing")
            for _ in range(10):
                self.assertEqual(self.renderer.state(), "playing")
            self.browser.ready.assert_called_once()
        with patch("webdisplay.scene_player.time.monotonic", return_value=106):
            self.assertEqual(self.renderer.state(), "playing")
        self.assertEqual(self.browser.ready.call_count, 2)
        self.browser.process.poll.return_value = 1
        self.assertEqual(self.renderer.state(), "failed")

    def test_video_reuses_player_and_applies_next_scene_settings(self):
        self.start_video()
        self.renderer.ipc = Mock()
        self.renderer._command = Mock(return_value=False)
        self.renderer.activated = True
        self.renderer.active_scene = self.renderer.scene
        with patch("webdisplay.scene_player.subprocess.Popen"):
            self.renderer.start(make_scene("video", audio_enabled=True))
        with patch("webdisplay.scene_player.subprocess.Popen") as spawn:
            self.renderer._launch_video("https://example.com/next.m3u8")
        spawn.assert_not_called()
        self.renderer._command.assert_any_call(["set_property", "aid", "auto"])
        self.renderer._command.assert_any_call(["loadfile", "https://example.com/next.m3u8", "replace"])
        self.renderer.windows.show_video.assert_not_called()
        self.renderer._activate()
        self.renderer.windows.show_video.assert_called_once()
        self.renderer._command.assert_any_call(["set_property", "mute", False])

    def test_video_handover_waits_for_decoded_output_and_ignores_outgoing_stop(self):
        self.start_video()
        ipc = Mock()
        ipc.recv.side_effect = [
            b'{"event":"end-file","reason":"stop"}\n{"request_id":1,"data":0}\n',
            socket.timeout(),
        ]
        self.renderer.ipc = ipc
        self.renderer._command = Mock(return_value=None)
        self.assertEqual(self.renderer.state(), "starting")
        self.renderer.windows.show_video.assert_not_called()
        ipc.recv.side_effect = [b'{"request_id":2,"data":0}\n', socket.timeout()]
        self.renderer._command.return_value = {"w": 1280, "h": 720}
        self.assertEqual(self.renderer.state(), "playing")
        self.renderer.windows.show_video.assert_called_once()

    def test_paused_video_is_retained_until_browser_is_ready_then_released(self):
        self.start_video()
        self.renderer.ipc = Mock()
        self.renderer._command = Mock(return_value=False)
        self.renderer.activated = True
        self.renderer.active_scene = self.renderer.scene
        self.browser.ready.return_value = False
        self.renderer.start(make_scene("web"))
        self.renderer._command.assert_any_call(["set_property", "pause", True])
        self.renderer._command.assert_any_call(["set_property", "mute", True])
        self.assertEqual(self.renderer.state(), "starting")
        self.assertFalse(any(c.args == (["stop"],) for c in self.renderer._command.call_args_list))
        self.browser.ready.return_value = True
        self.assertEqual(self.renderer.state(), "playing")
        self.renderer._command.assert_any_call(["stop"])
        self.stop_process.assert_any_call(self.process)
        self.assertIsNone(self.renderer.process)
        self.assertIsNone(self.renderer.ipc)

    def test_failed_preparation_cannot_activate_the_previous_video_as_the_new_scene(self):
        self.start_video()
        self.renderer.ipc = Mock()
        self.renderer._command = Mock(return_value=False)
        self.renderer.activated = True
        self.renderer.active_scene = self.renderer.scene
        scene = make_scene("video", source="plex", plex_rating_key="42", url="")
        self.renderer.start(scene)
        self.assertEqual(self.renderer.state(), "failed")
        self.assertFalse(self.renderer.activated)

    def test_control_replies_do_not_swallow_playback_error_events(self):
        self.start_video()
        ipc = Mock()
        self.renderer.ipc = ipc
        ipc.recv.return_value = (
            b'{"event":"end-file","reason":"error","file_error":"loading failed"}\n'
            b'{"request_id":1,"error":"success","data":false}\n'
        )
        self.assertFalse(self.renderer._command(["get_property", "eof-reached"]))
        self.assertEqual(self.renderer.state(), "failed")
        self.assertEqual(self.renderer.error, "Stream could not be opened or read.")

    def test_output_property_can_be_temporarily_unavailable_during_startup(self):
        self.start_video()
        ipc = Mock()
        self.renderer.ipc = ipc
        ipc.recv.return_value = b'{"request_id":1,"error":"property unavailable"}\n'
        self.assertIsNone(self.renderer._command(["get_property", "video-out-params"]))

    def test_browser_control_failure_restarts_browser_after_cooldown(self):
        self.browser.prepare.side_effect = BrowserError("Browser control disconnected")
        self.renderer.start(make_scene())
        self.assertEqual(self.renderer.state(), "failed")
        self.assertTrue(self.renderer.browser_failed)
        self.browser.prepare.side_effect = None
        with patch("webdisplay.scene_player.time.monotonic", return_value=100):
            self.renderer.start(make_scene("web"))
        self.browser.close.assert_called_once()
        self.assertEqual(self.renderer.state(), "failed")
        with patch("webdisplay.scene_player.time.monotonic", return_value=161):
            self.renderer.start(make_scene("web"))
        self.browser.ready.return_value = True
        self.assertEqual(self.renderer.state(), "playing")
        report = json.loads((Path(self.directory.name) / "carousel-status.json").read_text())
        self.assertEqual(report["active_scene"], "scene")
        self.assertIsNone(report["preparing_scene"])


if __name__ == "__main__":
    unittest.main()
