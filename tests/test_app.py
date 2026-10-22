import csv
import os
import tempfile
import unittest
from types import SimpleNamespace

from kontrol2_editor import APP_NAME, edit, gui, i18n, pages, protocol, theme
from kontrol2_editor.protocol import Scene

from fakes import FakeLink, FakeRunner

i18n.LANG = "en"  # the texts checked below are the English ones


def u_width(root):
    return gui.u(theme.WINDOW_W)


def u_height(root):
    return gui.u(theme.WINDOW_H)


def click(surface, x, y):
    event = SimpleNamespace(x=x, y=y)
    surface._on_motion(event)
    surface._on_press(event)
    surface._on_release(event)


def drag(surface, start, end):
    surface._on_press(SimpleNamespace(x=start[0], y=start[1]))
    surface._on_drag(SimpleNamespace(x=end[0], y=end[1]))
    surface._on_release(SimpleNamespace(x=end[0], y=end[1]))


class AppTest(unittest.TestCase):
    def setUp(self):
        self.root = gui.create_root()
        self.link = FakeLink()
        self.now = 100.0
        self.answer = True
        self.dialogs = []
        self.files = []
        self.runner = FakeRunner()
        self.loopmidi = True
        self.urls = []
        self.seq_saves = []
        self.app = gui.App(
            self.root, link=self.link, autostart=False, clock=lambda: self.now,
            ask_yes_no=self._confirm, ask_open=lambda **kw: self.files.pop(0),
            ask_save=lambda **kw: self.files.pop(0), **self.seq_kwargs())

    def seq_kwargs(self):
        return dict(seq_runner=self.runner, seq_settings={}, save_seq_settings=self.seq_saves.append,
                    loopmidi_check=lambda: self.loopmidi, open_url=self.urls.append,
                    port_lister=lambda: (["nanoKONTROL2", "loopMIDI clock"],
                                         ["nanoKONTROL2", "loopMIDI notes", "Microsoft GS Wavetable Synth"]))

    def tearDown(self):
        self.root.destroy()

    def _confirm(self, title, message):
        self.dialogs.append(title)
        return self.answer

    def connect(self, scene=None):
        self.link.events.put(("linked", None))
        self.link.events.put(("settings", scene or Scene()))
        self.app.tick()

    def page(self, section):
        self.app.show_section(section)
        return self.app._visible

    # --- identity and startup ---

    def test_window_uses_the_new_name_and_font(self):
        self.assertEqual(self.root.title(), "MIDI Editor nanoKONTROL 2")
        self.assertEqual(APP_NAME, "MIDI Editor nanoKONTROL 2")
        self.assertNotEqual(theme.ui_family, "Segoe UI")

    def test_layout_fits_the_window_at_any_scale(self):
        for zoom in (1.0, 1.1, 0.8):
            theme.scale = zoom
            root = gui.create_root()
            try:
                theme.scale = zoom
                app = gui.App(root, link=FakeLink(), autostart=False)
                root.update_idletasks()
                surfaces = [w for w in root.winfo_children() if isinstance(w, gui.Surface)]
                self.assertTrue(surfaces)
                for w in surfaces:
                    self.assertLessEqual(w.winfo_x() + w.winfo_reqwidth(), u_width(root) + 1)
                    self.assertLessEqual(w.winfo_y() + w.winfo_reqheight(), u_height(root) + 1)
                footer = max(surfaces, key=lambda w: w.winfo_y())
                nav = next(w for w in surfaces if w.winfo_reqwidth() == gui.u(theme.NAV_W))
                self.assertLessEqual(nav.winfo_y() + nav.winfo_reqheight(), footer.winfo_y())
                self.assertEqual(footer.winfo_y() + footer.winfo_reqheight(), u_height(root))
            finally:
                root.destroy()

    def test_waiting_page_until_a_scene_arrives(self):
        self.assertIsInstance(self.app._visible, pages.WaitingPage)
        self.connect()
        self.assertIsInstance(self.app._visible, pages.GlobalPage)

    def test_every_section_renders(self):
        self.connect()
        expected = {"global": pages.GlobalPage, "transport": pages.TransportPage,
                    "calibration": pages.CalibrationPage, "leds": pages.LedPage,
                    "sequencer": pages.SequencerPage, "monitor": pages.MonitorPage}
        for section, cls in expected.items():
            self.assertIsInstance(self.page(section), cls)
        for strip in range(1, 9):
            page = self.page(f"ch{strip}")
            self.assertIsInstance(page, pages.ChannelPage)
            self.assertEqual(page.key, strip - 1)
        self.assertEqual(len(self.app._pages), len(gui.PAGE_CLASSES))

    def test_background_build_creates_all_pages(self):
        for _ in range(len(gui.PAGE_CLASSES) + 1):
            self.app._build_remaining_pages()
        self.assertEqual(set(self.app._pages), set(gui.PAGE_CLASSES))

    def test_unchanged_page_is_not_redrawn(self):
        self.connect()
        page = self.page("transport")
        calls = []
        page.refresh = lambda scene, conflicts: calls.append(1)
        self.page("global")
        self.page("transport")
        self.assertEqual(calls, [])
        self.app.set_ctrl("transport", "play", cmd=5)
        self.assertEqual(calls, [1])

    # --- editing ---

    def test_plus_button_changes_the_scene_and_pushes_it(self):
        self.connect()
        row = self.page("ch3").rows["slider"]
        before = self.app.scene.groups[2].slider.cmd
        click(self.app._visible, row.cmd.x2 - 3, row.cmd.y1 + 5)
        self.assertEqual(self.app.scene.groups[2].slider.cmd, before + 1)
        self.assertEqual(row.cmd.value, before + 1)
        self.assertEqual(len(self.link.pushed), 1)

    def test_typed_values_are_clamped(self):
        self.connect()
        row = self.page("ch1").rows["knob"]
        row.hi._commit_text("500")
        row.lo._commit_text("not a number")
        self.assertEqual(self.app.scene.groups[0].knob.max_val, 127)
        self.assertEqual(self.app.scene.groups[0].knob.min_val, 0)

    def test_disabling_a_control_greys_out_its_fields(self):
        self.connect()
        row = self.page("ch1").rows["solo"]
        row.assign._pick("Disabled")
        self.assertEqual(self.app.scene.groups[0].solo.assign, 0)
        self.assertFalse(row.cmd.enabled or row.lo.enabled or row.behaviour.enabled)
        row.assign._pick("Note")
        self.assertTrue(row.cmd.enabled and row.behaviour.enabled)

    def test_channel_page_is_shared_but_shows_each_strip(self):
        self.connect()
        self.app.set_ctrl(4, "slider", cmd=70)
        self.assertEqual(self.page("ch1").rows["slider"].cmd.value, 0)
        self.assertEqual(self.page("ch5").rows["slider"].cmd.value, 70)
        self.assertEqual(self.page("ch2").rows["slider"].cmd.value, 1)

    def test_calibration_bar_drag_sets_max(self):
        self.connect()
        bar = self.page("calibration").rows[(0, "slider")].bar
        cy = (bar.y1 + bar.y2) // 2
        drag(self.app._visible, (round(bar._vx(127)), cy), (round(bar._vx(64)), cy))
        self.assertEqual(self.app.scene.groups[0].slider.max_val, 64)
        self.assertEqual(self.app.scene.groups[0].slider.min_val, 0)

    def test_bar_handles_can_cross_to_invert(self):
        self.connect()
        bar = self.page("calibration").rows[(1, "knob")].bar
        cy = (bar.y1 + bar.y2) // 2
        drag(self.app._visible, (round(bar._vx(0)), cy), (round(bar._vx(127)), cy))
        drag(self.app._visible, (round(bar._vx(127)), cy), (round(bar._vx(10)), cy))
        knob = self.app.scene.groups[1].knob
        self.assertGreater(knob.min_val, knob.max_val)

    def test_two_stationary_clicks_on_a_bar_reset_it(self):
        self.connect()
        self.app.set_ctrl(0, "slider", min_val=30, max_val=60)
        page = self.page("calibration")
        bar = page.rows[(0, "slider")].bar
        point = (round(bar._vx(30)), (bar.y1 + bar.y2) // 2)
        click(page, *point)
        click(page, *point)
        slider = self.app.scene.groups[0].slider
        self.assertEqual((slider.min_val, slider.max_val), (0, 127))

    def test_double_click_resets_a_bar(self):
        self.connect()
        self.app.set_ctrl(0, "slider", min_val=30, max_val=60)
        bar = self.page("calibration").rows[(0, "slider")].bar
        bar.on_double(0, 0)
        slider = self.app.scene.groups[0].slider
        self.assertEqual((slider.min_val, slider.max_val), (0, 127))

    def test_invert_button_swaps_min_and_max(self):
        self.connect()
        self.app.set_ctrl(2, "slider", min_val=10, max_val=90)
        row = self.page("ch3").rows["slider"]
        click(self.app._visible, row.invert.x1 + 5, row.invert.y1 + 5)
        slider = self.app.scene.groups[2].slider
        self.assertEqual((slider.min_val, slider.max_val), (90, 10))

    def test_reset_all_ranges_asks_first(self):
        self.connect()
        self.app.set_ctrl(0, "slider", min_val=30)
        self.answer = False
        self.app.reset_ranges()
        self.assertEqual(self.app.scene.groups[0].slider.min_val, 30)
        self.answer = True
        self.app.reset_ranges()
        self.assertEqual(self.app.scene.groups[0].slider.min_val, 0)

    def test_templates_and_factory_reset(self):
        self.connect()
        self.app.apply_template("Clear All")
        self.assertTrue(all(c.assign == 0 for _, _, c in edit.all_controls(self.app.scene)))
        self.answer = False
        self.app.factory_reset()
        self.assertTrue(all(c.assign == 0 for _, _, c in edit.all_controls(self.app.scene)))
        self.answer = True
        self.app.factory_reset()
        self.assertEqual(self.app.scene, Scene())

    def test_undo_all_restores_the_scene_read_from_the_device(self):
        original = Scene()
        original.global_channel = 7
        self.connect(original)
        self.app.set_global("global_channel", 2)
        self.app.undo_all()
        self.assertEqual(self.app.scene.global_channel, 7)

    def test_copy_strip_shifts_numbers(self):
        self.connect()
        self.app.set_ctrl(0, "slider", cmd=10)
        self.app.copy_group(0, [3])
        self.assertEqual(self.app.scene.groups[3].slider.cmd, 13)
        self.app.copy_group(0, list(range(1, 8)))
        self.assertIn("Copy strip", self.dialogs)

    def test_conflicts_are_flagged_in_rows_and_footer(self):
        self.connect()
        self.app.set_ctrl(2, "slider", cmd=0)
        self.assertEqual(self.app.conflicts, {(0, "slider"), (2, "slider")})
        self.assertTrue(self.page("ch3").rows["slider"].cmd.warn)
        self.assertFalse(self.page("ch3").rows["knob"].cmd.warn)
        self.assertIn("2 controls", self.app._footer.itemcget(self.app._conflict_note, "text"))

    def test_ext_led_chip_toggles_led_mode(self):
        self.connect()
        self.app.toggle_led_mode()
        self.assertEqual(self.app.scene.led_mode, 1)
        self.assertEqual(self.app._led_chip._text, "Ext LEDs: ON")

    def test_global_page_edits(self):
        self.connect()
        page = self.page("global")
        page.channel._pick("5")
        page.mode._pick("Ableton Live")
        page.daw[1]._pick("41")
        scene = self.app.scene
        self.assertEqual((scene.global_channel, scene.control_mode, scene.custom_daw[1]), (4, 3, 41))

    # --- live MIDI ---

    def test_live_midi_feeds_calibration_and_measured_range(self):
        self.connect()
        page = self.page("calibration")
        for value in (77, 20, 100):
            self.link.incoming.put((1.0, 0xB0, 0, value))
        self.app.tick()
        self.assertEqual(self.app.live[(0, "slider")], 100)
        self.assertEqual(self.app.seen[(0, "slider")], [20, 100])
        self.assertEqual(page.itemcget(page.rows[(0, "slider")].readout, "text"), "100")
        self.assertEqual(page.rows[(0, "slider")].bar._live, 100)
        self.app.reset_ranges()
        self.assertEqual(self.app.seen, {})
        self.assertIsNone(page.rows[(0, "slider")].bar._live)

    def test_spanish_interface(self):
        self.root.destroy()
        i18n.LANG = "es"
        try:
            self.root = gui.create_root()
            self.app = gui.App(self.root, link=self.link, autostart=False, clock=lambda: self.now,
                               ask_yes_no=self._confirm, **self.seq_kwargs())
            self.connect()
            self.assertEqual(self.app._send._text, "Enviar al nanoKONTROL")
            self.assertEqual(self.app._led_chip._text, "LEDs ext.: NO")
            row = self.page("transport").rows["play"]
            row._on_assign("Nota")
            self.assertEqual(self.app.scene.transport.play.assign, 2)
            self.app.apply_template("Borrar todo")
            self.assertEqual(self.dialogs[-1], "Aplicar plantilla")
            self.assertEqual(self.app.scene.transport.play.assign, 0)
        finally:
            i18n.LANG = "en"

    # --- sequencer ---

    def start_sequencer(self):
        self.connect()
        self.app.show_section("sequencer")
        self.app.set_seq_output("loopMIDI notes")
        self.app.set_seq_clock("loopMIDI clock")
        self.app.start_sequencer()

    def test_sequencer_lists_ports_without_the_device_itself(self):
        self.connect()
        self.app.show_section("sequencer")
        page = self.page("sequencer")
        self.assertEqual(page.out.values, ["loopMIDI notes", "Microsoft GS Wavetable Synth"])
        self.assertEqual(page.clock.values, ["Internal clock", "loopMIDI clock"])

    def test_sequencer_with_the_internal_clock_needs_only_an_output(self):
        self.connect()
        self.app.show_section("sequencer")
        self.app.set_seq_output("loopMIDI notes")
        self.app.set_seq_clock("Internal clock")
        self.assertEqual(self.app.seq_clock, "internal")
        self.app.start_sequencer()
        self.assertTrue(self.app.seq_on)
        self.assertEqual(self.runner.started, ("loopMIDI notes", None))
        self.assertIn("press Play on the nanoKONTROL 2", self.app.sequencer_status()[0])
        self.app.seq_tempo(97)
        self.assertEqual(self.app.seq.tempo, 97)

    def test_loopmidi_notice_only_when_it_is_missing(self):
        self.connect()
        self.app.show_section("sequencer")
        page = self.page("sequencer")
        self.assertFalse(page.download.enabled)
        self.assertEqual(page.itemcget(page._notice[0], "state"), "hidden")
        self.loopmidi = False
        self.app.show_section("global")
        self.app.show_section("sequencer")
        self.assertTrue(page.download.enabled)
        self.assertEqual(page.itemcget(page._notice[0], "state"), "normal")
        self.assertEqual(page.itemcget(page._help, "state"), "hidden")
        self.app.download_loopmidi()
        self.assertEqual(self.urls, ["https://www.tobias-erichsen.de/software/loopmidi.html"])

    def test_sequencer_needs_two_ports(self):
        self.connect()
        self.app.start_sequencer()
        self.assertFalse(self.app.seq_on)
        self.assertIsNone(self.runner.started)

    def test_sequencer_loads_its_scene_into_ram_and_restores_the_users(self):
        self.start_sequencer()
        self.assertTrue(self.app.seq_on)
        self.assertEqual(self.runner.started, ("loopMIDI notes", "loopMIDI clock"))
        self.assertEqual(self.link.saved[-1], False)
        self.assertEqual(self.link.pushed[-1].led_mode, 1)
        self.assertEqual(self.link.listener, self.runner.device_message)
        self.link.events.put(("written", self.link.pushed[-1]))
        self.app.tick()
        self.assertIn(("led", "play", False), self.runner.actions)
        self.app.toggle_sequencer()
        self.assertFalse(self.app.seq_on)
        self.assertIsNone(self.link.listener)
        self.assertIs(self.link.pushed[-1], self.app.scene)
        self.assertEqual(self.link.saved[-1], False)

    def test_scene_edits_wait_while_sequencing(self):
        self.start_sequencer()
        pushes = len(self.link.pushed)
        self.app.set_ctrl(0, "slider", max_val=90)
        self.app.send_to_device()
        self.assertEqual(len(self.link.pushed), pushes)
        self.assertEqual(self.app.scene.groups[0].slider.max_val, 127)

    def test_sequencer_stops_when_the_device_goes_away(self):
        self.start_sequencer()
        self.link.events.put(("searching", None))
        self.app.tick()
        self.assertFalse(self.app.seq_on)
        self.assertIsNone(self.runner.started)

    def test_sequencer_grid_edits_the_pattern_and_is_saved(self):
        self.connect()
        self.app.show_section("sequencer")
        page = self.page("sequencer")
        self.app.seq_toggle(2, 5)
        self.app.seq_solo(1)
        self.app.tick()
        self.assertTrue(self.app.seq.steps[2][5])
        self.assertEqual(page.cells[2][5].style, "led")
        self.assertEqual(page.solos[1].style, "led")
        self.now += gui.SEQ_SAVE_SECONDS + 1
        self.app.tick()
        self.assertEqual(self.seq_saves[-1]["pattern"]["steps"][2][5], 1)

    def test_send_button_pushes_the_whole_scene_now(self):
        self.connect()
        before = len(self.link.pushed)
        self.app.send_to_device()
        self.assertEqual(len(self.link.pushed), before + 1)
        self.assertIs(self.link.pushed[-1], self.app.scene)

    def test_changing_a_range_forgets_its_measurement(self):
        self.connect()
        self.link.incoming.put((1.0, 0xB0, 0, 50))
        self.app.tick()
        self.app.set_ctrl(0, "slider", max_val=100)
        self.assertNotIn((0, "slider"), self.app.seen)

    def test_pressing_a_button_lights_its_row_indicator(self):
        self.connect()
        row = self.page("ch1").rows["solo"]
        self.link.incoming.put((1.0, 0xB0, 32, 127))
        self.app.tick()
        self.assertTrue(row.dot.on)
        self.now += 1
        self.app.tick()
        self.assertFalse(row.dot.on)

    def test_other_strips_do_not_light_the_visible_page(self):
        self.connect()
        row = self.page("ch1").rows["solo"]
        self.link.incoming.put((1.0, 0xB0, 33, 127))
        self.app.tick()
        self.assertFalse(row.dot.on)

    def test_learn_takes_type_and_number_from_the_device(self):
        self.connect()
        row = self.page("ch1").rows["solo"]
        row._on_learn()
        self.assertTrue(self.link.learning and row.learning)
        self.link.events.put(("learned", ("note", 60)))
        self.app.tick()
        solo = self.app.scene.groups[0].solo
        self.assertEqual((solo.assign, solo.cmd), (2, 60))
        self.assertFalse(row.learning)

    def test_leaving_a_page_cancels_learn(self):
        self.connect()
        row = self.page("ch1").rows["solo"]
        row._on_learn()
        self.app.show_section("global")
        self.assertFalse(row.learning or self.link.learning)

    def test_monitor_shows_new_messages_and_clears(self):
        self.connect()
        self.link.incoming.put((1.0, 0xB0, 5, 99))
        self.app.tick()
        page = self.page("monitor")
        self.assertIn("CC", page.log.get("1.0", "end"))
        self.link.incoming.put((2.0, 0x90, 36, 100))
        self.app.tick()
        self.assertIn("Note On", page.log.get("1.0", "2.0"))
        self.app.clear_monitor()
        self.assertEqual(page.log.get("1.0", "end").strip(), "")

    def test_monitor_log_is_bounded(self):
        self.connect()
        page = self.page("monitor")
        for i in range(gui.MONITOR_KEEP + 50):
            self.link.incoming.put((float(i), 0xB0, i % 128, 1))
        self.app.tick()
        self.assertEqual(len(self.app.monitor_log), gui.MONITOR_KEEP)
        self.assertLessEqual(int(page.log.index("end-1c").split(".")[0]), pages.MonitorPage.MAX_LINES + 1)

    # --- LEDs ---

    def test_led_buttons_send_the_buttons_own_message(self):
        self.connect()
        self.app.toggle_led("play")
        self.app.toggle_led("play")
        self.assertEqual(self.link.leds, [(0xB0, 0x29, 0x7F), (0xB0, 0x29, 0x00)])
        self.link.leds.clear()
        self.app.set_all_leds(True)
        self.assertEqual(len(self.link.leds), len(protocol.LED_BUTTONS))
        self.assertEqual(self.page("leds").buttons["solo_3"]._text, "On")

    def test_led_follows_channel_and_assignment(self):
        self.connect()
        self.app.set_global("global_channel", 4)
        self.app.set_ctrl(2, "mute", assign=2, cmd=60)
        self.app.toggle_led("mute_3")
        self.assertEqual(self.link.leds[-1], (0x94, 60, 0x7F))
        self.app.set_ctrl(2, "mute", assign=0)
        self.link.leds.clear()
        self.app.toggle_led("mute_3")
        self.assertEqual(self.link.leds, [])

    def test_led_page_warns_when_external_mode_is_off(self):
        self.connect()
        page = self.page("leds")
        self.assertIn("is off", page.itemcget(page._banner, "text"))
        self.app.toggle_led_mode()
        self.assertIn("is on", page.itemcget(page._banner, "text"))

    # --- files ---

    def test_save_and_open_round_trip(self):
        self.connect()
        self.app.set_ctrl(0, "slider", min_val=12, max_val=99)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "scene.json")
            self.files = [path]
            self.app.save_file()
            self.app.set_ctrl(0, "slider", min_val=0, max_val=127)
            self.files = [path]
            self.app.open_file()
        slider = self.app.scene.groups[0].slider
        self.assertEqual((slider.min_val, slider.max_val), (12, 99))

    def test_opening_a_bad_file_reports_the_error(self):
        self.connect()
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "bad.json")
            with open(path, "w") as f:
                f.write("{not json")
            self.files = [path]
            self.app.open_file()
        self.assertEqual(self.app._footer.itemcget(self.app._message, "fill"), theme.ERROR)

    def test_export_writes_a_csv_overview(self):
        self.connect()
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "cc.csv")
            self.files = [path]
            self.app.export_cheatsheet()
            with open(path, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.reader(f))
        self.assertEqual(len(rows), 1 + 51)

    # --- header ---

    def test_status_pill_follows_the_connection(self):
        self.link.linked = False
        self.app.tick()
        self.assertEqual(self.app._status._text, "Searching for device...")
        self.link.linked = True
        self.connect()
        self.link.has_pending = False
        self.app._chrome_state = None
        self.app.tick()
        self.assertEqual(self.app._status._text, "Connected")
        self.app.set_ctrl(0, "slider", cmd=9)
        self.app.tick()
        self.assertEqual(self.app._status._text, "Writing to device...")
        self.link.has_pending = False
        self.link.events.put(("written", self.app.scene))
        self.app.tick()
        self.assertEqual(self.app._status._text, "Saved to device")
        self.now += 5
        self.app.tick()
        self.assertEqual(self.app._status._text, "Connected")

    def test_failed_write_can_be_retried_from_the_pill(self):
        self.connect()
        self.link.events.put(("write_failed", "boom"))
        self.app.tick()
        self.assertEqual(self.app._status.style, "display_err")
        self.link.pushed.clear()
        self.app._on_status_click()
        self.assertEqual(len(self.link.pushed), 1)

    def test_actions_are_disabled_without_a_scene(self):
        self.assertFalse(self.app._save.enabled)
        self.assertFalse(self.app._templates.enabled)
        self.assertTrue(self.app._open.enabled)
        self.connect()
        self.assertTrue(self.app._save.enabled and self.app._templates.enabled)

    def test_device_scene_does_not_overwrite_pending_edits(self):
        self.connect()
        self.app.set_ctrl(0, "slider", cmd=55)
        self.link.events.put(("settings", Scene()))
        self.app.tick()
        self.assertEqual(self.app.scene.groups[0].slider.cmd, 55)


if __name__ == "__main__":
    unittest.main()
