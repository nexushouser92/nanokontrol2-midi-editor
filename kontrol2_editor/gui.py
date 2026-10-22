"""MIDI Editor nanoKONTROL 2: main window.

Layout (design units, see theme.py):
  header   logo, name, Ext-LED switch, connection status
  sidebar  sections (Global, 8 channel strips, Transport, Calibration, LED Test, Monitor)
           and scene actions (Open, Save, Templates, Export, Undo, Factory reset)
  page     one Surface per page; switching a section only raises a canvas
  footer   messages and CC-conflict warning

The scene is never modified in place: every edit builds a copy, bumps `version` and
pushes it to the device, and pages redraw only when their `version` is stale.
"""
import collections
import csv
import os
import queue
import sys
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox

from PIL import Image, ImageTk

from . import APP_NAME, __version__, edit, presets, protocol, sequencer, theme
from .i18n import options, tr
from .link import KontrolLink
from . import midi_win
from .midi_win import MidiError
from .pages import (CalibrationPage, ChannelPage, ControlPage, GlobalPage, LedPage,
                    MonitorPage, SequencerPage, TransportPage, WaitingPage)
from .protocol import ProtocolError
from .theme import u
from .widgets import Button, Dropdown, Surface

POLL_MS = 15
SAVED_SECONDS = 2.0
CLOCK_TIMEOUT = 1.0
SEQ_SAVE_SECONDS = 2.0
PULSE_SECONDS = 0.2
MONITOR_KEEP = 400

PAGE_CLASSES = {
    "waiting": WaitingPage,
    "global": GlobalPage,
    "channels": ChannelPage,
    "transport": TransportPage,
    "calibration": CalibrationPage,
    "leds": LedPage,
    "sequencer": SequencerPage,
    "monitor": MonitorPage,
}


def led_name(key):
    """Display name of an LED button key such as "solo_3" or "play"."""
    if key in protocol.TRANSPORT_LABELS:
        return tr(protocol.TRANSPORT_LABELS[key])
    kind, number = key.rsplit("_", 1)
    return f"{tr(protocol.CHANNEL_CTRL_LABELS[kind])} {number}"


def page_key(section):
    return "channels" if section.startswith("ch") else section


def icon_path(name="icon.ico"):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets", name)


def create_root():
    """A hidden, dark, correctly scaled and positioned main window."""
    theme.enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    root.configure(bg=theme.BG)
    theme.setup(root)
    root.title(APP_NAME)
    width, height = u(theme.WINDOW_W), u(theme.WINDOW_H)
    x = max(0, (root.winfo_screenwidth() - width) // 2)
    y = max(0, (root.winfo_screenheight() - height) // 2 - u(20))
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.resizable(False, False)
    try:
        root.iconbitmap(icon_path())
    except tk.TclError:
        pass
    return root


class App:
    def __init__(self, root, link=None, autostart=True,
                 ask_yes_no=messagebox.askyesno,
                 ask_open=filedialog.askopenfilename,
                 ask_save=filedialog.asksaveasfilename,
                 clock=time.monotonic, seq_runner=None, seq_settings=None,
                 port_lister=midi_win.list_ports, save_seq_settings=sequencer.save_settings,
                 loopmidi_check=sequencer.loopmidi_installed, open_url=webbrowser.open):
        self.root = root
        self.link = link or KontrolLink()
        self.ask_yes_no = ask_yes_no
        self.ask_open = ask_open
        self.ask_save = ask_save
        self.clock = clock

        self.scene = None
        self.original = None          # scene as first read from the device, for "Undo all"
        self.version = 0              # bumped whenever the scene changes
        self.conflicts = set()
        self.section = "global"
        self.live = {}                # (strip, control) -> last value received
        self.seen = {}                # (strip, control) -> [lowest, highest] received
        self.monitor_start = clock()
        self.monitor_log = collections.deque(maxlen=MONITOR_KEEP)

        self._targets = None          # lazily built: MIDI message -> controls
        self._reading = self._writing = self._failed = self._new_link = False
        self._written_at = -SAVED_SECONDS
        self._learner = None
        self._leds = {}
        self._pulses = {}
        self._pages = {}
        self._visible = None
        self._chrome_state = None
        self._poll_id = None

        self.port_lister = port_lister
        self.loopmidi_check = loopmidi_check
        self.open_url = open_url
        self._save_seq_settings = save_seq_settings
        settings = sequencer.load_settings() if seq_settings is None else seq_settings
        self.seq = sequencer.Sequencer(protocol.Scene())
        self.seq.load_dict(settings.get("pattern", {}))
        self.seq_output = settings.get("output")
        self.seq_clock = settings.get("clock")
        self.seq_on = False
        self._seq_scene = None
        self._seq_leds_due = False
        self._seq_saved = (self.seq.version, self.seq_output, self.seq_clock)
        self._seq_changed_at = None
        self.seq_runner = seq_runner or sequencer.SequencerRunner(
            self.seq, lambda key, on: protocol.led_message(self._seq_scene, key, on), self.link.set_led)

        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind_all("<MouseWheel>", self._on_wheel)
        root.bind("<Control-s>", lambda _e: self.save_file())
        root.bind("<Control-o>", lambda _e: self.open_file())
        root.bind("<Escape>", lambda _e: self.cancel_learn())

        self._page_xy = (u(theme.MARGIN + theme.NAV_W + theme.GAP), u(theme.HEADER_H + theme.GAP))
        self._build_header()
        self._build_sidebar()
        self._build_footer()
        self._present()
        self._update_chrome()
        if autostart:
            self.link.start()
            self._schedule_poll()
        root.after(30, self._build_remaining_pages)

    # ------------------------------------------------------------------ construction

    def _page(self, key):
        page = self._pages.get(key)
        if page is None:
            page = self._pages[key] = PAGE_CLASSES[key](self)
            page.place(x=self._page_xy[0], y=self._page_xy[1])
            page.lower_widget()
        return page

    def _build_remaining_pages(self):
        """Build the pages not yet visited, one per idle tick, so the first paint is instant."""
        missing = [key for key in PAGE_CLASSES if key not in self._pages]
        if missing:
            self._page(missing[0])
            self.root.after(5, self._build_remaining_pages)

    def _build_header(self):
        width = theme.WINDOW_W
        header = Surface(self.root, width, theme.HEADER_H)
        header.place(x=0, y=0)
        try:
            logo = Image.open(icon_path("icon.png")).resize((u(40), u(40)), Image.LANCZOS)
            self._logo = ImageTk.PhotoImage(logo, master=self.root)
            header.create_image(u(14), u(11), image=self._logo, anchor="nw")
        except OSError:
            header.rrect(16, 13, 36, 36, 10, fill=theme.ACCENT)
        header.text(64, 23, APP_NAME, 19, weight="bold")
        header.text(64, 44, tr("Unofficial scene editor for the KORG nanoKONTROL 2"), 11, theme.MUTED)
        self._status = Button(header, width - 16 - 212, 14, 212, 32, "", self._on_status_click,
                              size=12, weight="bold", radius=16)
        self._led_chip = Button(header, width - 16 - 212 - 8 - 144, 14, 144, 32, tr("Ext LEDs: {state}", state="-"),
                                self.toggle_led_mode, size=12, weight="bold", radius=16)
        self._send = Button(header, width - 16 - 212 - 8 - 144 - 8 - 200, 14, 200, 32, tr("Send to nanoKONTROL"),
                            self.send_to_device, style="accent", size=12, weight="bold", radius=16)

    def _build_sidebar(self):
        """The black navigation rail, flush with the left edge of the window."""
        width, height = theme.MARGIN + theme.NAV_W, theme.WINDOW_H - theme.HEADER_H - theme.FOOTER_H
        nav = Surface(self.root, width, height, bg=theme.RAIL)
        nav.place(x=0, y=u(theme.HEADER_H))
        self._nav = {}
        x0 = 20

        def section(key, label, x, y, w, h=36, align="left"):
            cell = key.startswith("ch")
            self._nav[key] = Button(nav, x0 + x, y, w, h, label, lambda: self.show_section(key),
                                    style="rail_button" if cell else "rail", align=align,
                                    size=14 if cell else 13, weight="bold", radius=10)

        def caption(y, text):
            nav.text(x0 + 4, y, text, 10, theme.RAIL_MUTED, weight="bold")

        section("global", tr("Global"), 0, 14, 176)
        caption(74, tr("CHANNELS"))
        for i in range(protocol.NUM_GROUPS):
            section(f"ch{i + 1}", str(i + 1), (i % 4) * 46, 92 + (i // 4) * 44, 38, 38, "center")
        section("transport", tr("Transport"), 0, 190, 176)
        section("calibration", tr("Calibration"), 0, 232, 176)
        section("leds", tr("LED Test"), 0, 274, 176)
        section("sequencer", tr("Sequencer"), 0, 316, 176)
        section("monitor", tr("MIDI Monitor"), 0, 358, 176)

        nav.hline(x0 + 4, 408, 168, fill=theme.RAIL_EDGE)
        caption(428, tr("SCENE"))
        button = dict(style="rail_button", size=12, radius=10)
        self._open = Button(nav, x0, 444, 84, 32, tr("Open"), self.open_file, **button)
        self._save = Button(nav, x0 + 92, 444, 84, 32, tr("Save"), self.save_file, **button)
        self._templates = Dropdown(nav, x0, 484, 176, 32, options(edit.TEMPLATES), self.apply_template,
                                   caption=tr("Templates"), size=12, dark=True)
        self._export = Button(nav, x0, 524, 84, 32, tr("Export CC"), self.export_cheatsheet, **button)
        self._undo = Button(nav, x0 + 92, 524, 84, 32, tr("Undo all"), self.undo_all, **button)
        self._factory = Button(nav, x0, 564, 176, 32, tr("Factory reset"), self.factory_reset,
                               style="rail_danger", size=12, radius=10)

    def _build_footer(self):
        width = theme.WINDOW_W
        footer = Surface(self.root, width, theme.FOOTER_H)
        footer.place(x=0, y=u(theme.WINDOW_H - theme.FOOTER_H))
        self._footer = footer
        self._message = footer.text(theme.MARGIN + 4, 16, "", 12, theme.MUTED)
        self._conflict_note = footer.text(width - theme.MARGIN - 300, 16, "", 12, theme.WARN, anchor="e")
        footer.text(width - theme.MARGIN, 16, tr("Unofficial  ·  not affiliated with KORG  ·  v{version}", version=__version__),
                    11, theme.DIM, anchor="e")

    # ------------------------------------------------------------------ navigation

    def show_section(self, section):
        self.cancel_learn()
        self.section = section
        self._style_nav()
        self.root.update_idletasks()  # paint the clicked item before the heavier page
        self._present()

    def _style_nav(self):
        for key, button in self._nav.items():
            cell = key.startswith("ch")
            on = key == self.section
            button.set_style(("rail_cell_on" if cell else "rail_on") if on else "rail_button" if cell else "rail")

    def _present(self):
        self._style_nav()
        if self.scene is None:
            page = self._page("waiting")
        else:
            page = self._page(page_key(self.section))
            if isinstance(page, ChannelPage):
                page.show_strip(int(self.section[2:]) - 1)
            page.sync(self.scene, self.version, self.conflicts)
        if page is not self._visible:
            self._visible = page
            page.activate()
            page.raise_widget()

    def _on_wheel(self, event):
        try:
            target = self.root.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            return
        if isinstance(target, Surface):
            target.wheel(event.x_root - target.winfo_rootx(), event.y_root - target.winfo_rooty(), event.delta)

    # ------------------------------------------------------------------ scene edits

    def _scene_changed(self):
        self.version += 1
        self.conflicts = edit.find_conflicts(self.scene)
        self._targets = None
        count = len(self.conflicts)
        self._footer.itemconfigure(
            self._conflict_note, text=tr("{count} controls share a CC / Note number", count=count) if count else "")
        self._present()
        self._update_chrome()

    def _seq_blocks(self):
        """While the sequencer runs the device holds the sequencer scene: scene changes must wait."""
        if self.seq_on:
            self.say(tr("Stop the sequencer to change the scene."), theme.WARN)
        return self.seq_on

    def _replace_scene(self, scene, push):
        if push and self._seq_blocks():
            return
        self.scene = scene
        self.seen.clear()
        self._scene_changed()
        if push:
            self.link.push(scene)

    def _commit(self, scene):
        if self.scene is None or scene == self.scene or self._seq_blocks():
            return
        self.scene = scene
        self._failed = False
        self._scene_changed()
        self.link.push(scene)

    def edit_scene(self, change):
        if self.scene is not None:
            scene = self.scene.copy()
            change(scene)
            self._commit(scene)

    def set_global(self, attr, value):
        self.edit_scene(lambda s: setattr(s, attr, value))

    def set_custom_daw(self, slot, value):
        self.edit_scene(lambda s: s.custom_daw.__setitem__(slot, value))

    def set_channel(self, key, channel):
        self.edit_scene(lambda s: setattr(edit.group_of(s, key), "channel", channel))

    def set_ctrl(self, key, name, **changes):
        if "min_val" in changes or "max_val" in changes:
            self.seen.pop((key, name), None)

        def apply(scene):
            ctrl = edit.group_of(scene, key).get_ctrl(name)
            for attr, value in changes.items():
                setattr(ctrl, attr, value)

        self.edit_scene(apply)

    def copy_group(self, source, targets):
        if self.scene is None:
            return
        if len(targets) > 1 and not self.ask_yes_no(
                tr("Copy strip"),
                tr("Copy strip {n} onto all other strips?\nTheir current settings are replaced.", n=source + 1)):
            return
        self._commit(edit.copy_group(self.scene, source, targets))
        names = ", ".join(str(t + 1) for t in targets)
        self.say(tr("Strip {n} copied to {names}.", n=source + 1, names=names), theme.OK)

    def apply_template(self, label):
        name = edit.TEMPLATES[options(edit.TEMPLATES).index(label)]
        if self.scene is None or not self.ask_yes_no(
                tr("Apply template"), tr("Apply '{name}'?\nThis replaces the assignments of every control.", name=label)):
            return
        self._commit(edit.apply_template(self.scene, name))
        self.say(tr("Template applied: {name}.", name=label), theme.OK)

    def reset_ranges(self):
        if self.scene is None or not self.ask_yes_no(
                tr("Reset ranges"), tr("Set every fader and knob back to the full 0-127 range?")):
            return
        self.seen.clear()
        self.live.clear()
        self._commit(edit.reset_ranges(self.scene))
        if self._visible is not None:
            self._visible.activate()
        self.say(tr("All fader and knob ranges reset to 0-127."), theme.OK)

    def send_to_device(self):
        """Send the whole scene now and save it on the device (edits are also sent as you make them)."""
        if self.scene is None or not self.link.linked or self._seq_blocks():
            return
        self._failed = False
        self.link.push(self.scene)
        self.link.flush()
        self.say(tr("Sending the scene to the nanoKONTROL 2..."))

    def factory_reset(self):
        if self.scene is None or not self.ask_yes_no(
                tr("Factory reset"), tr("Reset ALL settings to factory defaults?\nEvery assignment and mode is replaced.")):
            return
        self._replace_scene(protocol.Scene(), push=True)
        self.say(tr("Factory defaults applied."), theme.OK)

    def undo_all(self):
        if self.original is None or self.scene is None:
            return
        if self.ask_yes_no(tr("Undo all changes"), tr("Restore the scene as it was when the device connected?")):
            self._replace_scene(self.original.copy(), push=True)
            self.say(tr("Restored the original scene."))

    def toggle_led_mode(self):
        if self.scene is not None:
            self.set_global("led_mode", 1 - self.scene.led_mode)

    # ------------------------------------------------------------------ files

    def open_file(self):
        path = self.ask_open(title=tr("Open preset"), filetypes=[
            (tr("Preset or SysEx"), "*.json *.syx"), (tr("JSON preset"), "*.json"), (tr("SysEx dump"), "*.syx")])
        if not path:
            return
        try:
            scene = presets.load(path)
        except (ProtocolError, OSError) as e:
            self.say(tr("Could not open the file: {error}", error=e), theme.ERROR)
            return
        self._replace_scene(scene, push=True)
        self.say(tr("Opened {name}.", name=os.path.basename(path)), theme.OK)

    def save_file(self):
        if self.scene is None:
            return
        path = self.ask_save(title=tr("Save preset"), defaultextension=".json", filetypes=[
            (tr("JSON preset"), "*.json"), (tr("SysEx dump"), "*.syx")])
        if not path:
            return
        try:
            presets.save(path, self.scene)
        except (ProtocolError, OSError) as e:
            self.say(tr("Could not save the file: {error}", error=e), theme.ERROR)
        else:
            self.say(tr("Saved {name}.", name=os.path.basename(path)), theme.OK)

    def export_cheatsheet(self):
        if self.scene is None:
            return
        path = self.ask_save(title=tr("Export CC overview"), defaultextension=".csv",
                             filetypes=[(tr("CSV spreadsheet"), "*.csv")])
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerows(edit.cheatsheet_rows(self.scene))
        except OSError as e:
            self.say(tr("Could not export: {error}", error=e), theme.ERROR)
        else:
            self.say(tr("Exported {name}.", name=os.path.basename(path)), theme.OK)

    def say(self, text, color=theme.MUTED):
        self._footer.itemconfigure(self._message, text=text, fill=color)

    # ------------------------------------------------------------------ LED test and learn

    def toggle_led(self, key):
        self._set_led(key, not self._leds.get(key, False))

    def set_all_leds(self, on):
        for key in protocol.LED_BUTTONS:
            self._set_led(key, on)

    def _set_led(self, key, on):
        if self.scene is None or self._seq_blocks():
            return
        message = protocol.led_message(self.scene, key, on)
        if message is None:
            self.say(tr("{name} is disabled in the scene: it has no LED message.", name=led_name(key)), theme.WARN)
            return
        if not self.link.set_led(message):
            self.say(tr("Could not send the LED message: is the nanoKONTROL 2 linked?"), theme.ERROR)
            return
        self._leds[key] = on
        self._page("leds").set_led(key, on)

    def start_learn(self, row):
        self.cancel_learn()
        self._learner = row
        row.set_learning(True)
        self.link.start_learn()
        self.say(tr("Move or press a control on the device..."))

    def cancel_learn(self):
        if self._learner is not None:
            self._learner.set_learning(False)
            self._learner = None
            self.link.cancel_learn()

    def _learned(self, kind, number):
        row, self._learner = self._learner, None
        if row is not None:
            row.set_learning(False)
            row.apply_learned(kind, number)
            self.say(tr("Learned {kind} {number}.", kind="CC" if kind == "cc" else tr("Note"), number=number), theme.OK)

    # ------------------------------------------------------------------ sequencer

    def seq_ports(self):
        """(outputs, inputs) the sequencer can use: every MIDI port except the nanoKONTROL 2's own."""
        try:
            ins, outs = self.port_lister()
        except OSError:
            return [], []
        own = ("nanoKONTROL2", "KONTROL2")
        keep = lambda names: [n for n in names if n and not any(o.upper() in n.upper() for o in own)]
        return keep(outs), keep(ins)

    def refresh_seq_ports(self):
        outs, ins = self.seq_ports()
        self._page("sequencer").set_loopmidi(self.loopmidi_check())
        self._page("sequencer").set_ports(outs, [tr("Internal clock")] + ins, self.seq_output, self.seq_clock)

    def download_loopmidi(self):
        self.open_url(sequencer.LOOPMIDI_URL)
        self.say(tr("Install loopMIDI, create two ports in it and come back to this page."))

    def set_seq_output(self, name):
        self.seq_output = name
        self._seq_changed_at = self.clock()

    def set_seq_clock(self, name):
        self.seq_clock = sequencer.INTERNAL if name == tr("Internal clock") else name
        self._seq_changed_at = self.clock()

    def toggle_sequencer(self):
        if self.seq_on:
            self.stop_sequencer()
        else:
            self.start_sequencer()

    def start_sequencer(self):
        if self.seq_on:
            return
        if self.scene is None or not self.link.linked:
            self.say(tr("Connect the nanoKONTROL 2 to use the sequencer."), theme.WARN)
            return
        if not self.seq_output or not self.seq_clock:
            self.say(tr("Choose the 'Notes to' and 'Clock from' MIDI ports first."), theme.WARN)
            return
        internal = self.seq_clock == sequencer.INTERNAL
        if self.seq_output == self.seq_clock:
            self.say(tr("Use two different ports: one for the notes and one for the clock."), theme.WARN)
            return
        self.cancel_learn()
        self._seq_scene = sequencer.sequencer_scene(self.scene)
        self.seq.set_scene(self._seq_scene)
        try:
            self.seq_runner.start(self.seq_output, None if internal else self.seq_clock)
        except MidiError as e:
            self.say(tr("Could not open the MIDI port: {error}", error=e), theme.ERROR)
            return
        self.seq_on = True
        self._seq_leds_due = True
        self.link.listener = self.seq_runner.device_message
        self.link.push(self._seq_scene, save=False)
        self.link.flush()
        self._chrome_state = None
        self.say(tr("Sequencer on: press Play on the nanoKONTROL 2.") if internal
                 else tr("Sequencer on: press Play in your DAW."), theme.OK)

    def stop_sequencer(self):
        if not self.seq_on:
            return
        self.link.listener = None
        self.seq_runner.stop()
        self.seq_on = False
        if self.link.linked and self.scene is not None:
            self.link.push(self.scene, save=False)
            self.link.flush()
        self._chrome_state = None
        self.say(tr("Sequencer off: the nanoKONTROL 2 is back to your scene."))

    def seq_toggle(self, track, step):
        self.seq_runner.post(self.seq.toggle_step, track, step)

    def seq_select(self, track):
        self.seq_runner.post(self.seq.select, track)

    def seq_solo(self, track):
        self.seq_runner.post(self.seq.toggle_solo, track)

    def seq_tempo(self, bpm):
        self.seq_runner.post(self.seq.set_tempo, bpm)

    def sequencer_status(self):
        if not self.seq_on:
            return tr("Sequencer off."), theme.MUTED
        runner = self.seq_runner
        if self.seq_clock == sequencer.INTERNAL:
            if self.seq.playing and self.seq.position >= 0:
                return tr("Playing  ·  {bpm} BPM  ·  step {step}", bpm=self.seq.tempo,
                          step=self.seq.position + 1), theme.OK
            return tr("Stopped: press Play on the nanoKONTROL 2."), theme.MUTED
        bpm = f"{runner.bpm:.0f}" if runner.bpm else "-"
        if runner.last_clock is None or self.clock() - runner.last_clock > CLOCK_TIMEOUT:
            return tr("Waiting for the DAW clock: press Play in your DAW."), theme.WARN
        if self.seq.playing and self.seq.position >= 0:
            return tr("Playing  ·  {bpm} BPM  ·  step {step}", bpm=bpm, step=self.seq.position + 1), theme.OK
        return tr("Stopped  ·  {bpm} BPM", bpm=bpm), theme.MUTED

    def update_sequencer_page(self):
        page = self._visible
        if isinstance(page, SequencerPage):
            page.show(self.seq, self.seq_on, *self.sequencer_status())

    def _autosave_sequencer(self, force=False):
        state = (self.seq.version, self.seq_output, self.seq_clock)
        if state == self._seq_saved:
            return
        now = self.clock()
        if self._seq_changed_at is None:
            self._seq_changed_at = now
        if force or now - self._seq_changed_at >= SEQ_SAVE_SECONDS:
            self._seq_saved = state
            self._seq_changed_at = None
            self._save_seq_settings({"pattern": self.seq.to_dict(), "output": self.seq_output,
                                     "clock": self.seq_clock})

    # ------------------------------------------------------------------ monitor

    def clear_monitor(self):
        self.monitor_log.clear()
        self.monitor_start = self.clock()
        if isinstance(self._visible, MonitorPage):
            self._visible.reset(self.monitor_start, ())

    # ------------------------------------------------------------------ polling

    def _schedule_poll(self):
        self._poll_id = self.root.after(POLL_MS, self._poll)

    def _poll(self):
        try:
            self.tick()
        finally:
            self._schedule_poll()

    def tick(self):
        """One poll step: device events, live MIDI, indicator timeouts, header state."""
        self._drain_events()
        self._drain_midi()
        self._expire_pulses()
        self._update_chrome()
        self.update_sequencer_page()
        self._autosave_sequencer()

    def _drain_events(self):
        while True:
            try:
                kind, payload = self.link.events.get_nowait()
            except queue.Empty:
                return
            self._handle_event(kind, payload)

    def _handle_event(self, kind, payload):
        if kind == "linked":
            self._reading = self._new_link = True
            self.say(tr("Reading the scene..."))
        elif kind == "settings":
            self._reading = False
            if self._new_link:
                self._new_link = False
                self.original = payload
            if self.scene is None or not self.link.has_pending:
                self._replace_scene(payload, push=False)
            self.say(tr("Scene read from the device."), theme.OK)
        elif kind == "writing":
            self._writing = True
        elif kind == "written":
            self._writing = self._failed = False
            self._written_at = self.clock()
            if self.seq_on and self._seq_leds_due:
                self._seq_leds_due = False
                self.seq_runner.post(self.seq.all_leds)
        elif kind == "write_failed":
            self._writing, self._failed = False, True
            self.say(tr("Write failed: {error}", error=payload), theme.ERROR)
        elif kind == "learned":
            self._learned(*payload)
        elif kind == "searching":
            self._reading = self._writing = False
            if self.seq_on:
                self.stop_sequencer()
            busy = isinstance(payload, MidiError) and "open" in str(payload).lower()
            note = tr("Port busy: close the other program using the device.") if busy else str(payload or "")
            self._page("waiting").set_note(note)
            if note:
                self.say(note, theme.ERROR)

    def _drain_midi(self):
        messages = []
        while True:
            try:
                messages.append(self.link.incoming.get_nowait())
            except queue.Empty:
                break
        if not messages:
            return
        self.monitor_log.extend(messages)
        if isinstance(self._visible, MonitorPage):
            self._visible.append_messages(self.monitor_start, messages)
        if self.scene is not None and not self.seq_on:
            self._route(messages)

    def _route(self, messages):
        """Match incoming CC / Note messages to the controls that send them."""
        if self._targets is None:
            self._targets = edit.message_targets(self.scene)
        touched = set()
        for _stamp, status, number, value in messages:
            kind = status & 0xF0
            assign = 1 if kind == 0xB0 else 2 if kind == 0x90 and value > 0 else None
            if assign is None:
                continue
            for target in self._targets.get((status & 0x0F, assign, number), ()):
                touched.add(target)
                if target[1] in edit.ANALOG_CONTROLS:
                    self.live[target] = value
                    extremes = self.seen.setdefault(target, [value, value])
                    extremes[0], extremes[1] = min(extremes[0], value), max(extremes[1], value)
        page = self._visible
        for target in touched:
            if isinstance(page, CalibrationPage):
                page.update_control(target)
            elif isinstance(page, ControlPage) and page.key == target[0]:
                row = page.rows[target[1]]
                row.dot.set(True)
                self._pulses[row] = self.clock() + PULSE_SECONDS

    def _expire_pulses(self):
        if self._pulses:
            now = self.clock()
            for row, until in list(self._pulses.items()):
                if now >= until:
                    row.dot.set(False)
                    del self._pulses[row]

    # ------------------------------------------------------------------ header state

    def _update_chrome(self):
        scene = self.scene
        linked = self.link.linked
        busy = self._writing or self.link.has_pending
        saved = self.clock() - self._written_at < SAVED_SECONDS
        state = (linked, self._reading, busy, self._failed, saved, scene is not None,
                 scene.led_mode if scene is not None else None, self.original is not None)
        if state == self._chrome_state:
            return
        self._chrome_state = state

        if not linked:
            text, style = tr("Searching for device..."), "display"
        elif self._reading:
            text, style = tr("Reading scene..."), "display_busy"
        elif self._failed:
            text, style = tr("Write failed - click to retry"), "display_err"
        elif busy:
            text, style = tr("Writing to device..."), "display_busy"
        elif saved:
            text, style = tr("Saved to device"), "display_ok"
        else:
            text, style = tr("Connected"), "display_ok"
        self._status.set_text(text)
        self._status.set_style(style)

        if scene is None:
            self._led_chip.set_text(tr("Ext LEDs: {state}", state="-"))
            self._led_chip.set_style("default")
        else:
            external = scene.led_mode == 1
            self._led_chip.set_text(tr("Ext LEDs: {state}", state=tr("ON") if external else tr("OFF")))
            self._led_chip.set_style("led" if external else "default")
        self._led_chip.set_enabled(scene is not None)

        for widget in (self._save, self._export, self._factory, self._templates):
            widget.set_enabled(scene is not None)
        self._undo.set_enabled(self.original is not None and linked)
        self._send.set_enabled(scene is not None and linked and not busy and not self.seq_on)

    def _on_status_click(self):
        if not self.link.linked or self._seq_blocks():
            return
        if self._failed and self.scene is not None:
            self._failed = False
            self.link.push(self.scene)
            self.link.flush()
        else:
            self.link.request_read()
            self._reading = True

    def close(self):
        if self._poll_id is not None:
            self.root.after_cancel(self._poll_id)
        self.cancel_learn()
        self.stop_sequencer()
        self._autosave_sequencer(force=True)
        self.link.stop()
        self.root.destroy()


def main():
    root = create_root()
    App(root)
    root.deiconify()
    theme.dark_title_bar(root)
    root.mainloop()


if __name__ == "__main__":
    main()
