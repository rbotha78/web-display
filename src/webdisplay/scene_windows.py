"""Small X11 stacking helper for the browser and one embedded video surface."""

from __future__ import annotations


class SceneWindows:
    def __init__(self) -> None:
        self.display = None
        self.video = None
        self.countdown_window = None
        self.countdown_pixmap = None
        self.countdown_gc = None
        self.countdown_mask = None
        self.countdown_mask_gc = None

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

    def show_countdown(self, progress: float) -> None:
        from Xlib import error

        try:
            self._draw_countdown(progress)
        except (error.XError, error.ConnectionClosedError):
            raise OSError("Could not draw the countdown on the display server") from None

    def _draw_countdown(self, progress: float) -> None:
        from Xlib import X
        from Xlib.ext import shape

        display = self._connect()
        if not display.has_extension("SHAPE"):
            raise OSError("The display server does not support transparent countdown shapes")
        screen = display.screen()
        size = 48
        if self.countdown_window is None:
            self.countdown_pixmap = screen.root.create_pixmap(size, size, screen.root_depth)
            self.countdown_gc = self.countdown_pixmap.create_gc(
                foreground=screen.white_pixel, background=screen.black_pixel,
                line_width=2, arc_mode=X.ArcPieSlice,
            )
            self.countdown_mask = screen.root.create_pixmap(size, size, 1)
            self.countdown_mask_gc = self.countdown_mask.create_gc(
                foreground=1, background=0, line_width=2, arc_mode=X.ArcPieSlice,
            )
            self.countdown_window = screen.root.create_window(
                max(0, screen.width_in_pixels - size - 16), 16, size, size, 0,
                screen.root_depth, X.InputOutput, X.CopyFromParent,
                background_pixmap=self.countdown_pixmap, override_redirect=True,
            )
            self.countdown_window.set_wm_name("Web Display countdown")
            self.countdown_window.shape_rectangles(shape.SO.Set, shape.SK.Input, X.Unsorted, 0, 0, [])
        sweep = -round(max(0.0, min(1.0, progress)) * 360 * 64)
        self.countdown_gc.change(foreground=screen.black_pixel)
        self.countdown_pixmap.fill_rectangle(self.countdown_gc, 0, 0, size, size)
        self.countdown_gc.change(foreground=screen.white_pixel)
        self.countdown_mask_gc.change(foreground=0)
        self.countdown_mask.fill_rectangle(self.countdown_mask_gc, 0, 0, size, size)
        self.countdown_mask_gc.change(foreground=1)
        for pixmap, gc in (
            (self.countdown_pixmap, self.countdown_gc),
            (self.countdown_mask, self.countdown_mask_gc),
        ):
            pixmap.arc(gc, 2, 2, size - 5, size - 5, 0, 360 * 64)
            if sweep:
                pixmap.fill_arc(gc, 4, 4, size - 9, size - 9, 90 * 64, sweep)
        self.countdown_window.shape_mask(shape.SO.Set, shape.SK.Bounding, 0, 0, self.countdown_mask)
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
                self.countdown_mask_gc.free()
                self.countdown_mask.free()
            if self.video is not None:
                self.video.destroy()
            self.display.close()
        self.video = self.display = None
        self.countdown_window = self.countdown_gc = self.countdown_pixmap = None
        self.countdown_mask = self.countdown_mask_gc = None
