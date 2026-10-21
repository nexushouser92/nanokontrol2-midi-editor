"""Palette, fonts, layout constants and the DPI / screen-fit scale shared by every widget.

All layout numbers in the code are "design units" (a 1160x720 window at 96 dpi).
`u()` converts them to real pixels using a scale that accounts for the display's
DPI and shrinks the whole interface when the screen is too small to fit it.
"""
import ctypes
import sys
from tkinter import font as tkfont

# --- layout (design units) ---
WINDOW_W, WINDOW_H = 1160, 720
MARGIN = 16
HEADER_H = 60
FOOTER_H = 32
NAV_W = 200
GAP = 12
PAGE_W = WINDOW_W - 2 * MARGIN - NAV_W - GAP
PAGE_H = WINDOW_H - HEADER_H - 4 - FOOTER_H

# --- palette ---
BG          = "#0e1014"
PANEL       = "#161a20"
ROW         = "#1c2129"
FIELD       = "#0d1116"
FIELD_HI    = "#262c37"
FIELD_HOVER = "#313846"
EDGE        = "#2b313c"
TEXT        = "#e9ecf2"
MUTED       = "#8c95a6"
GLYPH       = "#aab2c0"
DIM         = "#566072"
ACCENT      = "#5b8cff"
ACCENT_HI   = "#7ba2ff"
ACCENT_LO   = "#4670d8"
ACCENT_DARK = "#22325e"
OK          = "#2fbf71"
OK_DARK     = "#1f6b47"
WARN        = "#f5a623"
WARN_DARK   = "#4a3510"
ERROR       = "#ff6159"
ERROR_DARK  = "#5c2422"

UI_FONT_CHOICES = ("Bahnschrift", "Trebuchet MS", "Verdana", "Tahoma")
MONO_FONT_CHOICES = ("Consolas", "Lucida Console", "Courier New")

ZOOM = 1.1   # default size relative to the design units, when the screen has room
scale = 1.0
ui_family = "TkDefaultFont"
mono_family = "TkFixedFont"


def u(value):
    """Design units -> screen pixels."""
    return int(round(value * scale))


def font(size, weight="normal"):
    """Tk font tuple; the negative size means pixels, so it scales exactly like u()."""
    return (ui_family, -u(size), weight)


def mono(size):
    return (mono_family, -u(size))


def enable_dpi_awareness():
    """Must run before the first Tk window exists, or Windows blurs the whole UI."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def setup(root):
    """Pick the fonts and the scale for this display. Call once after creating the root."""
    global scale, ui_family, mono_family
    families = set(tkfont.families(root))
    ui_family = next((f for f in UI_FONT_CHOICES if f in families), "Helvetica")
    mono_family = next((f for f in MONO_FONT_CHOICES if f in families), "Courier")
    dpi_scale = root.winfo_fpixels("1i") / 96.0
    fit = min(
        ZOOM,
        (root.winfo_screenwidth() - 40) / (WINDOW_W * dpi_scale),
        (root.winfo_screenheight() - 110) / (WINDOW_H * dpi_scale),
    )
    scale = dpi_scale * max(fit, 0.5)


def dark_title_bar(root):
    """Ask Windows for a dark caption so the window frame matches the interface."""
    if sys.platform != "win32":
        return
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (Win10 2004+ / older builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), 4) == 0:
                break
    except (AttributeError, OSError):
        pass
