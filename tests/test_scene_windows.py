import sys
import types
import unittest
from unittest.mock import Mock, patch

from webdisplay.scene_windows import SceneWindows
from webdisplay.display import countdown_position


class SceneWindowsTests(unittest.TestCase):
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
