"""Widgets drawn on a Canvas.

A page is one `Surface` (a Canvas). Each control is a light Python object that owns a
few canvas items and receives mouse events through the Surface, so building a page or
changing a value costs microseconds instead of the ~10 ms a themed Tk widget takes.
Coordinates passed to widgets and drawing helpers are design units; see theme.u().
"""
import time
import tkinter as tk

from PIL import ImageTk

from . import shapes, theme
from .theme import u

HAND = "hand2"
DOUBLE_CLICK_SECONDS = 0.35


class Surface(tk.Canvas):
    """A Canvas that draws in design units and dispatches the mouse to its widgets."""

    def __init__(self, master, width, height, bg=theme.BG):
        super().__init__(master, width=u(width), height=u(height), bg=bg,
                         highlightthickness=0, bd=0, takefocus=0)
        self._widgets = []
        self._shapes = {}
        self.backdrop = bg   # colour widgets sit on; their sprites are flattened onto it
        self._hover = None
        self._grab = None
        self._last_click = (None, 0.0, 0, 0)   # widget, time, x, y of the last motionless click
        self._press_origin = (0, 0)
        self._moved = False
        self._editor = None
        self._edit_commit = None
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    # --- drawing helpers (design units) ---

    def _sprite(self, kind, w, h, r, color, bg):
        cache = self.winfo_toplevel().__dict__.setdefault("_sprite_cache", {})
        key = (kind, w, h, r, color, bg)
        image = cache.get(key)
        if image is None:
            image = cache[key] = ImageTk.PhotoImage(shapes.render(*key), master=self)
        return image

    def _shape(self, kind, x1, y1, w, h, r, color, anchor, bg=None):
        item = self.create_image(x1, y1, anchor=anchor, image=self._sprite(kind, w, h, r, color, bg))
        self._shapes[item] = [kind, w, h, r, color, bg]
        return item

    def rrect(self, x, y, w, h, r=6, fill=theme.PANEL, bg=None):
        """Anti-aliased rounded rectangle; give `bg` (the colour behind it) for big, static ones."""
        x1, y1, x2, y2 = u(x), u(y), u(x + w), u(y + h)
        return self._shape("round", x1, y1, x2 - x1, y2 - y1, u(r), fill, "nw", bg)

    def panel(self, x, y, w, h, r, fill, border=theme.EDGE):
        """A large rounded card with a hairline border: two plain rectangles plus four small
        anti-aliased corners, drawn in the border colour and again 1 px inside in `fill`."""
        if border:
            self._card(u(x), u(y), u(x + w), u(y + h), u(r), border)
            self._card(u(x) + 1, u(y) + 1, u(x + w) - 1, u(y + h) - 1, u(r) - 1, fill)
        else:
            self._card(u(x), u(y), u(x + w), u(y + h), u(r), fill)

    def _card(self, x1, y1, x2, y2, pr, fill):
        self.create_rectangle(x1 + pr, y1, x2 - pr, y2, fill=fill, outline="")
        self.create_rectangle(x1, y1 + pr, x2, y2 - pr, fill=fill, outline="")
        for kind, cx, cy, anchor in (("tl", x1, y1, "nw"), ("tr", x2, y1, "ne"),
                                     ("bl", x1, y2, "sw"), ("br", x2, y2, "se")):
            self._shape(kind, cx, cy, pr, pr, pr, fill, anchor)

    def recolor(self, item, color):
        shape = self._shapes[item]
        if shape[4] != color:
            shape[4] = color
            self.itemconfigure(item, image=self._sprite(*shape))

    def reshape(self, item, x, y, w, h, r=6):
        x1, y1, x2, y2 = u(x), u(y), u(x + w), u(y + h)
        shape = self._shapes[item]
        shape[1:4] = [x2 - x1, y2 - y1, u(r)]
        self.coords(item, x1, y1)
        self.itemconfigure(item, image=self._sprite(*shape))

    def rect(self, x, y, w, h, fill="", outline=""):
        return self.create_rectangle(u(x), u(y), u(x + w), u(y + h), fill=fill, outline=outline)

    def hline(self, x, y, w, fill=theme.EDGE):
        return self.create_line(u(x), u(y), u(x + w), u(y), fill=fill)

    def line(self, x1, y1, x2, y2, fill=theme.TEXT, width=1.5):
        return self.create_line(u(x1), u(y1), u(x2), u(y2), fill=fill,
                                width=max(1, round(u(width))), capstyle="round")

    def oval(self, x, y, r, fill=theme.TEXT):
        """Anti-aliased disc centred on (x, y); move it with coords(item, px, py)."""
        d = 2 * u(r)
        return self._shape("oval", u(x), u(y), d, d, 0, fill, "center")

    def triangle(self, x, y, size, fill=theme.MUTED):
        """Small downward chevron centred on (x, y)."""
        return self._shape("chevron", u(x), u(y), 2 * u(size), u(size), 0, fill, "center")

    def text(self, x, y, text, size=13, fill=theme.TEXT, anchor="w", weight="normal", mono=False):
        face = theme.mono(size) if mono else theme.font(size, weight)
        return self.create_text(u(x), u(y), text=text, fill=fill, anchor=anchor, font=face)

    def raise_widget(self):
        """Raise the Canvas itself (Canvas.tkraise would only reorder its items)."""
        tk.Misc.tkraise(self)

    def lower_widget(self):
        tk.Misc.lower(self)

    # --- widget registry and mouse dispatch ---

    def add(self, widget):
        self._widgets.append(widget)

    def _hit(self, px, py):
        for w in reversed(self._widgets):
            if w.enabled and w.contains(px, py):
                return w
        return None

    def _set_hover(self, widget):
        if widget is self._hover:
            return
        if self._hover is not None:
            self._hover.on_leave()
        self._hover = widget
        if widget is not None:
            widget.on_enter()
        self.configure(cursor=HAND if widget is not None else "")

    def _on_motion(self, e):
        w = self._hit(e.x, e.y)
        self._set_hover(w)
        if w is not None:
            w.on_motion(e.x, e.y)

    def _on_leave(self, _):
        if self._grab is None:
            self._set_hover(None)

    def _on_press(self, e):
        self.focus_set()  # commits an open number editor
        w = self._hit(e.x, e.y)
        if w is None:
            return
        last, when, lx, ly = self._last_click
        slop = u(4)
        double = (last is w and time.monotonic() - when < DOUBLE_CLICK_SECONDS
                  and abs(e.x - lx) <= slop and abs(e.y - ly) <= slop)
        self._last_click = (None, 0.0, 0, 0)
        self._press_origin = (e.x, e.y)
        self._moved = False
        self._grab = w
        w.on_press(e.x, e.y)
        if double:
            w.on_double(e.x, e.y)

    def _on_drag(self, e):
        if self._grab is None:
            return
        if abs(e.x - self._press_origin[0]) > u(3) or abs(e.y - self._press_origin[1]) > u(3):
            self._moved = True
        self._grab.on_drag(e.x, e.y)

    def _on_release(self, e):
        w, self._grab = self._grab, None
        if w is not None:
            w.on_release(e.x, e.y)
            if not self._moved:
                self._last_click = (w, time.monotonic(), e.x, e.y)
        self._set_hover(self._hit(e.x, e.y))

    def wheel(self, px, py, delta):
        w = self._hit(px, py)
        if w is not None:
            w.on_wheel(1 if delta > 0 else -1)

    # --- inline number editor ---

    def edit_number(self, x, y, w, h, value, commit):
        """Show a text entry over a field (pixel coordinates); commit(text) runs on Enter / focus loss."""
        self._finish_edit(True)
        if self._editor is None:
            ed = tk.Entry(self, justify="center", relief="flat", bd=0, highlightthickness=1,
                          highlightbackground=theme.ACCENT, highlightcolor=theme.ACCENT,
                          bg=theme.FIELD, fg=theme.TEXT, insertbackground=theme.TEXT,
                          selectbackground=theme.ACCENT, selectforeground=theme.ON_ACCENT,
                          font=theme.font(13))
            for sequence in ("<Return>", "<KP_Enter>", "<Tab>"):
                ed.bind(sequence, lambda _e: self._finish_edit(True))
            ed.bind("<Escape>", lambda _e: self._finish_edit(False))
            ed.bind("<FocusOut>", lambda _e: self._finish_edit(True))
            self._editor = ed
        self._edit_commit = commit
        self._editor.delete(0, "end")
        self._editor.insert(0, str(value))
        self._editor.place(x=x, y=y, width=w, height=h)
        self._editor.focus_set()
        self._editor.select_range(0, "end")

    def _finish_edit(self, accept):
        commit, self._edit_commit = self._edit_commit, None
        if commit is None:
            return
        text = self._editor.get()
        self._editor.place_forget()
        self.focus_set()
        if accept:
            commit(text)


class Widget:
    """Base class: a rectangle on a Surface that reacts to the mouse."""

    def __init__(self, cv, x, y, w, h):
        self.cv, self.x, self.y, self.w, self.h = cv, x, y, w, h
        self.x1, self.y1, self.x2, self.y2 = u(x), u(y), u(x + w), u(y + h)
        self.enabled = True
        self.hovered = False
        self.pressed = False
        cv.add(self)

    def contains(self, px, py):
        return self.x1 <= px < self.x2 and self.y1 <= py < self.y2

    def set_enabled(self, enabled):
        if enabled != self.enabled:
            self.enabled = enabled
            self.hovered = self.pressed = False
            self.paint()

    def on_enter(self):
        self.hovered = True
        self.paint()

    def on_leave(self):
        self.hovered = False
        self.paint()

    def on_press(self, px, py):
        self.pressed = True
        self.paint()

    def on_release(self, px, py):
        self.pressed = False
        self.paint()

    def on_motion(self, px, py):
        pass

    def on_drag(self, px, py):
        pass

    def on_double(self, px, py):
        pass

    def on_wheel(self, direction):
        pass

    def paint(self):
        pass


# (background, hover, pressed, text)
STYLES = {
    "default": (theme.FIELD_HI, theme.FIELD_HOVER, theme.PRESS, theme.TEXT),
    "accent": (theme.ACCENT, theme.ACCENT_HI, theme.ACCENT_LO, theme.ON_ACCENT),
    "ok": (theme.OK_DARK, theme.OK_HI, theme.OK_LO, theme.ON_ACCENT),
    "danger": (theme.ERROR_DARK, theme.ERROR_HOVER, theme.ERROR_PRESS, theme.ERROR_INK),
    "nav": (theme.PANEL, theme.FIELD_HI, theme.FIELD_HOVER, theme.GLYPH),
    "nav_on": (theme.ACCENT_DARK, theme.ACCENT_DARK, theme.ACCENT_DARK, theme.ON_ACCENT),
    "cell": (theme.FIELD_HI, theme.FIELD_HOVER, theme.PRESS, theme.TEXT),
    "cell_on": (theme.ACCENT_DARK, theme.ACCENT_DARK, theme.ACCENT_DARK, theme.ON_ACCENT),
    "led": (theme.LED, theme.LED_HI, theme.LED, theme.ON_ACCENT),
    "step_on": (theme.ACCENT_SOFT, theme.ACCENT_SOFT_HI, theme.ACCENT_SOFT_HI, theme.ACCENT_LO),
    "rail": (theme.RAIL, theme.RAIL_HOVER, theme.RAIL_PRESS, theme.RAIL_TEXT),
    "rail_on": (theme.RAIL_SELECTED, theme.RAIL_SELECTED, theme.RAIL_SELECTED, theme.ACCENT),
    "rail_cell_on": (theme.ACCENT, theme.ACCENT, theme.ACCENT, theme.ON_ACCENT),
    "rail_button": (theme.RAIL_FIELD, theme.RAIL_HOVER, theme.RAIL_PRESS, theme.RAIL_TEXT),
    "rail_danger": (theme.RAIL_DANGER, theme.RAIL_DANGER_HI, theme.RAIL_DANGER_HI, theme.RAIL_DANGER_INK),
    "display": (theme.DISPLAY, theme.DISPLAY_HI, theme.DISPLAY, theme.DISPLAY_INK),
    "display_ok": (theme.DISPLAY, theme.DISPLAY_HI, theme.DISPLAY, theme.DISPLAY_OK),
    "display_busy": (theme.DISPLAY, theme.DISPLAY_HI, theme.DISPLAY, theme.DISPLAY_BUSY),
    "display_err": (theme.DISPLAY, theme.DISPLAY_HI, theme.DISPLAY, theme.DISPLAY_ERR),
}


# (background, text) of a disabled button, for styles that do not sit on a light surface
DISABLED = {name: (theme.RAIL, theme.RAIL_DIM)
            for name in ("rail", "rail_on", "rail_cell_on", "rail_button", "rail_danger")}


class Button(Widget):
    def __init__(self, cv, x, y, w, h, text, command=None, style="default",
                 align="center", size=13, weight="normal", radius=9):
        super().__init__(cv, x, y, w, h)
        self.command = command
        self.style = style
        self._text = text
        self._painted = None
        self._body = cv.rrect(x, y, w, h, radius, bg=cv.backdrop)
        if align == "center":
            self._label = cv.text(x + w / 2, y + h / 2, text, size, anchor="center", weight=weight)
        else:
            self._label = cv.text(x + 14, y + h / 2, text, size, anchor="w", weight=weight)
        self.paint()

    def set_text(self, text):
        if text != self._text:
            self._text = text
            self.cv.itemconfigure(self._label, text=text)

    def set_style(self, style):
        if style != self.style:
            self.style = style
            self.paint()

    def on_release(self, px, py):
        was_pressed = self.pressed
        super().on_release(px, py)
        if was_pressed and self.command is not None and self.contains(px, py):
            self.command()

    def paint(self):
        state = (self.style, self.enabled, self.hovered, self.pressed)
        if state == self._painted:
            return
        self._painted = state
        base, hover, press, ink = STYLES[self.style]
        if not self.enabled:
            fill, ink = DISABLED.get(self.style, (theme.FIELD, theme.DIM))
        else:
            fill = press if self.pressed else hover if self.hovered else base
        self.cv.recolor(self._body, fill)
        self.cv.itemconfigure(self._label, fill=ink)


class Dropdown(Widget):
    """Click to open a menu. With `caption` it is an action menu: it never shows a selection."""

    def __init__(self, cv, x, y, w, h, values, command, caption=None, size=13, dark=False):
        super().__init__(cv, x, y, w, h)
        self.dark = dark       # on the black navigation rail
        self.values = list(values)
        self.command = command
        self.caption = caption
        self.value = None
        self._menu = None
        self._painted = None
        self._body = cv.rrect(x, y, w, h, 7, fill=theme.FIELD_HI, bg=cv.backdrop)
        self._label = cv.text(x + 12, y + h / 2, caption or "", size)
        self._arrow = cv.triangle(x + w - 15, y + h / 2, 4)
        self.paint()

    def set(self, value):
        if value != self.value and self.caption is None:
            self.value = value
            self.cv.itemconfigure(self._label, text=value)

    def set_values(self, values):
        values = list(values)
        if values != self.values:
            self.values = values
            if self._menu is not None:
                self._menu.destroy()
                self._menu = None

    def on_release(self, px, py):
        was_pressed = self.pressed
        super().on_release(px, py)
        if was_pressed and self.contains(px, py):
            self._open()

    def _open(self):
        if self._menu is None:
            self._menu = tk.Menu(
                self.cv, tearoff=0, bd=0, relief="flat", activeborderwidth=0,
                bg=theme.PANEL, fg=theme.TEXT, activebackground=theme.ACCENT,
                activeforeground=theme.ON_ACCENT, font=theme.font(13))
            for v in self.values:
                self._menu.add_command(label=v, command=lambda v=v: self._pick(v))
        self.cv.update_idletasks()
        try:
            self._menu.tk_popup(self.cv.winfo_rootx() + self.x1, self.cv.winfo_rooty() + self.y2 + u(2))
        finally:
            self._menu.grab_release()

    def _pick(self, value):
        self.set(value)
        self.command(value)

    def paint(self):
        state = (self.enabled, self.hovered, self.pressed)
        if state == self._painted:
            return
        self._painted = state
        if self.dark:
            idle, hover, press, text, off, off_ink = (theme.RAIL_FIELD, theme.RAIL_HOVER, theme.RAIL_PRESS,
                                                      theme.RAIL_TEXT, theme.RAIL, theme.RAIL_DIM)
        else:
            idle, hover, press, text, off, off_ink = (theme.FIELD_HI, theme.FIELD_HOVER, theme.PRESS,
                                                      theme.TEXT, theme.FIELD, theme.DIM)
        if not self.enabled:
            fill, ink = off, off_ink
        else:
            fill = press if self.pressed else hover if self.hovered else idle
            ink = text
        self.cv.recolor(self._body, fill)
        self.cv.itemconfigure(self._label, fill=ink)
        self.cv.recolor(self._arrow, ink)


class Segmented(Widget):
    """Two or more mutually exclusive options in one pill."""

    def __init__(self, cv, x, y, w, h, values, command, size=12):
        super().__init__(cv, x, y, w, h)
        self.values = list(values)
        self.command = command
        self.index = None
        self._painted = None
        n = len(self.values)
        self._cell = w / n
        cv.rrect(x, y, w, h, 7, fill=theme.FIELD, bg=cv.backdrop)
        self._thumb = cv.rrect(x + 2, y + 2, self._cell - 4, h - 4, 5, fill=theme.ACCENT)
        cv.itemconfigure(self._thumb, state="hidden")
        self._labels = [cv.text(x + self._cell * (i + 0.5), y + h / 2, v, size, anchor="center")
                        for i, v in enumerate(self.values)]
        self.paint()

    def set(self, value):
        index = self.values.index(value) if value in self.values else None
        if index != self.index:
            self.index = index
            self.paint()

    def on_release(self, px, py):
        was_pressed = self.pressed
        super().on_release(px, py)
        if not (was_pressed and self.contains(px, py)):
            return
        i = min(len(self.values) - 1, int((px - self.x1) / (self._cell * theme.scale)))
        if i != self.index:
            self.set(self.values[i])
            self.command(self.values[i])

    def paint(self):
        state = (self.index, self.enabled)
        if state == self._painted:
            return
        self._painted = state
        if self.index is None:
            self.cv.itemconfigure(self._thumb, state="hidden")
        else:
            self.cv.reshape(self._thumb, self.x + 2 + self._cell * self.index, self.y + 2,
                            self._cell - 4, self.h - 4, 5)
            self.cv.itemconfigure(self._thumb, state="normal")
            self.cv.recolor(self._thumb, theme.ACCENT if self.enabled else theme.FIELD_HI)
        for i, label in enumerate(self._labels):
            if not self.enabled:
                ink = theme.DIM
            else:
                ink = theme.ON_ACCENT if i == self.index else theme.MUTED
            self.cv.itemconfigure(label, fill=ink)


class Spin(Widget):
    """[-] value [+] number field: click +/- (hold to repeat), drag up/down, wheel, or click the value to type."""

    def __init__(self, cv, x, y, w, h, low, high, command, size=13, button=22):
        """`button` is the width of the - and + zones in design units."""
        super().__init__(cv, x, y, w, h)
        self.low, self.high, self.command = low, high, command
        self.value = None
        self.warn = False
        self.zone = None
        self._painted = None
        self._repeat = None
        self._drag_start = None
        self._moved = False
        self._zone_px = u(button)
        cy = y + h / 2
        self._body = cv.rrect(x, y, w, h, 7, fill=theme.FIELD_HI, bg=cv.backdrop)
        cv.rect(x + button, y + 1, w - 2 * button, h - 2, fill=theme.FIELD)
        self._minus = cv.text(x + button / 2, cy, "–", 15, anchor="center", weight="bold")
        self._plus = cv.text(x + w - button / 2, cy, "+", 15, anchor="center", weight="bold")
        self._text = cv.text(x + w / 2, cy, "", size, anchor="center")
        self.paint()

    def set(self, value):
        if value != self.value:
            self.value = value
            self.cv.itemconfigure(self._text, text=str(value))

    def set_warn(self, warn):
        if warn != self.warn:
            self.warn = warn
            self.paint()

    def _zone_at(self, px):
        if px < self.x1 + self._zone_px:
            return "minus"
        if px >= self.x2 - self._zone_px:
            return "plus"
        return "mid"

    def _apply(self, value):
        value = min(self.high, max(self.low, value))
        if value != self.value:
            self.set(value)
            self.command(value)
            return True
        return False

    def _step(self, direction):
        if self.value is not None:
            return self._apply(self.value + direction)
        return False

    def _auto_repeat(self, direction):
        if self._step(direction):
            self._repeat = self.cv.after(55, self._auto_repeat, direction)

    def _stop_repeat(self):
        if self._repeat is not None:
            self.cv.after_cancel(self._repeat)
            self._repeat = None

    def on_motion(self, px, py):
        zone = self._zone_at(px)
        if zone != self.zone:
            self.zone = zone
            self.paint()

    def on_leave(self):
        self.zone = None
        super().on_leave()

    def on_press(self, px, py):
        self.zone = self._zone_at(px)
        self.pressed = True
        if self.zone == "mid":
            self._drag_start = (py, self.value)
            self._moved = False
        else:
            direction = -1 if self.zone == "minus" else 1
            if self._step(direction):
                self._repeat = self.cv.after(380, self._auto_repeat, direction)
        self.paint()

    def on_drag(self, px, py):
        if self.zone != "mid" or self._drag_start is None or self._drag_start[1] is None:
            return
        start_y, start_value = self._drag_start
        delta = start_y - py
        if abs(delta) > u(3):
            self._moved = True
        if self._moved:
            self._apply(start_value + int(delta / max(1, u(3))))

    def on_release(self, px, py):
        self._stop_repeat()
        was_click = self.zone == "mid" and not self._moved and self.value is not None
        self._drag_start = None
        super().on_release(px, py)
        if was_click and self.contains(px, py):
            self.cv.edit_number(self.x1 + self._zone_px, self.y1 + 2,
                                (self.x2 - self.x1) - 2 * self._zone_px, (self.y2 - self.y1) - 4,
                                self.value, self._commit_text)

    def _commit_text(self, text):
        try:
            self._apply(int(text.strip()))
        except ValueError:
            pass

    def on_wheel(self, direction):
        self._step(direction)

    def paint(self):
        state = (self.enabled, self.zone, self.pressed, self.warn)
        if state == self._painted:
            return
        self._painted = state
        cv = self.cv
        cv.recolor(self._body, theme.WARN_DARK if self.warn else theme.FIELD_HI)
        for item, name in ((self._minus, "minus"), (self._plus, "plus")):
            if not self.enabled:
                ink = theme.DIM
            elif self.zone == name:
                ink = theme.ACCENT_HI if self.pressed else theme.TEXT
            else:
                ink = theme.GLYPH
            cv.itemconfigure(item, fill=ink)
        cv.itemconfigure(self._text, fill=theme.DIM if not self.enabled else theme.WARN if self.warn else theme.TEXT)


class RangeBar(Widget):
    """Horizontal 0-127 bar with a draggable Min and Max handle, a live position marker
    and a ghost line for the extremes actually measured from the hardware."""

    PAD = 10
    HANDLE = 7

    def __init__(self, cv, x, y, w, h, command, on_reset=None):
        super().__init__(cv, x, y, w, h)
        self.command, self.on_reset = command, on_reset
        self.lo = self.hi = None
        self._live = None
        self._active = None
        self._painted = None
        self._cy = u(y + h / 2)
        self._left = self.x1 + u(self.PAD)
        self._span = (self.x2 - self.x1) - 2 * u(self.PAD)
        t = max(2, u(3))
        self._half = t
        cv.rrect(x + self.PAD - 3, y + h / 2 - 3, w - 2 * self.PAD + 6, 6, 3, fill=theme.FIELD)
        self._fill = cv.create_rectangle(0, 0, 0, 0, fill=theme.ACCENT, outline="")
        self._seen = cv.create_line(0, 0, 0, 0, fill=theme.MUTED, width=max(2, u(2)), state="hidden")
        self._marker = cv.create_line(0, 0, 0, 0, fill=theme.OK, width=max(2, u(2)), state="hidden")
        self._h_lo = cv.oval(x, y, self.HANDLE, fill=theme.ACCENT_HI)
        self._h_hi = cv.oval(x, y, self.HANDLE, fill=theme.TEXT)

    # --- value <-> pixel ---

    def _vx(self, value):
        return self._left + self._span * value / 127

    def _xv(self, px):
        return min(127, max(0, round((px - self._left) / self._span * 127)))

    # --- state pushed by the owner ---

    def set(self, lo, hi):
        if (lo, hi) != (self.lo, self.hi):
            self.lo, self.hi = lo, hi
            self._layout()

    def set_live(self, value):
        if value == self._live:
            return
        self._live = value
        if value is None:
            self.cv.itemconfigure(self._marker, state="hidden")
        else:
            x, r = self._vx(value), u(11)
            self.cv.coords(self._marker, x, self._cy - r, x, self._cy + r)
            self.cv.itemconfigure(self._marker, state="normal")

    def set_seen(self, lo, hi):
        if lo is None:
            self.cv.itemconfigure(self._seen, state="hidden")
        else:
            y = self._cy + u(12)
            self.cv.coords(self._seen, self._vx(lo), y, self._vx(hi), y)
            self.cv.itemconfigure(self._seen, state="normal")

    def _layout(self):
        if self.lo is None:
            return
        cv, cy, t = self.cv, self._cy, self._half
        xa, xb = self._vx(self.lo), self._vx(self.hi)
        cv.coords(self._fill, min(xa, xb), cy - t, max(xa, xb), cy + t)
        cv.coords(self._h_lo, xa, cy)
        cv.coords(self._h_hi, xb, cy)
        self._painted = None
        self.paint()

    def paint(self):
        state = (self.enabled, self.lo is not None and self.lo > self.hi)
        if state == self._painted:
            return
        self._painted = state
        enabled, inverted = state
        cv = self.cv
        cv.itemconfigure(self._fill, fill=theme.DIM if not enabled else theme.WARN if inverted else theme.ACCENT)
        cv.recolor(self._h_lo, theme.DIM if not enabled else theme.ACCENT_HI)
        cv.recolor(self._h_hi, theme.MUTED if not enabled else theme.TEXT)

    # --- mouse ---

    def on_press(self, px, py):
        if self.lo is None:
            return
        d_lo, d_hi = abs(px - self._vx(self.lo)), abs(px - self._vx(self.hi))
        if d_lo == d_hi:
            self._active = "lo" if px < self._vx(self.lo) else "hi"
        else:
            self._active = "lo" if d_lo < d_hi else "hi"
        self.on_drag(px, py)

    def on_drag(self, px, py):
        if self._active is None:
            return
        value = self._xv(px)
        if self._active == "lo" and value != self.lo:
            self.set(value, self.hi)
            self.command(min_val=value)
        elif self._active == "hi" and value != self.hi:
            self.set(self.lo, value)
            self.command(max_val=value)

    def on_release(self, px, py):
        self._active = None

    def on_double(self, px, py):
        self._active = None
        if self.on_reset is not None:
            self.on_reset()


class Dot:
    """Tiny activity indicator."""

    def __init__(self, cv, x, y, r=4):
        self.cv = cv
        self.on = False
        self._item = cv.oval(x, y, r, fill=theme.EDGE)

    def set(self, on):
        if on != self.on:
            self.on = on
            self.cv.recolor(self._item, theme.LED if on else theme.EDGE)
