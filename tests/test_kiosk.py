import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from webdisplay.kiosk import DisplayRecovery, clear_stale_profile_locks, fallback_html, offline_html, main


class DisplayRecoveryTests(unittest.TestCase):
    @patch("webdisplay.kiosk.local_addresses", return_value=["192.0.2.1"])
    def test_setup_and_waiting_pages_use_the_same_safe_area(self, _addresses):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            root = Path(directory)
            for percent in (0, 5, 10):
                with self.subTest(percent=percent):
                    setup = fallback_html(root / "pairing", root / "cert", 8443,
                                          safe_area_percent=percent)
                    self.assertIn(f"padding:{percent}vh {percent}vw", setup)
                    self.assertIn(f"padding:{percent}vh {percent}vw", offline_html(percent))
                    self.assertNotIn("overflow:hidden", setup)

    @patch("webdisplay.kiosk.subprocess.run")
    def test_reconnect_enables_output_and_resets_ctm_once(self, run):
        recovery = DisplayRecovery()
        run.return_value.stdout = "HDMI-1 disconnected primary\n"
        self.assertFalse(recovery.check(0))
        run.return_value.stdout = "HDMI-1 connected primary\n\tCTM: 0 0 0\n"
        self.assertFalse(recovery.check(4))
        self.assertTrue(recovery.check(5))
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(["xrandr", "--output", "HDMI-1", "--auto"], commands)
        self.assertIn(["xrandr", "--output", "HDMI-1", "--set", "CTM", "1,0,0,0,1,0,0,0,1"], commands)
        run.reset_mock()
        run.return_value.stdout = "HDMI-1 connected primary 1920x1080+0+0\n\tCTM: 1\n"
        self.assertFalse(recovery.check(10))
        self.assertEqual(run.call_count, 1)

    @patch("webdisplay.kiosk.subprocess.run")
    def test_already_active_reconnect_is_repaired_but_normal_start_is_not(self, run):
        recovery = DisplayRecovery()
        run.return_value.stdout = "HDMI-1 connected primary 1920x1080+0+0\n"
        self.assertFalse(recovery.check(0))
        run.return_value.stdout = "HDMI-1 disconnected\n"
        self.assertFalse(recovery.check(5))
        run.return_value.stdout = "HDMI-1 connected primary 1920x1080+0+0\n"
        self.assertTrue(recovery.check(10))

    @patch("webdisplay.kiosk.subprocess.run")
    def test_inactive_at_start_is_recovered_and_failed_ctm_is_retried(self, run):
        import subprocess
        recovery = DisplayRecovery()
        query = Mock(stdout="HDMI-1 connected primary\n\tCTM: 0\n")
        run.side_effect = [query, Mock(), subprocess.CalledProcessError(1, "xrandr")]
        with self.assertLogs("webdisplay.kiosk", level="ERROR"):
            self.assertFalse(recovery.check(0))
        run.side_effect = None
        run.return_value.stdout = "HDMI-1 connected primary 1920x1080+0+0\n\tCTM: 0\n"
        self.assertTrue(recovery.check(5))


class ProfileLockTests(unittest.TestCase):
    def test_removes_stale_locks_and_keeps_data(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            (profile / "SingletonLock").symlink_to("oldhost-123")
            (profile / "SingletonCookie").symlink_to("456")
            (profile / "SingletonSocket").symlink_to("/tmp/x/SingletonSocket")
            (profile / "Preferences").write_text("{}")
            clear_stale_profile_locks(profile)
            self.assertEqual([p.name for p in profile.iterdir()], ["Preferences"])

    def test_missing_profile_is_fine(self):
        clear_stale_profile_locks(Path("/nonexistent-profile"))


class CarouselModeTests(unittest.TestCase):
    def run_sleep_transition(self, scenes, states, playback_state="playing"):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            root = Path(directory).resolve()
            target, playlist_path = root / "target", root / "playlist.json"
            target.write_text("")
            playlist_path.write_text(json.dumps({"enabled": True, "scenes": scenes}))
            renderer = Mock()
            renderer.active_scene = None
            renderer.state.return_value = playback_state
            iterations = 0

            def next_iteration(_):
                nonlocal iterations
                iterations += 1
                if iterations == len(states):
                    raise KeyboardInterrupt

            arguments = ["kiosk", "--target", str(target), "--playlist", str(playlist_path),
                         "--setup-ap", str(root / "ap"), "--fallback", str(root / "fallback")]
            with patch("sys.argv", arguments), \
                 patch("webdisplay.kiosk.Reachability"), \
                 patch("webdisplay.kiosk.SceneRenderer", return_value=renderer), \
                 patch("webdisplay.music_player.MusicPlayer"), \
                 patch("webdisplay.kiosk.SleepRuntime") as sleep_runtime, \
                 patch("webdisplay.kiosk.stop_process"), \
                 patch("webdisplay.kiosk.OfflineIcon"), \
                 patch("webdisplay.kiosk.DisplayRecovery") as display, \
                 patch("webdisplay.kiosk.signal.signal"), \
                 patch("webdisplay.kiosk.time.sleep", side_effect=next_iteration):
                display.return_value.check.return_value = False
                sleep_runtime.return_value.sleeping.side_effect = states
                sleep_runtime.return_value.status = {"sleeping": False, "scheduled_sleep": False, "wake_until": None}
                with self.assertRaises(KeyboardInterrupt):
                    main()
            return renderer

    def test_sleep_stops_active_or_preparing_video_and_does_not_rebuild_each_poll(self):
        scenes = [{"id": "video", "kind": "video", "url": "https://example.com/live", "duration": None},
                  {"id": "web", "kind": "web", "url": "https://example.com/"}]
        for state in ("starting", "playing"):
            with self.subTest(state=state):
                renderer = self.run_sleep_transition(scenes, [False, True, True, False], state)
                self.assertEqual([call.args[0]["kind"] for call in renderer.start.call_args_list],
                                 ["video", "web", "video"])
                self.assertEqual(renderer.stop.call_count, 2)

    def test_all_video_playlist_uses_recovery_without_starting_video(self):
        renderer = self.run_sleep_transition(
            [{"id": "video", "kind": "video", "url": "https://example.com/live"}], [True, True],
        )
        renderer.start.assert_not_called()
        self.assertEqual(renderer.recovery.call_count, 2)
        renderer.stop.assert_not_called()

    def test_nonvideo_scenes_are_not_restarted_by_sleep_or_wake(self):
        for kind, changes in (
            ("dashboard", {}), ("weather", {}), ("train", {"station_crs": "PAD"}),
            ("web", {"url": "https://example.com/"}),
        ):
            with self.subTest(kind=kind):
                renderer = self.run_sleep_transition(
                    [{"id": kind, "kind": kind, **changes}], [False, True, True, False],
                )
                self.assertEqual(renderer.start.call_count, 1)
                self.assertEqual(renderer.start.call_args.args[0]["kind"], kind)
                renderer.stop.assert_not_called()

    def test_apply_ap_override_resume_and_disable_restore_single_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target-url"
            target.write_text("https://example.com/single\n")
            playlist_path = root / "playlist.json"
            playlist = {"enabled": True, "scenes": [{"id": "clock", "kind": "dashboard"}],
                        "revision": "one"}
            playlist_path.write_text(json.dumps(playlist))
            ap = root / "setup-ap.json"
            fallback = root / "fallback.html"
            renderer = Mock()
            renderer.active_scene = None
            renderer.state.return_value = "playing"
            monitor = Mock(ok=True, failures=0)
            process = Mock()
            process.poll.return_value = None
            iterations = 0
            remote_views = []

            def next_iteration(_):
                from webdisplay.remote_view import load
                nonlocal iterations
                remote_views.append(load(root / "remote-view.json"))
                iterations += 1
                if iterations == 1:
                    playlist["revision"] = "two"
                    playlist_path.write_text(json.dumps(playlist))
                elif iterations == 2:
                    ap.write_text(json.dumps({"ssid": "setup", "password": "test", "address": "test"}))
                elif iterations == 3:
                    ap.unlink()
                elif iterations == 4:
                    playlist["enabled"] = False
                    playlist_path.write_text(json.dumps(playlist))
                else:
                    raise KeyboardInterrupt

            arguments = [
                "kiosk", "--target", str(target), "--playlist", str(playlist_path),
                "--setup-ap", str(ap), "--fallback", str(fallback),
            ]
            with patch("sys.argv", arguments), \
                 patch("webdisplay.kiosk.Reachability", return_value=monitor), \
                 patch("webdisplay.kiosk.SceneRenderer", return_value=renderer), \
                 patch("webdisplay.kiosk.launch", return_value=process) as launch, \
                 patch("webdisplay.kiosk.stop_process"), \
                 patch("webdisplay.kiosk.OfflineIcon"), \
                 patch("webdisplay.kiosk.DisplayRecovery") as display, \
                 patch("webdisplay.kiosk.fallback_html", return_value="setup"), \
                 patch("webdisplay.kiosk.write_fallback"), \
                 patch("webdisplay.kiosk.signal.signal"), \
                 patch("webdisplay.kiosk.time.sleep", side_effect=next_iteration):
                display.return_value.check.return_value = False
                with self.assertRaises(KeyboardInterrupt):
                    main()
            self.assertEqual(renderer.start.call_count, 3)
            self.assertEqual(renderer.close.call_count, 2)
            self.assertEqual([call.args[1] for call in launch.call_args_list],
                             [fallback.as_uri(), "https://example.com/single"])
            self.assertEqual([view["mode"] for view in remote_views],
                             ["recovery", "recovery", "recovery", "recovery", "single"])
            self.assertEqual(remote_views[2]["message"], "Setup access point active")
            self.assertNotIn("password", json.dumps(remote_views))
            self.assertEqual(remote_views[-1]["scene"]["url"], "https://example.com/single")
            self.assertFalse((root / "remote-view.json").exists())


if __name__ == "__main__":
    unittest.main()
