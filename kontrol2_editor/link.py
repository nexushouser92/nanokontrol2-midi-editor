"""Keeps the nanoKONTROL 2 linked while the editor is open.

- Polls every POLL_INTERVAL seconds for the device and opens it when found.
- On linking, reads the full scene.
- push(scene) schedules a write WRITE_DELAY seconds after the last edit.
- If the device disappears, closes the port and resumes polling.

All device I/O runs in a worker thread. The window reads from `events`:
  ("searching", error_or_None)
  ("linked", None)
  ("settings", scene)
  ("writing", None)
  ("written", scene)
  ("write_failed", error)
  ("learned", (kind, number))     kind is "cc" or "note"

Short MIDI messages from the device arrive on `incoming` as (timestamp, status, data1, data2).
"""
import queue
import threading
import time

from . import device, midi_win
from .protocol import LED_STATUS

POLL_INTERVAL = 1.0
WRITE_DELAY = 0.4


class KontrolLink:
    def __init__(self, opener=midi_win.open_kontrol2, lister=midi_win.list_ports,
                 clock=time.monotonic, write_delay=WRITE_DELAY):
        self.opener = opener
        self.lister = lister
        self.clock = clock
        self.write_delay = write_delay
        self.events = queue.Queue()
        self.port = None
        self.on_device = None   # scene the device currently has (last read or written)
        self.incoming = queue.SimpleQueue()
        self._learning = False
        self._channel = 0
        self._lock = threading.Lock()
        self._pending = None
        self._due = 0.0
        self._want_read = False
        self._next_poll = 0.0
        self._stop = threading.Event()
        self._thread = None

    # --------------------------------------------------------- called by the window

    @property
    def linked(self):
        return self.port is not None

    @property
    def has_pending(self):
        return self._pending is not None

    def push(self, scene):
        with self._lock:
            self._pending = scene
            self._due = self.clock() + self.write_delay

    def flush(self):
        with self._lock:
            self._due = 0.0

    def request_read(self):
        self._want_read = True

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, timeout=3.0):
        self.flush()
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        if self.linked and self._pending is not None:
            self.step()
        self._close()

    # --------------------------------------------------------- worker

    def _run(self):
        while not self._stop.is_set():
            self.step()
            self._stop.wait(0.05)

    def step(self):
        now = self.clock()
        if self.port is None:
            if now >= self._next_poll:
                self._next_poll = now + POLL_INTERVAL
                self._try_link()
            return
        if now >= self._next_poll:
            self._next_poll = now + POLL_INTERVAL
            if not self._present():
                self._drop(None)
                return
        if self._want_read:
            self._want_read = False
            if not self._read():
                return
        with self._lock:
            settings = self._pending if self._pending is not None and now >= self._due else None
        if settings is not None:
            self._write(settings)

    def _present(self):
        try:
            ins, outs = self.lister()
        except OSError:
            return False
        return midi_win.find_port(ins) is not None and midi_win.find_port(outs) is not None

    def set_led(self, cc, on):
        """Send a CC on channel 16 to control a button LED (External LED mode)."""
        if self.port is not None:
            try:
                self.port.send_short(LED_STATUS, cc & 0x7F, 0x7F if on else 0x00)
            except Exception:
                pass

    def start_learn(self):
        """The next CC or Note On from the device is reported once as a "learned" event."""
        self._learning = True

    def cancel_learn(self):
        self._learning = False

    def _on_short_all(self, status, data1, data2):
        """Runs on the MIDI driver thread: only touches thread-safe queues."""
        self.incoming.put((time.monotonic(), status, data1, data2))
        if not self._learning:
            return
        kind = status & 0xF0
        if kind == 0xB0:
            self._learning = False
            self.events.put(("learned", ("cc", data1)))
        elif kind == 0x90 and data2 > 0:
            self._learning = False
            self.events.put(("learned", ("note", data1)))

    def _try_link(self):
        if not self._present():
            return
        try:
            port = self.opener()
        except (midi_win.MidiError, OSError) as e:
            self.events.put(("searching", e))
            return
        port.on_short = self._on_short_all
        self.port = port
        self.events.put(("linked", None))
        if not self._read():
            return
        if self._pending is not None:
            self.flush()

    def _read(self):
        try:
            scene, channel = device.read_scene(self.port, self._channel)
        except (device.DeviceError, midi_win.MidiError, OSError) as e:
            self._drop(e)
            return False
        self._channel = channel
        self.on_device = scene
        self.events.put(("settings", scene))
        return True

    def _write(self, scene):
        self.events.put(("writing", None))
        try:
            device.write_scene(self.port, scene)
            self.on_device = scene
        except device.WriteFailedError as e:
            self._want_read = True
            self._clear_pending(scene)
            self.events.put(("write_failed", e))
            return
        except (device.DeviceError, midi_win.MidiError, OSError) as e:
            self.events.put(("write_failed", e))
            self._drop(e)
            return
        self._clear_pending(scene)
        self.events.put(("written", self.on_device))

    def _clear_pending(self, written):
        with self._lock:
            if self._pending is written:
                self._pending = None

    def _drop(self, error):
        self._close()
        self._next_poll = self.clock() + POLL_INTERVAL
        self.events.put(("searching", error))

    def _close(self):
        port, self.port = self.port, None
        if port is not None:
            try:
                port.close()
            except (midi_win.MidiError, OSError):
                pass
