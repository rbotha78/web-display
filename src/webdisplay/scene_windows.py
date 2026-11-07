"""Small X11 stacking helper for the browser and one embedded video surface."""

from __future__ import annotations


class SceneWindows:
    def __init__(self) -> None:
        self.display = None
        self.video = None
        self.countdown_window = None
        self.countdown_pixmap = None
        self.countdown_gc = None
        self.countdown_font = None

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

    def show_countdown(self, text: str) -> None:
        from Xlib import error

        try:
            self._draw_countdown(text)
        except (error.XError, error.ConnectionClosedError):
            raise OSError("Could not draw the countdown on the display server") from None

    def _draw_countdown(self, text: str) -> None:
        from Xlib import X

        display = self._connect()
        screen = display.screen()
        if self.countdown_window is None:
            fonts = display.list_fonts("-misc-fixed-medium-r-normal--20-*-*-*-*-*-*-*", 1)
            self.countdown_font = display.open_font(fonts[0] if fonts else "fixed")
            font = self.countdown_font.query()
            width = font.max_bounds.character_width * 24 + 24
            height = font.font_ascent + font.font_descent + 16
            self.countdown_size = (width, height, font.font_ascent + 8)
            self.countdown_pixmap = screen.root.create_pixmap(width, height, screen.root_depth)
            self.countdown_gc = self.countdown_pixmap.create_gc(
                foreground=screen.white_pixel, background=screen.black_pixel,
                font=self.countdown_font.id,
            )
            self.countdown_window = screen.root.create_window(
                max(0, screen.width_in_pixels - width - 16), 16, width, height, 0,
                screen.root_depth, X.InputOutput, X.CopyFromParent,
                background_pixmap=self.countdown_pixmap, override_redirect=True,
            )
            self.countdown_window.set_wm_name("Web Display countdown")
        width, height, baseline = self.countdown_size
        self.countdown_gc.change(foreground=screen.black_pixel)
        self.countdown_pixmap.fill_rectangle(self.countdown_gc, 0, 0, width, height)
        self.countdown_gc.change(foreground=screen.white_pixel)
        self.countdown_pixmap.draw_text(self.countdown_gc, 12, baseline, text)
        self.countdown_window.clear_area()
        self.countdown_window.map()
        self.countdown_window.configure(stack_mode=X.Above)
        display.sync()

    def hide_countdown(self) -> None:
        if self.countdown_window is not None:
            self.countdown_window.unmap()
            self.display.sync()

    def close(self) -> None:
        if self.display is not None:
            if self.countdown_window is not None:
                self.countdown_window.destroy()
                self.countdown_gc.free()
                self.countdown_pixmap.free()
                self.countdown_font.close()
            if self.video is not None:
                self.video.destroy()
            self.display.close()
        self.video = self.display = None
        self.countdown_window = self.countdown_gc = self.countdown_pixmap = self.countdown_font = None
