import unittest

from kontrol2_editor import protocol
from kontrol2_editor.protocol import Scene


class ProtocolTest(unittest.TestCase):
    def test_round_trip_through_midi_bytes(self):
        scene = Scene()
        scene.global_channel = 5
        scene.led_mode = 1
        scene.groups[3].knob.min_val, scene.groups[3].knob.max_val = 100, 20
        scene.transport.play.assign = 2
        scene.custom_daw[2] = 42
        again = Scene.from_midi_bytes(scene.to_midi_bytes())
        self.assertEqual(scene, again)
        self.assertEqual(again.custom_daw[2], 42)

    def test_scene_data_message_parses_back(self):
        scene = Scene()
        scene.groups[0].slider.cmd = 99
        reply = protocol.parse(protocol.build_scene_data(scene))
        self.assertIsInstance(reply, protocol.SceneData)
        self.assertEqual(reply.scene.groups[0].slider.cmd, 99)

    def test_values_the_device_should_never_send_are_clamped(self):
        data = bytearray(Scene().to_data())
        data[1] = 0x7F                                   # control mode
        data[protocol.GROUP_OFFSETS[0]] = 0x7F           # strip channel
        base = protocol.GROUP_OFFSETS[0] + protocol.CHANNEL_CTRL_OFFSETS["slider"]
        data[base], data[base + 1], data[base + 2] = 9, 7, 0xFF
        scene = Scene.from_data(bytes(data))
        self.assertLess(scene.control_mode, len(protocol.CONTROL_MODES))
        self.assertEqual(scene.groups[0].channel, 16)
        slider = scene.groups[0].slider
        self.assertEqual((slider.assign, slider.behaviour, slider.cmd), (2, 1, 0x7F))

    def test_factory_defaults_are_unique_per_control(self):
        scene = Scene()
        numbers = [(c.assign, c.cmd) for grp in scene.groups for c in
                   (grp.slider, grp.knob, grp.solo, grp.mute, grp.prime)]
        self.assertEqual(len(numbers), len(set(numbers)))

    def test_short_data_is_rejected(self):
        with self.assertRaises(protocol.ProtocolError):
            Scene.from_data(bytes(100))


if __name__ == "__main__":
    unittest.main()
