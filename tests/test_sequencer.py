import unittest

from kontrol2_editor import protocol, sequencer
from kontrol2_editor.sequencer import CLOCK, CONTINUE, SONG_POSITION, START, STOP, Sequencer


def outs(actions):
    return [a[1:] for a in actions if a[0] == "out"]


def leds(actions):
    return {a[1]: a[2] for a in actions if a[0] == "led"}


class SequencerTest(unittest.TestCase):
    def setUp(self):
        self.scene = sequencer.sequencer_scene(protocol.Scene())
        self.seq = Sequencer(self.scene)

    def press(self, key, name):
        """Press and release a momentary button of the sequencer scene; returns the press actions."""
        group = self.scene.transport if key == "transport" else self.scene.groups[key]
        ctrl = group.get_ctrl(name)
        actions = self.seq.device_message(0xB0, ctrl.cmd, 127)
        self.seq.device_message(0xB0, ctrl.cmd, 0)
        return actions

    def run_steps(self, steps):
        """Clock ticks for `steps` 16th-note steps after Start; returns all actions."""
        actions = self.seq.clock_message(START)
        for _ in range(steps * sequencer.TICKS_PER_STEP):
            actions += self.seq.clock_message(CLOCK)
        return actions

    def test_scene_uses_external_leds_and_momentary_factory_buttons(self):
        user = protocol.Scene()
        user.groups[0].slider.cmd = 99
        user.groups[2].mute.behaviour = 1
        seq = sequencer.sequencer_scene(user)
        self.assertEqual(seq.led_mode, 1)
        self.assertEqual(seq.control_mode, 0)
        self.assertEqual(seq.groups[0].slider.cmd, 99)
        self.assertEqual(seq.groups[2].mute.behaviour, 0)
        self.assertEqual(user.groups[2].mute.behaviour, 1)

    def test_mute_and_rec_rows_are_steps_1_to_16_of_the_selected_track(self):
        self.assertEqual(leds(self.press(3, "mute")), {"mute_4": True})
        self.assertEqual(leds(self.press(0, "prime")), {"prime_1": True})
        self.assertTrue(self.seq.steps[0][3])
        self.assertTrue(self.seq.steps[0][8])
        self.press(3, "mute")
        self.assertFalse(self.seq.steps[0][3])

    def test_track_buttons_select_the_track_and_redraw_its_steps(self):
        self.seq.steps[1][0] = True
        actions = self.press("transport", "next_track")
        self.assertEqual(self.seq.selected, 1)
        self.assertTrue(leds(actions)["mute_1"])
        self.assertEqual(len(leds(actions)), sequencer.STEPS)
        self.press("transport", "prev_track")
        self.press("transport", "prev_track")
        self.assertEqual(self.seq.selected, 7)

    def test_knobs_set_the_note_and_faders_go_to_the_daw(self):
        knob = self.scene.groups[2].knob.cmd
        self.assertEqual(self.seq.device_message(0xB0, knob, 64), [])
        self.assertEqual(self.seq.notes[2], 64)
        fader = self.scene.groups[5].slider.cmd
        self.assertEqual(outs(self.seq.device_message(0xB0, fader, 77)), [(0xB0, fader, 77)])

    def test_transport_buttons_also_go_to_the_daw(self):
        play = self.scene.transport.play.cmd
        self.assertEqual(outs(self.seq.device_message(0xB0, play, 127)), [(0xB0, play, 127)])

    def test_each_step_plays_its_tracks_on_their_own_channel(self):
        self.seq.steps[0][0] = True
        self.seq.steps[3][1] = True
        self.seq.notes[3] = 40
        actions = self.run_steps(2)
        self.assertEqual(outs(actions), [
            (0x90, 60, sequencer.VELOCITY), (0x80, 60, 0),
            (0x93, 40, sequencer.VELOCITY), (0x83, 40, 0)])
        self.assertEqual(self.seq.position, 1)

    def test_playhead_inverts_the_step_led(self):
        self.seq.steps[0][1] = True
        actions = self.run_steps(2)
        self.assertFalse(leds(actions)["mute_2"])   # active step under the playhead: shown off
        self.assertFalse(leds(actions)["mute_1"])   # playhead left step 1 (inactive): off again
        self.assertTrue(self.seq.playing)

    def test_solo_plays_only_the_soloed_tracks(self):
        self.seq.steps[0][0] = self.seq.steps[1][0] = True
        self.assertEqual(leds(self.press(1, "solo")), {"solo_2": True})
        notes = [o for o in outs(self.run_steps(1)) if o[0] & 0xF0 == 0x90]
        self.assertEqual(notes, [(0x91, 60, sequencer.VELOCITY)])

    def test_stop_releases_notes_and_clears_the_playhead(self):
        self.seq.steps[0][0] = True
        self.seq.clock_message(START)
        self.seq.clock_message(CLOCK)
        actions = self.seq.clock_message(STOP)
        self.assertEqual(outs(actions), [(0x80, 60, 0)])
        self.assertEqual(self.seq.position, -1)
        self.assertFalse(leds(actions)["play"])
        self.assertEqual(self.seq.clock_message(CLOCK), [])

    def test_song_position_and_continue(self):
        self.seq.clock_message(START)
        self.seq.clock_message(STOP)
        self.seq.clock_message(SONG_POSITION, 21, 0)    # 21 sixteenths = step 6 of the second bar
        self.seq.clock_message(CONTINUE)
        self.seq.clock_message(CLOCK)
        self.assertEqual(self.seq.position, 5)

    def test_rewind_goes_back_to_step_1_when_stopped(self):
        self.run_steps(3)
        self.seq.clock_message(STOP)
        self.press("transport", "rew")
        self.seq.clock_message(CONTINUE)
        self.seq.clock_message(CLOCK)
        self.assertEqual(self.seq.position, 0)

    def test_pattern_round_trip(self):
        self.seq.steps[4][15] = True
        self.seq.notes[4] = 72
        self.seq.solo[2] = True
        other = Sequencer(self.scene)
        other.load_dict(self.seq.to_dict())
        self.assertTrue(other.steps[4][15])
        self.assertEqual(other.notes[4], 72)
        self.assertTrue(other.solo[2])
        other.load_dict({"steps": "junk"})
        self.assertTrue(other.steps[4][15])

    def test_note_names(self):
        self.assertEqual(sequencer.note_name(60), "C4")
        self.assertEqual(sequencer.note_name(37), "C#2")


if __name__ == "__main__":
    unittest.main()
