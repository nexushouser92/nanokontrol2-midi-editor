"""The pages of the editor. Each page is one Surface; the Channel page is shared by all
eight strips (only its values change), so the whole interface needs just seven canvases."""
import tkinter as tk

from . import edit, protocol, sequencer, theme
from .i18n import options, tr
from .theme import PAGE_H, PAGE_W, u
from .widgets import Button, Dot, Dropdown, RangeBar, Segmented, Spin, Surface

CHANNEL_CHOICES = [str(i) for i in range(1, 17)] + ["Global"]
LED_MODES = protocol.LED_MODES

# Column layout shared by the Channel and Transport pages (design units).
ROW_X, ROW_W = 20, PAGE_W - 40
COL_DOT, COL_LABEL, COL_ASSIGN, COL_CMD, COL_LEARN, COL_LO, COL_HI, COL_RIGHT = (
    38, 52, 152, 266, 380, 462, 574, 696)
CONTROL_H = 32


def _channel_text(channel):
    return "Global" if channel >= 16 else str(channel + 1)


def _channel_value(text):
    return 16 if text == "Global" else int(text) - 1


class Page(Surface):
    def __init__(self, app):
        super().__init__(app.root, PAGE_W, PAGE_H)
        self.app = app
        self.version = -1
        self.panel(0, 0, PAGE_W, PAGE_H, 14, theme.PANEL)
        self.backdrop = theme.PANEL

    def heading(self, title, subtitle=""):
        self._title = self.text(28, 32, title, 22, weight="bold")
        self._subtitle = self.text(28, 62, subtitle, 12, theme.MUTED)

    def caption(self, x, y, text, anchor="w"):
        return self.text(x, y, text, 10, theme.MUTED, anchor=anchor, weight="bold")

    def sync(self, scene, version, conflicts):
        """Redraw only if the scene changed since this page last showed it."""
        if version != self.version:
            self.version = version
            self.refresh(scene, conflicts)

    def refresh(self, scene, conflicts):
        pass

    def activate(self):
        """Called every time the page is brought to the front."""


class WaitingPage(Page):
    def __init__(self, app):
        super().__init__(app)
        self.text(PAGE_W / 2, PAGE_H / 2 - 30, tr("Looking for your nanoKONTROL 2"), 20, anchor="center", weight="bold")
        self.text(PAGE_W / 2, PAGE_H / 2 + 8, tr("Connect it with USB and close any other program that uses it."),
                  13, theme.MUTED, anchor="center")
        self._note = self.text(PAGE_W / 2, PAGE_H / 2 + 38, "", 12, theme.WARN, anchor="center")
        self.text(PAGE_W / 2, PAGE_H / 2 + 76, tr("You can also open a saved preset from the sidebar."),
                  12, theme.DIM, anchor="center")
        self._shown = ""

    def set_note(self, text):
        if text != self._shown:
            self._shown = text
            self.itemconfigure(self._note, text=text)


# --------------------------------------------------------------------------- Global

class GlobalPage(Page):
    def __init__(self, app):
        super().__init__(app)
        self.heading(tr("Global"), tr("Settings that apply to the whole scene"))

        def setting(cy, title, hint):
            self.text(28, cy - 10, tr(title), 15)
            self.text(28, cy + 12, tr(hint), 11, theme.MUTED)

        setting(140, "Global MIDI channel", "Used by every strip set to Global")
        self.channel = Dropdown(self, 430, 122, 120, 36, CHANNEL_CHOICES[:16],
                                lambda v: app.set_global("global_channel", int(v) - 1))

        setting(226, "Control mode", "How the nanoKONTROL 2 talks to your DAW")
        self.mode = Dropdown(self, 430, 208, 220, 36, protocol.CONTROL_MODES,
                             lambda v: app.set_global("control_mode", protocol.CONTROL_MODES.index(v)))

        setting(312, "LED mode", "Internal: the device lights its LEDs  ·  External: your DAW does")
        self.led_modes = options(LED_MODES)
        self.led = Segmented(self, 430, 294, 240, 36, self.led_modes,
                             lambda v: app.set_global("led_mode", self.led_modes.index(v)), size=13)

        self.hline(28, 382, PAGE_W - 56)
        self.text(28, 416, tr("Custom DAW assign"), 15)
        self.text(28, 438, tr("Five scene slots for custom DAW assignments"), 11, theme.MUTED)
        choices = [str(v) for v in protocol.CUSTOM_DAW_VALID]
        self.daw = []
        for i, name in enumerate(protocol.CUSTOM_DAW_LABELS):
            x = 28 + i * 150
            self.caption(x, 480, tr(name).upper())
            self.daw.append(Dropdown(self, x, 494, 128, 36, choices,
                                     lambda v, slot=i: app.set_custom_daw(slot, int(v))))

    def refresh(self, scene, conflicts):
        self.channel.set(str(scene.global_channel + 1))
        self.mode.set(protocol.CONTROL_MODES[scene.control_mode])
        self.led.set(self.led_modes[scene.led_mode])
        for slot, dropdown in enumerate(self.daw):
            dropdown.set(str(scene.custom_daw[slot]))


# --------------------------------------------------------------------------- Channel / Transport

class ControlRow:
    """One control: type, CC/Note number, Min/Max (or Off/On) and a behaviour or range bar."""

    def __init__(self, page, y, h, label, analog, change):
        self.app = page.app
        self.change = change
        self.analog = analog
        self.ctrl = None
        self.learning = False
        page.rrect(ROW_X, y, ROW_W, h, 10, fill=theme.ROW, bg=theme.PANEL)
        page.backdrop = theme.ROW
        cy = y + h / 2
        top = cy - CONTROL_H / 2
        self.dot = Dot(page, COL_DOT, cy)
        page.text(COL_LABEL, cy, tr(label), 14)
        self.assign_opts = options(protocol.ASSIGN_OPTS)
        self.behaviour_opts = options(protocol.BEHAVIOUR_OPTS)
        self.assign = Dropdown(page, COL_ASSIGN, top, 106, CONTROL_H, self.assign_opts, self._on_assign)
        self.cmd = Spin(page, COL_CMD, top, 106, CONTROL_H, 0, 127, lambda v: change(cmd=v))
        self.learn = Button(page, COL_LEARN, top, 74, CONTROL_H, tr("Learn"), self._on_learn, size=12)
        self.lo = Spin(page, COL_LO, top, 104, CONTROL_H, 0, 127, lambda v: change(min_val=v))
        self.hi = Spin(page, COL_HI, top, 104, CONTROL_H, 0, 127, lambda v: change(max_val=v))
        if analog:
            self.bar = RangeBar(page, COL_RIGHT, top, 142, CONTROL_H, change, self._reset)
            self.invert = Button(page, COL_RIGHT + 150, top, 36, CONTROL_H, "Inv", self._invert, size=11)
            self.tail = (self.bar, self.invert)
        else:
            self.behaviour = Segmented(page, COL_RIGHT, top, 186, CONTROL_H, self.behaviour_opts,
                                       self._on_behaviour)
            self.tail = (self.behaviour,)
        page.backdrop = theme.PANEL

    def show(self, ctrl, conflict):
        self.ctrl = ctrl
        self.assign.set(self.assign_opts[ctrl.assign])
        self.cmd.set(ctrl.cmd)
        self.lo.set(ctrl.min_val)
        self.hi.set(ctrl.max_val)
        self.cmd.set_warn(conflict)
        if self.analog:
            self.bar.set(ctrl.min_val, ctrl.max_val)
        else:
            self.behaviour.set(self.behaviour_opts[ctrl.behaviour])
        enabled = ctrl.assign != 0
        for widget in (self.cmd, self.lo, self.hi, *self.tail):
            widget.set_enabled(enabled)

    def set_learning(self, active):
        self.learning = active
        self.learn.set_text(tr("Cancel") if active else tr("Learn"))
        self.learn.set_style("danger" if active else "default")

    def apply_learned(self, kind, number):
        self.change(assign=1 if kind == "cc" else 2, cmd=number)

    def _on_assign(self, value):
        self.change(assign=self.assign_opts.index(value))

    def _on_behaviour(self, value):
        self.change(behaviour=self.behaviour_opts.index(value))

    def _on_learn(self):
        if self.learning:
            self.app.cancel_learn()
        else:
            self.app.start_learn(self)

    def _invert(self):
        if self.ctrl is not None:
            self.change(min_val=self.ctrl.max_val, max_val=self.ctrl.min_val)

    def _reset(self):
        self.change(min_val=0, max_val=127)


class ControlPage(Page):
    """Layout shared by the Channel and Transport pages."""

    key = None

    def _channel_selector(self):
        self.text(PAGE_W - 28 - 120 - 12, 36, tr("MIDI channel"), 12, theme.MUTED, anchor="e")
        self.channel = Dropdown(self, PAGE_W - 28 - 120, 18, 120, 36, CHANNEL_CHOICES,
                                lambda v: self.app.set_channel(self.key, _channel_value(v)))

    def _column_titles(self, y):
        for x, title in ((COL_LABEL, "CONTROL"), (COL_ASSIGN, "TYPE"), (COL_CMD, "CC / NOTE"),
                         (COL_LO, "MIN  /  OFF"), (COL_HI, "MAX  /  ON"), (COL_RIGHT, "RANGE  /  BEHAVIOUR")):
            self.caption(x, y, tr(title))

    def _rows(self, names, labels, y0, step, height):
        self.rows = {}
        for i, name in enumerate(names):
            self.rows[name] = ControlRow(
                self, y0 + i * step, height, labels[name], name in edit.ANALOG_CONTROLS,
                lambda name=name, **kw: self.app.set_ctrl(self.key, name, **kw))

    def refresh(self, scene, conflicts):
        group = edit.group_of(scene, self.key)
        self.channel.set(_channel_text(group.channel))
        for name, row in self.rows.items():
            row.show(group.get_ctrl(name), (self.key, name) in conflicts)


class ChannelPage(ControlPage):
    def __init__(self, app):
        super().__init__(app)
        self.all_others = tr("All other channels")
        self.key = 0
        self.heading("", "")
        self._channel_selector()
        self._column_titles(106)
        self._rows(protocol.CHANNEL_CONTROLS, protocol.CHANNEL_CTRL_LABELS, 124, 72, 62)

        self.hline(28, 508, PAGE_W - 56)
        self.text(28, 540, tr("Copy this strip to other strips"), 14)
        self.text(28, 562, tr("Copies assignments and ranges; CC / Note numbers shift with the strip so they stay unique"),
                  11, theme.MUTED)
        self.copy = Dropdown(self, PAGE_W - 28 - 200, 524, 200, 36, [], self._copy, caption=tr("Copy to…"))
        self._draw_strip()

    def show_strip(self, g):
        """Show strip `g`; the next sync() redraws the rows."""
        if g != self.key:
            self.key = g
            self.version = -1
            self._draw_strip()

    def _draw_strip(self):
        g = self.key
        self.itemconfigure(self._title, text=tr("Channel {n}", n=g + 1))
        self.itemconfigure(self._subtitle, text=tr("Assignments for strip {n}", n=g + 1))
        self.copy.set_values([tr("Channel {n}", n=i + 1) for i in range(protocol.NUM_GROUPS) if i != g]
                             + [self.all_others])

    def _copy(self, label):
        if label == self.all_others:
            targets = [i for i in range(protocol.NUM_GROUPS) if i != self.key]
        else:
            targets = [int(label.split()[1]) - 1]
        self.app.copy_group(self.key, targets)


class TransportPage(ControlPage):
    key = "transport"

    def __init__(self, app):
        super().__init__(app)
        self.heading(tr("Transport"), tr("Play, record and marker buttons"))
        self._channel_selector()
        self._column_titles(100)
        self._rows(protocol.TRANSPORT_CONTROLS, protocol.TRANSPORT_LABELS, 116, 44, 40)


# --------------------------------------------------------------------------- Calibration

class CalibrationRow:
    WIDTH = 418

    def __init__(self, page, x, y, g, name, change):
        self.change = change
        self.ctrl = None
        page.rrect(x, y, self.WIDTH, 50, 10, fill=theme.ROW, bg=theme.PANEL)
        page.backdrop = theme.ROW
        cy = y + 25
        top = cy - CONTROL_H / 2
        page.text(x + 12, cy, tr("Fader {n}" if name == "slider" else "Knob {n}", n=g + 1), 13)
        self.bar = RangeBar(page, x + 72, top, 140, CONTROL_H, change, self._reset)
        self.readout = page.text(x + 226, cy, "", 12, theme.OK, anchor="center")
        self.lo = Spin(page, x + 242, top, 64, CONTROL_H, 0, 127, lambda v: change(min_val=v), size=12, button=17)
        self.hi = Spin(page, x + 310, top, 64, CONTROL_H, 0, 127, lambda v: change(max_val=v), size=12, button=17)
        self.invert = Button(page, x + 378, top, 34, CONTROL_H, "Inv", self._invert, size=11)
        page.backdrop = theme.PANEL
        self._page = page
        self._live = None

    def show(self, ctrl):
        self.ctrl = ctrl
        self.bar.set(ctrl.min_val, ctrl.max_val)
        self.lo.set(ctrl.min_val)
        self.hi.set(ctrl.max_val)
        enabled = ctrl.assign != 0
        for widget in (self.bar, self.lo, self.hi, self.invert):
            widget.set_enabled(enabled)

    def set_live(self, value):
        if value != self._live:
            self._live = value
            self.bar.set_live(value)
            self._page.itemconfigure(self.readout, text="" if value is None else str(value))

    def set_seen(self, seen):
        if seen is None:
            self.bar.set_seen(None, None)
        else:
            self.bar.set_seen(*seen)

    def _invert(self):
        if self.ctrl is not None:
            self.change(min_val=self.ctrl.max_val, max_val=self.ctrl.min_val)

    def _reset(self):
        self.change(min_val=0, max_val=127)


class CalibrationPage(Page):
    COLUMN_X = (28, 470)

    def __init__(self, app):
        super().__init__(app)
        self.heading(tr("Calibration"), "")
        self.text(28, 62, tr("Min is sent at the lowest position of a fader or knob and Max at the highest; "
                             "swap them to invert."), 12, theme.MUTED)
        self.text(28, 82, tr("Drag the handles or type a value. Double-click a bar to reset it. "
                             "Move a control to see its live position."), 12, theme.MUTED)
        Button(self, PAGE_W - 28 - 150, 18, 150, 34, tr("Reset all ranges"), app.reset_ranges, size=12)
        self.rows = {}
        for column, (name, title) in zip(self.COLUMN_X, (("slider", "FADERS"), ("knob", "KNOBS"))):
            self.caption(column + 12, 116, tr(title))
            self.caption(column + 72, 116, tr("RANGE"))
            self.caption(column + 242, 116, tr("MIN"))
            self.caption(column + 310, 116, tr("MAX"))
            for g in range(protocol.NUM_GROUPS):
                self.rows[(g, name)] = CalibrationRow(
                    self, column, 134 + g * 58, g, name,
                    lambda g=g, name=name, **kw: app.set_ctrl(g, name, **kw))

    def refresh(self, scene, conflicts):
        for (g, name), row in self.rows.items():
            row.show(scene.groups[g].get_ctrl(name))

    def activate(self):
        for key, row in self.rows.items():
            row.set_live(self.app.live.get(key))
            row.set_seen(self.app.seen.get(key))

    def update_control(self, key):
        row = self.rows.get(key)
        if row is not None:
            row.set_live(self.app.live.get(key))
            row.set_seen(self.app.seen.get(key))


# --------------------------------------------------------------------------- LED test

class LedPage(Page):
    def __init__(self, app):
        super().__init__(app)
        self.heading(tr("LED Test"), tr("Light the button LEDs from here (not stored in the scene)"))
        self.rrect(28, 84, PAGE_W - 56, 38, 10, fill=theme.ROW, bg=theme.PANEL)
        self._banner = self.text(46, 103, "", 12, theme.WARN)
        self._banner_text = ""

        self.caption(28, 164, tr("CHANNEL BUTTONS"))
        left = 104
        for i in range(protocol.NUM_GROUPS):
            self.text(left + i * 96 + 44, 192, str(i + 1), 12, theme.MUTED, anchor="center", weight="bold")
        self.buttons = {}
        for row, (kind, label) in enumerate((("solo", "Solo"), ("mute", "Mute"), ("prime", "Arm"))):
            y = 210 + row * 52
            self.text(28, y + 19, tr(label), 14)
            for i in range(protocol.NUM_GROUPS):
                key = f"{kind}_{i + 1}"
                self.buttons[key] = Button(self, left + i * 96, y, 88, 38, tr("Off"),
                                           lambda key=key: app.toggle_led(key), size=12)

        self.caption(28, 394, tr("TRANSPORT BUTTONS"))
        for i, name in enumerate(protocol.LED_TRANSPORT):
            x, y = 28 + i * 96, 416
            self.buttons[name] = Button(self, x, y, 88, 38, tr(protocol.TRANSPORT_LABELS[name]),
                                        lambda name=name: app.toggle_led(name), size=11)
        Button(self, 28, 476, 120, 38, tr("All off"), lambda: app.set_all_leds(False), size=12)
        Button(self, 156, 476, 120, 38, tr("All on"), lambda: app.set_all_leds(True), size=12)

    def set_led(self, key, on):
        button = self.buttons[key]
        button.set_style("ok" if on else "default")
        if "_" in key and key.split("_")[0] in ("solo", "mute", "prime"):
            button.set_text(tr("On") if on else tr("Off"))

    def refresh(self, scene, conflicts):
        external = scene.led_mode == 1
        text = (tr("External LED mode is on: LED commands are accepted.") if external else
                tr("External LED mode is off. Switch it on in Global > LED mode, or with the Ext LEDs button."))
        if text != self._banner_text:
            self._banner_text = text
            self.itemconfigure(self._banner, text=text, fill=theme.OK if external else theme.WARN)


# --------------------------------------------------------------------------- MIDI monitor

class MonitorPage(Page):
    MAX_LINES = 400
    KINDS = {0x80: "Note Off", 0x90: "Note On", 0xA0: "Aftertouch", 0xB0: "CC", 0xC0: "Program",
             0xD0: "Pressure", 0xE0: "Pitch"}

    def __init__(self, app):
        super().__init__(app)
        self.heading(tr("MIDI Monitor"), tr("Messages sent by the device, newest first"))
        Button(self, PAGE_W - 28 - 90, 18, 90, 34, tr("Clear"), app.clear_monitor, size=12)
        header = f"{tr('time'):>9}  {tr('ch'):>3}  {tr('type'):<10}{tr('num'):>4}{tr('val'):>5}   {tr('raw')}"
        self.text(28 + 12, 104, header, 12, theme.MUTED, mono=True)
        self.log = tk.Text(self, bg=theme.FIELD, fg=theme.TEXT, bd=0, highlightthickness=1,
                           highlightbackground=theme.EDGE, relief="flat", wrap="none",
                           padx=u(12), pady=u(8), font=theme.mono(12), cursor="arrow",
                           selectbackground=theme.ACCENT_DARK, state="disabled")
        self.log.tag_configure("cc", foreground=theme.ACCENT_HI)
        self.log.tag_configure("note", foreground=theme.OK)
        self.log.tag_configure("other", foreground=theme.MUTED)
        self.create_window(u(28), u(122), window=self.log, anchor="nw",
                           width=u(PAGE_W - 56), height=u(PAGE_H - 122 - 28))

    def activate(self):
        self.reset(self.app.monitor_start, self.app.monitor_log)

    def format(self, start, message):
        stamp, status, d1, d2 = message
        kind = self.KINDS.get(status & 0xF0, f"0x{status & 0xF0:02X}")
        tag = "cc" if status & 0xF0 == 0xB0 else "note" if status & 0xF0 in (0x80, 0x90) else "other"
        line = f"{stamp - start:9.3f}  {(status & 0x0F) + 1:>3}  {kind:<10}{d1:>4}{d2:>5}   {status:02X} {d1:02X} {d2:02X}\n"
        return line, tag

    def append_messages(self, start, messages):
        self.log.configure(state="normal")
        for message in messages:
            line, tag = self.format(start, message)
            self.log.insert("1.0", line, tag)
        self.log.delete(f"{self.MAX_LINES + 1}.0", "end")
        self.log.configure(state="disabled")

    def reset(self, start, messages):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.append_messages(start, messages)


# --------------------------------------------------------------------------- Sequencer

class SequencerPage(Page):
    STEP_X, STEP_W, ROW_Y, ROW_PITCH, ROW_H = 152, 38, 230, 40, 34

    def __init__(self, app):
        super().__init__(app)
        self.heading(tr("Sequencer"), tr("8 tracks × 16 steps, played from the nanoKONTROL 2"))
        self.power = Button(self, PAGE_W - 28 - 190, 18, 190, 34, tr("Start sequencer"), app.toggle_sequencer,
                            style="accent", size=12, weight="bold")

        self.text(28, 106, tr("Notes to"), 12, theme.MUTED)
        self.out = Dropdown(self, 110, 88, 250, 36, [], app.set_seq_output, size=12)
        self.text(392, 106, tr("Clock from"), 12, theme.MUTED)
        self.clock = Dropdown(self, 482, 88, 250, 36, [], app.set_seq_clock, size=12)
        self._swing = self.text(760, 106, "", 12, theme.MUTED)
        self._status = self.text(28, 150, "", 13, theme.MUTED)
        self.text(PAGE_W - 28 - 120 - 12, 150, tr("Tempo (BPM)"), 12, theme.MUTED, anchor="e")
        self.tempo = Spin(self, PAGE_W - 28 - 120, 134, 120, 32, sequencer.MIN_TEMPO, sequencer.MAX_TEMPO,
                          app.seq_tempo, size=12)
        self.text(28, 176, tr("Internal clock: plays on its own, with Play / Stop on the nanoKONTROL 2. To follow a DAW, "
                              "receive its MIDI clock through a loopMIDI port."), 11, theme.DIM)

        self.numbers = []
        for s in range(sequencer.STEPS):
            x = self._step_x(s) + self.STEP_W / 2
            self.numbers.append(self.text(x, 212, str(s + 1), 11, theme.MUTED, anchor="center", weight="bold"))
        self.tracks, self.solos, self.cells = [], [], []
        for t in range(sequencer.TRACKS):
            y = self.ROW_Y + t * self.ROW_PITCH
            self.tracks.append(Button(self, 28, y, 74, self.ROW_H, "", lambda t=t: app.seq_select(t),
                                      style="nav", align="left", size=12, weight="bold"))
            self.solos.append(Button(self, 108, y, 34, self.ROW_H, "S", lambda t=t: app.seq_solo(t),
                                     style="cell", size=11, weight="bold"))
            row = [Button(self, self._step_x(s), y, self.STEP_W, self.ROW_H, "",
                          lambda t=t, s=s: app.seq_toggle(t, s), style="cell", size=10)
                   for s in range(sequencer.STEPS)]
            self.cells.append(row)
        self.text(28, 564, tr("On the device: Mute = steps 1-8  ·  Rec = steps 9-16  ·  Solo = solo  ·  "
                              "Track ◀ ▶ = track  ·  Marker ◀ ▶ = note  ·  Knobs = velocity of steps N and N+8"),
                  11, theme.MUTED)
        self.text(28, 584, tr("Set + Knob 1 = swing  ·  Set + Knob 2 = swing 1/16 or 1/8  ·  Set + Knob 3 = tempo  ·  "
                              "Faders → output"), 11, theme.MUTED)
        self._shown = None

    def _step_x(self, step):
        return self.STEP_X + step * 44 + (step // 4) * 8

    def activate(self):
        self.app.refresh_seq_ports()
        self._shown = None
        self.app.update_sequencer_page()

    def set_ports(self, outs, ins, out, clock):
        self.out.set_values(outs)
        self.clock.set_values(ins)
        self.out.set(out or tr("Choose a port"))
        self.clock.set(tr("Internal clock") if clock == sequencer.INTERNAL else clock or tr("Choose a port"))

    def show(self, seq, on, status, status_color):
        self.power.set_text(tr("Stop sequencer") if on else tr("Start sequencer"))
        self.power.set_style("danger" if on else "accent")
        self.out.set_enabled(not on)
        self.clock.set_enabled(not on)
        self.itemconfigure(self._status, text=status, fill=status_color)
        state = (seq.version, seq.position)
        if state == self._shown:
            return
        self._shown = state
        self.tempo.set(seq.tempo)
        self.itemconfigure(self._swing, text=tr("Swing {resolution}: {percent} %",
                                                resolution="1/8" if seq.swing_eighths else "1/16",
                                                percent=sequencer.swing_percent(seq.swing)))
        for t in range(sequencer.TRACKS):
            self.tracks[t].set_text(f"{t + 1}   {sequencer.note_name(seq.notes[t])}")
            self.tracks[t].set_style("nav_on" if t == seq.selected else "nav")
            self.solos[t].set_style("ok" if seq.solo[t] else "cell")
            for s, cell in enumerate(self.cells[t]):
                cell.set_style("cell_on" if seq.steps[t][s] else "cell")
                cell.set_text(str(seq.velocity[t][s]) if seq.steps[t][s] else "")
        for s, item in enumerate(self.numbers):
            self.itemconfigure(item, fill=theme.OK if s == seq.position else theme.MUTED)
