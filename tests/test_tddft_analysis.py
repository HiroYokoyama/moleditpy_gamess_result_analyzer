"""
tests/test_tddft_analysis.py
Coverage for TDDFTDialog: construction, absorption/CD spectrum switching and
the physical-units conversions, sigma unit handling, auto/manual axis ranges,
the settings round-trip, and the PNG/CSV/stick/GAMESS-report exporters.

The dialog builds a real SpectrumWidget (matplotlib against a stub canvas), so
these tests exercise spectrum_widget's plotting and CSV paths too.

``settings_file`` normally points inside the installed package, so every test
redirects it into a temp dir — the suite must never write to the source tree.
"""

import os
import sys
import csv
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

T = gui_harness.load_isolated("tddft_analysis")

ABS_FACTOR = 2.315e8
CD_FACTOR = 0.04355


def _excitations():
    return [
        {
            "state": 1,
            "energy_ev": 3.0996,
            "energy_nm": 400.0,
            "energy_cm": 25000.0,
            "osc": 0.05,
            "osc_len": 0.05,
            "osc_vel": 0.045,
            "rot_len": 12.0,
            "rot_vel": 11.0,
            "transitions": ["HOMO -> LUMO  (95.0%)"],
        },
        {
            "state": 2,
            "energy_ev": 4.0,
            "energy_nm": 310.0,
            "energy_cm": 32258.0,
            "osc": 0.20,
            "osc_len": 0.20,
            "osc_vel": 0.19,
            "rot_len": -8.0,
            "rot_vel": -7.5,
            "transitions": ["HOMO-1 -> LUMO  (88.0%)"],
        },
    ]


class _TDDFTCase(unittest.TestCase):
    """Builds a dialog with a temp settings file and a fake host dialog."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.settings_path = os.path.join(self.tmp, "settings.json")

        self.context = MagicMock()
        self.host = MagicMock()
        self.host.context = self.context
        self.host.file_path = os.path.join(self.tmp, "job.out")

        self.dlg = T.TDDFTDialog(self.host, _excitations())
        self.dlg.parent = lambda: self.host
        self.dlg.settings_file = self.settings_path

    def tearDown(self):
        self._tmp.cleanup()

    def _out(self, name):
        return os.path.join(self.tmp, name)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction(_TDDFTCase):
    def test_excitations_are_retained(self):
        self.assertEqual(len(self.dlg.excitations), 2)

    def test_spectrum_widget_is_built(self):
        self.assertIsNotNone(self.dlg.spectrum)

    def test_spectrum_plots_against_wavelength(self):
        self.assertEqual(self.dlg.spectrum.x_key, "energy_nm")


    def test_broadening_happens_in_energy_space(self):
        # sigma is entered in cm-1 while the axis is nm, so broadening must be
        # done in energy space to keep line shapes correct
        self.assertTrue(self.dlg.spectrum.broaden_in_energy)

    def test_sigma_is_converted_from_fwhm(self):
        self.dlg.spin_sigma.setValue(3000.0)
        self.dlg.combo_sigma_unit.setCurrentIndex(0)
        self.dlg.update_spectrum_sigma()
        self.assertAlmostEqual(self.dlg.spectrum.sigma, 3000.0 / T.FWHM_TO_SIGMA)

    def test_the_rendered_band_actually_has_the_requested_fwhm(self):
        """SpectrumWidget's lineshape is exp(-((x-x0)/sigma)^2), so the
        conversion factor is 2*sqrt(ln2)=1.6651, not the GAMESS-distribution
        2.355. Using the latter made every band sqrt(2) too narrow."""
        import math

        fwhm = 3000.0
        self.dlg.spin_sigma.setValue(fwhm)
        self.dlg.combo_sigma_unit.setCurrentIndex(0)
        self.dlg.update_spectrum_sigma()

        sigma = self.dlg.spectrum.sigma
        # Half maximum of exp(-((x)/sigma)^2) is at x = sigma*sqrt(ln 2).
        half_width = sigma * math.sqrt(math.log(2))
        self.assertAlmostEqual(2 * half_width, fwhm, places=6)

    def test_no_excitations_still_constructs(self):
        dlg = T.TDDFTDialog(self.host, [])
        self.assertEqual(dlg.excitations, [])


# ---------------------------------------------------------------------------
# switch_spectrum_type — gauge / CD / physical-unit conversions
# ---------------------------------------------------------------------------


class TestSwitchSpectrumType(_TDDFTCase):
    def _configure(self, cd=False, physical=False, gauge=0):
        self.dlg.radio_cd.setChecked(cd)
        self.dlg.radio_abs.setChecked(not cd)
        self.dlg.chk_physical.setChecked(physical)
        self.dlg.combo_gauge.setCurrentIndex(gauge)
        self.dlg.switch_spectrum_type()
        return self.dlg.spectrum

    def test_absorption_length_gauge_uses_osc_len(self):
        spec = self._configure(cd=False, gauge=0)
        self.assertEqual(spec.y_key_sticks, "osc_len")


    def test_arbitrary_units_pass_the_raw_value_through(self):
        spec = self._configure(cd=False, physical=False)
        self.assertAlmostEqual(spec.data_list[0]["processed_y"], 0.05)

    def test_physical_absorption_applies_the_epsilon_factor(self):
        spec = self._configure(cd=False, physical=True)
        self.assertAlmostEqual(spec.data_list[0]["processed_y"], 0.05 * ABS_FACTOR)


    def test_missing_intensity_key_falls_back_to_zero(self):
        exc = [{"state": 1, "energy_nm": 400.0, "energy_cm": 25000.0}]
        dlg = T.TDDFTDialog(self.host, exc)
        dlg.chk_physical.setChecked(False)
        dlg.switch_spectrum_type()
        self.assertEqual(dlg.spectrum.data_list[0]["processed_y"], 0.0)

    def test_gauge_falls_back_when_the_split_key_is_absent(self):
        exc = [dict(e) for e in _excitations()]
        for e in exc:
            e.pop("osc_len")
            e.pop("osc_vel")
        dlg = T.TDDFTDialog(self.host, exc)
        dlg.switch_spectrum_type()
        self.assertEqual(dlg.spectrum.y_key_sticks, "osc")

    def test_physical_mode_normalises_by_area(self):
        spec = self._configure(physical=True)
        self.assertEqual(spec.normalization_mode, "area")

    def test_arbitrary_mode_normalises_by_height(self):
        spec = self._configure(physical=False)
        self.assertEqual(spec.normalization_mode, "height")

    def test_physical_mode_enables_the_second_axis(self):
        spec = self._configure(physical=True)
        self.assertTrue(spec.use_dual_axis)


    def test_source_data_is_not_mutated(self):
        original = _excitations()
        dlg = T.TDDFTDialog(self.host, original)
        dlg.chk_physical.setChecked(True)
        dlg.switch_spectrum_type()
        self.assertNotIn("processed_y", original[0])

    def test_empty_excitations_short_circuit(self):
        dlg = T.TDDFTDialog(self.host, [])
        dlg.switch_spectrum_type()  # must not raise

    def test_switching_refreshes_an_open_info_panel(self):
        self.dlg._current_item = self.dlg.excitations[0]
        with patch.object(self.dlg, "_populate_info_panel") as pop:
            self.dlg.switch_spectrum_type()
        pop.assert_called_once_with(self.dlg.excitations[0])

    def test_switching_skips_the_panel_when_nothing_is_selected(self):
        self.dlg._current_item = None
        with patch.object(self.dlg, "_populate_info_panel") as pop:
            self.dlg.switch_spectrum_type()
        pop.assert_not_called()


# ---------------------------------------------------------------------------
# Sigma units
# ---------------------------------------------------------------------------


class TestSigmaUnits(_TDDFTCase):
    def test_ev_input_is_converted_to_wavenumbers(self):
        self.dlg.spin_sigma.setValue(1.0)
        self.dlg.combo_sigma_unit.setCurrentIndex(1)  # eV
        self.dlg.update_spectrum_sigma()
        self.assertAlmostEqual(self.dlg.spectrum.sigma, 8065.544 / T.FWHM_TO_SIGMA)

    def test_switching_to_ev_rescales_the_displayed_value(self):
        self.dlg.spin_sigma.setValue(8065.544)
        self.dlg.prev_sigma_unit_idx = 0
        self.dlg.combo_sigma_unit.setCurrentIndex(1)
        self.dlg.on_sigma_unit_changed()
        self.assertAlmostEqual(self.dlg.spin_sigma.value(), 1.0, places=6)

    def test_switching_back_to_wavenumbers_restores_the_value(self):
        self.dlg.spin_sigma.setValue(1.0)
        self.dlg.prev_sigma_unit_idx = 1
        self.dlg.combo_sigma_unit.setCurrentIndex(0)
        self.dlg.on_sigma_unit_changed()
        self.assertAlmostEqual(self.dlg.spin_sigma.value(), 8065.544, places=3)

    def test_the_physical_width_survives_a_unit_switch(self):
        self.dlg.spin_sigma.setValue(8065.544)
        self.dlg.combo_sigma_unit.setCurrentIndex(0)
        self.dlg.prev_sigma_unit_idx = 0
        self.dlg.update_spectrum_sigma()
        before = self.dlg.spectrum.sigma

        self.dlg.combo_sigma_unit.setCurrentIndex(1)
        self.dlg.on_sigma_unit_changed()
        self.assertAlmostEqual(self.dlg.spectrum.sigma, before, places=3)

    def test_reselecting_the_same_unit_is_a_noop(self):
        self.dlg.spin_sigma.setValue(3000.0)
        self.dlg.prev_sigma_unit_idx = 0
        self.dlg.combo_sigma_unit.setCurrentIndex(0)
        self.dlg.on_sigma_unit_changed()
        self.assertAlmostEqual(self.dlg.spin_sigma.value(), 3000.0)

    def test_the_unit_index_is_remembered(self):
        self.dlg.prev_sigma_unit_idx = 0
        self.dlg.combo_sigma_unit.setCurrentIndex(1)
        self.dlg.on_sigma_unit_changed()
        self.assertEqual(self.dlg.prev_sigma_unit_idx, 1)


# ---------------------------------------------------------------------------
# Axis ranges
# ---------------------------------------------------------------------------


class TestAxisRanges(_TDDFTCase):
    def test_auto_y_clears_the_manual_range(self):
        self.dlg.chk_auto_y.setChecked(True)
        self.dlg.toggle_auto_y()
        self.assertIsNone(self.dlg.spectrum.y_range)

    def test_manual_y_applies_the_spin_values(self):
        self.dlg.chk_auto_y.setChecked(False)
        self.dlg.spin_y_min.setValue(-1.0)
        self.dlg.spin_y_max.setValue(5.0)
        self.dlg.toggle_auto_y()
        self.assertEqual(self.dlg.spectrum.y_range, (-1.0, 5.0))

    def test_auto_y_ignores_range_edits(self):
        self.dlg.chk_auto_y.setChecked(True)
        self.dlg.toggle_auto_y()
        self.dlg.spin_y_min.setValue(-9.0)
        self.dlg.update_range()
        self.assertIsNone(self.dlg.spectrum.y_range)

    def test_auto_x_clears_the_manual_range(self):
        self.dlg.chk_auto_x.setChecked(True)
        self.dlg.toggle_auto_x()
        self.assertIsNone(self.dlg.spectrum.x_range)

    def test_manual_x_applies_the_spin_values(self):
        self.dlg.chk_auto_x.setChecked(False)
        self.dlg.spin_x_min.setValue(250.0)
        self.dlg.spin_x_max.setValue(600.0)
        self.dlg.toggle_auto_x()
        self.assertEqual(self.dlg.spectrum.x_range, (250.0, 600.0))

    def test_auto_x_ignores_range_edits(self):
        self.dlg.chk_auto_x.setChecked(True)
        self.dlg.toggle_auto_x()
        self.dlg.update_x_range()
        self.assertIsNone(self.dlg.spectrum.x_range)

    def test_manual_mode_enables_the_spin_boxes(self):
        # Spy on setEnabled only, so the spin boxes keep returning real numbers
        # for the range plumbing that runs straight after.
        self.dlg.spin_y_min.setEnabled = MagicMock()
        self.dlg.spin_y_max.setEnabled = MagicMock()
        self.dlg.chk_auto_y.setChecked(False)
        self.dlg.toggle_auto_y()
        self.dlg.spin_y_min.setEnabled.assert_called_with(True)
        self.dlg.spin_y_max.setEnabled.assert_called_with(True)

    def test_auto_mode_disables_the_spin_boxes(self):
        self.dlg.spin_y_min.setEnabled = MagicMock()
        self.dlg.chk_auto_y.setChecked(True)
        self.dlg.toggle_auto_y()
        self.dlg.spin_y_min.setEnabled.assert_called_with(False)


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------


class TestSettings(_TDDFTCase):
    def test_save_writes_the_current_state(self):
        self.dlg.spin_sigma.setValue(1234.0)
        self.dlg.combo_sigma_unit.setCurrentIndex(1)
        self.dlg.chk_sticks.setChecked(False)
        self.dlg.chk_physical.setChecked(True)
        self.dlg.save_settings()

        with open(self.settings_path, encoding="utf-8") as fh:
            saved = json.load(fh)["tddft_settings"]
        self.assertEqual(saved["sigma"], 1234.0)
        self.assertEqual(saved["sigma_unit_idx"], 1)
        self.assertFalse(saved["show_sticks"])
        self.assertTrue(saved["physical"])

    def test_settings_round_trip(self):
        self.dlg.spin_sigma.setValue(2500.0)
        self.dlg.combo_sigma_unit.setCurrentIndex(1)
        self.dlg.chk_sticks.setChecked(False)
        self.dlg.save_settings()

        fresh = T.TDDFTDialog(self.host, _excitations())
        fresh.settings_file = self.settings_path
        fresh.load_settings()
        self.assertEqual(fresh.spin_sigma.value(), 2500.0)
        self.assertEqual(fresh.combo_sigma_unit.currentIndex(), 1)
        self.assertFalse(fresh.chk_sticks.isChecked())

    def test_save_preserves_unrelated_sections(self):
        with open(self.settings_path, "w", encoding="utf-8") as fh:
            json.dump({"nmr_settings": {"ref": 31.7}}, fh)
        self.dlg.save_settings()
        with open(self.settings_path, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["nmr_settings"], {"ref": 31.7})
        self.assertIn("tddft_settings", saved)

    def test_loading_restores_the_unit_index_field(self):
        with open(self.settings_path, "w", encoding="utf-8") as fh:
            json.dump({"tddft_settings": {"sigma_unit_idx": 1}}, fh)
        self.dlg.load_settings()
        self.assertEqual(self.dlg.prev_sigma_unit_idx, 1)

    def test_missing_file_leaves_defaults_untouched(self):
        self.dlg.settings_file = os.path.join(self.tmp, "nope.json")
        self.dlg.spin_sigma.setValue(777.0)
        self.dlg.load_settings()
        self.assertEqual(self.dlg.spin_sigma.value(), 777.0)

    def test_corrupt_settings_are_ignored(self):
        with open(self.settings_path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.dlg.spin_sigma.setValue(777.0)
        self.dlg.load_settings()  # must not raise
        self.assertEqual(self.dlg.spin_sigma.value(), 777.0)

    def test_partial_settings_only_apply_known_keys(self):
        with open(self.settings_path, "w", encoding="utf-8") as fh:
            json.dump({"tddft_settings": {"sigma": 42.0}}, fh)
        self.dlg.chk_sticks.setChecked(True)
        self.dlg.load_settings()
        self.assertEqual(self.dlg.spin_sigma.value(), 42.0)
        self.assertTrue(self.dlg.chk_sticks.isChecked())

    def test_save_reports_via_the_host_status_bar(self):
        self.dlg.save_settings()
        self.context.show_status_message.assert_called()

    def test_save_preserves_several_other_dialogs_sections(self):
        others = {
            "mo_settings": {"iso": 0.02},
            "nmr_settings": {"ref": "TMS"},
            "thermal_settings": {"show_details": True},
        }
        with open(self.settings_path, "w", encoding="utf-8") as fh:
            json.dump(others, fh)

        self.dlg.spin_sigma.setValue(999.0)
        self.dlg.save_settings()

        with open(self.settings_path, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        for key, val in others.items():
            self.assertEqual(on_disk[key], val)
        self.assertEqual(on_disk["tddft_settings"]["sigma"], 999.0)

    def test_round_trips_through_the_shared_helpers(self):
        S = gui_harness.load_isolated("settings")
        self.dlg.spin_sigma.setValue(555.0)
        self.dlg.save_settings()

        section = S.load_section(self.settings_path, "tddft_settings")
        self.assertEqual(section["sigma"], 555.0)

    def test_non_numeric_sigma_does_not_crash_load(self):
        # float(settings["sigma"]) raises ValueError; the narrowed handler
        # must swallow it (and still unblock the spinbox signals), not crash.
        with open(self.settings_path, "w", encoding="utf-8") as fh:
            json.dump({"tddft_settings": {"sigma": "not_a_number"}}, fh)
        self.dlg.spin_sigma.setValue(777.0)
        self.dlg.load_settings()  # must not raise
        self.assertEqual(self.dlg.spin_sigma.value(), 777.0)

    def test_close_persists_settings(self):
        self.dlg.spin_sigma.setValue(4321.0)
        self.dlg.closeEvent(MagicMock())
        with open(self.settings_path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["tddft_settings"]["sigma"], 4321.0)

    def test_escape_routes_through_close(self):
        with patch.object(self.dlg, "close") as closer:
            self.dlg.reject()
        closer.assert_called_once()

    def test_reset_restores_the_defaults(self):
        self.dlg.spin_sigma.setValue(50.0)
        self.dlg.combo_sigma_unit.setCurrentIndex(1)
        self.dlg.chk_sticks.setChecked(False)
        self.dlg.reset_defaults()
        self.assertEqual(self.dlg.spin_sigma.value(), 3000.0)
        self.assertEqual(self.dlg.combo_sigma_unit.currentIndex(), 0)
        self.assertTrue(self.dlg.chk_sticks.isChecked())
        self.assertEqual(self.dlg.prev_sigma_unit_idx, 0)


# ---------------------------------------------------------------------------
# Exporters
# ---------------------------------------------------------------------------


class TestExports(_TDDFTCase):
    def test_save_png_writes_the_figure(self):
        # The stub canvas base never stores a real figure, so assert the export
        # was driven rather than looking for bytes on disk.
        path = self._out("s.png")
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=(path, "")):
            with patch.object(self.dlg.spectrum, "save_png") as saver:
                self.dlg.save_png()
        saver.assert_called_once_with(path)

    def test_save_png_cancelled_writes_nothing(self):
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=("", "")):
            with patch.object(self.dlg.spectrum, "save_png") as saver:
                self.dlg.save_png()
        saver.assert_not_called()

    def test_save_csv_writes_a_broadened_curve(self):
        path = self._out("s.csv")
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.save_csv()
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(len(rows), 5001)  # header + 5000 samples
        self.assertEqual(len(rows[0]), 2)

    def test_save_csv_reports_success(self):
        with patch.object(
            T.QFileDialog, "getSaveFileName", return_value=(self._out("s.csv"), "")
        ):
            self.dlg.save_csv()
        self.context.show_status_message.assert_called()

    def test_save_csv_warns_on_failure(self):
        with patch.object(
            T.QFileDialog, "getSaveFileName", return_value=(self._out("s.csv"), "")
        ):
            with patch.object(self.dlg.spectrum, "save_csv", return_value=False):
                with patch.object(T.QMessageBox, "warning") as warn:
                    self.dlg.save_csv()
        warn.assert_called_once()

    def test_save_sticks_writes_one_row_per_state(self):
        path = self._out("sticks.csv")
        self.dlg.switch_spectrum_type()
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.save_sticks()
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(len(rows), 3)  # header + 2 excitations

    def test_sticks_are_sorted_by_wavelength(self):
        path = self._out("sticks.csv")
        self.dlg.switch_spectrum_type()
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.save_sticks()
        with open(path, encoding="utf-8") as fh:
            xs = [float(r[0]) for r in list(csv.reader(fh))[1:]]
        self.assertEqual(xs, sorted(xs))

    def test_save_sticks_warns_on_failure(self):
        with patch.object(
            T.QFileDialog, "getSaveFileName", return_value=(self._out("x.csv"), "")
        ):
            with patch.object(self.dlg.spectrum, "save_sticks_csv", return_value=False):
                with patch.object(T.QMessageBox, "warning") as warn:
                    self.dlg.save_sticks()
        warn.assert_called_once()

    def test_report_lists_every_state(self):
        path = self._out("r.txt")
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.save_gamess_report()
        text = open(path, encoding="utf-8").read()
        self.assertIn("STATE   1", text)
        self.assertIn("STATE   2", text)

    def test_report_includes_all_three_energy_units(self):
        path = self._out("r.txt")
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.save_gamess_report()
        text = open(path, encoding="utf-8").read()
        self.assertIn("eV", text)
        self.assertIn("nm", text)
        self.assertIn("cm^-1", text)

    def test_report_includes_transitions(self):
        path = self._out("r.txt")
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.save_gamess_report()
        self.assertIn("HOMO -> LUMO", open(path, encoding="utf-8").read())

    def test_report_notes_absent_transitions(self):
        exc = _excitations()
        for e in exc:
            e["transitions"] = []
        dlg = T.TDDFTDialog(self.host, exc)
        dlg.parent = lambda: self.host
        path = self._out("r.txt")
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=(path, "")):
            dlg.save_gamess_report()
        self.assertIn("No transition details", open(path, encoding="utf-8").read())


    def test_report_skips_the_ground_state(self):
        exc = _excitations()
        exc.insert(0, {"state": 0, "energy_nm": 0.0, "osc": 0.0})
        dlg = T.TDDFTDialog(self.host, exc)
        dlg.parent = lambda: self.host
        path = self._out("r.txt")
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=(path, "")):
            dlg.save_gamess_report()
        self.assertNotIn("STATE   0", open(path, encoding="utf-8").read())

    def test_report_derives_missing_energies_from_wavelength(self):
        exc = [
            {
                "state": 1,
                "energy_nm": 400.0,
                "energy_ev": 0.0,
                "energy_cm": 0.0,
                "osc": 0.1,
            }
        ]
        dlg = T.TDDFTDialog(self.host, exc)
        dlg.parent = lambda: self.host
        path = self._out("r.txt")
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=(path, "")):
            dlg.save_gamess_report()
        text = open(path, encoding="utf-8").read()
        self.assertIn("3.0996", text)  # 1239.84193 / 400
        self.assertIn("25000.0", text)  # 1e7 / 400

    def test_report_without_data_warns(self):
        dlg = T.TDDFTDialog(self.host, [])
        dlg.parent = lambda: self.host
        with patch.object(T.QMessageBox, "warning") as warn:
            dlg.save_gamess_report()
        warn.assert_called_once()

    def test_report_cancelled_writes_nothing(self):
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=("", "")):
            self.dlg.save_gamess_report()
        self.assertEqual(os.listdir(self.tmp), [])

    def test_unwritable_report_path_is_reported(self):
        # Point at a path under a *file* so the open() genuinely fails, rather
        # than patching builtins.open (which would break pytest's own I/O).
        blocker = self._out("blocker")
        with open(blocker, "w", encoding="utf-8") as fh:
            fh.write("x")
        bad = os.path.join(blocker, "r.txt")
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=(bad, "")):
            with patch.object(T.QMessageBox, "critical") as crit:
                self.dlg.save_gamess_report()
        crit.assert_called_once()


# ---------------------------------------------------------------------------
# Info panel
# ---------------------------------------------------------------------------


class TestInfoPanel(_TDDFTCase):
    def test_clicking_a_peak_records_the_selection(self):
        item = self.dlg.excitations[1]
        self.dlg.on_spectrum_click(item)
        self.assertIs(self.dlg._current_item, item)

    def test_clicking_a_peak_populates_the_panel(self):
        item = self.dlg.excitations[0]
        with patch.object(self.dlg, "_populate_info_panel") as pop:
            self.dlg.on_spectrum_click(item)
        pop.assert_called_once_with(item)

    def test_populating_the_panel_does_not_raise(self):
        self.dlg._populate_info_panel(self.dlg.excitations[0])

    def test_clearing_the_panel_does_not_raise(self):
        self.dlg._clear_info_panel()


if __name__ == "__main__":
    unittest.main()
