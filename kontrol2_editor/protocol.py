"""Korg nanoKONTROL 2 SysEx protocol: scene data model, message building and parsing.

Scene = one 339-byte blob sent/received as a single SysEx message.
The 8-in-7 bit packing (Korg's standard) expands 339 bytes to 388 MIDI bytes.

Send flow:
  1. send dump request  -> device replies with scene data (388 bytes)
  2. send scene data    -> device replies LoadAck
  3. send scene write   -> device replies WriteOk  (saves to flash)

Header: F0 42 (40|chan) 00 01 13 00
"""
from copy import deepcopy
from dataclasses import dataclass, field
from typing import List

MANUFACTURER = 0x42
DEVICE_ID = bytes([0x00, 0x01, 0x13, 0x00])
SYSEX_END = 0xF7

CMD_DUMP_REQUEST = bytes([0x1F, 0x10, 0x00])
CMD_SCENE_DATA   = bytes([0x7F, 0x7F, 0x02, 0x03, 0x05, 0x40])
CMD_SCENE_WRITE  = bytes([0x1F, 0x11, 0x00])

RESP_SCENE_DATA  = bytes([0x7F, 0x7F, 0x02, 0x03, 0x05, 0x40])
RESP_LOAD_ACK    = bytes([0x5F, 0x23, 0x00])
RESP_LOAD_NAK    = bytes([0x5F, 0x24, 0x00])
RESP_WRITE_OK    = bytes([0x5F, 0x21, 0x00])
RESP_WRITE_ERR   = bytes([0x5F, 0x22, 0x00])

SYSEX_DATA_LEN = 388   # packed 7-bit MIDI byte count
DATA_LEN = 339         # unpacked 8-bit internal data bytes

CONTROL_MODES = ("CC", "Cubase", "Digital Performer", "Ableton Live", "Pro Tools", "SONAR")
LED_MODES = ("Internal", "External")
ASSIGN_OPTS = ("Disabled", "CC", "Note")
BEHAVIOUR_OPTS = ("Momentary", "Toggle")

NUM_GROUPS = 8
GROUP_OFFSETS = [3, 34, 65, 96, 127, 158, 189, 220]
TRANSPORT_OFFSET = 251

CHANNEL_CONTROLS = ("slider", "knob", "solo", "mute", "prime")
CHANNEL_CTRL_OFFSETS = {"slider": 1, "knob": 7, "solo": 13, "mute": 19, "prime": 25}

TRANSPORT_CONTROLS = (
    "prev_track", "next_track", "cycle", "set_marker", "prev_marker",
    "next_marker", "rew", "ff", "stop", "play", "rec",
)
TRANSPORT_CTRL_OFFSETS = {
    "prev_track": 1, "next_track": 7, "cycle": 13, "set_marker": 19,
    "prev_marker": 25, "next_marker": 31, "rew": 37, "ff": 43,
    "stop": 49, "play": 55, "rec": 61,
}

TRANSPORT_LABELS = {
    "prev_track": "Prev Track", "next_track": "Next Track", "cycle": "Cycle",
    "set_marker": "Set Marker", "prev_marker": "Prev Marker", "next_marker": "Next Marker",
    "rew": "Rewind", "ff": "Fast Forward", "stop": "Stop", "play": "Play", "rec": "Record",
}

CHANNEL_CTRL_LABELS = {
    "slider": "Slider", "knob": "Knob",
    "solo": "Solo", "mute": "Mute", "prime": "Prime (Arm)",
}

# Factory default CC numbers for transport buttons (CC mode)
TRANSPORT_CC_DEFAULTS = {
    "play": 0x29, "stop": 0x2A, "rew": 0x2B, "ff": 0x2C, "rec": 0x2D,
    "cycle": 0x2E, "prev_track": 0x3A, "next_track": 0x3B,
    "set_marker": 0x3C, "prev_marker": 0x3D, "next_marker": 0x3E,
}


class ProtocolError(Exception):
    pass


# --- 8-in-7 bit packing ---

def _pack_7bit(data: bytes) -> bytes:
    result = bytearray()
    for offset in range(0, len(data), 7):
        block = data[offset:offset + 7]
        msb = 0
        for i, b in enumerate(block):
            msb |= ((b & 0x80) >> (7 - i))
        result.append(msb)
        for b in block:
            result.append(b & 0x7F)
    return bytes(result)


def _unpack_7bit(data: bytes) -> bytes:
    result = bytearray()
    for offset in range(0, len(data), 8):
        block = data[offset:offset + 8]
        for i in range(1, len(block)):
            result.append(block[i] | (((block[0] >> (i - 1)) & 1) << 7))
    return bytes(result)


# --- Data classes ---

@dataclass
class ControlSettings:
    assign: int = 1       # 0=Disabled, 1=CC, 2=Note
    behaviour: int = 0    # 0=Momentary, 1=Toggle (buttons only)
    cmd: int = 0          # CC / Note number 0-127
    min_val: int = 0      # Min value 0-127
    max_val: int = 127    # Max value 0-127

    def copy(self):
        return ControlSettings(self.assign, self.behaviour, self.cmd, self.min_val, self.max_val)


@dataclass
class ChannelGroup:
    channel: int = 16   # 0-15 = explicit channel, 16 = use global
    slider: ControlSettings = field(default_factory=ControlSettings)
    knob: ControlSettings = field(default_factory=ControlSettings)
    solo: ControlSettings = field(default_factory=ControlSettings)
    mute: ControlSettings = field(default_factory=ControlSettings)
    prime: ControlSettings = field(default_factory=ControlSettings)

    def get_ctrl(self, name: str) -> ControlSettings:
        return getattr(self, name)

    def copy(self):
        return ChannelGroup(
            self.channel,
            self.slider.copy(), self.knob.copy(), self.solo.copy(),
            self.mute.copy(), self.prime.copy(),
        )


@dataclass
class TransportGroup:
    channel: int = 16
    prev_track: ControlSettings = field(default_factory=ControlSettings)
    next_track: ControlSettings = field(default_factory=ControlSettings)
    cycle: ControlSettings = field(default_factory=ControlSettings)
    set_marker: ControlSettings = field(default_factory=ControlSettings)
    prev_marker: ControlSettings = field(default_factory=ControlSettings)
    next_marker: ControlSettings = field(default_factory=ControlSettings)
    rew: ControlSettings = field(default_factory=ControlSettings)
    ff: ControlSettings = field(default_factory=ControlSettings)
    stop: ControlSettings = field(default_factory=ControlSettings)
    play: ControlSettings = field(default_factory=ControlSettings)
    rec: ControlSettings = field(default_factory=ControlSettings)

    def get_ctrl(self, name: str) -> ControlSettings:
        return getattr(self, name)

    def copy(self):
        t = TransportGroup(self.channel)
        for c in TRANSPORT_CONTROLS:
            object.__setattr__(t, c, getattr(self, c).copy())
        return t


class Scene:
    """Full nanoKONTROL 2 configuration: 339 internal bytes."""

    def __init__(self):
        self.global_channel: int = 0
        self.control_mode: int = 0
        self.led_mode: int = 0
        self.groups: List[ChannelGroup] = [ChannelGroup() for _ in range(NUM_GROUPS)]
        self.transport: TransportGroup = TransportGroup()
        self._apply_factory_defaults()

    def _apply_factory_defaults(self):
        for g, grp in enumerate(self.groups):
            grp.channel = 16
            grp.slider = ControlSettings(assign=1, cmd=g, min_val=0, max_val=127)
            grp.knob   = ControlSettings(assign=1, cmd=16 + g, min_val=0, max_val=127)
            grp.solo   = ControlSettings(assign=1, cmd=32 + g, min_val=0, max_val=127)
            grp.mute   = ControlSettings(assign=1, cmd=48 + g, min_val=0, max_val=127)
            grp.prime  = ControlSettings(assign=1, cmd=64 + g, min_val=0, max_val=127)
        t = self.transport
        t.channel = 16
        for name in TRANSPORT_CONTROLS:
            ctrl = t.get_ctrl(name)
            ctrl.assign = 1
            ctrl.cmd = TRANSPORT_CC_DEFAULTS.get(name, 0)
            ctrl.min_val = 0
            ctrl.max_val = 127
            ctrl.behaviour = 0

    # --- serialise to 339 bytes ---

    def to_data(self) -> bytes:
        data = bytearray(DATA_LEN)
        data[0] = self.global_channel & 0x0F
        data[1] = self.control_mode & 0x07
        data[2] = self.led_mode & 0x01
        for grp, offset in zip(self.groups, GROUP_OFFSETS):
            data[offset] = grp.channel
            for name, ctrl_off in CHANNEL_CTRL_OFFSETS.items():
                ctrl = grp.get_ctrl(name)
                base = offset + ctrl_off
                data[base]   = ctrl.assign & 0x03
                data[base+1] = ctrl.behaviour & 0x01
                data[base+2] = ctrl.cmd & 0x7F
                data[base+3] = ctrl.min_val & 0x7F
                data[base+4] = ctrl.max_val & 0x7F
                # byte base+5 is reserved, left as 0
        t = self.transport
        data[TRANSPORT_OFFSET] = t.channel
        for name, ctrl_off in TRANSPORT_CTRL_OFFSETS.items():
            ctrl = t.get_ctrl(name)
            base = TRANSPORT_OFFSET + ctrl_off
            data[base]   = ctrl.assign & 0x03
            data[base+1] = ctrl.behaviour & 0x01
            data[base+2] = ctrl.cmd & 0x7F
            data[base+3] = ctrl.min_val & 0x7F
            data[base+4] = ctrl.max_val & 0x7F
        return bytes(data)

    @classmethod
    def from_data(cls, data: bytes) -> "Scene":
        if len(data) < 323:
            raise ProtocolError(f"scene data too short: {len(data)} bytes")
        s = cls.__new__(cls)
        s.global_channel = data[0] & 0x0F
        s.control_mode = data[1] & 0x07
        s.led_mode = data[2] & 0x01
        s.groups = []
        for offset in GROUP_OFFSETS:
            grp = ChannelGroup(channel=data[offset])
            for name, ctrl_off in CHANNEL_CTRL_OFFSETS.items():
                base = offset + ctrl_off
                ctrl = ControlSettings(
                    assign=data[base], behaviour=data[base+1], cmd=data[base+2],
                    min_val=data[base+3], max_val=data[base+4],
                )
                object.__setattr__(grp, name, ctrl) if hasattr(grp, '__dict__') else setattr(grp, name, ctrl)
                setattr(grp, name, ctrl)
            s.groups.append(grp)
        t = TransportGroup(channel=data[TRANSPORT_OFFSET])
        for name, ctrl_off in TRANSPORT_CTRL_OFFSETS.items():
            base = TRANSPORT_OFFSET + ctrl_off
            ctrl = ControlSettings(
                assign=data[base], behaviour=data[base+1], cmd=data[base+2],
                min_val=data[base+3], max_val=data[base+4],
            )
            setattr(t, name, ctrl)
        s.transport = t
        return s

    def to_midi_bytes(self) -> bytes:
        return _pack_7bit(self.to_data())

    @classmethod
    def from_midi_bytes(cls, data: bytes) -> "Scene":
        if len(data) != SYSEX_DATA_LEN:
            raise ProtocolError(f"scene MIDI data must be {SYSEX_DATA_LEN} bytes, got {len(data)}")
        return cls.from_data(_unpack_7bit(data))

    def copy(self) -> "Scene":
        return Scene.from_data(self.to_data())

    def __eq__(self, other):
        return isinstance(other, Scene) and self.to_data() == other.to_data()

    def __repr__(self):
        return f"Scene(ch={self.global_channel}, mode={CONTROL_MODES[self.control_mode]}, led={LED_MODES[self.led_mode]})"


# --- message builders ---

def _header(channel: int) -> bytes:
    return bytes([0xF0, MANUFACTURER, 0x40 | (channel & 0x0F)]) + DEVICE_ID


def build_dump_request(channel: int = 0) -> bytes:
    return _header(channel) + CMD_DUMP_REQUEST + bytes([SYSEX_END])


def build_scene_data(scene: Scene) -> bytes:
    return _header(scene.global_channel) + CMD_SCENE_DATA + scene.to_midi_bytes() + bytes([SYSEX_END])


def build_scene_write(channel: int = 0) -> bytes:
    return _header(channel) + CMD_SCENE_WRITE + bytes([SYSEX_END])


def build_device_search(echo_id: int = 0) -> bytes:
    return bytes([0xF0, MANUFACTURER, 0x50, 0x00, echo_id & 0x7F, SYSEX_END])


# --- parsed reply types ---

@dataclass(frozen=True)
class SceneData:
    scene: Scene
    channel: int


@dataclass(frozen=True)
class LoadAck:
    pass


@dataclass(frozen=True)
class LoadNak:
    pass


@dataclass(frozen=True)
class WriteOk:
    pass


@dataclass(frozen=True)
class WriteErr:
    pass


@dataclass(frozen=True)
class DeviceFound:
    channel: int
    family_id: int
    version: tuple


# --- parser ---

def _korg_header(m: bytes):
    """Returns (channel, body_bytes) if m starts with a valid nanoKONTROL 2 header, else None."""
    if (len(m) >= 8 and m[0] == 0xF0 and m[1] == MANUFACTURER
            and 0x40 <= m[2] <= 0x4F and m[3:7] == DEVICE_ID):
        return m[2] & 0x0F, m[7:-1]
    return None


def is_nanokontrol2_message(message) -> bool:
    m = bytes(message)
    if len(m) < 3 or m[0] != 0xF0 or m[-1] != SYSEX_END:
        return False
    if m[1] == MANUFACTURER and m[2] == 0x50:
        return True
    return _korg_header(m) is not None


def parse(message):
    """Return SceneData, LoadAck, LoadNak, WriteOk, WriteErr, DeviceFound, or None."""
    m = bytes(message)
    if len(m) < 3 or m[0] != 0xF0 or m[-1] != SYSEX_END:
        return None

    # Device search reply: F0 42 50 01 chan echo fam_lo fam_hi mem_lo mem_hi min_lo min_hi maj_lo maj_hi F7
    if len(m) >= 15 and m[1] == MANUFACTURER and m[2] == 0x50 and m[3] == 0x01:
        channel = m[4] & 0x0F
        family_id = m[6] + (m[7] << 7)
        major = m[12] + (m[13] << 7)
        minor = m[10] + (m[11] << 7)
        if family_id == 147:  # 0x93 = nanoKONTROL2
            return DeviceFound(channel=channel, family_id=family_id, version=(major, minor))
        return None

    result = _korg_header(m)
    if result is None:
        return None
    channel, body = result

    if body[:6] == RESP_SCENE_DATA:
        payload = body[6:]
        try:
            return SceneData(scene=Scene.from_midi_bytes(payload), channel=channel)
        except ProtocolError:
            return None

    if body == RESP_LOAD_ACK:
        return LoadAck()
    if body == RESP_LOAD_NAK:
        return LoadNak()
    if body == RESP_WRITE_OK:
        return WriteOk()
    if body == RESP_WRITE_ERR:
        return WriteErr()

    return None
