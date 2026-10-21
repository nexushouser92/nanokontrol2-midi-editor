"""Main window for the nanoKONTROL 2 Editor (CustomTkinter).

Layout:
  ┌─ title row ───────────────────────────────────────────────────────────────┐
  │  nanoKONTROL 2 Editor           [Ext LEDs: OFF]  [Connected ●]           │
  ├─ toolbar ─────────────────────────────────────────────────────────────────┤
  │  [Open][Save][Undo]  [Templates▾][Factory Reset][Export CC]               │
  ├─ nav ───┬─ content ─────────────────────────────────────────────────────  │
  │ Global  │  ...                                                             │
  │ Ch 1-8  │                                                                  │
  │ Transp. │                                                                  │
  │ LEDs    │                                                                  │
  │ Monitor │                                                                  │
  └─────────┴─────────────────────────────────────────────────────────────────┘

Pages are built lazily (first visit only) — startup is instant.
"""
import csv
import os
import sys
import time
from tkinter import filedialog, messagebox

import customtkinter as ctk

from . import __version__, presets, protocol
from .device import DeviceError
from .link import KontrolLink
from .midi_win import MidiError
from .protocol import ProtocolError, Scene

# ── palette ────────────────────────────────────────────────────────────────────
BG       = "#121418"
CARD     = "#1b1e24"
CARD_HI  = "#262b34"
EDGE     = "#333944"
TEXT     = "#e9ecf1"
MUTED    = "#8a93a3"
ACCENT   = "#3d8bfd"
ACCENT_H = "#2f6fd0"
OK       = "#2fbf71"
OK_DARK  = "#1d6e45"
ERROR    = "#ff6159"
WARN     = "#f5a623"
CAL_BG   = "#1e2630"      # calibration zone tint
FONT     = "Segoe UI"

NAV_W     = 155
CONTENT_W = 760
CONTENT_H = 580
WRITTEN_SECONDS = 2.0

SECTIONS = ["global"] + [f"ch{i}" for i in range(1, 9)] + ["transport", "leds", "monitor"]
SECTION_LABELS = {
    "global": "Global", "transport": "Transport",
    "leds": "LED Test", "monitor": "MIDI Monitor",
    **{f"ch{i}": f"Channel {i}" for i in range(1, 9)},
}


# ── Spin: compact [-] n [+] ────────────────────────────────────────────────────

class Spin(ctk.CTkFrame):
    """Compact [-] value [+] spinbox; calls on_change(int) clamped to low..high."""

    def __init__(self, master, low, high, on_change, width=38):
        super().__init__(master, fg_color="transparent")
        self.low, self.high, self.on_change = low, high, on_change
        self.value = None
        btn = dict(width=22, height=26, fg_color=CARD_HI, hover_color=EDGE, corner_radius=6)
        self.minus = ctk.CTkButton(self, text="−", command=lambda: self._step(-1), **btn)
        self.entry = ctk.CTkEntry(self, width=width, height=26, justify="center",
                                  border_width=1, border_color=EDGE, fg_color=BG,
                                  corner_radius=6, font=(FONT, 12))
        self.plus  = ctk.CTkButton(self, text="+", command=lambda: self._step(1),  **btn)
        self.minus.pack(side="left")
        self.entry.pack(side="left", padx=3)
        self.plus.pack(side="left")
        self.entry.bind("<Return>",   self._commit)
        self.entry.bind("<FocusOut>", self._commit)

    def set(self, value):
        if value != self.value or self.entry.get() != str(value):
            self.value = value
            self.entry.delete(0, "end")
            self.entry.insert(0, str(value))

    def set_state(self, state):
        for w in (self.minus, self.entry, self.plus):
            w.configure(state=state)

    def _step(self, d):
        if self.value is None:
            return
        v = min(self.high, max(self.low, self.value + d))
        if v != self.value:
            self.on_change(v)

    def _commit(self, _=None):
        try:
            v = int(self.entry.get())
            if not (self.low <= v <= self.high):
                raise ValueError
        except ValueError:
            self.set(self.value if self.value is not None else self.low)
            return
        if v != self.value:
            self.on_change(v)


# ── ControlRow ─────────────────────────────────────────────────────────────────

class ControlRow(ctk.CTkFrame):
    """Single-row widget for one control (slider/knob/button).

    Sliders/knobs:
      [Label][Assign▾][CC# + ●][── Calibration ──── Min▸   Max▸  ↕ ─]
    Buttons:
      [Label][Assign▾][CC# + ●][── Value ────────── Off▸   On▸  Moment/Toggle]
    """

    def __init__(self, master, app, label, is_button, on_change):
        super().__init__(master, fg_color="transparent")
        self._app = app
        self._on_change = on_change
        self._is_button = is_button
        self._ctrl = None
        self._learning = False

        # Label
        ctk.CTkLabel(self, text=label, font=(FONT, 13), text_color=TEXT,
                     anchor="w", width=88).pack(side="left")

        # Assign
        self._assign_menu = app._option(self, list(protocol.ASSIGN_OPTS),
                                        self._on_assign, width=76)
        self._assign_menu.pack(side="left", padx=(0, 6))

        # CC / Note + Learn button
        cmd_f = ctk.CTkFrame(self, fg_color="transparent")
        cmd_f.pack(side="left", padx=(0, 8))
        self._cmd_spin = Spin(cmd_f, 0, 127, self._on_cmd, width=32)
        self._cmd_spin.pack(side="left")
        self._learn_btn = ctk.CTkButton(
            cmd_f, text="●", width=22, height=26,
            fg_color=CARD_HI, hover_color=EDGE, corner_radius=6,
            font=(FONT, 10, "bold"), command=self._on_learn_toggle)
        self._learn_btn.pack(side="left", padx=(4, 0))

        # Calibration / value zone (tinted background)
        cal = ctk.CTkFrame(self, fg_color=CAL_BG, corner_radius=8)
        cal.pack(side="left", fill="y", padx=(0, 0))

        lo_text = "Off" if is_button else "Min"
        hi_text = "On"  if is_button else "Max"

        ctk.CTkLabel(cal, text=lo_text, font=(FONT, 11), text_color=MUTED,
                     width=26).pack(side="left", padx=(8, 2))
        self._min_spin = Spin(cal, 0, 127, self._on_min, width=36)
        self._min_spin.pack(side="left", padx=(0, 6))

        ctk.CTkLabel(cal, text=hi_text, font=(FONT, 11), text_color=MUTED,
                     width=26).pack(side="left", padx=(0, 2))
        self._max_spin = Spin(cal, 0, 127, self._on_max, width=36)
        self._max_spin.pack(side="left", padx=(0, 8))

        if is_button:
            self._behav_seg = app._segmented(cal, list(protocol.BEHAVIOUR_OPTS),
                                             self._on_behav, width=130)
            self._behav_seg.pack(side="left", padx=(0, 8))
        else:
            # Range display + Invert
            self._range_lbl = ctk.CTkLabel(
                cal, text="0→127", font=("Consolas", 10), text_color=ACCENT,
                width=52, anchor="center")
            self._range_lbl.pack(side="left", padx=(0, 4))
            self._invert_btn = ctk.CTkButton(
                cal, text="↕", width=26, height=26,
                fg_color=CARD_HI, hover_color=EDGE, corner_radius=6,
                font=(FONT, 12), command=self._on_invert)
            self._invert_btn.pack(side="left", padx=(0, 8))

    # ── public ────────────────────────────────────────────────────────────────

    def show(self, ctrl: protocol.ControlSettings):
        self._ctrl = ctrl
        a = protocol.ASSIGN_OPTS[ctrl.assign]
        if self._assign_menu.get() != a:
            self._assign_menu.set(a)
        self._cmd_spin.set(ctrl.cmd)
        self._min_spin.set(ctrl.min_val)
        self._max_spin.set(ctrl.max_val)
        if self._is_button:
            b = protocol.BEHAVIOUR_OPTS[ctrl.behaviour]
            if self._behav_seg.get() != b:
                self._behav_seg.set(b)
        else:
            lo, hi = ctrl.min_val, ctrl.max_val
            self._range_lbl.configure(text=f"{lo}→{hi}")
        st = "disabled" if ctrl.assign == 0 else "normal"
        self._cmd_spin.set_state(st)
        self._min_spin.set_state(st)
        self._max_spin.set_state(st)
        self._learn_btn.configure(state=st)
        if self._is_button:
            self._behav_seg.configure(state=st)

    def set_learning(self, active: bool):
        self._learning = active
        self._learn_btn.configure(
            text="✕" if active else "●",
            fg_color=ERROR if active else CARD_HI,
            hover_color="#c44a43" if active else EDGE)

    # ── callbacks ─────────────────────────────────────────────────────────────

    def _on_assign(self, label):
        if self._ctrl is not None:
            self._on_change(assign=protocol.ASSIGN_OPTS.index(label))

    def _on_cmd(self, v):
        if self._ctrl is not None:
            self._on_change(cmd=v)

    def _on_min(self, v):
        if self._ctrl is not None:
            self._on_change(min_val=v)

    def _on_max(self, v):
        if self._ctrl is not None:
            self._on_change(max_val=v)

    def _on_behav(self, label):
        if self._ctrl is not None:
            self._on_change(behaviour=protocol.BEHAVIOUR_OPTS.index(label))

    def _on_learn_toggle(self):
        if self._learning:
            self._app._cancel_learn()
        else:
            self._app._start_learn(self, lambda num: self._on_change(cmd=num))

    def _on_invert(self):
        if self._ctrl is not None:
            self._on_change(min_val=self._ctrl.max_val, max_val=self._ctrl.min_val)


# ── main app ───────────────────────────────────────────────────────────────────

class App(ctk.CTkFrame):
    def __init__(self, master, link=None, autostart=True,
                 ask_yes_no=messagebox.askyesno,
                 ask_open=filedialog.askopenfilename,
                 ask_save=filedialog.asksaveasfilename,
                 clock=time.monotonic):
        super().__init__(master, fg_color=BG, corner_radius=0)
        self.link  = link or KontrolLink()
        self.ask_yes_no = ask_yes_no
        self.ask_open   = ask_open
        self.ask_save   = ask_save
        self.clock      = clock

        self.scene    = None
        self.original = None
        self._new_link     = False
        self._section      = "global"
        self._reading      = False
        self._writing      = False
        self._failed       = False
        self._written_at   = -100.0
        self._shown        = {}
        self._ctrl_rows    = {}
        self._scene_dirty  = True
        self._full_refresh = True
        self._learn_row    = None
        self._led_states   = {}
        self._led_btns     = {}
        self._conflict_count = 0
        self._monitor_start = clock()
        self._monitor_seen  = 0
        self._page_built    = set()   # lazy-build tracking

        self.master.title("nanoKONTROL 2 Editor")
        self.master.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()
        self.pack(fill="both", expand=True)
        self.refresh()
        if autostart:
            self.link.start()
            self.after(40, self._poll)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _card(self, parent, **kw):
        return ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14,
                            border_width=1, border_color="#232730", **kw)

    def _segmented(self, parent, values, command, width=None, **kw):
        return ctk.CTkSegmentedButton(
            parent, values=values, command=command,
            selected_color=ACCENT, selected_hover_color=ACCENT_H,
            unselected_color=CARD_HI, unselected_hover_color=EDGE,
            fg_color=CARD_HI, corner_radius=8, font=(FONT, 12), height=28,
            **({"width": width} if width else {}), **kw)

    def _option(self, parent, values, command, width=110):
        return ctk.CTkOptionMenu(
            parent, values=values, command=command, width=width, height=28,
            fg_color=CARD_HI, button_color=CARD_HI, button_hover_color=EDGE,
            corner_radius=8, dynamic_resizing=False, font=(FONT, 12))

    def _tbtn(self, parent, text, width, command):
        return ctk.CTkButton(parent, text=text, width=width, height=28,
                             fg_color=CARD_HI, hover_color=EDGE,
                             corner_radius=8, font=(FONT, 12), command=command)

    def _show(self, key, value, apply):
        if self._shown.get(key, object()) != value:
            self._shown[key] = value
            apply(value)

    # ── build ─────────────────────────────────────────────────────────────────

    def _build(self):
        # ── title row ────────────────────────────────────────────────────────
        tr = ctk.CTkFrame(self, fg_color="transparent")
        tr.pack(fill="x", padx=22, pady=(14, 6))

        ti = ctk.CTkFrame(tr, fg_color="transparent")
        ti.pack(side="left")
        ctk.CTkLabel(ti, text="nanoKONTROL 2 Editor",
                     font=(FONT, 20, "bold"), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(ti, text="Scene editor · Windows",
                     font=(FONT, 11), text_color=MUTED).pack(anchor="w")

        tr_right = ctk.CTkFrame(tr, fg_color="transparent")
        tr_right.pack(side="right")
        self._led_btn = ctk.CTkButton(
            tr_right, text="Ext LEDs: OFF", width=138, height=32,
            corner_radius=16, font=(FONT, 12, "bold"), command=self._toggle_led_mode)
        self._led_btn.pack(side="left", padx=(0, 8))
        self._status_btn = ctk.CTkButton(
            tr_right, text="", width=200, height=32, corner_radius=16,
            font=(FONT, 12, "bold"), command=self._on_status_click)
        self._status_btn.pack(side="left")

        # ── toolbar row ───────────────────────────────────────────────────────
        tb = ctk.CTkFrame(self, fg_color="transparent")
        tb.pack(fill="x", padx=22, pady=(0, 10))

        self._action_btns = {}
        for key, lbl, w in (("open", "Open", 64), ("save", "Save", 64), ("undo_all", "Undo all", 90)):
            b = self._tbtn(tb, lbl, w,
                           {"open": self._open_file, "save": self._save_file,
                            "undo_all": self._undo_all}[key])
            b.pack(side="left", padx=(0, 4))
            self._action_btns[key] = b

        ctk.CTkFrame(tb, fg_color=EDGE, width=1, height=22).pack(side="left", padx=(6, 6))

        tpl_opts = list(protocol.DAW_TEMPLATES)
        self._tpl_menu = self._option(tb, tpl_opts, self._apply_template, width=155)
        self._tpl_menu.set(tpl_opts[0])
        self._tpl_menu.pack(side="left", padx=(0, 4))

        b_fr = self._tbtn(tb, "Factory Reset", 110, self._factory_reset)
        b_fr.pack(side="left", padx=(0, 4))
        self._action_btns["factory"] = b_fr

        b_ex = self._tbtn(tb, "Export CC", 88, self._export_cheatsheet)
        b_ex.pack(side="left")
        self._action_btns["export"] = b_ex

        # ── body ──────────────────────────────────────────────────────────────
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=22)

        nav = self._card(body)
        nav.pack(side="left", fill="y")
        self._nav_btns = {}
        for i, sec in enumerate(SECTIONS):
            btn = ctk.CTkButton(
                nav, text=SECTION_LABELS[sec], width=NAV_W, height=32, anchor="w",
                corner_radius=8, font=(FONT, 12), fg_color="transparent",
                hover_color=CARD_HI, text_color=TEXT,
                command=lambda s=sec: self._show_section(s))
            btn.pack(padx=8, pady=(8 if i == 0 else 2, 0))
            self._nav_btns[sec] = btn

        self._content = self._card(body, width=CONTENT_W, height=CONTENT_H)
        self._content.pack(side="left", fill="both", expand=True, padx=(12, 0))
        self._content.pack_propagate(False)

        self._placeholder = ctk.CTkLabel(
            self._content,
            text="Searching for nanoKONTROL 2…\n\n"
                 "Make sure the device is connected\n"
                 "and no other program is using it.",
            font=(FONT, 14), text_color=MUTED, justify="center")

        # Create empty frames for all pages now; content built lazily on first visit
        self._pages = {}
        for sec in SECTIONS:
            frame = ctk.CTkFrame(self._content, fg_color="transparent", corner_radius=0)
            self._pages[sec] = frame
            frame.place(x=0, y=0, relwidth=1, relheight=1)

        self._placeholder.place(x=0, y=0, relwidth=1, relheight=1)
        self._placeholder.lift()

        # ── footer ────────────────────────────────────────────────────────────
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=22, pady=(6, 10))
        self._msg_label = ctk.CTkLabel(footer, text="", font=(FONT, 11),
                                       text_color=MUTED, anchor="w")
        self._msg_label.pack(side="left", fill="x", expand=True)
        self._conflict_label = ctk.CTkLabel(footer, text="", font=(FONT, 11),
                                            text_color=WARN, anchor="e")
        self._conflict_label.pack(side="left", padx=(8, 12))
        ctk.CTkLabel(footer, text=f"Configuration only — not for live use   v{__version__}",
                     font=(FONT, 10), text_color="#5d6573").pack(side="right")

    # ── lazy page builder ─────────────────────────────────────────────────────

    def _ensure_page_built(self, section):
        if section in self._page_built:
            return
        self._page_built.add(section)
        frame = self._pages[section]
        if section == "global":
            self._build_global_page(frame)
        elif section.startswith("ch"):
            self._build_channel_page(frame, int(section[2:]) - 1)
        elif section == "transport":
            self._build_transport_page(frame)
        elif section == "leds":
            self._build_leds_page(frame)
        elif section == "monitor":
            self._build_monitor_page(frame)
        # Populate with current scene data if available
        if self.scene is not None:
            if section == "global":
                self._refresh_global(self.scene)
            elif section.startswith("ch"):
                self._refresh_channel(self.scene, int(section[2:]) - 1)
            elif section == "transport":
                self._refresh_transport(self.scene)

    # ── section/title helpers ─────────────────────────────────────────────────

    def _section_title(self, parent, text):
        ctk.CTkLabel(parent, text=text, font=(FONT, 16, "bold"),
                     text_color=TEXT, anchor="w").pack(fill="x", padx=20, pady=(16, 6))

    def _labeled_row(self, parent, label_text, label_w=210):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=5)
        ctk.CTkLabel(row, text=label_text, font=(FONT, 13), text_color=TEXT,
                     anchor="w", width=label_w).pack(side="left")
        return row

    def _divider(self, parent, pady=(12, 6)):
        ctk.CTkFrame(parent, fg_color=EDGE, height=1).pack(fill="x", padx=20, pady=pady)

    # ── page builders ─────────────────────────────────────────────────────────

    def _build_global_page(self, parent):
        self._section_title(parent, "Global Settings")

        row = self._labeled_row(parent, "Global MIDI channel")
        self._global_ch_menu = self._option(row, [str(i + 1) for i in range(16)],
                                            lambda v: self._set_global("global_channel", int(v) - 1),
                                            width=80)
        self._global_ch_menu.pack(side="right")

        row2 = self._labeled_row(parent, "Control mode")
        self._ctrl_mode_menu = self._option(
            row2, list(protocol.CONTROL_MODES),
            lambda v: self._set_global("control_mode", list(protocol.CONTROL_MODES).index(v)),
            width=185)
        self._ctrl_mode_menu.pack(side="right")

        self._divider(parent)
        ctk.CTkLabel(parent, text="LED Mode", font=(FONT, 13, "bold"),
                     text_color=TEXT, anchor="w").pack(fill="x", padx=20, pady=(2, 2))
        ctk.CTkLabel(
            parent,
            text="Internal: device drives its own LEDs.   "
                 "External: DAW drives them (Ableton, Cubase…). Use LED Test to verify.",
            font=(FONT, 11), text_color=MUTED, anchor="w",
        ).pack(fill="x", padx=20, pady=(0, 6))
        self._led_seg = self._segmented(parent, list(protocol.LED_MODES),
                                        lambda v: self._set_global("led_mode",
                                                                    list(protocol.LED_MODES).index(v)),
                                        width=220)
        self._led_seg.pack(padx=20, anchor="w", pady=(0, 10))

        self._divider(parent)
        ctk.CTkLabel(parent, text="Custom DAW Assign  (bytes 318–322)",
                     font=(FONT, 13, "bold"), text_color=TEXT, anchor="w").pack(fill="x", padx=20, pady=(2, 2))
        ctk.CTkLabel(parent, text="Device-specific CC assignments for custom DAW integration.",
                     font=(FONT, 11), text_color=MUTED, anchor="w").pack(fill="x", padx=20, pady=(0, 6))
        daw_f = ctk.CTkFrame(parent, fg_color="transparent")
        daw_f.pack(fill="x", padx=20, pady=(0, 8))
        valid_vals = [str(v) for v in protocol.CUSTOM_DAW_VALID]
        self._custom_daw_menus = []
        for i, lbl_text in enumerate(protocol.CUSTOM_DAW_LABELS):
            col = ctk.CTkFrame(daw_f, fg_color="transparent")
            col.pack(side="left", padx=(0, 16))
            ctk.CTkLabel(col, text=lbl_text, font=(FONT, 11), text_color=MUTED).pack(pady=(0, 2))
            menu = self._option(col, valid_vals,
                                lambda v, idx=i: self._set_custom_daw(idx, int(v)),
                                width=68)
            menu.pack()
            self._custom_daw_menus.append(menu)

    def _build_channel_page(self, parent, g: int):
        sf = ctk.CTkScrollableFrame(parent, fg_color="transparent",
                                    scrollbar_button_color=EDGE,
                                    scrollbar_button_hover_color=CARD_HI,
                                    corner_radius=0)
        sf.pack(fill="both", expand=True)

        self._section_title(sf, f"Channel {g + 1}")

        ch_opts = [str(i + 1) for i in range(16)] + ["Global"]
        ch_row = self._labeled_row(sf, "MIDI channel", label_w=180)
        ch_menu = self._option(ch_row, ch_opts,
                               lambda v, gi=g: self._set_group_channel(gi, 16 if v == "Global" else int(v) - 1),
                               width=90)
        ch_menu.pack(side="right")
        self._ctrl_rows[f"ch{g+1}_channel"] = ch_menu

        self._divider(sf, pady=(8, 4))
        ctk.CTkLabel(sf, text="Controls  —  Assign · CC# · Calibration (Min/Max)",
                     font=(FONT, 11, "bold"), text_color=MUTED, anchor="w").pack(fill="x", padx=20, pady=(0, 4))

        for ctrl_name in protocol.CHANNEL_CONTROLS:
            is_btn = ctrl_name not in ("slider", "knob")
            label  = protocol.CHANNEL_CTRL_LABELS[ctrl_name]

            def make_change(gi=g, cn=ctrl_name):
                def on_change(**kw):
                    self._set_ctrl(gi, cn, **kw)
                return on_change

            row = ControlRow(sf, self, label, is_btn, make_change())
            row.pack(fill="x", padx=20, pady=3)
            self._ctrl_rows[f"ch{g+1}_{ctrl_name}"] = row

        self._divider(sf, pady=(10, 4))
        copy_f = ctk.CTkFrame(sf, fg_color="transparent")
        copy_f.pack(fill="x", padx=20, pady=(0, 10))
        ctk.CTkLabel(copy_f, text="Copy settings:", font=(FONT, 12), text_color=MUTED).pack(side="left", padx=(0, 8))
        copy_opts = [f"→ Ch {i}" for i in range(1, 9) if i != g + 1] + ["→ All channels"]
        copy_menu = self._option(copy_f, ["Copy to…"] + copy_opts,
                                 lambda v, gi=g: self._copy_group(gi, v), width=148)
        copy_menu.set("Copy to…")
        copy_menu.pack(side="left")
        self._ctrl_rows[f"ch{g+1}_copy"] = copy_menu

    def _build_transport_page(self, parent):
        sf = ctk.CTkScrollableFrame(parent, fg_color="transparent",
                                    scrollbar_button_color=EDGE,
                                    scrollbar_button_hover_color=CARD_HI,
                                    corner_radius=0)
        sf.pack(fill="both", expand=True)

        self._section_title(sf, "Transport")

        ch_opts = [str(i + 1) for i in range(16)] + ["Global"]
        t_ch_row = self._labeled_row(sf, "MIDI channel", label_w=180)
        t_ch_menu = self._option(t_ch_row, ch_opts,
                                 lambda v: self._set_transport_channel(16 if v == "Global" else int(v) - 1),
                                 width=90)
        t_ch_menu.pack(side="right")
        self._ctrl_rows["transport_channel"] = t_ch_menu

        self._divider(sf, pady=(8, 4))
        ctk.CTkLabel(sf, text="Controls  —  Assign · CC# · Off / On · Behaviour",
                     font=(FONT, 11, "bold"), text_color=MUTED, anchor="w").pack(fill="x", padx=20, pady=(0, 4))

        for ctrl_name in protocol.TRANSPORT_CONTROLS:
            label = protocol.TRANSPORT_LABELS[ctrl_name]

            def make_change(cn=ctrl_name):
                def on_change(**kw):
                    self._set_transport_ctrl(cn, **kw)
                return on_change

            row = ControlRow(sf, self, label, is_button=True, on_change=make_change())
            row.pack(fill="x", padx=20, pady=3)
            self._ctrl_rows[f"transport_{ctrl_name}"] = row

    def _build_leds_page(self, parent):
        sf = ctk.CTkScrollableFrame(parent, fg_color="transparent",
                                    scrollbar_button_color=EDGE,
                                    scrollbar_button_hover_color=CARD_HI,
                                    corner_radius=0)
        sf.pack(fill="both", expand=True)

        self._section_title(sf, "LED Test")
        ctk.CTkLabel(sf,
            text="Toggle button LEDs directly. Requires External LED mode ON (Global → LED Mode).\n"
                 "These controls are for testing only and are not saved to the scene.",
            font=(FONT, 11), text_color=MUTED, anchor="w", justify="left",
        ).pack(fill="x", padx=20, pady=(0, 8))

        self._led_warn = ctk.CTkLabel(sf, text="",
            font=(FONT, 11, "bold"), text_color=WARN,
            fg_color=CARD_HI, corner_radius=8, anchor="w")
        self._led_warn.pack(fill="x", padx=20, pady=(0, 10), ipady=5, ipadx=10)

        ctk.CTkLabel(sf, text="Channel button LEDs", font=(FONT, 12, "bold"),
                     text_color=TEXT, anchor="w").pack(fill="x", padx=20, pady=(0, 4))
        grid_f = ctk.CTkFrame(sf, fg_color="transparent")
        grid_f.pack(fill="x", padx=20, pady=(0, 10))
        ctk.CTkLabel(grid_f, text="", width=55, font=(FONT, 11)).grid(row=0, column=0, padx=(0, 3))
        for i in range(8):
            ctk.CTkLabel(grid_f, text=f"Ch {i+1}", width=76, font=(FONT, 11, "bold"),
                         text_color=MUTED).grid(row=0, column=i + 1, padx=2, pady=(0, 3))
        for ri, (rk, rl) in enumerate((("prime", "Arm"), ("mute", "Mute"), ("solo", "Solo"))):
            ctk.CTkLabel(grid_f, text=rl, width=55, font=(FONT, 12), text_color=TEXT,
                         anchor="w").grid(row=ri + 1, column=0, padx=(0, 3), pady=2)
            for ch in range(8):
                key = f"{rk}_{ch+1}"
                btn = ctk.CTkButton(grid_f, text="OFF", width=76, height=30, corner_radius=6,
                    fg_color=CARD_HI, hover_color=EDGE, font=(FONT, 11),
                    command=lambda k=key: self._toggle_led(k))
                btn.grid(row=ri + 1, column=ch + 1, padx=2, pady=2)
                self._led_btns[key] = btn
                self._led_states[key] = False

        ctk.CTkLabel(sf, text="Transport button LEDs", font=(FONT, 12, "bold"),
                     text_color=TEXT, anchor="w").pack(fill="x", padx=20, pady=(4, 4))
        tr_f = ctk.CTkFrame(sf, fg_color="transparent")
        tr_f.pack(fill="x", padx=20, pady=(0, 10))
        tr_keys = [
            ("prev_track", "◀◀"), ("next_track", "▶▶"), ("cycle", "Cycle"),
            ("set_marker", "SetMk"), ("prev_marker", "<Mk"), ("next_marker", "Mk>"),
            ("rew", "REW"), ("ff", "FF"), ("stop", "STOP"), ("play", "PLAY"), ("rec", "REC"),
        ]
        for i, (key, lbl) in enumerate(tr_keys):
            btn = ctk.CTkButton(tr_f, text=lbl, width=70, height=30, corner_radius=6,
                fg_color=CARD_HI, hover_color=EDGE, font=(FONT, 11),
                command=lambda k=key: self._toggle_led(k))
            btn.grid(row=i // 6, column=i % 6, padx=3, pady=3)
            self._led_btns[key] = btn
            self._led_states[key] = False

        bulk_f = ctk.CTkFrame(sf, fg_color="transparent")
        bulk_f.pack(fill="x", padx=20, pady=(6, 10))
        self._tbtn(bulk_f, "All OFF", 100, lambda: self._set_all_leds(False)).pack(side="left", padx=(0, 6))
        ctk.CTkButton(bulk_f, text="All ON", width=100, height=28, corner_radius=8,
                      fg_color=OK_DARK, hover_color=OK, font=(FONT, 12),
                      command=lambda: self._set_all_leds(True)).pack(side="left")

    def _build_monitor_page(self, parent):
        self._section_title(parent, "MIDI Monitor")
        ctk.CTkLabel(parent, text="Live incoming MIDI messages from the device.",
                     font=(FONT, 11), text_color=MUTED, anchor="w").pack(fill="x", padx=20, pady=(0, 6))
        ctrl_f = ctk.CTkFrame(parent, fg_color="transparent")
        ctrl_f.pack(fill="x", padx=20, pady=(0, 4))
        self._tbtn(ctrl_f, "Clear", 70, self._clear_monitor).pack(side="left")

        hdr = ctk.CTkFrame(parent, fg_color=CARD_HI, corner_radius=6)
        hdr.pack(fill="x", padx=20, pady=(4, 2))
        for txt, w in (("Time", 65), ("Ch", 36), ("Type", 66), ("#", 46), ("Val", 46), ("Hex", 100)):
            ctk.CTkLabel(hdr, text=txt, font=(FONT, 11, "bold"), text_color=MUTED,
                         width=w, anchor="w").pack(side="left", padx=(8 if txt == "Time" else 2, 2), pady=3)

        self._monitor_box = ctk.CTkTextbox(
            parent, font=("Consolas", 11), fg_color=BG, text_color=TEXT,
            corner_radius=8, border_width=1, border_color=EDGE)
        self._monitor_box.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        self._monitor_box.configure(state="disabled")

    # ── mutations ─────────────────────────────────────────────────────────────

    def _set_global(self, attr, value):
        if self.scene is None:
            return
        s = self.scene.copy()
        setattr(s, attr, value)
        self._apply(s)

    def _set_custom_daw(self, index, value):
        if self.scene is None:
            return
        s = self.scene.copy()
        s.custom_daw[index] = value
        self._apply(s)

    def _set_group_channel(self, gi, channel):
        if self.scene is None:
            return
        s = self.scene.copy()
        s.groups[gi].channel = channel
        self._apply(s)

    def _set_ctrl(self, gi, ctrl_name, **kw):
        if self.scene is None:
            return
        s = self.scene.copy()
        ctrl = s.groups[gi].get_ctrl(ctrl_name)
        for k, v in kw.items():
            setattr(ctrl, k, v)
        self._apply(s)

    def _set_transport_channel(self, channel):
        if self.scene is None:
            return
        s = self.scene.copy()
        s.transport.channel = channel
        self._apply(s)

    def _set_transport_ctrl(self, ctrl_name, **kw):
        if self.scene is None:
            return
        s = self.scene.copy()
        ctrl = s.transport.get_ctrl(ctrl_name)
        for k, v in kw.items():
            setattr(ctrl, k, v)
        self._apply(s)

    def _apply(self, new_scene):
        if new_scene != self.scene:
            self.scene = new_scene
            self._failed = False
            self._scene_dirty = True
            self._full_refresh = False
            self.link.push(new_scene)
        self.refresh()

    def _toggle_led_mode(self):
        if self.scene is None:
            return
        new_mode = 1 - self.scene.led_mode
        verb = "enable" if new_mode == 1 else "disable"
        if not self.ask_yes_no("External LED Mode",
                               f"Do you want to {verb} External LED mode?\n\n"
                               "External: DAW controls the button LEDs.\n"
                               "Internal: device controls its own LEDs."):
            return
        self._set_global("led_mode", new_mode)

    # ── LED test ──────────────────────────────────────────────────────────────

    def _toggle_led(self, key):
        on = not self._led_states.get(key, False)
        self._led_states[key] = on
        cc = protocol.LED_CCS.get(key)
        if cc is not None:
            self.link.set_led(cc, on)
        btn = self._led_btns.get(key)
        if btn:
            btn.configure(text="ON " if on else "OFF",
                          fg_color=OK_DARK if on else CARD_HI,
                          hover_color=OK if on else EDGE)

    def _set_all_leds(self, on):
        for key in list(self._led_states):
            self._led_states[key] = on
            cc = protocol.LED_CCS.get(key)
            if cc is not None:
                self.link.set_led(cc, on)
            btn = self._led_btns.get(key)
            if btn:
                btn.configure(text="ON " if on else "OFF",
                              fg_color=OK_DARK if on else CARD_HI,
                              hover_color=OK if on else EDGE)

    # ── MIDI learn ────────────────────────────────────────────────────────────

    def _start_learn(self, row: ControlRow, on_learned):
        if self._learn_row is not None:
            self._cancel_learn()
        self._learn_row = row
        row.set_learning(True)
        self.link.start_learn(
            lambda kind, num: self.after(0, lambda: self._on_learned(num, on_learned)))

    def _on_learned(self, number, on_learned):
        if self._learn_row:
            self._learn_row.set_learning(False)
            self._learn_row = None
        on_learned(number)

    def _cancel_learn(self):
        if self._learn_row:
            self._learn_row.set_learning(False)
            self._learn_row = None
        self.link.cancel_learn()

    # ── MIDI monitor ──────────────────────────────────────────────────────────

    def _update_monitor(self):
        msgs = list(self.link.monitor_msgs)
        new = msgs[self._monitor_seen:]
        if not new:
            return
        self._monitor_seen = len(msgs)
        lines = []
        for ts, status, d1, d2 in new:
            t = ts - self._monitor_start
            mt = status & 0xF0
            ch = (status & 0x0F) + 1
            if mt == 0xB0:   tstr, nlbl = "CC    ", f"CC#{d1}"
            elif mt == 0x90: tstr, nlbl = "NoteOn", f"N#{d1}"
            elif mt == 0x80: tstr, nlbl = "NoteOff", f"N#{d1}"
            else:            tstr, nlbl = f"0x{mt:02X}", f"{d1}"
            lines.append(f"{t:6.3f}s  {ch:<3}  {tstr}  {nlbl:<7}  {d2:<5}  [{status:02X} {d1:02X} {d2:02X}]\n")
        self._monitor_box.configure(state="normal")
        self._monitor_box.insert("1.0", "".join(lines))
        self._monitor_box.configure(state="disabled")

    def _clear_monitor(self):
        self._monitor_seen = len(self.link.monitor_msgs)
        self._monitor_start = self.clock()
        self._monitor_box.configure(state="normal")
        self._monitor_box.delete("1.0", "end")
        self._monitor_box.configure(state="disabled")

    # ── templates / factory reset ─────────────────────────────────────────────

    def _apply_template(self, name):
        self._tpl_menu.set(list(protocol.DAW_TEMPLATES)[0])
        if name == list(protocol.DAW_TEMPLATES)[0] or self.scene is None:
            return
        s = self.scene.copy()
        if name == "Factory Defaults":
            s._apply_factory_defaults()
        elif name == "Ableton Live":
            s._apply_factory_defaults()
            s.led_mode = 1
        elif name == "Generic CC":
            s._apply_factory_defaults()
            s.control_mode = 0
            s.led_mode = 0
        elif name == "Clear All":
            for grp in s.groups:
                for cn in protocol.CHANNEL_CONTROLS:
                    getattr(grp, cn).assign = 0
            for cn in protocol.TRANSPORT_CONTROLS:
                getattr(s.transport, cn).assign = 0
        self.scene = s
        self._scene_dirty = True
        self._full_refresh = True
        self.link.push(s)
        self._show_msg(f"Template: {name}", OK)
        self.refresh()

    def _factory_reset(self):
        if self.scene is None:
            return
        if not self.ask_yes_no("Factory Reset",
                               "Reset ALL settings to factory defaults?\n"
                               "This will overwrite all current assignments."):
            return
        s = Scene()
        self.scene = s
        self._scene_dirty = True
        self._full_refresh = True
        self.link.push(s)
        self._show_msg("Factory defaults applied.", OK)
        self.refresh()

    # ── copy group ────────────────────────────────────────────────────────────

    def _copy_group(self, from_g, target_label):
        menu = self._ctrl_rows.get(f"ch{from_g+1}_copy")
        if menu:
            menu.set("Copy to…")
        if target_label in ("Copy to…", "") or self.scene is None:
            return
        s = self.scene.copy()
        src = s.groups[from_g]
        if target_label == "→ All channels":
            targets = [i for i in range(8) if i != from_g]
        else:
            try:
                t = int(target_label.replace("→ Ch ", "").strip()) - 1
                targets = [t]
            except ValueError:
                return
        for t in targets:
            for cn in protocol.CHANNEL_CONTROLS:
                setattr(s.groups[t], cn, getattr(src, cn).copy())
        self._apply(s)
        self._full_refresh = True
        self._scene_dirty = True
        self._show_msg(f"Ch {from_g+1} copied → {', '.join(str(t+1) for t in targets)}", OK)
        self.refresh()

    # ── CC conflict detection ─────────────────────────────────────────────────

    def _compute_conflicts(self):
        if self.scene is None:
            return 0
        seen = {}
        conflicts = set()
        s = self.scene
        gc = s.global_channel

        def chk(owner, channel, ctrl):
            if ctrl.assign == 0:
                return
            eff = gc if channel >= 16 else channel
            key = (eff, ctrl.assign, ctrl.cmd)
            if key in seen:
                conflicts.add(owner)
                conflicts.add(seen[key])
            else:
                seen[key] = owner

        for g, grp in enumerate(s.groups):
            for cn in protocol.CHANNEL_CONTROLS:
                chk(f"ch{g+1}_{cn}", grp.channel, grp.get_ctrl(cn))
        for cn in protocol.TRANSPORT_CONTROLS:
            chk(f"tr_{cn}", s.transport.channel, s.transport.get_ctrl(cn))
        return len(conflicts)

    # ── export CC cheatsheet ──────────────────────────────────────────────────

    def _export_cheatsheet(self):
        if self.scene is None:
            return
        path = self.ask_save(
            filetypes=[("CSV spreadsheet", "*.csv"), ("Text", "*.txt")],
            defaultextension=".csv")
        if not path:
            return
        s  = self.scene
        gc = s.global_channel
        an = protocol.ASSIGN_OPTS
        bn = protocol.BEHAVIOUR_OPTS
        rows = [["Section", "Control", "Ch(eff)", "Assign", "CC/Note#", "Min/Off", "Max/On", "Behaviour"]]
        for g, grp in enumerate(s.groups):
            eff = (gc if grp.channel >= 16 else grp.channel) + 1
            for cn in protocol.CHANNEL_CONTROLS:
                ctrl = grp.get_ctrl(cn)
                rows.append([f"Channel {g+1}", protocol.CHANNEL_CTRL_LABELS[cn],
                              eff, an[ctrl.assign], ctrl.cmd,
                              ctrl.min_val, ctrl.max_val,
                              bn[ctrl.behaviour] if ctrl.assign else ""])
        t = s.transport
        eff = (gc if t.channel >= 16 else t.channel) + 1
        for cn in protocol.TRANSPORT_CONTROLS:
            ctrl = t.get_ctrl(cn)
            rows.append(["Transport", protocol.TRANSPORT_LABELS[cn],
                         eff, an[ctrl.assign], ctrl.cmd,
                         ctrl.min_val, ctrl.max_val,
                         bn[ctrl.behaviour] if ctrl.assign else ""])
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(rows)
            self._show_msg(f"Exported: {os.path.basename(path)}", OK)
        except OSError as e:
            self._show_msg(f"Export failed: {e}", ERROR)

    # ── actions ───────────────────────────────────────────────────────────────

    def _show_section(self, section):
        self._cancel_learn()
        self._section = section
        self.refresh()

    def _open_file(self):
        path = self.ask_open(filetypes=[("nanoKONTROL 2 Preset", "*.json *.syx"),
                                         ("JSON", "*.json"), ("SysEx", "*.syx")])
        if not path:
            return
        try:
            loaded = presets.load(path)
        except (ProtocolError, OSError) as e:
            self._show_msg(f"Could not open: {e}", ERROR)
            return
        self.scene = loaded
        self._scene_dirty = True
        self._full_refresh = True
        self.link.push(self.scene)
        self._show_msg(f"Opened {os.path.basename(path)}", OK)
        self.refresh()

    def _save_file(self):
        if self.scene is None:
            return
        path = self.ask_save(
            filetypes=[("JSON preset", "*.json"), ("SysEx dump", "*.syx")],
            defaultextension=".json")
        if not path:
            return
        try:
            presets.save(path, self.scene)
            self._show_msg(f"Saved {os.path.basename(path)}", OK)
        except (ProtocolError, OSError) as e:
            self._show_msg(f"Could not save: {e}", ERROR)

    def _undo_all(self):
        if self.original is None or self.scene is None:
            return
        if not self.ask_yes_no("Undo all changes",
                               "Restore the scene to how it was when the device connected?"):
            return
        self.scene = self.original.copy()
        self._scene_dirty = True
        self._full_refresh = True
        self.link.push(self.scene)
        self._show_msg("Restored to original scene")
        self.refresh()

    def _show_msg(self, text, color=MUTED):
        self._msg_label.configure(text=text, text_color=color)

    # ── polling ───────────────────────────────────────────────────────────────

    def _poll(self):
        self._process_events()
        if self._section == "monitor" and "monitor" in self._page_built:
            self._update_monitor()
        self.after(40, self._poll)

    def _process_events(self):
        while not self.link.events.empty():
            kind, payload = self.link.events.get_nowait()
            if kind == "linked":
                self._reading, self._new_link = True, True
                self._show_msg("Reading scene…")
            elif kind == "settings":
                self._reading = False
                if self._new_link:
                    self._new_link = False
                    self.original = payload
                if self.scene is None or not self.link.has_pending:
                    self.scene = payload
                    self._scene_dirty = True
                    self._full_refresh = True
                self._show_msg("Scene read.", OK)
            elif kind == "writing":
                self._writing = True
            elif kind == "written":
                self._writing, self._failed = False, False
                self._written_at = self.clock()
            elif kind == "write_failed":
                self._writing, self._failed = False, True
                self._show_msg(f"Write failed: {payload}", ERROR)
            elif kind == "searching":
                self._reading = self._writing = False
                busy = isinstance(payload, MidiError) and "open" in str(payload).lower()
                if busy:
                    self._show_msg("Port busy — close the other program using the device.", ERROR)
                elif payload:
                    self._show_msg(str(payload), ERROR)
        self.refresh()

    # ── refresh ───────────────────────────────────────────────────────────────

    def refresh(self):
        # Nav highlight
        for sec, btn in self._nav_btns.items():
            sel = sec == self._section
            self._show(("nav", sec), sel, lambda s, b=btn: b.configure(
                fg_color=ACCENT if s else "transparent",
                hover_color=ACCENT_H if s else CARD_HI))

        # Lazy-build then show current page
        if self.scene is not None:
            self._ensure_page_built(self._section)
            page = self._pages[self._section]
        else:
            page = self._placeholder
        self._show("page", page, lambda p: p.lift())

        # Button states
        has_scene = self.scene is not None
        for k in ("save", "factory", "export"):
            self._show(f"btn_{k}", has_scene,
                       lambda s, b=self._action_btns[k]: b.configure(
                           state="normal" if s else "disabled"))
        undo_ok = self.original is not None and self.link.linked
        self._show("btn_undo", undo_ok,
                   lambda s: self._action_btns["undo_all"].configure(
                       state="normal" if s else "disabled"))
        self._show("btn_tpl", has_scene,
                   lambda s: self._tpl_menu.configure(state="normal" if s else "disabled"))

        # Scene refresh
        if has_scene and self._scene_dirty:
            self._scene_dirty = False
            full, self._full_refresh = self._full_refresh, False
            self._refresh_scene(full=full)
            # Recompute conflicts only when scene changes
            self._conflict_count = self._compute_conflicts()
            self._shown.pop("conflicts", None)  # force redraw

        # Conflict badge
        self._show("conflicts", self._conflict_count, lambda n: self._conflict_label.configure(
            text=f"⚠ {n} CC conflicts" if n else ""))

        # LED warn banner (built lazily, may not exist yet)
        if "leds" in self._page_built and has_scene:
            active = self.scene.led_mode == 1
            self._show("led_warn", active, lambda a: self._led_warn.configure(
                fg_color="transparent" if a else CARD_HI,
                text="" if a else "⚠  External LED mode is OFF — go to Global → LED Mode to enable it."))

        self._draw_status()
        self._draw_led_btn()

    def _refresh_global(self, s):
        if not hasattr(self, "_global_ch_menu"):
            return
        ch_lbl = str(s.global_channel + 1)
        if self._global_ch_menu.get() != ch_lbl:
            self._global_ch_menu.set(ch_lbl)
        cm_lbl = protocol.CONTROL_MODES[s.control_mode]
        if self._ctrl_mode_menu.get() != cm_lbl:
            self._ctrl_mode_menu.set(cm_lbl)
        led_lbl = protocol.LED_MODES[s.led_mode]
        if self._led_seg.get() != led_lbl:
            self._led_seg.set(led_lbl)
        for i, menu in enumerate(getattr(self, "_custom_daw_menus", [])):
            v = str(s.custom_daw[i]) if i < len(s.custom_daw) else "0"
            if menu.get() != v:
                menu.set(v)

    def _refresh_channel(self, s, g):
        grp = s.groups[g]
        ch_w = self._ctrl_rows.get(f"ch{g+1}_channel")
        if ch_w:
            val = "Global" if grp.channel >= 16 else str(grp.channel + 1)
            if ch_w.get() != val:
                ch_w.set(val)
        for cn in protocol.CHANNEL_CONTROLS:
            row = self._ctrl_rows.get(f"ch{g+1}_{cn}")
            if row:
                row.show(grp.get_ctrl(cn))

    def _refresh_transport(self, s):
        t = s.transport
        t_ch = self._ctrl_rows.get("transport_channel")
        if t_ch:
            val = "Global" if t.channel >= 16 else str(t.channel + 1)
            if t_ch.get() != val:
                t_ch.set(val)
        for cn in protocol.TRANSPORT_CONTROLS:
            row = self._ctrl_rows.get(f"transport_{cn}")
            if row:
                row.show(t.get_ctrl(cn))

    def _refresh_scene(self, full=False):
        s = self.scene
        if full:
            for sec in self._page_built:
                if sec == "global":
                    self._refresh_global(s)
                elif sec.startswith("ch"):
                    self._refresh_channel(s, int(sec[2:]) - 1)
                elif sec == "transport":
                    self._refresh_transport(s)
        else:
            sec = self._section
            if sec == "global":
                self._refresh_global(s)
            elif sec.startswith("ch"):
                self._refresh_channel(s, int(sec[2:]) - 1)
            elif sec == "transport":
                self._refresh_transport(s)

    def _status(self):
        if not self.link.linked:
            return "Searching for nanoKONTROL 2…", CARD_HI, MUTED
        if self._reading:
            return "Reading scene…", OK_DARK, "#ffffff"
        if self._failed:
            return "Write failed — click to retry", "#5c2422", "#ffd6d3"
        if self._writing or self.link.has_pending:
            return "Writing to device…", ACCENT, "#ffffff"
        if self.clock() - self._written_at < WRITTEN_SECONDS:
            return "Scene saved to device ✓", OK, "#ffffff"
        return "Connected ●", OK_DARK, "#ffffff"

    def _draw_status(self):
        text, bg, fg = self._status()
        self._show("status", (text, bg, fg), lambda v: self._status_btn.configure(
            text=v[0], fg_color=v[1], hover_color=v[1], text_color=v[2]))

    def _draw_led_btn(self):
        if self.scene is None:
            self._led_btn.configure(state="disabled", fg_color=CARD_HI, text="Ext LEDs: —")
            return
        on = self.scene.led_mode == 1
        self._led_btn.configure(
            state="normal",
            text=f"Ext LEDs: {'ON' if on else 'OFF'}",
            fg_color=ACCENT if on else CARD_HI,
            hover_color=ACCENT_H if on else EDGE)

    def _on_status_click(self):
        if not self.link.linked:
            return
        if self._failed and self.scene is not None:
            self._failed = False
            self.link.push(self.scene)
            self.link.flush()
        else:
            self.link.request_read()
            self._reading = True
        self.refresh()

    def _on_close(self):
        self._cancel_learn()
        self.link.stop()
        self.master.destroy()


# ── entry point ────────────────────────────────────────────────────────────────

def _icon_path():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets", "icon.ico")


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk(fg_color=BG)
    root.resizable(False, False)
    try:
        root.iconbitmap(_icon_path())
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
