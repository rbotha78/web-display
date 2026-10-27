"""Small X11 stacking helper for the browser and one embedded video surface."""

from __future__ import annotations


class SceneWindows:
    def __init__(self) -> None:
        self.display = None
        self.video = None

    def _connect(self):
        if self.display is None:
            from Xlib import error
            from Xlib.display import Display

            try:
                self.display = Display()
            except error.DisplayError:
                raise OSError("Could not connect to the display server") from None
        return self.display

    def video_id(self) -> int:
        from Xlib import X

        display = self._connect()
        if self.video is None:
            screen = display.screen()
            self.video = screen.root.create_window(
                0, 0, screen.width_in_pixels, screen.height_in_pixels, 0,
                screen.root_depth, X.InputOutput, X.CopyFromParent,
                background_pixel=screen.black_pixel, override_redirect=True,
            )
            self.video.configure(stack_mode=X.Below)
            self.video.map()
            display.sync()
        return self.video.id

    def show_video(self) -> None:
        from Xlib import X

        self.video_id()
        self.video.configure(stack_mode=X.Above)
        self.video.set_input_focus(X.RevertToParent, X.CurrentTime)
        self.display.sync()

    def show_browser(self, pid: int) -> None:
        from Xlib import X, error

        display = self._connect()
        atom = display.intern_atom("_NET_WM_PID")
        for window in display.screen().root.query_tree().children:
            try:
                prop = window.get_full_property(atom, X.AnyPropertyType)
            except error.BadWindow:
                continue
            if (
                prop is not None and len(prop.value) and prop.value[0] == pid
                and window.get_attributes().map_state == X.IsViewable
            ):
                window.configure(stack_mode=X.Above)
                window.set_input_focus(X.RevertToParent, X.CurrentTime)
                display.sync()
                return
        raise OSError("Browser window is not available")

    def close(self) -> None:
        if self.display is not None:
            if self.video is not None:
                self.video.destroy()
            self.display.close()
        self.video = self.display = None
