"""Save and load nanoKONTROL 2 scenes: JSON presets and raw .syx files."""
import json
import os

from . import protocol
from .protocol import ProtocolError, Scene

FORMAT_NAME = "kontrol2-editor-preset"
FORMAT_VERSION = 1


def _scene_to_dict(scene: Scene) -> dict:
    groups = []
    for i, grp in enumerate(scene.groups):
        g = {"channel": grp.channel, "controls": {}}
        for name in protocol.CHANNEL_CONTROLS:
            ctrl = grp.get_ctrl(name)
            g["controls"][name] = {
                "assign": ctrl.assign,
                "behaviour": ctrl.behaviour,
                "cmd": ctrl.cmd,
                "min": ctrl.min_val,
                "max": ctrl.max_val,
            }
        groups.append(g)
    transport = {"channel": scene.transport.channel, "controls": {}}
    for name in protocol.TRANSPORT_CONTROLS:
        ctrl = scene.transport.get_ctrl(name)
        transport["controls"][name] = {
            "assign": ctrl.assign,
            "behaviour": ctrl.behaviour,
            "cmd": ctrl.cmd,
            "min": ctrl.min_val,
            "max": ctrl.max_val,
        }
    return {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "global_channel": scene.global_channel,
        "control_mode": scene.control_mode,
        "led_mode": scene.led_mode,
        "groups": groups,
        "transport": transport,
        "custom_daw": list(scene.custom_daw),
    }


def _scene_from_dict(data: dict) -> Scene:
    try:
        if data.get("format") != FORMAT_NAME:
            raise ProtocolError("not a nanoKONTROL 2 Editor preset")
        if data.get("version") != FORMAT_VERSION:
            raise ProtocolError(f"unsupported preset version {data.get('version')!r}")
        scene = Scene()
        scene.global_channel = int(data["global_channel"]) & 0x0F
        scene.control_mode = int(data["control_mode"]) & 0x07
        scene.led_mode = int(data["led_mode"]) & 0x01
        for i, grp_data in enumerate(data["groups"][:protocol.NUM_GROUPS]):
            grp = scene.groups[i]
            grp.channel = int(grp_data["channel"])
            for name, vals in grp_data.get("controls", {}).items():
                if name not in protocol.CHANNEL_CONTROLS:
                    continue
                ctrl = grp.get_ctrl(name)
                ctrl.assign = int(vals["assign"])
                ctrl.behaviour = int(vals["behaviour"])
                ctrl.cmd = int(vals["cmd"])
                ctrl.min_val = int(vals["min"])
                ctrl.max_val = int(vals["max"])
        t_data = data.get("transport", {})
        scene.transport.channel = int(t_data.get("channel", 16))
        for name, vals in t_data.get("controls", {}).items():
            if name not in protocol.TRANSPORT_CONTROLS:
                continue
            ctrl = scene.transport.get_ctrl(name)
            ctrl.assign = int(vals["assign"])
            ctrl.behaviour = int(vals["behaviour"])
            ctrl.cmd = int(vals["cmd"])
            ctrl.min_val = int(vals["min"])
            ctrl.max_val = int(vals["max"])
        raw_daw = data.get("custom_daw", [0] * protocol.CUSTOM_DAW_LEN)
        scene.custom_daw = [int(v) & 0xFF for v in raw_daw[:protocol.CUSTOM_DAW_LEN]]
        while len(scene.custom_daw) < protocol.CUSTOM_DAW_LEN:
            scene.custom_daw.append(0)
    except (KeyError, TypeError, ValueError) as e:
        raise ProtocolError(f"malformed preset: {e!r}") from None
    return scene


def to_syx(scene: Scene) -> bytes:
    """Full SysEx scene dump (load + write commands)."""
    return protocol.build_scene_data(scene) + protocol.build_scene_write(scene.global_channel)


def from_syx(data: bytes) -> Scene:
    """Parse a .syx file containing a nanoKONTROL 2 scene data dump."""
    messages = list(_split_sysex(data))
    for msg in messages:
        result = protocol.parse(msg)
        if isinstance(result, protocol.SceneData):
            return result.scene
    raise ProtocolError("no nanoKONTROL 2 scene data found in the .syx file")


def _split_sysex(data):
    start = None
    for i, b in enumerate(data):
        if b == 0xF0:
            start = i
        elif b == 0xF7 and start is not None:
            yield bytes(data[start:i + 1])
            start = None
    if start is not None:
        raise ProtocolError("the .syx file ends in the middle of a message")


def save(path, scene: Scene):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".syx":
        with open(path, "wb") as f:
            f.write(to_syx(scene))
    else:
        content = json.dumps(_scene_to_dict(scene), indent=2, ensure_ascii=False) + "\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


def load(path) -> Scene:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".syx":
        with open(path, "rb") as f:
            return from_syx(f.read())
    try:
        with open(path, encoding="utf-8") as f:
            return _scene_from_dict(json.load(f))
    except json.JSONDecodeError as e:
        raise ProtocolError(f"not a valid JSON file: {e}") from None
