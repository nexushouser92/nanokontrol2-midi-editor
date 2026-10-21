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

    def start(self):
        pass

    def stop(self):
        pass

    def push(self, scene):
        self.pushed.append(scene)
        self.has_pending = True

    def flush(self):
        pass

    def request_read(self):
        self.read_requests += 1

    def set_led(self, cc, on):
        self.leds.append((cc, on))

    def start_learn(self):
        self.learning = True

    def cancel_learn(self):
        self.learning = False
