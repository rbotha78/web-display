"""Unicode text rendering using the Cairo/Pango libraries installed with Chromium."""

from __future__ import annotations

import ctypes


def _function(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = arguments
    return function


class DisplayText:
    def __init__(self) -> None:
        pointer, integer, number = ctypes.c_void_p, ctypes.c_int, ctypes.c_double
        x11 = ctypes.CDLL("libX11.so.6")
        cairo = ctypes.CDLL("libcairo.so.2")
        pango = ctypes.CDLL("libpango-1.0.so.0")
        pangocairo = ctypes.CDLL("libpangocairo-1.0.so.0")
        gobject = ctypes.CDLL("libgobject-2.0.so.0")
        self._libraries = (x11, cairo, pango, pangocairo, gobject)
        self.open_display = _function(x11, "XOpenDisplay", pointer, ctypes.c_char_p)
        self.close_display = _function(x11, "XCloseDisplay", integer, pointer)
        self.default_screen = _function(x11, "XDefaultScreen", integer, pointer)
        self.default_visual = _function(x11, "XDefaultVisual", pointer, pointer, integer)
        self.sync = _function(x11, "XSync", integer, pointer, integer)
        self.surface_create = _function(
            cairo, "cairo_xlib_surface_create", pointer, pointer, ctypes.c_ulong, pointer, integer, integer,
        )
        self.surface_destroy = _function(cairo, "cairo_surface_destroy", None, pointer)
        self.surface_flush = _function(cairo, "cairo_surface_flush", None, pointer)
        self.surface_status = _function(cairo, "cairo_surface_status", integer, pointer)
        self.create = _function(cairo, "cairo_create", pointer, pointer)
        self.destroy = _function(cairo, "cairo_destroy", None, pointer)
        self.status = _function(cairo, "cairo_status", integer, pointer)
        self.color = _function(cairo, "cairo_set_source_rgb", None, pointer, number, number, number)
        self.paint = _function(cairo, "cairo_paint", None, pointer)
        self.move = _function(cairo, "cairo_move_to", None, pointer, number, number)
        self.layout_create = _function(pangocairo, "pango_cairo_create_layout", pointer, pointer)
        self.layout_show = _function(pangocairo, "pango_cairo_show_layout", None, pointer, pointer)
        self.layout_text = _function(pango, "pango_layout_set_text", None, pointer, ctypes.c_char_p, integer)
        self.layout_width = _function(pango, "pango_layout_set_width", None, pointer, integer)
        self.layout_height = _function(pango, "pango_layout_set_height", None, pointer, integer)
        self.layout_ellipsize = _function(pango, "pango_layout_set_ellipsize", None, pointer, integer)
        self.layout_font = _function(pango, "pango_layout_set_font_description", None, pointer, pointer)
        self.layout_size = _function(
            pango, "pango_layout_get_pixel_size", None, pointer,
            ctypes.POINTER(integer), ctypes.POINTER(integer),
        )
        self.font_create = _function(pango, "pango_font_description_from_string", pointer, ctypes.c_char_p)
        self.font_size = _function(pango, "pango_font_description_set_absolute_size", None, pointer, number)
        self.font_free = _function(pango, "pango_font_description_free", None, pointer)
        self.unref = _function(gobject, "g_object_unref", None, pointer)
        self.display = self.open_display(None)
        if not self.display:
            raise OSError("Could not connect native text rendering to the display server")
        self.visual = self.default_visual(self.display, self.default_screen(self.display))

    def draw(self, drawable: int, width: int, height: int, text: str) -> None:
        surface = self.surface_create(self.display, drawable, self.visual, width, height)
        context = layout = font = None
        try:
            if self.surface_status(surface):
                raise OSError("Could not create the now-playing text surface")
            context = self.create(surface)
            layout = self.layout_create(context)
            font = self.font_create(b"Sans")
            if not layout or not font:
                raise OSError("Could not create the now-playing text layout")
            self.font_size(font, round(height * 0.6) * 1024)
            self.layout_font(layout, font)
            encoded = text.encode("utf-8")
            self.layout_text(layout, encoded, len(encoded))
            self.layout_width(layout, max(1, width - 24) * 1024)
            self.layout_height(layout, -1)
            self.layout_ellipsize(layout, 3)
            text_width, text_height = ctypes.c_int(), ctypes.c_int()
            self.layout_size(layout, ctypes.byref(text_width), ctypes.byref(text_height))
            self.color(context, 0, 0, 0)
            self.paint(context)
            self.color(context, 0.95, 0.96, 0.97)
            self.move(context, 12, max(0, (height - text_height.value) / 2))
            self.layout_show(context, layout)
            self.surface_flush(surface)
            self.sync(self.display, 0)
            if self.status(context) or self.surface_status(surface):
                raise OSError("Could not render the now-playing text")
        finally:
            if font:
                self.font_free(font)
            if layout:
                self.unref(layout)
            if context:
                self.destroy(context)
            self.surface_destroy(surface)

    def close(self) -> None:
        if self.display:
            self.close_display(self.display)
            self.display = None
