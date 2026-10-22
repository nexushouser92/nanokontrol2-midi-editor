"""Step sequencer played from the nanoKONTROL 2 and clocked by the DAW.

8 tracks x 16 steps. On the device:
  Mute 1-8 / Rec 1-8   steps 1-8 / 9-16 of the selected track (LEDs show the pattern and the playhead)
  Solo 1-8             solo of tracks 1-8
  Track < >            select the previous / next track
  Knob 1-8             note of tracks 1-8
  Fader 1-8            passed through to the DAW unchanged
  Play / Stop / Rewind passed through to the DAW (map them there); Rewind also goes back to step 1

Track t plays on MIDI channel t. The tempo comes from the DAW's MIDI clock (24 per quarter note,
so 6 per 16th-note step); Start, Continue, Stop and Song Position Pointer are followed.

`Sequencer` is the pure logic: every method returns a list of actions,
  ("out", status, data1, data2)   to the DAW
  ("led", key, on)                to a button LED of the device (protocol.LED_BUTTONS key)
`SequencerRunner` connects it to the MIDI ports on its own thread.
"""
import json
import os
import queue
import threading
import time

from . import edit, midi_win, protocol

TRACKS = 8
STEPS = 16
TICKS_PER_STEP = 6
GATE_TICKS = 3
VELOCITY = 100
DEFAULT_NOTE = 60

CLOCK, START, CONTINUE, STOP, SONG_POSITION = 0xF8, 0xFA, 0xFB, 0xFC, 0xF2

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(note):
    """MIDI note number as a name, middle C (60) = C4."""
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 1}"


def step_key(step):
    """LED button key of a step: Mute 1-8 for steps 0-7, Rec (prime) 1-8 for steps 8-15."""
    return f"mute_{step + 1}" if step < 8 else f"prime_{step - 7}"


def sequencer_scene(scene):
    """The scene the device runs while sequencing: the user's faders and knobs, factory-default
    momentary buttons (so presses are easy to tell from releases), CC mode and External LEDs."""
    seq = scene.copy()
    seq.control_mode = 0
    seq.led_mode = 1
    factory = protocol.Scene()
    for g in range(protocol.NUM_GROUPS):
        seq.groups[g].channel = 16
        for name in ("solo", "mute", "prime"):
            setattr(seq.groups[g], name, factory.groups[g].get_ctrl(name).copy())
    seq.transport.channel = 16
    for name in protocol.TRANSPORT_CONTROLS:
        setattr(seq.transport, name, factory.transport.get_ctrl(name).copy())
    return seq


def control_map(scene):
    """(status, data1) -> (group key, control name) for every enabled control of `scene`."""
    roles = {}
    for (channel, assign, number), targets in edit.message_targets(scene).items():
        status = (0xB0 if assign == 1 else 0x90) | channel
        roles.setdefault((status, number), targets[0])
    return roles


class Sequencer:
    def __init__(self, scene):
        self.steps = [[False] * STEPS for _ in range(TRACKS)]
        self.notes = [DEFAULT_NOTE] * TRACKS
        self.solo = [False] * TRACKS
        self.selected = 0
        self.playing = False
        self.position = -1          # step being played, -1 when stopped
        self._ticks = -1
        self._sounding = []         # (channel, note) waiting for their note-off
        self.version = 0            # bumped on every change the window should redraw
        self.set_scene(scene)

    # ------------------------------------------------------------------ configuration

    def set_scene(self, scene):
        """Use `scene` (the one the device runs) to tell which control a message comes from."""
        self.scene = scene
        self._roles = control_map(scene)
        self._max = {target: edit.group_of(scene, target[0]).get_ctrl(target[1]).max_val
                     for target in self._roles.values()}

    def to_dict(self):
        return {"steps": [[int(s) for s in row] for row in self.steps], "notes": list(self.notes),
                "solo": [int(s) for s in self.solo], "selected": self.selected}

    def load_dict(self, data):
        try:
            steps = [[bool(s) for s in row][:STEPS] for row in data["steps"]][:TRACKS]
            notes = [min(127, max(0, int(n))) for n in data["notes"]][:TRACKS]
            solo = [bool(s) for s in data.get("solo", [])][:TRACKS]
            selected = int(data.get("selected", 0))
        except (KeyError, TypeError, ValueError):
            return
        for t, row in enumerate(steps):
            self.steps[t][:len(row)] = row
        self.notes[:len(notes)] = notes
        self.solo[:len(solo)] = solo
        self.selected = min(TRACKS - 1, max(0, selected))
        self.version += 1

    # ------------------------------------------------------------------ LEDs

    def _step_led(self, step):
        lit = self.steps[self.selected][step]
        if step == self.position:
            lit = not lit
        return ("led", step_key(step), lit)

    def all_leds(self):
        """Every LED as it should be now."""
        actions = [self._step_led(s) for s in range(STEPS)]
        actions += [("led", f"solo_{t + 1}", self.solo[t]) for t in range(TRACKS)]
        actions.append(("led", "play", self.playing))
        for key in ("cycle", "rew", "ff", "stop", "rec"):
            actions.append(("led", key, False))
        return actions

    @staticmethod
    def leds_off():
        return [("led", key, False) for key in protocol.LED_BUTTONS]

    # ------------------------------------------------------------------ editing (window or device)

    def toggle_step(self, track, step):
        self.steps[track][step] = not self.steps[track][step]
        self.version += 1
        return [self._step_led(step)] if track == self.selected else []

    def select(self, track):
        self.selected = track % TRACKS
        self.version += 1
        return [self._step_led(s) for s in range(STEPS)]

    def toggle_solo(self, track):
        self.solo[track] = not self.solo[track]
        self.version += 1
        return [("led", f"solo_{track + 1}", self.solo[track])]

    def set_note(self, track, note):
        if self.notes[track] != note:
            self.notes[track] = note
            self.version += 1
        return []

    # ------------------------------------------------------------------ device input

    def device_message(self, status, data1, data2):
        """A short message from the nanoKONTROL 2."""
        target = self._roles.get((status, data1))
        if target is None:
            return []
        key, name = target
        if name == "slider":
            return [("out", status, data1, data2)]
        if name == "knob":
            return self.set_note(key, data2)
        pressed = data2 == self._max.get(target, 127) and data2 > 0
        if key == "transport":
            if name in ("play", "stop", "rew"):
                actions = [("out", status, data1, data2)]
                if pressed and name == "rew":
                    actions += self.rewind()
                return actions
            if pressed and name == "prev_track":
                return self.select(self.selected - 1)
            if pressed and name == "next_track":
                return self.select(self.selected + 1)
            return []
        if not pressed:
            return []
        if name == "solo":
            return self.toggle_solo(key)
        if name == "mute":
            return self.toggle_step(self.selected, key)
        if name == "prime":
            return self.toggle_step(self.selected, key + 8)
        return []

    # ------------------------------------------------------------------ clock input

    def clock_message(self, status, data1=0, data2=0):
        """A MIDI realtime / song position message from the DAW."""
        if status == CLOCK:
            return self._tick()
        if status == START:
            actions = self._release()
            self._ticks = -1
            self.playing = True
            self.version += 1
            return actions + [("led", "play", True)]
        if status == CONTINUE:
            self.playing = True
            self.version += 1
            return [("led", "play", True)]
        if status == STOP:
            actions = self._release()
            self.playing = False
            old, self.position = self.position, -1
            self.version += 1
            if old >= 0:
                actions.append(self._step_led(old))
            return actions + [("led", "play", False)]
        if status == SONG_POSITION:
            sixteenths = data1 | (data2 << 7)
            self._ticks = sixteenths * TICKS_PER_STEP - 1
            return []
        return []

    def rewind(self):
        if self.playing:
            return []
        self._ticks = -1
        old, self.position = self.position, -1
        self.version += 1
        return [self._step_led(old)] if old >= 0 else []

    def _audible(self, track):
        return self.solo[track] or not any(self.solo)

    def _tick(self):
        if not self.playing:
            return []
        self._ticks += 1
        phase = self._ticks % TICKS_PER_STEP
        actions = []
        if phase == GATE_TICKS:
            actions += self._release()
        if phase != 0:
            return actions
        actions += self._release()
        old, self.position = self.position, (self._ticks // TICKS_PER_STEP) % STEPS
        self.version += 1
        for track in range(TRACKS):
            if self.steps[track][self.position] and self._audible(track):
                note = self.notes[track]
                actions.append(("out", 0x90 | track, note, VELOCITY))
                self._sounding.append((track, note))
        if old >= 0 and old != self.position:
            actions.append(self._step_led(old))
        actions.append(self._step_led(self.position))
        return actions

    def _release(self):
        actions = [("out", 0x80 | channel, note, 0) for channel, note in self._sounding]
        self._sounding.clear()
        return actions

    def all_notes_off(self):
        return self._release()


# --------------------------------------------------------------------------- ports and thread

def settings_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "MIDI Editor nanoKONTROL 2", "sequencer.json")


class SequencerRunner:
    """Runs a Sequencer: DAW clock in, notes and faders out, device messages in, LEDs out.

    MIDI driver callbacks only queue messages; one worker thread does all the sending."""

    def __init__(self, sequencer, led_message, send_led, opener=midi_win.WinMidiPort,
                 lister=midi_win.list_ports, clock=time.monotonic):
        self.seq = sequencer
        self.led_message = led_message      # (key, on) -> (status, data1, data2) or None
        self.send_led = send_led            # (status, data1, data2) -> bool
        self.opener = opener
        self.lister = lister
        self.clock = clock
        self._queue = queue.SimpleQueue()
        self._out = self._in = None
        self._thread = None
        self._lock = threading.Lock()
        self.last_clock = None
        self.bpm = None
        self._clock_times = []

    @property
    def running(self):
        return self._thread is not None

    def start(self, out_name, clock_name):
        """Open the ports by name and start. Raises midi_win.MidiError if one cannot be opened."""
        ins, outs = self.lister()
        if out_name not in outs:
            raise midi_win.MidiError(f"MIDI output not found: {out_name}")
        if clock_name not in ins:
            raise midi_win.MidiError(f"MIDI input not found: {clock_name}")
        self._out = self.opener(None, outs.index(out_name))
        try:
            self._in = self.opener(ins.index(clock_name), None)
        except BaseException:
            self._out.close()
            self._out = None
            raise
        self._in.on_short = lambda s, d1, d2: self._queue.put(("clock", s, d1, d2))
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.post(self.seq.all_leds)

    def stop(self):
        if self._thread is None:
            return
        self._queue.put(("quit",))
        self._thread.join(2.0)
        self._thread = None
        for port in (self._in, self._out):
            if port is not None:
                port.close()
        self._in = self._out = None
        with self._lock:
            self._apply(self.seq.leds_off())

    def device_message(self, status, data1, data2):
        """From the device link's MIDI callback."""
        self._queue.put(("device", status, data1, data2))

    def post(self, call, *args):
        """Run a Sequencer method on the worker thread and perform the actions it returns."""
        if self._thread is None:
            call(*args)     # not running: change the pattern only, the device is not in sequencer mode
        else:
            self._queue.put(("call", call, args))

    def _run(self):
        while True:
            item = self._queue.get()
            kind = item[0]
            if kind == "quit":
                with self._lock:
                    self._apply(self.seq.all_notes_off())
                return
            with self._lock:
                if kind == "clock":
                    _, status, d1, d2 = item
                    if status == CLOCK:
                        self._measure()
                    self._apply(self.seq.clock_message(status, d1, d2))
                elif kind == "device":
                    self._apply(self.seq.device_message(*item[1:]))
                elif kind == "call":
                    self._apply(item[1](*item[2]))

    def _measure(self):
        now = self.clock()
        self.last_clock = now
        times = self._clock_times
        times.append(now)
        if len(times) > 48:
            del times[0]
        if len(times) >= 12 and times[-1] > times[0]:
            self.bpm = 60.0 / ((times[-1] - times[0]) / (len(times) - 1) * 24)

    def _apply(self, actions):
        for action in actions:
            if action[0] == "out":
                if self._out is not None:
                    try:
                        self._out.send_short(*action[1:])
                    except midi_win.MidiError:
                        pass
            else:
                message = self.led_message(action[1], action[2])
                if message is not None:
                    self.send_led(message)


def load_settings():
    try:
        with open(settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(data):
    path = settings_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass
