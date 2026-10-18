import unittest
from unittest.mock import Mock

from webdisplay.carousel import Carousel, validate_playlist


def playlist(*items):
    return validate_playlist({"enabled": True, "scenes": list(items)})


def scene(scene_id, **changes):
    return {"id": scene_id, "kind": "dashboard", **changes}


class PlaylistTests(unittest.TestCase):
    def test_defaults_and_no_cutoff_are_normalized(self):
        saved = playlist(
            scene("clock"),
            scene("web", kind="web", url="https://example.com/"),
            scene("video", kind="video", url="https://example.com/stream"),
            scene("live", kind="video", url="https://example.com/live", duration=None),
        )
        self.assertEqual([item["duration"] for item in saved["scenes"]], [60, 60, 300, None])
        self.assertFalse(validate_playlist({"enabled": False, "scenes": []})["enabled"])

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
        ])
        invalid.append({"enabled": True, "scenes": [scene("same"), scene("same")]})
        for value in invalid:
            with self.subTest(value=str(value)[:80]), self.assertRaises(ValueError):
                validate_playlist(value)


class CarouselTests(unittest.TestCase):
    def setUp(self):
        self.renderer = Mock()
        self.renderer.state.return_value = "playing"

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
        self.assertEqual(self.renderer.stop.call_count, 2)
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
