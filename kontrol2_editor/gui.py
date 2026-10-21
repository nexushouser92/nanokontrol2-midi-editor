"""Main window for the nanoKONTROL 2 Editor (CustomTkinter).

Sections (left nav):
  Global       — MIDI channel, control mode, LED mode, Custom DAW Assign
  Channel 1-8  — per-channel slider, knob, solo, mute, prime assignments
  Transport    — 11 transport button assignments
  LED Test     — live LED toggle grid (External LED mode required)
  MIDI Monitor — live view of incoming CC / Note messages
"""
import collections
import csv
import io
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
FONT     = "Segoe UI"

CONTENT_W, CONTENT_H = 720, 640
WRITTEN_SECONDS = 2.0

SECTIONS = ["global"] + [f"ch{i}" for i in range(1, 9)] + ["transport", "leds", "monitor"]

SECTION_LABELS = {
    "global":    "Global",
    "transport": "Transport",
    "leds":      "LED Test",
    "monitor":   "MIDI Monitor",
    **{f"ch{i}": f"Channel {i}" for i in range(1, 9)},
}


# ── small reusable widgets ─────────────────────────────────────────────────────

class Spin(ctk.CTkFrame):
    """[-] number [+]; calls on_change(int) clamped to low..high."""

    def __init__(self, master, low, high, on_change, width=52):
        super().__init__(master, fg_color="transparent")
        self.low, self.high, self.on_change = low, high, on_change
        self.value = None
        style = dict(width=28, height=30, fg_color=CARD_HI, hover_color=EDGE, corner_radius=8)
        self.minus = ctk.CTkButton(self, text="−", command=lambda: self._step(-1), **style)
        self.entry = ctk.CTkEntry(self, width=width, height=30, justify="center",
                                  border_width=1, border_color=EDGE, fg_color=BG, corner_radius=8)
        self.plus = ctk.CTkButton(self, text="+", command=lambda: self._step(1), **style)
        self.minus.pack(side="left")
        self.entry.pack(side="left", padx=4)
        self.plus.pack(side="left")
        self.entry.bind("<Return>", self._commit)
        self.entry.bind("<FocusOut>", self._commit)

    def set(self, value):
        if value != self.value or self.entry.get() != str(value):
            self.value = value
            self.entry.delete(0, "end")
            self.entry.insert(0, str(value))

    def _step(self, delta):
        if self.value is None:
            return
        v = min(self.high, max(self.low, self.value + delta))
        if v != self.value:
            self.on_change(v)

    def _commit(self, _=None):
        try:
            v = int(self.entry.get())
            if not self.low <= v <= self.high:
                raise ValueError
        except ValueError:
            self.set(self.value if self.value is not None else self.low)
            return
        if v != self.value:
            self.on_change(v)


# ── per-control row ────────────────────────────────────────────────────────────

class ControlRow(ctk.CTkFrame):
    """One row for a single control (slider / knob / button).

    Layout — two sub-rows:
      Top:    [label] [assign] [cmd + Learn] [Behavior (buttons) | Invert (cont.)]
      Bottom: [indent] [Off/Min label] [Off/Min spin] [On/Max label] [On/Max spin]
    """

    def __init__(self, master, app, label, is_button, on_change):
        super().__init__(master, fg_color="transparent")
        self._app = app
        self._on_change = on_change
        self._is_button = is_button
        self._ctrl = None
        self._learning = False

        # ── top row ──────────────────────────────────────────────────────────
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x")

        ctk.CTkLabel(top, text=label, font=(FONT, 13), text_color=TEXT,
                     anchor="w", width=100).pack(side="left")

        self._assign_menu = app._option(top, list(protocol.ASSIGN_OPTS),
                                        self._on_assign, width=80)
        self._assign_menu.pack(side="left", padx=(0, 6))

        cmd_f = ctk.CTkFrame(top, fg_color="transparent")
        cmd_f.pack(side="left", padx=(0, 8))
        self._cmd_spin = Spin(cmd_f, 0, 127, self._on_cmd, width=40)
        self._cmd_spin.pack(side="left")
        self._learn_btn = ctk.CTkButton(
            cmd_f, text="●", width=24, height=28,
            fg_color=CARD_HI, hover_color=EDGE, corner_radius=6,
            font=(FONT, 11, "bold"), command=self._on_learn_toggle)
        self._learn_btn.pack(side="left", padx=(4, 0))

        if is_button:
            self._behav_seg = app._segmented(top, list(protocol.BEHAVIOUR_OPTS), self._on_behav)
            self._behav_seg.pack(side="left")
        else:
            self._invert_btn = ctk.CTkButton(
                top, text="↕", width=28, height=28,
                fg_color=CARD_HI, hover_color=EDGE, corner_radius=6,
                font=(FONT, 12), command=self._on_invert)
            self._invert_btn.pack(side="left")

        # ── bottom row (min/max / off/on) ─────────────────────────────────
        bot = ctk.CTkFrame(self, fg_color="transparent")
        bot.pack(fill="x", pady=(2, 0))

        lo_lbl = "Off" if is_button else "Min"
        hi_lbl = "On " if is_button else "Max"

        ctk.CTkLabel(bot, text=lo_lbl, font=(FONT, 11), text_color=MUTED,
                     width=26).pack(side="left", padx=(190, 3))
        self._min_spin = Spin(bot, 0, 127, self._on_min, width=44)
        self._min_spin.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(bot, text=hi_lbl, font=(FONT, 11), text_color=MUTED,
                     width=26).pack(side="left", padx=(0, 3))
        self._max_spin = Spin(bot, 0, 127, self._on_max, width=44)
        self._max_spin.pack(side="left")

    # ── public interface ──────────────────────────────────────────────────────

    def show(self, ctrl: protocol.ControlSettings):
        self._ctrl = ctrl
        assign_label = protocol.ASSIGN_OPTS[ctrl.assign]
        if self._assign_menu.get() != assign_label:
            self._assign_menu.set(assign_label)
        self._cmd_spin.set(ctrl.cmd)
        self._min_spin.set(ctrl.min_val)
        self._max_spin.set(ctrl.max_val)
        if self._is_button:
            behav_label = protocol.BEHAVIOUR_OPTS[ctrl.behaviour]
            if self._behav_seg.get() != behav_label:
                self._behav_seg.set(behav_label)
        disabled = ctrl.assign == 0
        state = "disabled" if disabled else "normal"
        for w in (self._cmd_spin.minus, self._cmd_spin.plus, self._cmd_spin.entry,
                  self._min_spin.minus, self._min_spin.plus, self._min_spin.entry,
                  self._max_spin.minus, self._max_spin.plus, self._max_spin.entry):
            w.configure(state=state)
        if self._is_button:
            self._behav_seg.configure(state=state)
        self._learn_btn.configure(state=state)

    def set_learning(self, active: bool):
        self._learning = active
        if active:
            self._learn_btn.configure(text="✕", fg_color=ERROR, hover_color="#c44a43")
        else:
            self._learn_btn.configure(text="●", fg_color=CARD_HI, hover_color=EDGE)

    # ── callbacks ─────────────────────────────────────────────────────────────

    def _on_assign(self, label):
        if self._ctrl is None:
            return
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
        if self._ctrl is None:
            return
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
        self.link = link or KontrolLink()
        self.ask_yes_no = ask_yes_no
        self.ask_open = ask_open
        self.ask_save = ask_save
        self.clock = clock

        self.scene = None
        self.original = None
        self._new_link = False
        self._section = "global"
        self._reading = False
        self._writing = False
        self._failed = False
        self._written_at = -100.0
        self._shown = {}
        self._ctrl_rows = {}
        self._scene_dirty = True
        self._full_refresh = True
        self._learn_row = None
        self._led_states = {}       # key -> bool
        self._led_btns = {}         # key -> CTkButton
        self._monitor_start = clock()
        self._monitor_seen = 0      # count of msgs already displayed

        self.master.title("nanoKONTROL 2 Editor")
        self.master.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()
        self.pack(fill="both", expand=True)
        self.refresh()
        if autostart:
            self.link.start()
            self.after(40, self._poll)

    # ── layout helpers ────────────────────────────────────────────────────────

    def _card(self, parent, **kw):
        return ctk.CTkFrame(parent, fg_color=CARD, corner_radius=16,
                            border_width=1, border_color="#232730", **kw)

    def _segmented(self, parent, values, command, **kw):
        return ctk.CTkSegmentedButton(
            parent, values=values, command=command,
            selected_color=ACCENT, selected_hover_color=ACCENT_H,
            unselected_color=CARD_HI, unselected_hover_color=EDGE,
            fg_color=CARD_HI, corner_radius=8, font=(FONT, 12), height=30, **kw)

    def _option(self, parent, values, command, width=110):
        return ctk.CTkOptionMenu(
            parent, values=values, command=command, width=width, height=30,
            fg_color=CARD_HI, button_color=CARD_HI, button_hover_color=EDGE,
            corner_radius=8, dynamic_resizing=False, font=(FONT, 12))

    def _show(self, key, value, apply):
        if self._shown.get(key, object()) != value:
            self._shown[key] = value
            apply(value)

    # ── build ─────────────────────────────────────────────────────────────────

    def _build(self):
        # ── header ──────────────────────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=22, pady=(16, 12))

        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.pack(side="left", padx=(0, 20))
        ctk.CTkLabel(titles, text="nanoKONTROL 2 Editor",
                     font=(FONT, 22, "bold"), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(titles, text="Scene configuration for Windows",
                     font=(FONT, 12), text_color=MUTED).pack(anchor="w")

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.pack(side="right")
        plain = dict(height=36, fg_color=CARD_HI, hover_color=EDGE, corner_radius=10, font=(FONT, 13))
        self._action_btns = {}

        # File actions
        for key, lbl, w in (("open", "Open", 72), ("save", "Save", 72),
                             ("undo_all", "Undo all", 100)):
            btn = ctk.CTkButton(actions, text=lbl, width=w,
                                command={"open": self._open_file, "save": self._save_file,
                                         "undo_all": self._undo_all}[key], **plain)
            btn.pack(side="left", padx=(0, 5))
            self._action_btns[key] = btn

        ctk.CTkFrame(actions, fg_color=EDGE, width=1, height=28).pack(side="left", padx=(4, 4))

        # Template selector
        tpl_opts = list(protocol.DAW_TEMPLATES)
        self._tpl_menu = self._option(actions, tpl_opts, self._apply_template, width=160)
        self._tpl_menu.set(tpl_opts[0])
        self._tpl_menu.pack(side="left", padx=(0, 5))

        # Factory reset
        btn_fr = ctk.CTkButton(actions, text="Factory Reset", width=110,
                               command=self._factory_reset, **plain)
        btn_fr.pack(side="left", padx=(0, 5))
        self._action_btns["factory"] = btn_fr

        # Export CC cheatsheet
        btn_ex = ctk.CTkButton(actions, text="Export CC", width=90,
                               command=self._export_cheatsheet, **plain)
        btn_ex.pack(side="left", padx=(0, 5))
        self._action_btns["export"] = btn_ex

        ctk.CTkFrame(actions, fg_color=EDGE, width=1, height=28).pack(side="left", padx=(4, 4))

        self._led_btn = ctk.CTkButton(
            actions, text="Ext LEDs: OFF", width=140, height=36,
            corner_radius=18, font=(FONT, 13, "bold"), command=self._toggle_led_mode)
        self._led_btn.pack(side="left", padx=(0, 5))

        self._status_btn = ctk.CTkButton(
            actions, text="", width=220, height=36, corner_radius=18,
            font=(FONT, 13, "bold"), command=self._on_status_click)
        self._status_btn.pack(side="left")

        # ── body ─────────────────────────────────────────────────────────────
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=22)

        nav = self._card(body)
        nav.pack(side="left", fill="y")
        self._nav_btns = {}
        for sec in SECTIONS:
            btn = ctk.CTkButton(
                nav, text=SECTION_LABELS[sec], width=150, height=34, anchor="w",
                corner_radius=10, font=(FONT, 13), fg_color="transparent",
                hover_color=CARD_HI, text_color=TEXT,
                command=lambda s=sec: self._show_section(s))
            btn.pack(padx=10, pady=(10 if sec == SECTIONS[0] else 2, 0))
            self._nav_btns[sec] = btn

        self._content = self._card(body, width=CONTENT_W, height=CONTENT_H)
        self._content.pack(side="left", fill="both", expand=True, padx=(14, 0))
        self._content.pack_propagate(False)

        self._placeholder = ctk.CTkLabel(
            self._content,
            text="Searching for nanoKONTROL 2…\n\nMake sure the device is connected\nand no other program is using it.",
            font=(FONT, 15), text_color=MUTED, justify="center")

        self._pages = {}
        for sec in SECTIONS:
            self._pages[sec] = self._build_page(sec)

        self._placeholder.place(x=0, y=0, relwidth=1, relheight=1)
        for page in self._pages.values():
            page.place(x=0, y=0, relwidth=1, relheight=1)
        self._placeholder.lift()

        # ── footer ────────────────────────────────────────────────────────────
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=22, pady=(6, 12))
        self._msg_label = ctk.CTkLabel(footer, text="", font=(FONT, 12),
                                       text_color=MUTED, anchor="w")
        self._msg_label.pack(side="left", fill="x", expand=True)
        self._conflict_label = ctk.CTkLabel(footer, text="", font=(FONT, 12),
                                            text_color=WARN, anchor="e")
        self._conflict_label.pack(side="left", padx=(8, 12))
        ctk.CTkLabel(footer, text=f"Not for live use — configuration only   v{__version__}",
                     font=(FONT, 11), text_color="#5d6573").pack(side="right")

    # ── page builders ─────────────────────────────────────────────────────────

    def _build_page(self, section):
        if section == "transport":
            frame = ctk.CTkScrollableFrame(self._content, fg_color="transparent",
                                           scrollbar_button_color=EDGE,
                                           scrollbar_button_hover_color=CARD_HI,
                                           corner_radius=0)
            self._build_transport_page(frame)
        elif section == "leds":
            frame = ctk.CTkScrollableFrame(self._content, fg_color="transparent",
                                           scrollbar_button_color=EDGE,
                                           scrollbar_button_hover_color=CARD_HI,
                                           corner_radius=0)
            self._build_leds_page(frame)
        elif section == "monitor":
            frame = ctk.CTkFrame(self._content, fg_color="transparent", corner_radius=0)
            self._build_monitor_page(frame)
        else:
            frame = ctk.CTkFrame(self._content, fg_color="transparent", corner_radius=0)
            if section == "global":
                self._build_global_page(frame)
            elif section.startswith("ch"):
                g = int(section[2:]) - 1
                self._build_channel_page(frame, g)
        return frame

    def _section_title(self, parent, text):
        ctk.CTkLabel(parent, text=text, font=(FONT, 17, "bold"),
                     text_color=TEXT, anchor="w").pack(fill="x", padx=22, pady=(18, 8))

    def _labeled_row(self, parent, label_text):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=5)
        ctk.CTkLabel(row, text=label_text, font=(FONT, 13), text_color=TEXT,
                     anchor="w", width=200).pack(side="left")
        return row

    def _build_global_page(self, parent):
        self._section_title(parent, "Global Settings")

        row = self._labeled_row(parent, "Global MIDI channel")
        channels = [str(i + 1) for i in range(16)]
        self._global_ch_menu = self._option(row, channels,
                                            lambda v: self._set_global("global_channel", int(v) - 1),
                                            width=80)
        self._global_ch_menu.pack(side="right")

        row2 = self._labeled_row(parent, "Control mode")
        self._ctrl_mode_menu = self._option(row2, list(protocol.CONTROL_MODES),
                                            lambda v: self._set_global("control_mode",
                                                                        list(protocol.CONTROL_MODES).index(v)),
                                            width=180)
        self._ctrl_mode_menu.pack(side="right")

        # LED mode
        divider = ctk.CTkFrame(parent, fg_color=EDGE, height=1)
        divider.pack(fill="x", padx=22, pady=(14, 6))
        ctk.CTkLabel(parent, text="LED Mode", font=(FONT, 14, "bold"),
                     text_color=TEXT, anchor="w").pack(fill="x", padx=22, pady=(4, 2))
        ctk.CTkLabel(
            parent,
            text="Internal: device controls its own LEDs.\n"
                 "External: DAW controls the LEDs (Ableton, Cubase, etc.). "
                 "Use LED Test section to test.",
            font=(FONT, 11), text_color=MUTED, anchor="w", justify="left",
        ).pack(fill="x", padx=22, pady=(0, 8))
        self._led_seg = self._segmented(parent, list(protocol.LED_MODES),
                                        lambda v: self._set_global("led_mode",
                                                                    list(protocol.LED_MODES).index(v)))
        self._led_seg.pack(padx=22, anchor="w", pady=(0, 12))

        # Custom DAW Assign
        divider2 = ctk.CTkFrame(parent, fg_color=EDGE, height=1)
        divider2.pack(fill="x", padx=22, pady=(4, 6))
        ctk.CTkLabel(parent, text="Custom DAW Assign", font=(FONT, 14, "bold"),
                     text_color=TEXT, anchor="w").pack(fill="x", padx=22, pady=(4, 2))
        ctk.CTkLabel(
            parent,
            text="Device-specific CC assignments for custom DAW integration (bytes 318–322).",
            font=(FONT, 11), text_color=MUTED, anchor="w",
        ).pack(fill="x", padx=22, pady=(0, 6))

        daw_f = ctk.CTkFrame(parent, fg_color="transparent")
        daw_f.pack(fill="x", padx=22, pady=(0, 8))
        valid_vals = [str(v) for v in protocol.CUSTOM_DAW_VALID]
        self._custom_daw_menus = []
        for i, lbl_text in enumerate(protocol.CUSTOM_DAW_LABELS):
            col = ctk.CTkFrame(daw_f, fg_color="transparent")
            col.pack(side="left", padx=(0, 16))
            ctk.CTkLabel(col, text=lbl_text, font=(FONT, 11), text_color=MUTED).pack(pady=(0, 2))
            menu = self._option(col, valid_vals,
                                lambda v, idx=i: self._set_custom_daw(idx, int(v)),
                                width=70)
            menu.pack()
            self._custom_daw_menus.append(menu)

    def _build_channel_page(self, parent, group_index: int):
        g = group_index
        self._section_title(parent, f"Channel {g + 1}")

        ch_opts = [str(i + 1) for i in range(16)] + ["Global"]
        ch_row = self._labeled_row(parent, "MIDI channel")
        ch_menu = self._option(ch_row, ch_opts,
                               lambda v, gi=g: self._set_group_channel(gi, 16 if v == "Global" else int(v) - 1),
                               width=90)
        ch_menu.pack(side="right")
        self._ctrl_rows[f"ch{g+1}_channel"] = ch_menu

        divider = ctk.CTkFrame(parent, fg_color=EDGE, height=1)
        divider.pack(fill="x", padx=22, pady=(10, 6))
        ctk.CTkLabel(parent, text="Controls", font=(FONT, 13, "bold"),
                     text_color=MUTED, anchor="w").pack(fill="x", padx=22, pady=(0, 4))

        for ctrl_name in protocol.CHANNEL_CONTROLS:
            is_button = ctrl_name not in ("slider", "knob")
            label = protocol.CHANNEL_CTRL_LABELS[ctrl_name]

            def make_change(gi=g, cn=ctrl_name):
                def on_change(**kw):
                    self._set_ctrl(gi, cn, **kw)
                return on_change

            row = ControlRow(parent, self, label, is_button, make_change())
            row.pack(fill="x", padx=22, pady=4)
            self._ctrl_rows[f"ch{g+1}_{ctrl_name}"] = row

        # Copy-to section
        div2 = ctk.CTkFrame(parent, fg_color=EDGE, height=1)
        div2.pack(fill="x", padx=22, pady=(12, 6))
        copy_f = ctk.CTkFrame(parent, fg_color="transparent")
        copy_f.pack(fill="x", padx=22, pady=(0, 8))
        ctk.CTkLabel(copy_f, text="Copy settings:", font=(FONT, 12), text_color=MUTED).pack(side="left", padx=(0, 8))
        copy_opts = [f"→ Ch {i}" for i in range(1, 9) if i != g + 1] + ["→ All channels"]
        copy_menu = self._option(copy_f, ["Copy to..."] + copy_opts,
                                 lambda v, gi=g: self._copy_group(gi, v), width=150)
        copy_menu.set("Copy to...")
        copy_menu.pack(side="left")
        self._ctrl_rows[f"ch{g+1}_copy"] = copy_menu

    def _build_transport_page(self, parent):
        self._section_title(parent, "Transport")

        ch_opts = [str(i + 1) for i in range(16)] + ["Global"]
        t_ch_row = self._labeled_row(parent, "MIDI channel")
        t_ch_menu = self._option(t_ch_row, ch_opts,
                                 lambda v: self._set_transport_channel(16 if v == "Global" else int(v) - 1),
                                 width=90)
        t_ch_menu.pack(side="right")
        self._ctrl_rows["transport_channel"] = t_ch_menu

        divider = ctk.CTkFrame(parent, fg_color=EDGE, height=1)
        divider.pack(fill="x", padx=22, pady=(10, 6))

        for ctrl_name in protocol.TRANSPORT_CONTROLS:
            label = protocol.TRANSPORT_LABELS[ctrl_name]

            def make_change(cn=ctrl_name):
                def on_change(**kw):
                    self._set_transport_ctrl(cn, **kw)
                return on_change

            row = ControlRow(parent, self, label, is_button=True, on_change=make_change())
            row.pack(fill="x", padx=22, pady=4)
            self._ctrl_rows[f"transport_{ctrl_name}"] = row

    def _build_leds_page(self, parent):
        self._section_title(parent, "LED Test")

        ctk.CTkLabel(
            parent,
            text="Toggle LEDs directly (requires External LED mode ON). "
                 "These controls are for testing only — not saved to the scene.",
            font=(FONT, 11), text_color=MUTED, anchor="w", justify="left",
        ).pack(fill="x", padx=22, pady=(0, 10))

        # Warning banner shown when External LED is OFF
        self._led_warn = ctk.CTkLabel(
            parent,
            text="⚠  External LED mode is OFF — enable it in Global Settings to control LEDs.",
            font=(FONT, 12, "bold"), text_color=WARN,
            fg_color=CARD_HI, corner_radius=8, anchor="w")
        self._led_warn.pack(fill="x", padx=22, pady=(0, 10), ipady=6, ipadx=8)

        # Channel button grid
        ctk.CTkLabel(parent, text="Channel buttons", font=(FONT, 13, "bold"),
                     text_color=TEXT, anchor="w").pack(fill="x", padx=22, pady=(0, 6))

        grid_f = ctk.CTkFrame(parent, fg_color="transparent")
        grid_f.pack(fill="x", padx=22, pady=(0, 12))

        # Header row
        ctk.CTkLabel(grid_f, text="", width=60, font=(FONT, 12), text_color=MUTED).grid(
            row=0, column=0, padx=(0, 4))
        for i in range(8):
            ctk.CTkLabel(grid_f, text=f"Ch {i+1}", width=70, font=(FONT, 12, "bold"),
                         text_color=MUTED).grid(row=0, column=i + 1, padx=2, pady=(0, 4))

        for row_idx, (row_key, row_label) in enumerate((("prime", "Arm"), ("mute", "Mute"), ("solo", "Solo"))):
            ctk.CTkLabel(grid_f, text=row_label, width=60, font=(FONT, 12),
                         text_color=TEXT, anchor="w").grid(row=row_idx + 1, column=0, padx=(0, 4), pady=2)
            for ch in range(8):
                key = f"{row_key}_{ch+1}"
                btn = ctk.CTkButton(
                    grid_f, text="OFF", width=70, height=32, corner_radius=8,
                    fg_color=CARD_HI, hover_color=EDGE, font=(FONT, 11),
                    command=lambda k=key: self._toggle_led(k))
                btn.grid(row=row_idx + 1, column=ch + 1, padx=2, pady=2)
                self._led_btns[key] = btn
                self._led_states[key] = False

        # Transport LEDs
        ctk.CTkLabel(parent, text="Transport buttons", font=(FONT, 13, "bold"),
                     text_color=TEXT, anchor="w").pack(fill="x", padx=22, pady=(4, 6))

        tr_f = ctk.CTkFrame(parent, fg_color="transparent")
        tr_f.pack(fill="x", padx=22, pady=(0, 12))
        tr_keys = [
            ("prev_track", "◀◀ Tr"), ("next_track", "Tr ▶▶"), ("cycle", "Cycle"),
            ("set_marker", "Set Mk"), ("prev_marker", "< Mk"), ("next_marker", "Mk >"),
            ("rew", "REW"), ("ff", "FF"), ("stop", "STOP"), ("play", "PLAY"), ("rec", "REC"),
        ]
        for i, (key, lbl) in enumerate(tr_keys):
            btn = ctk.CTkButton(
                tr_f, text=lbl, width=72, height=32, corner_radius=8,
                fg_color=CARD_HI, hover_color=EDGE, font=(FONT, 11),
                command=lambda k=key: self._toggle_led(k))
            btn.grid(row=i // 6, column=i % 6, padx=3, pady=3)
            self._led_btns[key] = btn
            self._led_states[key] = False

        # Bulk controls
        bulk_f = ctk.CTkFrame(parent, fg_color="transparent")
        bulk_f.pack(fill="x", padx=22, pady=(4, 12))
        ctk.CTkButton(bulk_f, text="All LEDs OFF", width=130, height=32, corner_radius=8,
                      fg_color=CARD_HI, hover_color=EDGE, font=(FONT, 12),
                      command=lambda: self._set_all_leds(False)).pack(side="left", padx=(0, 8))
        ctk.CTkButton(bulk_f, text="All LEDs ON", width=130, height=32, corner_radius=8,
                      fg_color=OK_DARK, hover_color=OK, font=(FONT, 12),
                      command=lambda: self._set_all_leds(True)).pack(side="left")

    def _build_monitor_page(self, parent):
        self._section_title(parent, "MIDI Monitor")

        ctk.CTkLabel(
            parent,
            text="Live view of incoming MIDI messages from the device.",
            font=(FONT, 11), text_color=MUTED, anchor="w",
        ).pack(fill="x", padx=22, pady=(0, 8))

        ctrl_f = ctk.CTkFrame(parent, fg_color="transparent")
        ctrl_f.pack(fill="x", padx=22, pady=(0, 6))
        ctk.CTkButton(ctrl_f, text="Clear", width=80, height=30, corner_radius=8,
                      fg_color=CARD_HI, hover_color=EDGE, font=(FONT, 12),
                      command=self._clear_monitor).pack(side="left")

        hdr_f = ctk.CTkFrame(parent, fg_color=CARD_HI, corner_radius=6)
        hdr_f.pack(fill="x", padx=22, pady=(0, 2))
        for txt, w in (("Time", 70), ("Ch", 40), ("Type", 70), ("#", 50), ("Value", 60), ("Hex", 120)):
            ctk.CTkLabel(hdr_f, text=txt, font=(FONT, 12, "bold"), text_color=MUTED,
                         width=w, anchor="w").pack(side="left", padx=(8 if txt == "Time" else 2, 2), pady=4)

        self._monitor_box = ctk.CTkTextbox(
            parent, font=("Consolas", 12), fg_color=BG, text_color=TEXT,
            corner_radius=8, border_width=1, border_color=EDGE)
        self._monitor_box.pack(fill="both", expand=True, padx=22, pady=(0, 12))
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

    def _set_group_channel(self, group_index, channel):
        if self.scene is None:
            return
        s = self.scene.copy()
        s.groups[group_index].channel = channel
        self._apply(s)

    def _set_ctrl(self, group_index, ctrl_name, **kw):
        if self.scene is None:
            return
        s = self.scene.copy()
        ctrl = s.groups[group_index].get_ctrl(ctrl_name)
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
        if not self.ask_yes_no(
            "External LED Mode",
            f"Do you want to {verb} External LED mode?\n\n"
            "External mode lets the DAW control the LED buttons.\n"
            "Internal mode means the device controls its own LEDs.",
        ):
            return
        self._set_global("led_mode", new_mode)

    # ── LED test ──────────────────────────────────────────────────────────────

    def _toggle_led(self, key):
        new_state = not self._led_states.get(key, False)
        self._led_states[key] = new_state
        cc = protocol.LED_CCS.get(key)
        if cc is not None:
            self.link.set_led(cc, new_state)
        btn = self._led_btns.get(key)
        if btn:
            btn.configure(
                text="ON " if new_state else "OFF",
                fg_color=OK_DARK if new_state else CARD_HI,
                hover_color=OK if new_state else EDGE,
            )

    def _set_all_leds(self, on):
        for key in list(self._led_states):
            self._led_states[key] = on
            cc = protocol.LED_CCS.get(key)
            if cc is not None:
                self.link.set_led(cc, on)
            btn = self._led_btns.get(key)
            if btn:
                btn.configure(
                    text="ON " if on else "OFF",
                    fg_color=OK_DARK if on else CARD_HI,
                    hover_color=OK if on else EDGE,
                )

    # ── MIDI learn ────────────────────────────────────────────────────────────

    def _start_learn(self, row: ControlRow, on_learned):
        if self._learn_row is not None:
            self._cancel_learn()
        self._learn_row = row
        row.set_learning(True)
        self.link.start_learn(lambda kind, num: self.after(0, lambda: self._on_learned(num, on_learned)))

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
        new_msgs = msgs[self._monitor_seen:]
        if not new_msgs:
            return
        self._monitor_seen = len(msgs)
        lines = []
        for ts, status, d1, d2 in new_msgs:
            elapsed = ts - self._monitor_start
            msg_type = status & 0xF0
            ch = (status & 0x0F) + 1
            if msg_type == 0xB0:
                type_str, n_label = "CC  ", f"CC#{d1}"
            elif msg_type == 0x90:
                type_str, n_label = "NoteOn", f"N#{d1}"
            elif msg_type == 0x80:
                type_str, n_label = "NoteOff", f"N#{d1}"
            else:
                type_str, n_label = f"{msg_type:02X}  ", f"{d1}"
            line = f"{elapsed:7.3f}s  Ch{ch:<2}  {type_str}  {n_label:<6}  {d2:<5}  [{status:02X} {d1:02X} {d2:02X}]\n"
            lines.append(line)
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
        self._tpl_menu.set(list(protocol.DAW_TEMPLATES)[0])  # reset to placeholder
        if name == list(protocol.DAW_TEMPLATES)[0]:
            return
        if self.scene is None:
            return
        s = self.scene.copy()
        if name == "Factory Defaults":
            s._apply_factory_defaults()
        elif name == "Ableton Live":
            s._apply_factory_defaults()
            s.led_mode = 1  # External LED required for Live
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
        self._show_msg(f"Template applied: {name}", OK)
        self.refresh()

    def _factory_reset(self):
        if self.scene is None:
            return
        if not self.ask_yes_no("Factory Reset",
                               "Reset all settings to factory defaults?\n"
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
            menu.set("Copy to...")
        if target_label == "Copy to..." or self.scene is None:
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
        self._show_msg(f"Ch {from_g+1} settings copied to {', '.join(str(t+1) for t in targets)}", OK)
        self.refresh()

    # ── CC conflict detection ─────────────────────────────────────────────────

    def _find_conflicts(self):
        if self.scene is None:
            return 0
        seen = {}  # (effective_ch, type, num) -> first_owner
        conflicts = set()
        s = self.scene
        gc = s.global_channel

        def check(owner, channel, ctrl):
            if ctrl.assign == 0:
                return
            eff_ch = gc if channel >= 16 else channel
            key = (eff_ch, ctrl.assign, ctrl.cmd)
            if key in seen:
                conflicts.add(owner)
                conflicts.add(seen[key])
            else:
                seen[key] = owner

        for g, grp in enumerate(s.groups):
            for cn in protocol.CHANNEL_CONTROLS:
                check(f"ch{g+1}_{cn}", grp.channel, grp.get_ctrl(cn))
        for cn in protocol.TRANSPORT_CONTROLS:
            check(f"transport_{cn}", s.transport.channel, s.transport.get_ctrl(cn))

        return len(conflicts)

    # ── export CC cheatsheet ──────────────────────────────────────────────────

    def _export_cheatsheet(self):
        if self.scene is None:
            return
        path = self.ask_save(
            filetypes=[("CSV spreadsheet", "*.csv"), ("Text file", "*.txt")],
            defaultextension=".csv")
        if not path:
            return
        s = self.scene
        gc = s.global_channel
        rows = [["Section", "Control", "Ch (eff.)", "Assign", "CC/Note", "Min/Off", "Max/On", "Behaviour"]]
        assign_names = protocol.ASSIGN_OPTS
        behav_names = protocol.BEHAVIOUR_OPTS
        for g, grp in enumerate(s.groups):
            eff_ch = (gc if grp.channel >= 16 else grp.channel) + 1
            for cn in protocol.CHANNEL_CONTROLS:
                ctrl = grp.get_ctrl(cn)
                rows.append([f"Channel {g+1}", protocol.CHANNEL_CTRL_LABELS[cn],
                              eff_ch, assign_names[ctrl.assign], ctrl.cmd,
                              ctrl.min_val, ctrl.max_val,
                              behav_names[ctrl.behaviour] if ctrl.assign else ""])
        t = s.transport
        eff_ch = (gc if t.channel >= 16 else t.channel) + 1
        for cn in protocol.TRANSPORT_CONTROLS:
            ctrl = t.get_ctrl(cn)
            rows.append(["Transport", protocol.TRANSPORT_LABELS[cn],
                         eff_ch, assign_names[ctrl.assign], ctrl.cmd,
                         ctrl.min_val, ctrl.max_val,
                         behav_names[ctrl.behaviour] if ctrl.assign else ""])
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerows(rows)
            self._show_msg(f"CC cheatsheet exported: {os.path.basename(path)}", OK)
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
        if self._section == "monitor":
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
        for sec, btn in self._nav_btns.items():
            sel = sec == self._section
            self._show(("nav", sec), sel, lambda s, b=btn: b.configure(
                fg_color=ACCENT if s else "transparent",
                hover_color=ACCENT_H if s else CARD_HI))

        page = self._pages[self._section] if self.scene is not None else self._placeholder
        self._show("page", page, self._place_page)

        state = "normal" if self.scene is not None else "disabled"
        for k in ("save", "factory", "export"):
            self._show(f"{k}_state", state,
                       lambda s, btn=self._action_btns[k]: btn.configure(state=s))
        undo = "normal" if self.original is not None and self.link.linked else "disabled"
        self._show("undo_state", undo,
                   lambda s: self._action_btns["undo_all"].configure(state=s))
        tpl_state = "normal" if self.scene is not None else "disabled"
        self._show("tpl_state", tpl_state, lambda s: self._tpl_menu.configure(state=s))

        if self.scene is not None and self._scene_dirty:
            self._scene_dirty = False
            full, self._full_refresh = self._full_refresh, False
            self._refresh_scene(full=full)

        # CC conflict counter
        conflicts = self._find_conflicts()
        self._show("conflicts", conflicts, lambda n: self._conflict_label.configure(
            text=f"⚠ {n} CC conflicts" if n else ""))

        # LED warn banner visibility
        if self.scene is not None:
            warn_vis = self.scene.led_mode == 0
            self._show("led_warn_vis", warn_vis, lambda v: self._led_warn.configure(
                fg_color=CARD_HI if v else "transparent",
                text="⚠  External LED mode is OFF — enable it in Global Settings to control LEDs."
                     if v else ""))

        self._draw_status()
        self._draw_led_btn()

    def _refresh_global(self, s):
        w = getattr(self, "_global_ch_menu", None)
        ch_label = str(s.global_channel + 1)
        if w and w.get() != ch_label:
            w.set(ch_label)
        w = getattr(self, "_ctrl_mode_menu", None)
        cm_label = protocol.CONTROL_MODES[s.control_mode]
        if w and w.get() != cm_label:
            w.set(cm_label)
        w = getattr(self, "_led_seg", None)
        led_label = protocol.LED_MODES[s.led_mode]
        if w and w.get() != led_label:
            w.set(led_label)
        for i, menu in enumerate(getattr(self, "_custom_daw_menus", [])):
            val_str = str(s.custom_daw[i]) if i < len(s.custom_daw) else "0"
            if menu.get() != val_str:
                menu.set(val_str)

    def _refresh_channel(self, s, g):
        grp = s.groups[g]
        ch_widget = self._ctrl_rows.get(f"ch{g+1}_channel")
        if ch_widget:
            ch_val = "Global" if grp.channel >= 16 else str(grp.channel + 1)
            if ch_widget.get() != ch_val:
                ch_widget.set(ch_val)
        for ctrl_name in protocol.CHANNEL_CONTROLS:
            row = self._ctrl_rows.get(f"ch{g+1}_{ctrl_name}")
            if row:
                row.show(grp.get_ctrl(ctrl_name))

    def _refresh_transport(self, s):
        t = s.transport
        t_ch = self._ctrl_rows.get("transport_channel")
        if t_ch:
            t_ch_val = "Global" if t.channel >= 16 else str(t.channel + 1)
            if t_ch.get() != t_ch_val:
                t_ch.set(t_ch_val)
        for ctrl_name in protocol.TRANSPORT_CONTROLS:
            row = self._ctrl_rows.get(f"transport_{ctrl_name}")
            if row:
                row.show(t.get_ctrl(ctrl_name))

    def _refresh_scene(self, full=False):
        s = self.scene
        sec = self._section
        if full:
            self._refresh_global(s)
            for g in range(protocol.NUM_GROUPS):
                self._refresh_channel(s, g)
            self._refresh_transport(s)
        else:
            if sec == "global":
                self._refresh_global(s)
            elif sec.startswith("ch"):
                self._refresh_channel(s, int(sec[2:]) - 1)
            elif sec == "transport":
                self._refresh_transport(s)
            # "leds" and "monitor" have no scene fields to refresh here

    def _place_page(self, page):
        page.lift()

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
            return "Scene saved to device", OK, "#ffffff"
        return "Connected", OK_DARK, "#ffffff"

    def _draw_status(self):
        text, bg, fg = self._status()
        self._show("status", (text, bg, fg), lambda v: self._status_btn.configure(
            text=v[0], fg_color=v[1], hover_color=v[1], text_color=v[2]))

    def _draw_led_btn(self):
        if self.scene is None:
            self._led_btn.configure(state="disabled", fg_color=CARD_HI, text="Ext LEDs: —")
            return
        active = self.scene.led_mode == 1
        self._led_btn.configure(
            state="normal",
            text=f"Ext LEDs: {'ON' if active else 'OFF'}",
            fg_color=ACCENT if active else CARD_HI,
            hover_color=ACCENT_H if active else EDGE,
        )

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
