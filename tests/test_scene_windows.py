import sys
import types
import unittest
from unittest.mock import Mock, patch

from webdisplay.scene_windows import SceneWindows


class SceneWindowsTests(unittest.TestCase):
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
