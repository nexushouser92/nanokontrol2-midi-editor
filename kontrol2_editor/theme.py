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
# Clean studio look: white header and cards on a light grey workspace, a black navigation rail,
# one vivid blue for actions and selection, orange only as a sparing highlight (the playhead, LEDs).
BG          = "#f4f5f7"   # workspace, behind the cards
PANEL       = "#ffffff"   # header, footer and cards
ROW         = "#f7f8fa"   # rows inside a card
FIELD       = "#ffffff"   # text fields and value boxes
FIELD_HI    = "#f0f1f4"   # buttons
FIELD_HOVER = "#e6e8ed"
PRESS       = "#dcdfe6"
EDGE        = "#e4e6eb"   # hairlines and card borders
TEXT        = "#1d1d21"
MUTED       = "#6f7077"
GLYPH       = "#383838"
DIM         = "#a9abb2"
ACCENT      = "#1962f9"   # primary blue
ACCENT_HI   = "#3a7bff"
ACCENT_LO   = "#1552cf"
ACCENT_DARK = "#1962f9"   # selected item
ACCENT_SOFT = "#dbe6fe"   # light blue fills (active sequencer steps)
ACCENT_SOFT_HI = "#cddcfd"
LED         = "#f26b1d"   # orange: the playhead and what the hardware would light
LED_HI      = "#f5823f"
OK          = "#1f9d55"
OK_DARK     = "#1f9d55"
OK_HI       = "#25b062"
OK_LO       = "#1a8448"
WARN        = "#d97706"
WARN_DARK   = "#fef3c7"
ERROR       = "#e5383b"
ERROR_DARK  = "#fde8e8"
ERROR_HOVER = "#fbd5d5"
ERROR_PRESS = "#f8c1c1"
ERROR_INK   = "#c81e1e"
ON_ACCENT   = "#ffffff"   # text on blue, green or orange
DISPLAY     = "#1d1d21"   # dark readout of the connection status
DISPLAY_HI  = "#2a2a30"
DISPLAY_OK  = "#6ee7a8"
DISPLAY_BUSY = "#fdba74"
DISPLAY_ERR = "#fca5a5"
DISPLAY_INK = "#f4f4f5"
RAIL        = "#101010"   # navigation rail
RAIL_FIELD  = "#1d1d21"
RAIL_HOVER  = "#2a2a30"
RAIL_PRESS  = "#34343b"
RAIL_TEXT   = "#e8e8ec"
RAIL_MUTED  = "#8a8a93"
RAIL_DIM    = "#4a4a52"
RAIL_EDGE   = "#2a2a30"
RAIL_DANGER = "#2b1716"
RAIL_DANGER_HI = "#3a1d1b"
RAIL_DANGER_INK = "#ff8a80"
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
