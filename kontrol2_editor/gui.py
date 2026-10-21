"""Main window for the nanoKONTROL 2 Editor (CustomTkinter).

Sections (left nav):
  Global       — MIDI channel, control mode, LED mode
  Channel 1-8  — per-channel slider, knob, solo, mute, prime assignments
  Transport    — 11 transport button assignments

The device is kept linked in the background (link.py): found automatically,
scene read on connect, every change written after a short debounce.
"""
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

SECTIONS = ["global"] + [f"ch{i}" for i in range(1, 9)] + ["transport"]

SECTION_LABELS = {
    "global":    "Global",
    "transport": "Transport",
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
    """One row for a single control (slider / knob / button)."""

    def __init__(self, master, app, label, is_button, on_change):
        super().__init__(master, fg_color="transparent")
        self._app = app
        self._on_change = on_change
        self._is_button = is_button
        self._ctrl = None

        # label
        ctk.CTkLabel(self, text=label, font=(FONT, 13), text_color=TEXT,
                     anchor="w", width=110).pack(side="left")

        # assign dropdown
        self._assign_menu = app._option(self, list(protocol.ASSIGN_OPTS),
                                        self._on_assign, width=88)
        self._assign_menu.pack(side="left", padx=(0, 8))

        # CC / Note number
        self._cmd_spin = Spin(self, 0, 127, self._on_cmd, width=46)
        self._cmd_spin.pack(side="left", padx=(0, 8))

        # min / max (only for non-buttons)
        self._minmax = ctk.CTkFrame(self, fg_color="transparent")
        self._minmax.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(self._minmax, text="Min", font=(FONT, 11), text_color=MUTED).pack(side="left", padx=(0, 3))
        self._min_spin = Spin(self._minmax, 0, 127, self._on_min, width=46)
        self._min_spin.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(self._minmax, text="Max", font=(FONT, 11), text_color=MUTED).pack(side="left", padx=(0, 3))
        self._max_spin = Spin(self._minmax, 0, 127, self._on_max, width=46)
        self._max_spin.pack(side="left")

        # behaviour toggle (buttons only)
        self._behav = ctk.CTkFrame(self, fg_color="transparent")
        self._behav.pack(side="left")
        self._behav_seg = app._segmented(self._behav, list(protocol.BEHAVIOUR_OPTS), self._on_behav)
        self._behav_seg.pack()

        if not is_button:
            self._behav.pack_forget()
        else:
            self._minmax.pack_forget()

    def show(self, ctrl: protocol.ControlSettings):
        self._ctrl = ctrl
        assign_label = protocol.ASSIGN_OPTS[ctrl.assign]
        if self._assign_menu.get() != assign_label:
            self._assign_menu.set(assign_label)
        self._cmd_spin.set(ctrl.cmd)
        self._min_spin.set(ctrl.min_val)
        self._max_spin.set(ctrl.max_val)
        behav_label = protocol.BEHAVIOUR_OPTS[ctrl.behaviour]
        if self._behav_seg.get() != behav_label:
            self._behav_seg.set(behav_label)
        disabled = ctrl.assign == 0
        state = "disabled" if disabled else "normal"
        self._cmd_spin.minus.configure(state=state)
        self._cmd_spin.plus.configure(state=state)
        self._cmd_spin.entry.configure(state=state)
        if not self._is_button:
            self._min_spin.minus.configure(state=state)
            self._min_spin.plus.configure(state=state)
            self._min_spin.entry.configure(state=state)
            self._max_spin.minus.configure(state=state)
            self._max_spin.plus.configure(state=state)
            self._max_spin.entry.configure(state=state)
        else:
            self._behav_seg.configure(state=state)

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

        self.scene = None       # what the window shows / wants on the device
        self.original = None    # scene when first linked (for Undo all)
        self._new_link = False
        self._section = "global"
        self._reading = False
        self._writing = False
        self._failed = False
        self._written_at = -100.0
        self._shown = {}
        self._ctrl_rows = {}    # key -> ControlRow
        self._scene_dirty = True
        self._full_refresh = True   # True = update all sections, False = current only

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
        for key, cmd, w in (("open", self._open_file, 80), ("save", self._save_file, 86),
                            ("undo_all", self._undo_all, 120)):
            btn = ctk.CTkButton(actions, text={"open": "Open", "save": "Save",
                                               "undo_all": "Undo all"}[key],
                                width=w, command=cmd, **plain)
            btn.pack(side="left", padx=(0, 6))
            self._action_btns[key] = btn

        ctk.CTkFrame(actions, fg_color="transparent", width=6, height=1).pack(side="left")
        self._led_btn = ctk.CTkButton(
            actions, text="External LEDs: OFF", width=170, height=36,
            corner_radius=18, font=(FONT, 13, "bold"), command=self._toggle_led_mode)
        self._led_btn.pack(side="left", padx=(0, 6))

        self._status_btn = ctk.CTkButton(
            actions, text="", width=250, height=36, corner_radius=18,
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

        # Stack all pages via place() — switching is just lift(), zero layout recalc
        self._placeholder.place(x=0, y=0, relwidth=1, relheight=1)
        for page in self._pages.values():
            page.place(x=0, y=0, relwidth=1, relheight=1)
        self._placeholder.lift()

        # ── footer ────────────────────────────────────────────────────────────
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=22, pady=(10, 12))
        self._msg_label = ctk.CTkLabel(footer, text="", font=(FONT, 12),
                                       text_color=MUTED, anchor="w")
        self._msg_label.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(footer, text=f"Not for live use — for configuration only   v{__version__}",
                     font=(FONT, 11), text_color="#5d6573").pack(side="right")

    # ── page builders ─────────────────────────────────────────────────────────

    def _build_page(self, section):
        # Transport has 11+ rows; use scrollable frame. Other sections fit in a fixed frame.
        if section == "transport":
            frame = ctk.CTkScrollableFrame(self._content, fg_color="transparent",
                                           scrollbar_button_color=EDGE,
                                           scrollbar_button_hover_color=CARD_HI,
                                           corner_radius=0)
            self._build_transport_page(frame)
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
        """Create a label+widget row frame; returns (row, label_widget)."""
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

        # LED mode — prominent description
        divider = ctk.CTkFrame(parent, fg_color=EDGE, height=1)
        divider.pack(fill="x", padx=22, pady=(14, 6))
        ctk.CTkLabel(parent, text="LED Mode", font=(FONT, 14, "bold"),
                     text_color=TEXT, anchor="w").pack(fill="x", padx=22, pady=(4, 2))
        ctk.CTkLabel(
            parent,
            text="Internal: device controls its own LEDs.\n"
                 "External: DAW controls the LEDs (needed for Ableton, Cubase, etc.).",
            font=(FONT, 11), text_color=MUTED, anchor="w", justify="left",
        ).pack(fill="x", padx=22, pady=(0, 8))
        self._led_seg = self._segmented(parent, list(protocol.LED_MODES),
                                        lambda v: self._set_global("led_mode",
                                                                    list(protocol.LED_MODES).index(v)))
        self._led_seg.pack(padx=22, anchor="w", pady=(0, 12))

    def _build_channel_page(self, parent, group_index: int):
        g = group_index
        self._section_title(parent, f"Channel {g + 1}")

        # Channel selector
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

    def _build_transport_page(self, parent):
        self._section_title(parent, "Transport")

        # Transport MIDI channel
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

    # ── mutations ─────────────────────────────────────────────────────────────

    def _set_global(self, attr, value):
        if self.scene is None:
            return
        s = self.scene.copy()
        setattr(s, attr, value)
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
            self._full_refresh = False  # only current section changed
            self.link.push(new_scene)
        self.refresh()

    def _toggle_led_mode(self):
        if self.scene is None:
            return
        new_mode = 1 - self.scene.led_mode
        label = protocol.LED_MODES[new_mode]
        verb = "enable" if new_mode == 1 else "disable"
        if not self.ask_yes_no(
            "External LED Mode",
            f"Do you want to {verb} External LED mode?\n\n"
            "External mode lets the DAW control the LED buttons.\n"
            "Internal mode means the device controls its own LEDs.",
        ):
            return
        self._set_global("led_mode", new_mode)

    # ── actions ───────────────────────────────────────────────────────────────

    def _show_section(self, section):
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
        if not self.ask_yes_no("Undo all changes", "Restore the scene to how it was when the device connected?"):
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
        self._show("save_state", state,
                   lambda s: self._action_btns["save"].configure(state=s))
        undo = "normal" if self.original is not None and self.link.linked else "disabled"
        self._show("undo_state", undo,
                   lambda s: self._action_btns["undo_all"].configure(state=s))

        if self.scene is not None and self._scene_dirty:
            self._scene_dirty = False
            full, self._full_refresh = self._full_refresh, False
            self._refresh_scene(full=full)

        self._draw_status()
        self._draw_led_btn()

    def _refresh_global(self, s):
        ch_label = str(s.global_channel + 1)
        w = getattr(self, "_global_ch_menu", None)
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
        if full:
            self._refresh_global(s)
            for g in range(protocol.NUM_GROUPS):
                self._refresh_channel(s, g)
            self._refresh_transport(s)
        else:
            sec = self._section
            if sec == "global":
                self._refresh_global(s)
            elif sec.startswith("ch"):
                self._refresh_channel(s, int(sec[2:]) - 1)
            elif sec == "transport":
                self._refresh_transport(s)

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
            self._led_btn.configure(state="disabled", fg_color=CARD_HI,
                                    text="External LEDs: —")
            return
        active = self.scene.led_mode == 1
        self._led_btn.configure(
            state="normal",
            text=f"External LEDs: {'ON' if active else 'OFF'}",
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
