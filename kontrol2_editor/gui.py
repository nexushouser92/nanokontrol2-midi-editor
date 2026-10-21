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
from tkinter import filedialog, messagebox

from . import APP_NAME, __version__, edit, presets, protocol, theme
from .link import KontrolLink
from .midi_win import MidiError
from .pages import (CalibrationPage, ChannelPage, ControlPage, GlobalPage, LedPage,
                    MonitorPage, TransportPage, WaitingPage)
from .protocol import ProtocolError
from .theme import u
from .widgets import Button, Dropdown, Surface

POLL_MS = 15
SAVED_SECONDS = 2.0
PULSE_SECONDS = 0.2
MONITOR_KEEP = 400

PAGE_CLASSES = {
    "waiting": WaitingPage,
    "global": GlobalPage,
    "channels": ChannelPage,
    "transport": TransportPage,
    "calibration": CalibrationPage,
    "leds": LedPage,
    "monitor": MonitorPage,
}


def page_key(section):
    return "channels" if section.startswith("ch") else section


def icon_path():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets", "icon.ico")


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
                 clock=time.monotonic):
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

        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind_all("<MouseWheel>", self._on_wheel)
        root.bind("<Control-s>", lambda _e: self.save_file())
        root.bind("<Control-o>", lambda _e: self.open_file())
        root.bind("<Escape>", lambda _e: self.cancel_learn())

        self._page_xy = (u(theme.MARGIN + theme.NAV_W + theme.GAP), u(theme.HEADER_H + 4))
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
        header.rrect(16, 13, 36, 36, 10, fill=theme.ACCENT)
        for x, knob in ((25, 26), (34, 36), (43, 30)):
            header.line(x, 21, x, 41, fill="#ffffff", width=1.6)
            header.oval(x, knob, 3.2, fill="#ffffff")
        header.text(64, 23, APP_NAME, 19, weight="bold")
        header.text(64, 44, "Unofficial scene editor for the KORG nanoKONTROL 2", 11, theme.MUTED)
        self._status = Button(header, width - 16 - 212, 14, 212, 32, "", self._on_status_click,
                              size=12, weight="bold", radius=16)
        self._led_chip = Button(header, width - 16 - 212 - 8 - 144, 14, 144, 32, "Ext LEDs: -",
                                self.toggle_led_mode, size=12, weight="bold", radius=16)

    def _build_sidebar(self):
        nav = Surface(self.root, theme.NAV_W, theme.PAGE_H)
        nav.place(x=u(theme.MARGIN), y=u(theme.HEADER_H + 4))
        nav.panel(0, 0, theme.NAV_W, theme.PAGE_H, 14, theme.PANEL)
        nav.backdrop = theme.PANEL
        self._nav = {}

        def section(key, label, x, y, w, h=36, align="left"):
            self._nav[key] = Button(nav, x, y, w, h, label, lambda: self.show_section(key),
                                    style="cell" if key.startswith("ch") else "nav",
                                    align=align, size=14 if key.startswith("ch") else 13,
                                    weight="bold" if key.startswith("ch") else "normal")

        section("global", "Global", 12, 14, 176)
        nav.text(16, 74, "CHANNELS", 10, theme.MUTED, weight="bold")
        for i in range(protocol.NUM_GROUPS):
            section(f"ch{i + 1}", str(i + 1), 12 + (i % 4) * 46, 92 + (i // 4) * 44, 38, 38, "center")
        section("transport", "Transport", 12, 190, 176)
        section("calibration", "Calibration", 12, 232, 176)
        section("leds", "LED Test", 12, 274, 176)
        section("monitor", "MIDI Monitor", 12, 316, 176)

        nav.hline(16, 366, 168)
        nav.text(16, 386, "SCENE", 10, theme.MUTED, weight="bold")
        self._open = Button(nav, 12, 402, 84, 32, "Open", self.open_file, size=12)
        self._save = Button(nav, 104, 402, 84, 32, "Save", self.save_file, size=12)
        self._templates = Dropdown(nav, 12, 442, 176, 32, edit.TEMPLATES, self.apply_template,
                                   caption="Templates", size=12)
        self._export = Button(nav, 12, 482, 84, 32, "Export CC", self.export_cheatsheet, size=12)
        self._undo = Button(nav, 104, 482, 84, 32, "Undo all", self.undo_all, size=12)
        self._factory = Button(nav, 12, 522, 176, 32, "Factory reset", self.factory_reset,
                               style="danger", size=12)

    def _build_footer(self):
        width = theme.WINDOW_W
        footer = Surface(self.root, width, theme.FOOTER_H)
        footer.place(x=0, y=u(theme.WINDOW_H - theme.FOOTER_H))
        self._footer = footer
        self._message = footer.text(theme.MARGIN + 4, 16, "", 12, theme.MUTED)
        self._conflict_note = footer.text(width - theme.MARGIN - 300, 16, "", 12, theme.WARN, anchor="e")
        footer.text(width - theme.MARGIN, 16, f"Unofficial  ·  not affiliated with KORG  ·  v{__version__}",
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
            button.set_style(("cell_on" if on else "cell") if cell else ("nav_on" if on else "nav"))

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
            self._conflict_note, text=f"{count} controls share a CC / Note number" if count else "")
        self._present()
        self._update_chrome()

    def _replace_scene(self, scene, push):
        self.scene = scene
        self.seen.clear()
        self._scene_changed()
        if push:
            self.link.push(scene)

    def _commit(self, scene):
        if self.scene is None or scene == self.scene:
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
                "Copy strip", f"Copy strip {source + 1} onto all other strips?\nTheir current settings are replaced."):
            return
        self._commit(edit.copy_group(self.scene, source, targets))
        names = ", ".join(str(t + 1) for t in targets)
        self.say(f"Strip {source + 1} copied to {names}.", theme.OK)

    def apply_template(self, name):
        if self.scene is None or not self.ask_yes_no(
                "Apply template", f"Apply '{name}'?\nThis replaces the assignments of every control."):
            return
        self._commit(edit.apply_template(self.scene, name))
        self.say(f"Template applied: {name}.", theme.OK)

    def reset_ranges(self):
        if self.scene is None or not self.ask_yes_no(
                "Reset ranges", "Set every fader and knob back to the full 0-127 range?"):
            return
        self.seen.clear()
        self._commit(edit.reset_ranges(self.scene))
        self.say("All fader and knob ranges reset to 0-127.", theme.OK)

    def clear_measured(self):
        self.seen.clear()
        self.live.clear()
        if self._visible is not None:
            self._visible.activate()

    def factory_reset(self):
        if self.scene is None or not self.ask_yes_no(
                "Factory reset", "Reset ALL settings to factory defaults?\nEvery assignment and mode is replaced."):
            return
        self._replace_scene(protocol.Scene(), push=True)
        self.say("Factory defaults applied.", theme.OK)

    def undo_all(self):
        if self.original is None or self.scene is None:
            return
        if self.ask_yes_no("Undo all changes", "Restore the scene as it was when the device connected?"):
            self._replace_scene(self.original.copy(), push=True)
            self.say("Restored the original scene.")

    def toggle_led_mode(self):
        if self.scene is not None:
            self.set_global("led_mode", 1 - self.scene.led_mode)

    # ------------------------------------------------------------------ files

    def open_file(self):
        path = self.ask_open(title="Open preset", filetypes=[
            ("Preset or SysEx", "*.json *.syx"), ("JSON preset", "*.json"), ("SysEx dump", "*.syx")])
        if not path:
            return
        try:
            scene = presets.load(path)
        except (ProtocolError, OSError) as e:
            self.say(f"Could not open the file: {e}", theme.ERROR)
            return
        self._replace_scene(scene, push=True)
        self.say(f"Opened {os.path.basename(path)}.", theme.OK)

    def save_file(self):
        if self.scene is None:
            return
        path = self.ask_save(title="Save preset", defaultextension=".json", filetypes=[
            ("JSON preset", "*.json"), ("SysEx dump", "*.syx")])
        if not path:
            return
        try:
            presets.save(path, self.scene)
        except (ProtocolError, OSError) as e:
            self.say(f"Could not save the file: {e}", theme.ERROR)
        else:
            self.say(f"Saved {os.path.basename(path)}.", theme.OK)

    def export_cheatsheet(self):
        if self.scene is None:
            return
        path = self.ask_save(title="Export CC overview", defaultextension=".csv",
                             filetypes=[("CSV spreadsheet", "*.csv")])
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerows(edit.cheatsheet_rows(self.scene))
        except OSError as e:
            self.say(f"Could not export: {e}", theme.ERROR)
        else:
            self.say(f"Exported {os.path.basename(path)}.", theme.OK)

    def say(self, text, color=theme.MUTED):
        self._footer.itemconfigure(self._message, text=text, fill=color)

    # ------------------------------------------------------------------ LED test and learn

    def toggle_led(self, key):
        self._set_led(key, not self._leds.get(key, False))

    def set_all_leds(self, on):
        for key in protocol.LED_CCS:
            self._set_led(key, on)

    def _set_led(self, key, on):
        self._leds[key] = on
        self.link.set_led(protocol.LED_CCS[key], on)
        self._page("leds").set_led(key, on)

    def start_learn(self, row):
        self.cancel_learn()
        self._learner = row
        row.set_learning(True)
        self.link.start_learn()
        self.say("Move or press a control on the device...")

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
            self.say(f"Learned {'CC' if kind == 'cc' else 'Note'} {number}.", theme.OK)

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
            self.say("Reading the scene...")
        elif kind == "settings":
            self._reading = False
            if self._new_link:
                self._new_link = False
                self.original = payload
            if self.scene is None or not self.link.has_pending:
                self._replace_scene(payload, push=False)
            self.say("Scene read from the device.", theme.OK)
        elif kind == "writing":
            self._writing = True
        elif kind == "written":
            self._writing = self._failed = False
            self._written_at = self.clock()
        elif kind == "write_failed":
            self._writing, self._failed = False, True
            self.say(f"Write failed: {payload}", theme.ERROR)
        elif kind == "learned":
            self._learned(*payload)
        elif kind == "searching":
            self._reading = self._writing = False
            busy = isinstance(payload, MidiError) and "open" in str(payload).lower()
            note = "Port busy: close the other program using the device." if busy else str(payload or "")
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
        if self.scene is not None:
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
            text, style = "Searching for device...", "default"
        elif self._reading:
            text, style = "Reading scene...", "accent"
        elif self._failed:
            text, style = "Write failed - click to retry", "danger"
        elif busy:
            text, style = "Writing to device...", "accent"
        elif saved:
            text, style = "Saved to device", "ok"
        else:
            text, style = "Connected", "ok"
        self._status.set_text(text)
        self._status.set_style(style)

        if scene is None:
            self._led_chip.set_text("Ext LEDs: -")
            self._led_chip.set_style("default")
        else:
            external = scene.led_mode == 1
            self._led_chip.set_text(f"Ext LEDs: {'ON' if external else 'OFF'}")
            self._led_chip.set_style("accent" if external else "default")
        self._led_chip.set_enabled(scene is not None)

        for widget in (self._save, self._export, self._factory, self._templates):
            widget.set_enabled(scene is not None)
        self._undo.set_enabled(self.original is not None and linked)

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

    def close(self):
        if self._poll_id is not None:
            self.root.after_cancel(self._poll_id)
        self.cancel_learn()
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
