"""X11 surfaces for browser/video handover and non-interactive display indicators."""

from __future__ import annotations

from contextlib import ExitStack
import time

from .display import DEFAULT_SAFE_AREA_PERCENT, countdown_position, now_playing_geometry


class SceneWindows:
    def __init__(self) -> None:
        self.display = None
        self.video = None
        self.countdown_window = None
        self.countdown_pixmap = None
        self.countdown_gc = None
        self.countdown_mask = None
        self.countdown_mask_gc = None
        self.safe_area_percent = DEFAULT_SAFE_AREA_PERCENT
        self.music_window = self.music_pixmap = self.music_renderer = None
        self.music_gc = None
        self.music_frame = None
        self.music_geometry = None
        self.music_text = ""

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

    def _browser_window(self, pid: int):
        from Xlib import X, error

        display = self._connect()
        atom = display.intern_atom("_NET_WM_PID")
        for window in display.screen().root.query_tree().children:
            try:
                prop = window.get_full_property(atom, X.AnyPropertyType)
                if (
                    prop is not None and len(prop.value) and prop.value[0] == pid
                    and window.get_attributes().map_state == X.IsViewable
                ):
                    return window
            except error.BadWindow:
                continue

    def show_browser(self, pid: int) -> None:
        from Xlib import X

        window = self._browser_window(pid)
        if window is not None:
            window.configure(stack_mode=X.Above)
            window.set_input_focus(X.RevertToParent, X.CurrentTime)
            self.display.sync()
            return
        raise OSError("Browser window is not available")

    def show_now_playing(self, status: dict) -> None:
        if self.display is None and status.get("state") not in {"playing", "paused", "waiting", "error"}:
            return
        from Xlib import error

        try:
            self._draw_now_playing(status)
        except (error.XError, error.ConnectionClosedError):
            raise OSError("Could not update now-playing information on the display server") from None

    def _draw_now_playing(self, status: dict) -> None:
        from Xlib import X
        from Xlib.ext import shape

        state = status.get("state")
        if state not in {"playing", "paused", "waiting", "error"}:
            self.hide_now_playing()
            return
        from .display_text import DisplayText

        display = self._connect()
        screen = display.screen()
        size = screen.root.get_geometry()
        geometry = now_playing_geometry(size.width, size.height, self.safe_area_percent)
        x, y, width, height = geometry
        title = " ".join(str(status.get("title", "")).split())[:1024]
        artist = " ".join(str(status.get("artist", "")).split())[:1024]
        label = {"playing": "", "paused": "Music paused",
                 "waiting": "Waiting for music", "error": "Music interrupted - retrying"}[state]
        text = f"{label}: {title}" if label and title else title or label
        if not text:
            text = "Unknown track"
        if title and artist:
            text += f" - {artist}"
        frame = int(time.monotonic() * 5) % 8 if state == "playing" else None
        if self.music_renderer is None:
            self.music_renderer = DisplayText()
        if geometry != self.music_geometry:
            if self.music_window is not None:
                self.music_window.destroy()
                self.music_window = None
            if self.music_pixmap is not None:
                self.music_gc.free()
                self.music_pixmap.free()
            self.music_pixmap = screen.root.create_pixmap(width, height, screen.root_depth)
            self.music_gc = self.music_pixmap.create_gc()
            display.sync()
            self.music_renderer.draw(
                self.music_pixmap.id, width, height, text, music_icon=frame is not None,
            )
            self.music_window = screen.root.create_window(
                x, y, width, height, 0, screen.root_depth, X.InputOutput, X.CopyFromParent,
                background_pixmap=self.music_pixmap, override_redirect=True,
            )
            self.music_window.set_wm_name("Web Display now playing")
            if display.has_extension("SHAPE"):
                self.music_window.shape_rectangles(shape.SO.Set, shape.SK.Input, X.Unsorted, 0, 0, [])
            self.music_geometry, self.music_text, self.music_frame = geometry, text, None
        elif text != self.music_text or (frame is None) != (self.music_frame is None):
            self.music_renderer.draw(
                self.music_pixmap.id, width, height, text, music_icon=frame is not None,
            )
            self.music_window.clear_area()
            self.music_text = text
            self.music_frame = None
        if frame is not None and frame != self.music_frame:
            self._draw_music_icon(frame, height)
            self.music_window.clear_area(0, 0, height, height)
        self.music_frame = frame
        self.music_window.map()
        self.music_window.configure(stack_mode=X.Above)
        display.sync()

    def _draw_music_icon(self, frame: int, size: int) -> None:
        screen = self.display.screen()
        self.music_gc.change(foreground=screen.black_pixel)
        self.music_pixmap.fill_rectangle(self.music_gc, 0, 0, size, size)
        self.music_gc.change(foreground=screen.white_pixel)
        levels = (0.25, 0.4, 0.65, 0.9, 1.0, 0.75, 0.5, 0.3)
        bar_width, gap = max(2, size // 12), max(2, size // 14)
        for index in range(4):
            bar_height = max(2, round(size * 0.65 * levels[(frame + index * 2) % 8]))
            self.music_pixmap.fill_rectangle(
                self.music_gc, 12 + index * (bar_width + gap),
                (size - bar_height) // 2, bar_width, bar_height,
            )

    def hide_now_playing(self) -> None:
        if self.display is None:
            return
        from Xlib import error

        try:
            if self.music_window is not None:
                self.music_window.unmap()
            self.display.sync()
        except (error.XError, error.ConnectionClosedError):
            raise OSError("Could not hide now-playing information") from None

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
        x, y = countdown_position(
            screen.width_in_pixels, screen.height_in_pixels, self.safe_area_percent,
        )
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
                x, y, size, size, 0,
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
        self.countdown_window.configure(x=x, y=y, stack_mode=X.Above)
        display.sync()

    def hide_countdown(self) -> None:
        if self.countdown_window is not None:
            self.countdown_window.unmap()
            self.display.sync()

    def close(self) -> None:
        with ExitStack() as cleanup:
            if self.display is not None:
                cleanup.callback(self.display.close)
            if self.music_renderer is not None:
                cleanup.callback(self.music_renderer.close)
            try:
                if self.display is not None:
                    self.hide_now_playing()
                    if self.music_window is not None:
                        self.music_window.destroy()
                        self.music_gc.free()
                        self.music_pixmap.free()
                    if self.countdown_window is not None:
                        self.countdown_window.destroy()
                        self.countdown_gc.free()
                        self.countdown_pixmap.free()
                        self.countdown_mask_gc.free()
                        self.countdown_mask.free()
                    if self.video is not None:
                        self.video.destroy()
            finally:
                self.video = self.display = None
                self.countdown_window = self.countdown_gc = self.countdown_pixmap = None
                self.countdown_mask = self.countdown_mask_gc = None
                self.music_window = self.music_pixmap = self.music_renderer = None
                self.music_gc = self.music_frame = None
                self.music_geometry = None
