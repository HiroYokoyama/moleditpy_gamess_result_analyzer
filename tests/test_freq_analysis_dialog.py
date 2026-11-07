"""
tests/test_freq_analysis_dialog.py
Coverage for freq_analysis under the headless Qt harness: FrequencyDialog
(mode list construction, frequency scaling and presets, manual displacement,
animation, settings) and FreqSpectrumWindow (IR/Raman switching, scaling
propagation, axis ranges, exports).

Kept separate from the existing tests/test_freq_analysis.py, which installs its
own partial PySide6 stubs process-wide; this module drives the real dialog code
through gui_harness instead.

``settings_file`` points inside the installed package, so every test redirects
it into a temp dir — the suite must never write to the source tree.
"""

import os
import sys
import json
import math
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

F = gui_harness.load_isolated("freq_analysis")


# ---------------------------------------------------------------------------
# Stateful tree stand-ins (the shared stubs return MagicMock, but the source
# round-trips item text: populate_list writes it, update_data reads it back).
# ---------------------------------------------------------------------------


class _FakeTreeItem:
    def __init__(self, texts=None):
        self._texts = list(texts or [])
        self.colors = {}

    def text(self, col):
        return self._texts[col] if col < len(self._texts) else ""

    def setText(self, col, value):
        while len(self._texts) <= col:
            self._texts.append("")
        self._texts[col] = value

    def setForeground(self, col, color):
        self.colors[col] = color


class _FakeTree:
    def __init__(self):
        self._items = []
        self.cleared = 0

    def clear(self):
        self._items = []
        self.cleared += 1

    def addTopLevelItem(self, item):
        self._items.append(item)

    def topLevelItemCount(self):
        return len(self._items)

    def topLevelItem(self, i):
        return self._items[i]

    def invisibleRootItem(self):
        return self

    def childCount(self):
        return len(self._items)

    def child(self, i):
        return self._items[i]

    def __getattr__(self, name):
        return MagicMock()


def _frequencies(n=None):
    """Six near-zero trivial modes followed by three real vibrations."""
    trivial = [
        {"freq": v, "ir": 0.0, "raman": 0.0, "vector": [[0.0, 0.0, 0.0]] * 3}
        for v in (0.01, 0.02, 0.03, 0.04, 0.05, 0.06)
    ]
    real = [
        {
            "freq": -120.5,  # imaginary mode
            "ir": 12.0,
            "raman": 1.0,
            "vector": [[0.1, 0.0, 0.0], [0.0, 0.1, 0.0], [0.0, 0.0, 0.1]],
        },
        {
            "freq": 1600.0,
            "ir": 55.0,
            "raman": 3.0,
            "vector": [[0.0, 0.1, 0.0]] * 3,
        },
        {
            "freq": 3200.0,
            "ir": 5.0,
            "raman": 9.0,
            "vector": [[0.0, 0.0, 0.1]] * 3,
        },
    ]
    out = trivial + real
    return out if n is None else out[:n]


class _FreqCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.settings_path = os.path.join(self.tmp, "settings.json")

        self.conf = MagicMock()
        self.mol = MagicMock()
        self.mol.GetConformer.return_value = self.conf
        self.context = MagicMock()

        self.mw = MagicMock()
        self.mw.current_mol = self.mol
        self.mw.file_path = os.path.join(self.tmp, "job.out")

        self.atoms = ["O", "H", "H"]
        self.coords = [[0.0, 0.0, 0.0], [0.76, 0.59, 0.0], [-0.76, 0.59, 0.0]]

        self.dlg = F.FrequencyDialog(
            self.mw, _frequencies(), self.atoms, self.coords, self.context
        )
        self.dlg.settings_file = self.settings_path
        self.dlg.parent = lambda: self.mw

        # Give the dialog a tree that actually stores what it is told.
        self.tree = _FakeTree()
        self.dlg.tree = self.tree

    def tearDown(self):
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# Mode list
# ---------------------------------------------------------------------------


class TestPopulateList(_FreqCase):
    def setUp(self):
        super().setUp()
        self._saved_item = F.QTreeWidgetItem
        F.QTreeWidgetItem = _FakeTreeItem

    def tearDown(self):
        F.QTreeWidgetItem = self._saved_item
        super().tearDown()

    def test_six_trivial_modes_are_hidden(self):
        self.dlg.populate_list()
        self.assertEqual(self.tree.topLevelItemCount(), 3)

    def test_listed_modes_keep_their_original_indices(self):
        self.dlg.populate_list()
        shown = [self.tree.topLevelItem(i).text(0) for i in range(3)]
        self.assertEqual(shown, ["6", "7", "8"])

    def test_five_trivial_modes_are_hidden_for_a_linear_molecule(self):
        freqs = _frequencies()
        freqs[5]["freq"] = 600.0  # only the first five are trivial
        dlg = F.FrequencyDialog(self.mw, freqs, self.atoms, self.coords, self.context)
        dlg.tree = _FakeTree()
        dlg.populate_list()
        self.assertEqual(dlg.tree.topLevelItemCount(), 4)

    def test_nothing_is_hidden_when_no_modes_are_trivial(self):
        freqs = _frequencies()
        for f in freqs[:6]:
            f["freq"] = 500.0
        dlg = F.FrequencyDialog(self.mw, freqs, self.atoms, self.coords, self.context)
        dlg.tree = _FakeTree()
        dlg.populate_list()
        self.assertEqual(dlg.tree.topLevelItemCount(), 9)

    def test_short_mode_lists_are_shown_in_full(self):
        dlg = F.FrequencyDialog(
            self.mw, _frequencies(4), self.atoms, self.coords, self.context
        )
        dlg.tree = _FakeTree()
        dlg.populate_list()
        self.assertEqual(dlg.tree.topLevelItemCount(), 4)

    def test_unscaled_column_shows_the_raw_frequency(self):
        self.dlg.spin_sf_a.setValue(0.95)
        self.dlg.spin_sf_b.setValue(0.0)
        self.dlg.populate_list()
        self.assertEqual(self.tree.topLevelItem(1).text(2), "1600.00")

    def test_scaled_column_applies_the_factor(self):
        self.dlg.spin_sf_a.setValue(0.95)
        self.dlg.spin_sf_b.setValue(0.0)
        self.dlg.populate_list()
        self.assertEqual(self.tree.topLevelItem(1).text(1), "1520.00")

    def test_scaled_column_applies_the_offset(self):
        self.dlg.spin_sf_a.setValue(1.0)
        self.dlg.spin_sf_b.setValue(-25.0)
        self.dlg.populate_list()
        self.assertEqual(self.tree.topLevelItem(1).text(1), "1575.00")

    def test_ir_and_raman_columns_are_filled(self):
        self.dlg.populate_list()
        item = self.tree.topLevelItem(1)
        self.assertEqual(item.text(3), "55.00")
        self.assertEqual(item.text(4), "3.00")

    def test_imaginary_modes_are_coloured(self):
        self.dlg.populate_list()
        self.assertEqual(len(self.tree.topLevelItem(0).colors), 5)

    def test_repopulating_clears_the_previous_rows(self):
        self.dlg.populate_list()
        self.dlg.populate_list()
        self.assertEqual(self.tree.topLevelItemCount(), 3)


# ---------------------------------------------------------------------------
# Scaling
# ---------------------------------------------------------------------------


class TestScaling(_FreqCase):
    def setUp(self):
        super().setUp()
        self._saved_item = F.QTreeWidgetItem
        F.QTreeWidgetItem = _FakeTreeItem
        self.dlg.populate_list()

    def tearDown(self):
        F.QTreeWidgetItem = self._saved_item
        super().tearDown()

    def test_update_data_rescales_the_shown_column(self):
        self.dlg.spin_sf_a.setValue(0.5)
        self.dlg.spin_sf_b.setValue(0.0)
        self.dlg.update_data()
        self.assertEqual(self.tree.topLevelItem(1).text(1), "800.00")

    def test_update_data_leaves_the_unscaled_column_alone(self):
        self.dlg.spin_sf_a.setValue(0.5)
        self.dlg.update_data()
        self.assertEqual(self.tree.topLevelItem(1).text(2), "1600.00")

    def test_scaling_never_reclassifies_an_imaginary_mode(self):
        # A large positive offset makes the displayed value positive, but the
        # mode is imaginary by its *unscaled* frequency and stays coloured.
        self.dlg.spin_sf_a.setValue(1.0)
        self.dlg.spin_sf_b.setValue(500.0)
        self.dlg.update_data()
        item = self.tree.topLevelItem(0)
        self.assertEqual(item.text(1), "379.50")
        self.assertEqual(len(item.colors), 5)

    def test_update_data_forwards_scaling_to_the_spectrum_window(self):
        self.dlg.spectrum_win = MagicMock()
        self.dlg.spin_sf_a.setValue(0.97)
        self.dlg.spin_sf_b.setValue(2.0)
        self.dlg.update_data()
        self.dlg.spectrum_win.set_scaling_params.assert_called_once_with(0.97, 2.0)

    def test_update_data_without_a_spectrum_window_is_fine(self):
        self.dlg.spectrum_win = None
        self.dlg.update_data()  # must not raise


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


class TestPresets(_FreqCase):
    def test_unscaled_is_listed_first(self):
        self.dlg.update_preset_combo()
        self.assertEqual(self.dlg.combo_preset.itemText(0), "Unscaled")

    def test_manual_is_listed_second(self):
        self.dlg.update_preset_combo()
        self.assertEqual(self.dlg.combo_preset.itemText(1), "Manual")

    def test_custom_presets_are_listed(self):
        self.dlg.custom_presets["MyScale"] = {"a": 0.5, "b": 1.0}
        self.dlg.update_preset_combo()
        names = [
            self.dlg.combo_preset.itemText(i)
            for i in range(self.dlg.combo_preset.count())
        ]
        self.assertIn("MyScale", names)

    def test_applying_a_preset_sets_both_coefficients(self):
        self.dlg.custom_presets["MyScale"] = {"a": 0.9, "b": 3.0}
        self.dlg.update_preset_combo()
        self.dlg.combo_preset.setCurrentText("MyScale")
        self.dlg.apply_preset()
        self.assertAlmostEqual(self.dlg.spin_sf_a.value(), 0.9)
        self.assertAlmostEqual(self.dlg.spin_sf_b.value(), 3.0)

    def test_manual_preset_leaves_the_coefficients_editable(self):
        self.dlg.update_preset_combo()
        self.dlg.combo_preset.setCurrentText("Manual")
        self.dlg.spin_sf_a.setEnabled = MagicMock()
        self.dlg.apply_preset()
        self.dlg.spin_sf_a.setEnabled.assert_called_with(True)

    def test_named_preset_locks_the_coefficients(self):
        self.dlg.update_preset_combo()
        self.dlg.combo_preset.setCurrentText("Unscaled")
        self.dlg.spin_sf_a.setEnabled = MagicMock()
        self.dlg.apply_preset()
        self.dlg.spin_sf_a.setEnabled.assert_called_with(False)

    def test_unscaled_preset_is_the_identity(self):
        self.dlg.update_preset_combo()
        self.dlg.combo_preset.setCurrentText("Unscaled")
        self.dlg.apply_preset()
        self.assertAlmostEqual(self.dlg.spin_sf_a.value(), 1.0)
        self.assertAlmostEqual(self.dlg.spin_sf_b.value(), 0.0)

    @staticmethod
    def _name_prompt(name, accepted):
        """save_custom_preset imports QInputDialog lazily, inside the method."""
        prompt = MagicMock()
        prompt.getText.return_value = (name, accepted)
        return gui_harness.qt_available(QInputDialog=prompt)

    def test_saving_a_custom_preset_records_the_coefficients(self):
        self.dlg.spin_sf_a.setValue(0.98)
        self.dlg.spin_sf_b.setValue(-5.0)
        with self._name_prompt("Mine", True):
            self.dlg.save_custom_preset()
        self.assertEqual(self.dlg.custom_presets["Mine"], {"a": 0.98, "b": -5.0})

    def test_a_custom_preset_cannot_shadow_a_default(self):
        default_name = next(iter(self.dlg.default_presets))
        with self._name_prompt(default_name, True):
            with patch.object(F.QMessageBox, "warning") as warn:
                self.dlg.save_custom_preset()
        warn.assert_called_once()
        self.assertNotIn(default_name, self.dlg.custom_presets)

    def test_cancelling_the_name_prompt_saves_nothing(self):
        with self._name_prompt("", False):
            self.dlg.save_custom_preset()
        self.assertEqual(self.dlg.custom_presets, {})


# ---------------------------------------------------------------------------
# Manual displacement / animation
# ---------------------------------------------------------------------------


class TestDisplacement(_FreqCase):
    def _positions(self):
        return [c.args for c in self.conf.SetAtomPosition.call_args_list]

    def test_no_displacement_without_a_selected_mode(self):
        self.dlg.current_mode_idx = -1
        self.dlg.apply_manual_displacement()
        self.conf.SetAtomPosition.assert_not_called()

    def test_displacement_moves_every_atom(self):
        self.dlg.current_mode_idx = 6
        self.dlg.spin_amp.setValue(1.0)
        self.dlg.slider_displ.setValue(100)
        self.dlg.apply_manual_displacement()
        self.assertEqual(self.conf.SetAtomPosition.call_count, 3)

    def test_displacement_is_the_base_geometry_plus_the_scaled_mode(self):
        self.dlg.current_mode_idx = 6
        self.dlg.spin_amp.setValue(2.0)
        self.dlg.slider_displ.setValue(50)  # -> 0.5 * amp = factor 1.0
        self.dlg.apply_manual_displacement()
        idx, point = self._positions()[0]
        self.assertEqual(idx, 0)
        # atom 0 base (0,0,0) + vector (0.1,0,0) * 1.0
        self.assertAlmostEqual(point.x, 0.1)

    def test_zero_slider_leaves_the_geometry_at_rest(self):
        self.dlg.current_mode_idx = 6
        self.dlg.slider_displ.setValue(0)
        self.dlg.apply_manual_displacement()
        _, point = self._positions()[0]
        self.assertAlmostEqual(point.x, 0.0)

    def test_negative_slider_displaces_the_other_way(self):
        self.dlg.current_mode_idx = 6
        self.dlg.spin_amp.setValue(1.0)
        self.dlg.slider_displ.setValue(-100)
        self.dlg.apply_manual_displacement()
        _, point = self._positions()[0]
        self.assertAlmostEqual(point.x, -0.1)

    def test_displacement_redraws_through_the_plugin_context(self):
        self.dlg.current_mode_idx = 6
        self.dlg.apply_manual_displacement()
        self.context.draw_molecule_3d.assert_called_with(self.mol)

    def test_displacement_falls_back_to_the_view_manager(self):
        dlg = F.FrequencyDialog(
            self.mw, _frequencies(), self.atoms, self.coords, context=None
        )
        dlg.current_mode_idx = 6
        dlg.apply_manual_displacement()
        self.mw.view_3d_manager.draw_molecule_3d.assert_called_with(self.mol)

    def test_a_mode_without_vectors_is_skipped(self):
        freqs = _frequencies()
        freqs[6]["vector"] = []
        dlg = F.FrequencyDialog(self.mw, freqs, self.atoms, self.coords, self.context)
        dlg.current_mode_idx = 6
        dlg.apply_manual_displacement()
        self.conf.SetAtomPosition.assert_not_called()

    def test_reset_restores_the_base_geometry(self):
        self.dlg.reset_geometry()
        points = [c.args[1] for c in self.conf.SetAtomPosition.call_args_list]
        self.assertEqual(len(points), 3)
        self.assertAlmostEqual(points[1].x, 0.76)

    def test_animation_advances_the_phase(self):
        self.dlg.current_mode_idx = 6
        before = self.dlg.animation_step
        self.dlg.animate_frame()
        self.assertEqual(self.dlg.animation_step, before + 1)

    def test_animation_follows_a_sine_displacement(self):
        self.dlg.current_mode_idx = 6
        self.dlg.spin_amp.setValue(1.0)
        self.dlg.animation_step = 0
        self.dlg.animate_frame()  # step 1 -> phase 0.2
        _, point = self._positions()[0]
        self.assertAlmostEqual(point.x, 0.1 * math.sin(0.2))

    def test_animation_without_a_mode_does_nothing(self):
        self.dlg.current_mode_idx = -1
        self.dlg.animate_frame()
        self.conf.SetAtomPosition.assert_not_called()

    def test_play_starts_the_timer(self):
        self.dlg.current_mode_idx = 6
        self.dlg.timer = MagicMock()
        self.dlg.spin_fps.setValue(20)
        self.dlg.start_animation()
        self.assertTrue(self.dlg.is_playing)
        self.dlg.timer.start.assert_called_once_with(50)

    def test_play_is_ignored_without_a_mode(self):
        self.dlg.current_mode_idx = -1
        self.dlg.timer = MagicMock()
        self.dlg.start_animation()
        self.assertFalse(self.dlg.is_playing)
        self.dlg.timer.start.assert_not_called()

    def test_play_twice_does_not_restart_the_timer(self):
        self.dlg.current_mode_idx = 6
        self.dlg.timer = MagicMock()
        self.dlg.start_animation()
        self.dlg.start_animation()
        self.dlg.timer.start.assert_called_once()

    def test_pause_stops_the_timer_but_keeps_the_phase(self):
        self.dlg.current_mode_idx = 6
        self.dlg.timer = MagicMock()
        self.dlg.start_animation()
        self.dlg.animation_step = 7
        self.dlg.pause_animation()
        self.assertFalse(self.dlg.is_playing)
        self.assertEqual(self.dlg.animation_step, 7)

    def test_stop_resets_the_phase_and_geometry(self):
        self.dlg.current_mode_idx = 6
        self.dlg.timer = MagicMock()
        self.dlg.start_animation()
        self.dlg.animation_step = 7
        self.dlg.stop_animation()
        self.assertFalse(self.dlg.is_playing)
        self.assertEqual(self.dlg.animation_step, 0)
        self.conf.SetAtomPosition.assert_called()

    def test_fps_change_retimes_a_running_animation(self):
        self.dlg.current_mode_idx = 6
        self.dlg.timer = MagicMock()
        self.dlg.start_animation()
        self.dlg.spin_fps.setValue(50)
        self.dlg.update_fps()
        self.dlg.timer.setInterval.assert_called_once_with(20)

    def test_fps_change_is_ignored_while_paused(self):
        self.dlg.timer = MagicMock()
        self.dlg.is_playing = False
        self.dlg.update_fps()
        self.dlg.timer.setInterval.assert_not_called()

    def test_manual_mode_stops_a_running_animation(self):
        self.dlg.current_mode_idx = 6
        self.dlg.timer = MagicMock()
        self.dlg.start_animation()
        self.dlg.chk_manual_displ.setChecked(True)
        self.dlg.toggle_manual_displacement(True)
        self.assertFalse(self.dlg.is_playing)

    def test_leaving_manual_mode_recentres_the_slider(self):
        self.dlg.slider_displ.setValue(80)
        self.dlg.toggle_manual_displacement(False)
        self.assertEqual(self.dlg.slider_displ.value(), 0)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestSettings(_FreqCase):
    def test_save_writes_the_scaling_state(self):
        self.dlg.spin_sf_a.setValue(0.97)
        self.dlg.spin_sf_b.setValue(-3.0)
        self.dlg.save_settings()
        saved = json.load(open(self.settings_path, encoding="utf-8"))["freq_settings"]
        self.assertAlmostEqual(saved["sf_a"], 0.97)
        self.assertAlmostEqual(saved["sf_b"], -3.0)

    def test_custom_presets_survive_a_round_trip(self):
        self.dlg.custom_presets["Mine"] = {"a": 0.9, "b": 1.0}
        self.dlg.save_settings()

        fresh = F.FrequencyDialog(
            self.mw, _frequencies(), self.atoms, self.coords, self.context
        )
        fresh.settings_file = self.settings_path
        fresh.load_settings()
        self.assertEqual(fresh.custom_presets["Mine"], {"a": 0.9, "b": 1.0})

    def test_scaling_survives_a_round_trip(self):
        # Under "Manual" the stored coefficients are authoritative; a named
        # preset would (correctly) re-impose its own values on load.
        self.dlg.update_preset_combo()
        self.dlg.combo_preset.setCurrentText("Manual")
        self.dlg.spin_sf_a.setValue(0.955)
        self.dlg.spin_sf_b.setValue(12.0)
        self.dlg.save_settings()

        fresh = F.FrequencyDialog(
            self.mw, _frequencies(), self.atoms, self.coords, self.context
        )
        fresh.settings_file = self.settings_path
        fresh.load_settings()
        self.assertAlmostEqual(fresh.spin_sf_a.value(), 0.955)
        self.assertAlmostEqual(fresh.spin_sf_b.value(), 12.0)

    def test_a_legacy_single_scale_factor_is_still_read(self):
        with open(self.settings_path, "w", encoding="utf-8") as fh:
            json.dump({"freq_settings": {"sf": 0.89}}, fh)
        self.dlg.load_settings()
        self.assertAlmostEqual(self.dlg.spin_sf_a.value(), 0.89)

    def test_vector_appearance_survives_a_round_trip(self):
        self.dlg.vector_color = "#123456"
        self.dlg.spin_vec_res.setValue(12)
        self.dlg.save_settings()

        fresh = F.FrequencyDialog(
            self.mw, _frequencies(), self.atoms, self.coords, self.context
        )
        fresh.settings_file = self.settings_path
        fresh.load_settings()
        self.assertEqual(fresh.vector_color, "#123456")
        self.assertEqual(fresh.spin_vec_res.value(), 12)

    def test_save_preserves_unrelated_sections(self):
        with open(self.settings_path, "w", encoding="utf-8") as fh:
            json.dump({"nmr_settings": {"ref": 31.7}}, fh)
        self.dlg.save_settings()
        saved = json.load(open(self.settings_path, encoding="utf-8"))
        self.assertEqual(saved["nmr_settings"], {"ref": 31.7})

    def test_corrupt_settings_are_ignored(self):
        with open(self.settings_path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.dlg.spin_sf_a.setValue(0.5)
        self.dlg.load_settings()  # must not raise
        self.assertAlmostEqual(self.dlg.spin_sf_a.value(), 0.5)

    def test_missing_settings_file_is_ignored(self):
        self.dlg.settings_file = os.path.join(self.tmp, "nope.json")
        self.dlg.load_settings()  # must not raise

    def test_save_preserves_several_other_dialogs_sections(self):
        others = {
            "mo_settings": {"iso": 0.02},
            "nmr_settings": {"ref": "TMS"},
            "thermal_settings": {"show_details": True},
        }
        with open(self.settings_path, "w", encoding="utf-8") as fh:
            json.dump(others, fh)

        self.dlg.spin_sf_a.setValue(0.96)
        self.dlg.save_settings()

        with open(self.settings_path, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        for key, val in others.items():
            self.assertEqual(on_disk[key], val)
        self.assertAlmostEqual(on_disk["freq_settings"]["sf_a"], 0.96)

    def test_round_trips_through_the_shared_helpers(self):
        S = gui_harness.load_isolated("settings")
        self.dlg.spin_sf_a.setValue(0.93)
        self.dlg.save_settings()

        section = S.load_section(self.settings_path, "freq_settings")
        self.assertAlmostEqual(section["sf_a"], 0.93)

    def test_escape_routes_through_close(self):
        with patch.object(self.dlg, "close") as closer:
            self.dlg.reject()
        closer.assert_called_once()


# ---------------------------------------------------------------------------
# Spectrum window
# ---------------------------------------------------------------------------


class _SpectrumCase(_FreqCase):
    def setUp(self):
        super().setUp()
        self.win = F.FreqSpectrumWindow(self.dlg, _frequencies())
        self.win.settings_file = self.settings_path


class TestSpectrumWindow(_SpectrumCase):
    def test_scaling_is_applied_to_the_plotted_frequencies(self):
        self.win.set_scaling_params(0.5, 0.0)
        plotted = {round(d["freq"], 2) for d in self.win.spectrum.data_list}
        self.assertIn(800.0, plotted)

    def test_scaling_offset_is_applied(self):
        self.win.set_scaling_params(1.0, 10.0)
        plotted = {round(d["freq"], 2) for d in self.win.spectrum.data_list}
        self.assertIn(1610.0, plotted)

    def test_each_point_keeps_its_original_index(self):
        self.win.update_data()
        indices = [d["_orig_idx"] for d in self.win.spectrum.data_list]
        self.assertEqual(indices, list(range(9)))

    def test_scaling_does_not_mutate_the_source_frequencies(self):
        original = self.win.frequencies[7]["freq"]
        self.win.set_scaling_params(0.5, 0.0)
        self.assertEqual(self.win.frequencies[7]["freq"], original)

    def test_ir_mode_plots_the_ir_intensities(self):
        self.win.radio_raman.setChecked(False)
        self.win.switch_spectrum_type()
        self.assertEqual(self.win.spectrum.y_key, "ir")

    def test_raman_mode_plots_the_raman_activities(self):
        self.win.radio_raman.setChecked(True)
        self.win.switch_spectrum_type()
        self.assertEqual(self.win.spectrum.y_key, "raman")

    def test_ir_axes_are_labelled(self):
        self.win.radio_raman.setChecked(False)
        self.win.switch_spectrum_type()
        self.assertIn("km/mol", self.win.spectrum.y_unit)

    def test_raman_axes_are_labelled(self):
        self.win.radio_raman.setChecked(True)
        self.win.switch_spectrum_type()
        self.assertIn("Raman", self.win.spectrum.x_unit)

    def test_ir_inverts_both_axes(self):
        self.win.radio_raman.setChecked(False)
        self.win.switch_spectrum_type()
        self.assertTrue(self.win.spectrum.invert_x)
        self.assertTrue(self.win.spectrum.invert_y)

    def test_raman_inverts_only_the_x_axis(self):
        self.win.radio_raman.setChecked(True)
        self.win.switch_spectrum_type()
        self.assertTrue(self.win.spectrum.invert_x)
        self.assertFalse(self.win.spectrum.invert_y)

    def test_auto_y_clears_the_manual_range(self):
        self.win.chk_auto_y.setChecked(True)
        self.win.toggle_auto_y()
        self.assertIsNone(self.win.spectrum.y_range)

    def test_manual_y_applies_the_spin_values(self):
        self.win.chk_auto_y.setChecked(False)
        self.win.spin_y_min.setValue(0.0)
        self.win.spin_y_max.setValue(80.0)
        self.win.toggle_auto_y()
        self.assertEqual(self.win.spectrum.y_range, (0.0, 80.0))

    def test_auto_x_clears_the_manual_range(self):
        self.win.chk_auto_x.setChecked(True)
        self.win.toggle_auto_x()
        self.assertIsNone(self.win.spectrum.x_range)

    def test_leaving_auto_x_restores_the_standard_ir_window(self):
        self.win.radio_raman.setChecked(False)
        self.win.switch_spectrum_type()
        self.win.chk_auto_x.setChecked(False)
        self.win.toggle_auto_x()
        self.assertEqual(self.win.spectrum.x_range, (400, 4000))

    def test_clicking_a_peak_selects_the_mode_in_the_dialog(self):
        item = {"_orig_idx": 7}
        with patch.object(self.dlg, "select_mode_by_item") as sel:
            self.win.on_spectrum_clicked(item)
        sel.assert_called_once_with(item)

    def test_save_csv_writes_a_broadened_curve(self):
        path = os.path.join(self.tmp, "spec.csv")
        with patch.object(F.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.win.save_csv()
        self.assertTrue(os.path.exists(path))

    def test_save_sticks_writes_one_row_per_mode(self):
        path = os.path.join(self.tmp, "sticks.csv")
        self.win.update_data()
        with patch.object(F.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.win.save_sticks()
        with open(path, encoding="utf-8") as fh:
            rows = fh.read().strip().splitlines()
        # header + the modes carrying non-zero IR intensity
        self.assertEqual(len(rows), 4)

    def test_cancelling_an_export_writes_nothing(self):
        with patch.object(F.QFileDialog, "getSaveFileName", return_value=("", "")):
            self.win.save_csv()
            self.win.save_sticks()
        self.assertFalse([f for f in os.listdir(self.tmp) if f.endswith(".csv")])


class TestOpenSpectrum(_FreqCase):
    def test_opening_creates_the_window_once(self):
        self.dlg.spectrum_win = None
        self.dlg.open_spectrum()
        first = self.dlg.spectrum_win
        self.dlg.open_spectrum()
        self.assertIs(self.dlg.spectrum_win, first)

    def test_the_new_window_inherits_the_current_scaling(self):
        self.dlg.spectrum_win = None
        self.dlg.spin_sf_a.setValue(0.9)
        self.dlg.spin_sf_b.setValue(1.0)
        self.dlg.open_spectrum()
        self.assertAlmostEqual(self.dlg.spectrum_win.scaling_a, 0.9)
        self.assertAlmostEqual(self.dlg.spectrum_win.scaling_b, 1.0)


if __name__ == "__main__":
    unittest.main()
