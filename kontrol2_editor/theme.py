"""Palette, fonts, layout constants and the DPI / screen-fit scale shared by every widget.

All layout numbers in the code are "design units" (a 1160x720 window at 96 dpi).
`u()` converts them to real pixels using a scale that accounts for the display's
DPI and shrinks the whole interface when the screen is too small to fit it.
"""
import ctypes
import os
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

# --- palette: macOS (light), from the system colours of the macOS UI Kit ---
# Window background #ececec, white content, a light toolbar and sidebar, white push buttons
# with a hairline border, system blue #007aff for accents and #0063e1 for selections,
# labels in black at 85 / 50 / 26 %. A colour "fill|border" draws a 1 px border.
BUTTON      = "#ffffff|#cfcfcf"   # push button
BUTTON_HOVER = "#f9f9f9|#c4c4c4"
BUTTON_PRESS = "#e6e6e6|#bcbcbc"
BG          = "#ececec"   # window background
TOOLBAR     = "#f6f6f6"   # unified toolbar (header)
PANEL       = "#ffffff"   # content view
ROW         = "#f5f5f5"   # rows inside the content (alternating-row grey)
FIELD       = "#ffffff"   # text fields and value boxes
FIELD_HI    = BUTTON      # buttons, pop-ups, steppers
FIELD_HOVER = BUTTON_HOVER
PRESS       = BUTTON_PRESS
EDGE        = "#d9d9d9"   # separators
TEXT        = "#262626"   # label colour (black 85 %)
MUTED       = "#808080"   # secondary label (black 50 %)
GLYPH       = "#4d4d4d"
DIM         = "#bfbfbf"   # tertiary label (black 26 %)
ACCENT      = "#007aff"   # control accent (system blue)
ACCENT_HI   = "#1a88ff"
ACCENT_LO   = "#0063e1"
ACCENT_DARK = "#0063e1"   # selected content background
ACCENT_SOFT = "#cce4ff|#a6cdff"   # selected control (active sequencer steps)
ACCENT_SOFT_HI = "#b3d7ff|#94c3ff"
SEG_TRACK   = "#e3e3e3"   # segmented control track
SEG_ON      = "#ffffff|#c8c8c8"   # its selected segment
KNOB_LO     = "#007aff"   # slider knobs: min in blue, max in grey
KNOB_HI     = "#8e8e93"
LED         = "#ff9500"   # system orange: the playhead and what the hardware would light
LED_HI      = "#ffaa33"
OK          = "#1f9d3a"   # system green, darkened to read as text
OK_DARK     = "#28cd41"
OK_HI       = "#3dd655"
OK_LO       = "#22b338"
WARN        = "#c26a00"
WARN_DARK   = "#fff1dc|#f3c98a"
ERROR       = "#d70015"
ERROR_DARK  = BUTTON      # destructive push button: red label
ERROR_HOVER = BUTTON_HOVER
ERROR_PRESS = BUTTON_PRESS
ERROR_INK   = "#d70015"
ON_ACCENT   = "#ffffff"
DISPLAY     = BUTTON      # connection status: a push-button-like label with a coloured text
DISPLAY_HI  = BUTTON_HOVER
DISPLAY_OK  = "#1f9d3a"
DISPLAY_BUSY = "#c26a00"
DISPLAY_ERR = "#d70015"
DISPLAY_INK = "#262626"
RAIL        = "#e7e7e9"   # sidebar
RAIL_HOVER  = "#dcdcdf"
RAIL_PRESS  = "#d2d2d5"
RAIL_SELECTED = "#0063e1"  # selected sidebar item: blue with white text
RAIL_FIELD  = BUTTON      # push buttons in the sidebar
RAIL_FIELD_HOVER = BUTTON_HOVER
RAIL_FIELD_PRESS = BUTTON_PRESS
RAIL_TEXT   = "#262626"
RAIL_MUTED  = "#8a8a8e"   # sidebar section headers
RAIL_DIM    = "#b5b5b8"
RAIL_EDGE   = "#d2d2d5"
RAIL_DANGER = BUTTON
RAIL_DANGER_HI = BUTTON_HOVER
RAIL_DANGER_INK = "#d70015"
DARK_UI = False           # light window frame

UI_FONT_CHOICES = ("Inter", "Segoe UI", "Bahnschrift", "Trebuchet MS", "Verdana", "Tahoma")
FR_PRIVATE = 0x10
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


def load_fonts(folder):
    """Make the .ttf files in `folder` (Inter, shipped in assets/fonts) usable by this process only,
    without installing them in Windows. Must run before Tk lists the font families."""
    if sys.platform != "win32" or not os.path.isdir(folder):
        return
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith(".ttf"):
            try:
                ctypes.windll.gdi32.AddFontResourceExW(os.path.join(folder, name), FR_PRIVATE, 0)
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
