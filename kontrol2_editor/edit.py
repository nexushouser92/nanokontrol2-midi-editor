"""Scene-editing operations that do not depend on the GUI.

Every function returns a new Scene (or plain data) and never mutates its input.
"""
from . import protocol
from .protocol import Scene

TEMPLATES = ("Factory Defaults", "Generic CC", "Clear All")
ANALOG_CONTROLS = ("slider", "knob")


def group_of(scene, key):
    """The channel group `key` (0-7), or the transport group for "transport"."""
    return scene.transport if key == "transport" else scene.groups[key]


def all_controls(scene):
    """Yield (group key, control name, ControlSettings) for all 51 controls."""
    for g, grp in enumerate(scene.groups):
        for name in protocol.CHANNEL_CONTROLS:
            yield g, name, grp.get_ctrl(name)
    for name in protocol.TRANSPORT_CONTROLS:
        yield "transport", name, scene.transport.get_ctrl(name)


def effective_channel(scene, key):
    """MIDI channel (0-15) a group really transmits on."""
    channel = group_of(scene, key).channel
    return scene.global_channel if channel >= 16 else channel


def apply_template(scene, name):
    s = scene.copy()
    if name == "Factory Defaults":
        s.apply_factory_defaults()
    elif name == "Generic CC":
        s.apply_factory_defaults()
        s.control_mode = 0
        s.led_mode = 0
    elif name == "Clear All":
        for _, _, ctrl in all_controls(s):
            ctrl.assign = 0
    else:
        raise ValueError(f"unknown template: {name!r}")
    return s


def copy_group(scene, source, targets):
    """Copy channel group `source` onto each group in `targets`.

    CC / Note numbers shift with the strip index so the copies stay unique.
    """
    s = scene.copy()
    src = s.groups[source]
    for t in targets:
        if t == source:
            continue
        dst = s.groups[t]
        dst.channel = src.channel
        for name in protocol.CHANNEL_CONTROLS:
            ctrl = src.get_ctrl(name).copy()
            ctrl.cmd = min(127, max(0, ctrl.cmd + t - source))
            setattr(dst, name, ctrl)
    return s


def reset_ranges(scene):
    """Calibration back to the full 0-127 range for every fader and knob."""
    s = scene.copy()
    for grp in s.groups:
        for name in ANALOG_CONTROLS:
            ctrl = grp.get_ctrl(name)
            ctrl.min_val, ctrl.max_val = 0, 127
    return s


def message_key(scene, key, ctrl):
    """(channel, assign, number) identifying the MIDI message a control sends."""
    return effective_channel(scene, key), ctrl.assign, ctrl.cmd


def find_conflicts(scene):
    """Set of (group key, control name) whose message is also sent by another enabled control."""
    seen = {}
    conflicts = set()
    for key, name, ctrl in all_controls(scene):
        if ctrl.assign == 0:
            continue
        msg = message_key(scene, key, ctrl)
        if msg in seen:
            conflicts.add((key, name))
            conflicts.add(seen[msg])
        else:
            seen[msg] = (key, name)
    return conflicts


def message_targets(scene):
    """Map (channel, assign, number) -> [(group key, control name)] for routing live MIDI."""
    targets = {}
    for key, name, ctrl in all_controls(scene):
        if ctrl.assign != 0:
            targets.setdefault(message_key(scene, key, ctrl), []).append((key, name))
    return targets


def cheatsheet_rows(scene):
    """Rows for a CSV overview of every assignment."""
    rows = [["Section", "Control", "MIDI channel", "Type", "CC / Note", "Min / Off", "Max / On", "Behaviour"]]
    for key, name, ctrl in all_controls(scene):
        if key == "transport":
            section, label = "Transport", protocol.TRANSPORT_LABELS[name]
        else:
            section, label = f"Channel {key + 1}", protocol.CHANNEL_CTRL_LABELS[name]
        enabled = ctrl.assign != 0
        rows.append([
            section, label, effective_channel(scene, key) + 1,
            protocol.ASSIGN_OPTS[ctrl.assign], ctrl.cmd if enabled else "",
            ctrl.min_val if enabled else "", ctrl.max_val if enabled else "",
            protocol.BEHAVIOUR_OPTS[ctrl.behaviour] if enabled and name not in ANALOG_CONTROLS else "",
        ])
    return rows
