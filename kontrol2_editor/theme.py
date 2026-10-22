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
PAGE_H = WINDOW_H - HEADER_H - FOOTER_H - 2 * GAP

# --- palette ---
# Soft lavender-grey workspace and sidebar, white rounded cards, navy ink with lavender-grey
# secondary text, teal for selection and actions, coral as the sparing highlight (playhead, LEDs).
BG          = "#eef0f4"   # workspace, header and footer
PANEL       = "#ffffff"   # cards
ROW         = "#f5f6fa"   # rows inside a card
FIELD       = "#ffffff"   # text fields and value boxes
FIELD_HI    = "#f1f3f8"   # buttons
FIELD_HOVER = "#e6e9f2"
PRESS       = "#dce1ee"
EDGE        = "#e4e7f0"   # hairlines and card borders
TEXT        = "#212145"   # navy ink
MUTED       = "#9191af"   # lavender grey
GLYPH       = "#565682"
DIM         = "#b9bccf"
ACCENT      = "#3fb0ab"   # teal: selection, main buttons, sliders
ACCENT_HI   = "#4cc1bc"
ACCENT_LO   = "#349893"
ACCENT_DARK = "#3fb0ab"   # selected item
ACCENT_SOFT = "#dff3f2"   # light teal fills (active sequencer steps)
ACCENT_SOFT_HI = "#cdecea"
LED         = "#e86c60"   # coral: the playhead and what the hardware would light
LED_HI      = "#ee8277"
OK          = "#0fb269"
OK_DARK     = "#0fb269"
OK_HI       = "#13c476"
OK_LO       = "#0d9a5b"
WARN        = "#c99a1e"
WARN_DARK   = "#fbf3d9"
ERROR       = "#e86c60"
ERROR_DARK  = "#fbe4e2"
ERROR_HOVER = "#f8d3cf"
ERROR_PRESS = "#f4c0bb"
ERROR_INK   = "#c94f49"
ON_ACCENT   = "#ffffff"   # text on teal, green or coral
DISPLAY     = "#212145"   # dark navy readout of the connection status
DISPLAY_HI  = "#2e2e5a"
DISPLAY_OK  = "#6fe3b0"
DISPLAY_BUSY = "#efd358"
DISPLAY_ERR = "#ff9a90"
DISPLAY_INK = "#f1f3f8"
RAIL        = "#e3e7f1"   # sidebar
RAIL_FIELD  = "#ffffff"   # its buttons: white pills
RAIL_HOVER  = "#eceff6"
RAIL_PRESS  = "#dce1ee"
RAIL_SELECTED = "#f4f5fa"  # selected section: a lighter pill with teal text
RAIL_TEXT   = "#565682"
RAIL_MUTED  = "#9191af"
RAIL_DIM    = "#b9bccf"
RAIL_EDGE   = "#d3d8e6"
RAIL_DANGER = "#fbe4e2"
RAIL_DANGER_HI = "#f8d3cf"
RAIL_DANGER_INK = "#c94f49"
DARK_UI = False           # light window frame

UI_FONT_CHOICES = ("Segoe UI", "Bahnschrift", "Trebuchet MS", "Verdana", "Tahoma")
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
