"""A KontrolLink stand-in so the interface can run without hardware."""
import queue


class FakeLink:
    def __init__(self, linked=True):
        self.events = queue.Queue()
        self.incoming = queue.SimpleQueue()
        self.linked = linked
        self.has_pending = False
        self.pushed = []
        self.leds = []
        self.learning = False
        self.read_requests = 0
        self.saved = []
        self.listener = None

    def start(self):
        pass

    def stop(self):
        pass

    def push(self, scene, save=True):
        self.pushed.append(scene)
        self.saved.append(save)
        self.has_pending = True

    def flush(self):
        pass

    def request_read(self):
        self.read_requests += 1

    def set_led(self, message):
        self.leds.append(message)
        return True

    def start_learn(self):
        self.learning = True

    def cancel_learn(self):
        self.learning = False


class FakeRunner:
    """A SequencerRunner stand-in: no ports, calls run at once and their actions are recorded."""

    def __init__(self):
        self.started = None
        self.actions = []
        self.device = []
        self.bpm = None
        self.last_clock = None
        self.fail = None

    @property
    def running(self):
        return self.started is not None

    def start(self, out_name, clock_name):
        if self.fail:
            raise self.fail
        self.started = (out_name, clock_name)

    def stop(self):
        self.started = None

    def device_message(self, status, data1, data2):
        self.device.append((status, data1, data2))

    def post(self, call, *args):
        self.actions.extend(call(*args))
