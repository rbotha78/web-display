import sys
import types
import unittest
from unittest.mock import Mock, patch

from webdisplay.scene_windows import SceneWindows


class SceneWindowsTests(unittest.TestCase):
    def test_countdown_is_above_other_windows_and_reuses_resources(self):
        xlib = types.ModuleType("Xlib")
        xlib.X = types.SimpleNamespace(Above=0, InputOutput=1, CopyFromParent=0)
        xlib.error = types.SimpleNamespace(XError=ValueError, ConnectionClosedError=ConnectionError)
        windows = SceneWindows()
        windows.display = Mock()
        screen = windows.display.screen.return_value
        screen.width_in_pixels = 1152
        screen.root_depth = 24
        windows.display.list_fonts.return_value = ["fixed"]
        font = windows.display.open_font.return_value
        font.query.return_value = types.SimpleNamespace(
            max_bounds=types.SimpleNamespace(character_width=10),
            font_ascent=16, font_descent=4,
        )
        with patch.dict(sys.modules, {"Xlib": xlib}):
            windows.show_countdown("Next scene: 01:00")
            windows.show_countdown("Next scene: 00:59")
            screen.root.create_window.assert_called_once()
            screen.root.create_pixmap.assert_called_once()
            window = screen.root.create_window.return_value
            window.configure.assert_called_with(stack_mode=xlib.X.Above)
            window.set_input_focus.assert_not_called()
            windows.countdown_pixmap.draw_text.assert_called_with(
                windows.countdown_gc, 12, 24, "Next scene: 00:59",
            )
            windows.hide_countdown()
            window.unmap.assert_called_once()
            windows.close()
            window.destroy.assert_called_once()
            font.close.assert_called_once()

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
