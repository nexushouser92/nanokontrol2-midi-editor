"""Talk to the nanoKONTROL 2 through a MIDI port (send/receive/drain).

The device works with whole-scene dumps:
  read_scene()   -> one dump request, one SysEx reply with 339 bytes of settings
  write_scene()  -> one SysEx scene-data message -> LoadAck -> scene-write message -> WriteOk
"""
import time

from . import protocol
from .protocol import LoadAck, LoadNak, SceneData, WriteErr, WriteOk

REPLY_TIMEOUT = 1.0
RETRIES = 2


class DeviceError(Exception):
    pass


class WriteFailedError(DeviceError):
    pass


def _wait_for(port, wanted, timeout):
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        msg = port.receive(remaining) if remaining > 0 else None
        if msg is None:
            return None
        reply = protocol.parse(msg)
        if reply is not None and wanted(reply):
            return reply


def _ask(port, message, wanted, what, timeout, retries):
    for _ in range(retries + 1):
        port.send(message)
        reply = _wait_for(port, wanted, timeout)
        if reply is not None:
            return reply
    raise DeviceError(f"nanoKONTROL 2 did not respond ({what})")


def read_scene(port, channel=0, timeout=REPLY_TIMEOUT, retries=RETRIES):
    """Request and return (scene, channel). channel is the device's global MIDI channel."""
    port.drain()
    reply = _ask(
        port,
        protocol.build_dump_request(channel),
        lambda r: isinstance(r, SceneData),
        "scene dump",
        timeout,
        retries,
    )
    return reply.scene, reply.channel


def write_scene(port, scene, timeout=REPLY_TIMEOUT, retries=RETRIES):
    """Write scene to device RAM then save to flash. Raises DeviceError on failure."""
    port.drain()
    load_msg = protocol.build_scene_data(scene)

    for _ in range(retries + 1):
        port.send(load_msg)
        reply = _wait_for(port, lambda r: isinstance(r, (LoadAck, LoadNak)), timeout)
        if isinstance(reply, LoadAck):
            break
        if isinstance(reply, LoadNak):
            raise WriteFailedError("nanoKONTROL 2 rejected the scene data (LoadNak)")
    else:
        raise DeviceError("nanoKONTROL 2 did not acknowledge scene load")

    save_msg = protocol.build_scene_write(scene.global_channel)
    port.send(save_msg)
    reply = _wait_for(port, lambda r: isinstance(r, (WriteOk, WriteErr)), timeout)
    if isinstance(reply, WriteErr):
        raise WriteFailedError("nanoKONTROL 2 failed to save scene to flash")
    if reply is None:
        raise DeviceError("nanoKONTROL 2 did not acknowledge scene write")
