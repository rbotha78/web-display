import sys
import types
import unittest
from unittest.mock import Mock, patch

from webdisplay.scene_windows import SceneWindows
from webdisplay.display import countdown_position, now_playing_geometry


class SceneWindowsTests(unittest.TestCase):
    def test_now_playing_floats_at_top_left_inside_each_screen_safe_area(self):
        for width, height in ((1920, 1080), (1280, 720), (1152, 864), (800, 600)):
            for percent in (0, 5, 10):
                with self.subTest(size=(width, height), percent=percent):
                    x, y, strip_width, strip_height = now_playing_geometry(width, height, percent)
                    self.assertGreaterEqual(x, width * percent / 100 - 1)
                    self.assertLessEqual(x + strip_width, width * (1 - percent / 100) + 1)
                    self.assertLessEqual(y + strip_height, height * (1 - percent / 100) + 1)
                    self.assertGreaterEqual(y, height * percent / 100 - 1)
                    self.assertLessEqual(strip_width, width / 2)

    def test_floating_now_playing_does_not_resize_or_focus_browser_or_video(self):
        xlib = types.ModuleType("Xlib")
        xlib.X = types.SimpleNamespace(Above=0, InputOutput=1, CopyFromParent=0, Unsorted=0)
        xlib.error = types.SimpleNamespace(
            XError=ValueError, ConnectionClosedError=ConnectionError,
            BadWindow=OSError, BadDrawable=OSError,
        )
        ext = types.ModuleType("Xlib.ext")
        ext.shape = types.SimpleNamespace(
            SO=types.SimpleNamespace(Set=0), SK=types.SimpleNamespace(Input=2),
        )
        windows = SceneWindows()
        windows.display = Mock()
        screen = windows.display.screen.return_value
        screen.root.get_geometry.return_value = types.SimpleNamespace(width=1920, height=1080)
        screen.root_depth = 24
        browser, video = Mock(), Mock()
        browser.id, video.id = 123, 456
        screen.root.query_tree.return_value.children = [browser, video]
        for window in (browser, video):
            geometry = types.SimpleNamespace(x=0, y=0, width=1920, height=1080)
            window.get_geometry.return_value = geometry
            window.configure.side_effect = lambda geometry=geometry, **changes: vars(geometry).update(changes)
        status = {"state": "playing", "title": "Song", "artist": "Bj\u00f6rk"}
        with patch.dict(sys.modules, {"Xlib": xlib, "Xlib.ext": ext}), \
                patch("webdisplay.scene_windows.time.monotonic", return_value=0) as clock, \
                patch("webdisplay.display_text.DisplayText") as text:
            windows.show_now_playing(status)
            footer = windows.music_window
            self.assertEqual(windows.music_text, "Song - Bj\u00f6rk")
            text.return_value.draw.assert_called_once_with(
                windows.music_pixmap.id, *windows.music_geometry[2:],
                "Song - Bj\u00f6rk", music_icon=True,
            )
            first_bars = windows.music_pixmap.fill_rectangle.call_args_list[-4:]
            windows.show_now_playing(status)
            text.return_value.draw.assert_called_once()
            self.assertEqual(windows.music_pixmap.fill_rectangle.call_count, 5)
            clock.return_value = 0.2
            windows.show_now_playing(status)
            self.assertNotEqual(first_bars, windows.music_pixmap.fill_rectangle.call_args_list[-4:])
            text.return_value.draw.assert_called_once()
            screen.root.create_pixmap.assert_called_once()
            windows.show_now_playing({**status, "state": "paused"})
            self.assertEqual(windows.music_text, "Music paused: Song - Bj\u00f6rk")
            self.assertEqual(text.return_value.draw.call_count, 2)
            self.assertFalse(text.return_value.draw.call_args.kwargs["music_icon"])
            windows.music_pixmap.fill_rectangle.reset_mock()
            clock.return_value = 0.4
            windows.show_now_playing({**status, "state": "paused"})
            windows.music_pixmap.fill_rectangle.assert_not_called()
            for state, label in (("waiting", "Waiting for music"),
                                 ("error", "Music interrupted - retrying")):
                windows.show_now_playing({"state": state})
                self.assertEqual(windows.music_text, label)
                self.assertFalse(text.return_value.draw.call_args.kwargs["music_icon"])
            windows.show_now_playing({"state": "playing"})
            self.assertEqual(windows.music_text, "Unknown track")
            windows.music_pixmap.fill_rectangle.reset_mock()
            windows.show_now_playing({**status, "title": "Next song"})
            self.assertEqual(windows.music_pixmap.fill_rectangle.call_count, 5)
            footer.set_input_focus.assert_not_called()
            browser.set_input_focus.assert_not_called()
            video.set_input_focus.assert_not_called()
            windows.show_now_playing({"state": "disabled"})
            self.assertEqual(browser.get_geometry().height, 1080)
            self.assertEqual(video.get_geometry().height, 1080)
            footer.unmap.assert_called_once()
            windows.show_now_playing(status)
            screen.root.get_geometry.return_value = types.SimpleNamespace(width=1280, height=720)
            windows.show_now_playing(status)
            x, y, width, height = windows.music_geometry
            self.assertEqual((x, y), (64, 36))
            self.assertLess(x + width, 1280)
            self.assertLess(y + height, 720)
            windows.show_now_playing({"state": "disabled"})
            browser.configure.assert_not_called()
            video.configure.assert_not_called()
            windows.close()
            text.return_value.close.assert_called_once()

    def test_native_connections_close_even_if_hiding_notification_fails(self):
        windows = SceneWindows()
        display, text = Mock(), Mock()
        windows.display, windows.music_renderer = display, text
        with patch.object(windows, "hide_now_playing", side_effect=OSError("Display disconnected")):
            with self.assertRaisesRegex(OSError, "Display disconnected"):
                windows.close()
        text.close.assert_called_once()
        display.close.assert_called_once()
        self.assertIsNone(windows.display)

    def test_countdown_safe_area_tracks_each_screen_dimension(self):
        for width, height, percent, expected in (
            (1920, 1080, 5, (1760, 70)),
            (1152, 864, 5, (1030, 59)),
            (1920, 1080, 0, (1856, 16)),
            (1152, 864, 10, (973, 102)),
        ):
            with self.subTest(size=(width, height), percent=percent):
                self.assertEqual(countdown_position(width, height, percent), expected)

    def test_countdown_is_above_other_windows_and_reuses_resources(self):
        xlib = types.ModuleType("Xlib")
        xlib.X = types.SimpleNamespace(Above=0, InputOutput=1, CopyFromParent=0, ArcPieSlice=1, Unsorted=0)
        xlib.error = types.SimpleNamespace(XError=ValueError, ConnectionClosedError=ConnectionError)
        ext = types.ModuleType("Xlib.ext")
        ext.shape = types.SimpleNamespace(
            SO=types.SimpleNamespace(Set=0), SK=types.SimpleNamespace(Bounding=0, Input=2),
        )
        windows = SceneWindows()
        windows.display = Mock()
        screen = windows.display.screen.return_value
        screen.width_in_pixels = 1152
        screen.height_in_pixels = 864
        screen.root_depth = 24
        with patch.dict(sys.modules, {"Xlib": xlib, "Xlib.ext": ext}):
            windows.show_countdown(0)
            windows.countdown_pixmap.fill_arc.assert_not_called()
            windows.show_countdown(0.25)
            screen.root.create_window.assert_called_once()
            self.assertEqual(screen.root.create_pixmap.call_count, 2)
            window = screen.root.create_window.return_value
            window.configure.assert_called_with(x=1030, y=59, stack_mode=xlib.X.Above)
            windows.safe_area_percent = 10
            window.set_input_focus.assert_not_called()
            windows.countdown_pixmap.fill_arc.assert_called_with(
                windows.countdown_gc, 4, 4, 39, 39, 90 * 64, -90 * 64,
            )
            window.shape_mask.assert_called_with(0, 0, 0, 0, windows.countdown_mask)
            window.shape_rectangles.assert_called_once_with(0, 2, 0, 0, 0, [])
            windows.show_countdown(1)
            window.configure.assert_called_with(x=973, y=102, stack_mode=xlib.X.Above)
            windows.countdown_pixmap.fill_arc.assert_called_with(
                windows.countdown_gc, 4, 4, 39, 39, 90 * 64, -360 * 64,
            )
            windows.hide_countdown()
            window.unmap.assert_called_once()
            windows.close()
            window.destroy.assert_called_once()

    def test_browser_handover_skips_unmapped_windows_with_the_same_pid(self):
        x = types.SimpleNamespace(AnyPropertyType=0, IsViewable=2, Above=0,
                                  RevertToParent=2, CurrentTime=0)
        xlib = types.ModuleType("Xlib")
        xlib.X = x
        xlib.error = types.SimpleNamespace(BadWindow=OSError)
        hidden, visible = Mock(), Mock()
        for window in (hidden, visible):
            window.get_full_property.return_value.value = [123]
        hidden.get_attributes.return_value.map_state = 0
        visible.get_attributes.return_value.map_state = 2
        windows = SceneWindows()
        windows.display = Mock()
        windows.display.screen.return_value.root.query_tree.return_value.children = [hidden, visible]
        with patch.dict(sys.modules, {"Xlib": xlib}):
            windows.show_browser(123)
        hidden.configure.assert_not_called()
        hidden.set_input_focus.assert_not_called()
        visible.configure.assert_called_once_with(stack_mode=x.Above)
        visible.set_input_focus.assert_called_once_with(x.RevertToParent, x.CurrentTime)
