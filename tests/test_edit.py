import unittest

from kontrol2_editor import edit, i18n, protocol
from kontrol2_editor.protocol import Scene


i18n.LANG = "en"


class EditTest(unittest.TestCase):
    def test_ableton_live_is_not_a_template(self):
        self.assertNotIn("Ableton Live", edit.TEMPLATES)
        self.assertEqual(edit.TEMPLATES, ("Factory Defaults", "Generic CC", "Clear All"))

    def test_clear_all_disables_every_control(self):
        cleared = edit.apply_template(Scene(), "Clear All")
        self.assertTrue(all(c.assign == 0 for _, _, c in edit.all_controls(cleared)))

    def test_generic_cc_resets_modes(self):
        scene = Scene()
        scene.control_mode, scene.led_mode = 3, 1
        generic = edit.apply_template(scene, "Generic CC")
        self.assertEqual((generic.control_mode, generic.led_mode), (0, 0))

    def test_factory_defaults_keep_modes(self):
        scene = Scene()
        scene.control_mode = 3
        scene.groups[0].slider.cmd = 99
        reset = edit.apply_template(scene, "Factory Defaults")
        self.assertEqual(reset.control_mode, 3)
        self.assertEqual(reset.groups[0].slider.cmd, 0)

    def test_unknown_template_is_an_error(self):
        with self.assertRaises(ValueError):
            edit.apply_template(Scene(), "Nope")

    def test_templates_do_not_mutate_the_input(self):
        scene = Scene()
        before = scene.to_data()
        edit.apply_template(scene, "Clear All")
        self.assertEqual(scene.to_data(), before)

    def test_copy_group_shifts_numbers_with_the_strip(self):
        scene = Scene()
        scene.groups[0].slider.cmd = 10
        scene.groups[0].slider.min_val = 5
        copied = edit.copy_group(scene, 0, [3])
        self.assertEqual(copied.groups[3].slider.cmd, 13)
        self.assertEqual(copied.groups[3].slider.min_val, 5)
        self.assertEqual(copied.groups[1].slider.cmd, 1)

    def test_copy_group_clamps_numbers(self):
        scene = Scene()
        scene.groups[0].slider.cmd = 127
        self.assertEqual(edit.copy_group(scene, 0, [7]).groups[7].slider.cmd, 127)

    def test_no_conflicts_in_factory_scene(self):
        self.assertEqual(edit.find_conflicts(Scene()), set())

    def test_conflict_needs_same_channel_type_and_number(self):
        scene = Scene()
        scene.groups[2].slider.cmd = 0
        self.assertEqual(edit.find_conflicts(scene), {(0, "slider"), (2, "slider")})
        scene.groups[2].channel = 4                      # different channel: fine
        self.assertEqual(edit.find_conflicts(scene), set())
        scene.groups[2].channel = 16
        scene.groups[2].slider.assign = 2                # Note vs CC: fine
        self.assertEqual(edit.find_conflicts(scene), set())

    def test_disabled_controls_never_conflict(self):
        scene = Scene()
        scene.groups[2].slider.cmd = 0
        scene.groups[2].slider.assign = 0
        self.assertEqual(edit.find_conflicts(scene), set())

    def test_reset_ranges_only_touches_faders_and_knobs(self):
        scene = Scene()
        scene.groups[1].slider.min_val = 30
        scene.groups[1].knob.max_val = 40
        scene.groups[1].solo.max_val = 50
        reset = edit.reset_ranges(scene)
        self.assertEqual((reset.groups[1].slider.min_val, reset.groups[1].knob.max_val), (0, 127))
        self.assertEqual(reset.groups[1].solo.max_val, 50)

    def test_message_targets_follow_the_global_channel(self):
        scene = Scene()
        scene.global_channel = 3
        scene.groups[1].channel = 9
        targets = edit.message_targets(scene)
        self.assertIn((1, "slider"), targets[(9, 1, 1)])
        self.assertIn((0, "slider"), targets[(3, 1, 0)])

    def test_cheatsheet_has_one_row_per_control(self):
        rows = edit.cheatsheet_rows(Scene())
        self.assertEqual(len(rows), 1 + 8 * 5 + len(protocol.TRANSPORT_CONTROLS))
        self.assertEqual(rows[1][:2], ["Channel 1", "Slider"])


if __name__ == "__main__":
    unittest.main()
