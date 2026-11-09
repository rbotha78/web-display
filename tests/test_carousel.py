import unittest
from unittest.mock import Mock

from webdisplay.carousel import Carousel, validate_playlist


def playlist(*items):
    return validate_playlist({"enabled": True, "scenes": list(items)})


def scene(scene_id, **changes):
    return {"id": scene_id, "kind": "dashboard", **changes}


class PlaylistTests(unittest.TestCase):
    def test_weather_scene_and_optional_dashboard_widget(self):
        saved = playlist(scene("weather", kind="weather"), scene("widgets", show_weather=True))
        self.assertEqual(saved["scenes"][0]["kind"], "weather")
        self.assertTrue(saved["scenes"][1]["show_weather"])
        self.assertFalse(playlist(scene("existing"))["scenes"][0]["show_weather"])
        with self.assertRaises(ValueError):
            playlist(scene("bad", show_weather="yes"))

    def test_defaults_and_no_cutoff_are_normalized(self):
        saved = playlist(
            scene("clock"),
            scene("web", kind="web", url="https://example.com/"),
            scene("video", kind="video", url="https://example.com/stream"),
            scene("train", kind="train", station_crs="pad"),
            scene("live", kind="video", url="https://example.com/live", duration=None),
        )
        self.assertEqual([item["duration"] for item in saved["scenes"]], [60, 60, 300, 300, None])
        self.assertEqual(saved["scenes"][3]["station_crs"], "PAD")
        self.assertEqual(saved["scenes"][3]["destination_crs"], "")
        self.assertFalse(saved["scenes"][2]["audio_enabled"])
        self.assertFalse(validate_playlist({"enabled": False, "scenes": []})["enabled"])

    def test_train_destination_filter_is_optional_uppercase_and_validated(self):
        filtered = playlist(scene(
            "filtered", kind="train", station_crs="hww", destination_crs="wfj", filter_station_crs="bwo",
        ))["scenes"][0]
        self.assertEqual(filtered["destination_crs"], "WFJ")
        self.assertEqual(filtered["filter_station_crs"], "BWO")
        for invalid in ("WF", "WFJJ", "123", None, True, 123):
            for field in ("destination_crs", "filter_station_crs"):
                with self.subTest(field=field, value=invalid), self.assertRaises(ValueError):
                    playlist(scene("bad", kind="train", station_crs="HWW", **{field: invalid}))

    def test_video_audio_setting_is_validated_and_normalized(self):
        disabled = playlist(scene("silent", kind="video", url="https://example.com/stream"))
        enabled = playlist(scene(
            "sound", kind="video", url="https://example.com/stream", audio_enabled=True,
        ))
        self.assertFalse(disabled["scenes"][0]["audio_enabled"])
        self.assertTrue(enabled["scenes"][0]["audio_enabled"])
        with self.assertRaisesRegex(ValueError, "Video audio"):
            playlist(scene(
                "invalid", kind="video", url="https://example.com/stream", audio_enabled="yes",
            ))

    def test_previous_departure_is_optional_and_validated_per_train_scene(self):
        saved = playlist(
            scene("existing", kind="train", station_crs="PAD"),
            scene("hidden", kind="train", station_crs="PAD", show_previous_departure=False),
            scene("shown", kind="train", station_crs="PAD", show_previous_departure=True),
        )
        self.assertEqual(
            [item["show_previous_departure"] for item in saved["scenes"]], [True, False, True],
        )
        self.assertEqual(validate_playlist(saved), saved)
        for invalid in ("false", 0, None):
            with self.subTest(value=invalid), self.assertRaisesRegex(ValueError, "Show previous departure"):
                playlist(scene("bad", kind="train", station_crs="PAD", show_previous_departure=invalid))

    def test_plex_bitrate_override_is_optional_and_bounded(self):
        default = playlist(scene(
            "plex-default", kind="video", source="plex", plex_rating_key="42",
        ))["scenes"][0]
        custom = playlist(scene(
            "plex-custom", kind="video", source="plex", plex_rating_key="42",
            plex_bitrate_kbps=6500,
        ))["scenes"][0]
        self.assertIsNone(default["plex_bitrate_kbps"])
        self.assertEqual(custom["plex_bitrate_kbps"], 6500)
        for value in (499, 20001, True, "6500"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                playlist(scene(
                    "invalid", kind="video", source="plex", plex_rating_key="42",
                    plex_bitrate_kbps=value,
                ))

    def test_rejects_invalid_playlist_and_scenes(self):
        invalid = [
            None, {}, {"enabled": "true", "scenes": []},
            {"enabled": True, "scenes": []},
            {"enabled": True, "scenes": [scene("off", enabled=False)]},
            {"enabled": False, "scenes": [scene(str(i)) for i in range(31)]},
        ]
        invalid.extend({"enabled": True, "scenes": [item]} for item in [
            None, scene("../escape"), scene("x", kind="image"),
            scene("x", duration=True), scene("x", duration=4), scene("x", duration=86401),
            scene("x", duration=None), scene("x", enabled="yes"),
            scene("x", kind="video", url="file:///etc/passwd"),
            scene("x", kind="web", url="https://user:pass@example.com"),
            scene("x", kind="web", url="https://example.com:99999"),
            scene("x", kind="video", url=""),
            scene("x", text="x" * 2001), scene("x", layout="arbitrary"),
            scene("x", kind="train", station_crs=""),
        ])
        invalid.append({"enabled": True, "scenes": [scene("same"), scene("same")]})
        for value in invalid:
            with self.subTest(value=str(value)[:80]), self.assertRaises(ValueError):
                validate_playlist(value)


class CarouselTests(unittest.TestCase):
    def setUp(self):
        self.renderer = Mock()
        self.renderer.state.return_value = "playing"

    def test_countdown_uses_playback_start_and_resets_at_scene_change(self):
        carousel = Carousel(playlist(scene("a", duration=5), scene("b", duration=10)), self.renderer)
        carousel.tick(0)
        self.renderer.countdown.assert_called_with(None)
        self.renderer.state.return_value = "starting"
        carousel.tick(3)
        self.renderer.countdown.assert_called_with(None)
        self.renderer.state.return_value = "playing"
        carousel.tick(4)
        self.renderer.countdown.assert_called_with(0.0)
        carousel.tick(5.2)
        self.assertAlmostEqual(self.renderer.countdown.call_args.args[0], 0.24)
        carousel.tick(9)
        self.renderer.countdown.assert_called_with(None)
        carousel.tick(10)
        self.renderer.countdown.assert_called_with(0.0)

    def test_uncapped_video_countdown_has_no_deadline(self):
        carousel = Carousel(playlist(scene(
            "video", kind="video", duration=None, url="https://example.com/live",
        )), self.renderer)
        carousel.tick(0)
        carousel.tick(1)
        self.renderer.countdown.assert_called_with(None)

    def test_loops_in_order_skips_disabled_and_starts_timer_after_startup(self):
        carousel = Carousel(playlist(
            scene("a", duration=5), scene("off", enabled=False), scene("b", duration=5),
        ), self.renderer)
        carousel.tick(0)
        self.assertEqual(carousel.current["id"], "a")
        self.renderer.state.return_value = "starting"
        carousel.tick(4)
        self.renderer.state.return_value = "playing"
        carousel.tick(10)
        carousel.tick(14)
        self.assertEqual(carousel.current["id"], "a")
        carousel.tick(15)
        self.assertEqual(carousel.current["id"], "b")
        carousel.tick(16)
        carousel.tick(21)
        self.assertEqual(carousel.current["id"], "a")
        self.renderer.stop.assert_not_called()
        carousel.stop()
        self.renderer.stop.assert_called_once()
        restarted = Carousel(playlist(scene("a"), scene("b")), self.renderer)
        restarted.tick(100)
        self.assertEqual(restarted.current["id"], "a")

    def test_failed_scenes_cool_down_and_recover_without_rapid_loops(self):
        carousel = Carousel(playlist(scene("a"), scene("b")), self.renderer)
        carousel.tick(0)
        self.renderer.state.return_value = "failed"
        carousel.tick(1)
        self.assertEqual(carousel.current["id"], "b")
        carousel.tick(2)
        self.assertIsNone(carousel.current)
        for now in range(3, 61):
            carousel.tick(now)
        self.assertEqual(self.renderer.start.call_count, 2)
        self.renderer.recovery.assert_called()
        carousel.tick(61)
        self.assertEqual(carousel.current["id"], "a")

    def test_startup_timeout_is_separate_from_scene_duration(self):
        carousel = Carousel(playlist(scene("a", duration=5)), self.renderer)
        carousel.tick(0)
        self.renderer.state.return_value = "starting"
        carousel.tick(29)
        self.assertIsNotNone(carousel.current)
        carousel.tick(30)
        self.assertIsNone(carousel.current)
        carousel.tick(89)
        self.assertIsNone(carousel.current)
        carousel.tick(90)
        self.assertEqual(carousel.current["id"], "a")

    def test_late_playback_does_not_bypass_startup_deadline(self):
        carousel = Carousel(playlist(scene("late")), self.renderer)
        carousel.tick(0)
        carousel.tick(31)
        self.assertIsNone(carousel.current)
        self.renderer.recovery.assert_called_once()

    def test_video_finishes_before_cutoff_and_live_stream_can_disable_it(self):
        carousel = Carousel(playlist(
            scene("video", kind="video", url="https://example.com/a", duration=300),
            scene("live", kind="video", url="https://example.com/live", duration=None),
        ), self.renderer)
        carousel.tick(0)
        carousel.tick(1)
        self.renderer.state.return_value = "finished"
        carousel.tick(12)
        self.assertEqual(carousel.current["id"], "live")
        self.renderer.state.return_value = "playing"
        carousel.tick(13)
        carousel.tick(1000000)
        self.assertEqual(carousel.current["id"], "live")

    def test_video_cutoff_advances_even_if_player_never_exits(self):
        carousel = Carousel(playlist(
            scene("video", kind="video", url="https://example.com/a", duration=5), scene("next"),
        ), self.renderer)
        carousel.tick(0)
        carousel.tick(1)
        carousel.tick(6)
        self.assertEqual(carousel.current["id"], "next")


if __name__ == "__main__":
    unittest.main()
