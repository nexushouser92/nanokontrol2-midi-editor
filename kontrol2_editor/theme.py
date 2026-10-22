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

# --- palette: a light "hardware" look ---
# Soft grey case, white panels, navy ink instead of black, LED orange for anything lit,
# and navy as the dark "display" colour of selected items.
BG          = "#e3e5eb"   # window, behind the panels
PANEL       = "#f7f8fa"   # cards
ROW         = "#eef0f4"   # rows inside a card
FIELD       = "#ffffff"   # text fields and value boxes
FIELD_HI    = "#e6e8ee"   # buttons
FIELD_HOVER = "#dadde6"
PRESS       = "#cdd1dc"
EDGE        = "#d3d7e0"   # hairlines and card borders
TEXT        = "#3a3d63"   # navy ink
MUTED       = "#83879f"
GLYPH       = "#5d6185"
DIM         = "#a9adbf"
ACCENT      = "#464b7c"   # navy: main buttons, selected options, sliders
ACCENT_HI   = "#575d94"
ACCENT_LO   = "#383c66"
ACCENT_DARK = "#3a3d63"   # selected navigation
LED         = "#ff6a2b"   # LED orange: only what the hardware would light (steps, solos, activity)
LED_HI      = "#ff8550"
DISPLAY     = "#25273a"   # the dark "screen" of the status readout
DISPLAY_HI  = "#30334a"
DISPLAY_OK  = "#6fdba0"
DISPLAY_BUSY = "#ffb067"
DISPLAY_ERR = "#ff8a80"
DISPLAY_INK = "#d9dbe8"
OK          = "#2e9a63"
OK_DARK     = "#2e9a63"
OK_HI       = "#36ad70"
OK_LO       = "#277f53"
WARN        = "#d4761b"
WARN_DARK   = "#fbe8d3"
ERROR       = "#d8443c"
ERROR_DARK  = "#f9dedb"
ERROR_HOVER = "#f4cbc7"
ERROR_PRESS = "#edb7b2"
ERROR_INK   = "#b3261e"
ON_ACCENT   = "#ffffff"   # text on orange, green or navy
DARK_UI = False           # light window frame

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
    """Ask Windows for a dark caption so the window frame matches a dark interface."""
    if sys.platform != "win32" or not DARK_UI:
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
