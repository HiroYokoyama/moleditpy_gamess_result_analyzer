"""
tests/test_gui_dialog.py
Coverage for the main GAMESSResultAnalyzerDialog under the headless Qt harness:
the status suffix helper, per-analysis button enablement from parsed data, the
file info labels, path elision, and the directory picker flow.

Complements tests/test_about_menu.py and tests/test_gui_reset_on_load.py, which
install their own process-wide stubs; this module drives the dialog through
gui_harness instead.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

G = gui_harness.load_isolated("gui")


# ---------------------------------------------------------------------------
# build_status_suffix (pure)
# ---------------------------------------------------------------------------


class TestStatusSuffix(unittest.TestCase):
    def test_no_frequency_data_has_no_suffix(self):
        self.assertEqual(G.build_status_suffix({}), ("", 0))

    def test_an_empty_frequency_list_has_no_suffix(self):
        self.assertEqual(G.build_status_suffix({"frequencies": []}), ("", 0))

    def test_a_null_frequency_entry_has_no_suffix(self):
        self.assertEqual(G.build_status_suffix({"frequencies": None}), ("", 0))

    def test_all_real_modes_have_no_suffix(self):
        data = {"frequencies": [{"freq": 100.0}, {"freq": 1600.0}]}
        self.assertEqual(G.build_status_suffix(data), ("", 0))

    def test_one_imaginary_mode_is_reported_in_the_singular(self):
        data = {"frequencies": [{"freq": -120.0}, {"freq": 1600.0}]}
        suffix, count = G.build_status_suffix(data)
        self.assertEqual(count, 1)
        self.assertIn("1 imaginary mode", suffix)
        self.assertNotIn("modes", suffix)

    def test_several_imaginary_modes_are_reported_in_the_plural(self):
        data = {"frequencies": [{"freq": -120.0}, {"freq": -30.0}]}
        suffix, count = G.build_status_suffix(data)
        self.assertEqual(count, 2)
        self.assertIn("2 imaginary modes", suffix)

    def test_a_zero_frequency_is_not_imaginary(self):
        self.assertEqual(
            G.build_status_suffix({"frequencies": [{"freq": 0.0}]}), ("", 0)
        )

    def test_a_frequency_entry_without_a_value_is_not_imaginary(self):
        self.assertEqual(G.build_status_suffix({"frequencies": [{}]}), ("", 0))


# ---------------------------------------------------------------------------
# Dialog fixture
# ---------------------------------------------------------------------------


class _GuiCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        self.path = os.path.join(self.tmp, "job.out")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("GAMESS output\n")

        self.context = MagicMock()
        self.mw = MagicMock()
        self.parser = MagicMock()
        self.parser.filename = self.path
        self.parser.data = self._data()

        self.dlg = G.GAMESSResultAnalyzerDialog(
            self.mw, self.parser, self.path, context=self.context
        )

    def _data(self, **overrides):
        data = {
            "coords": [[0.0, 0.0, 0.0], [0.96, 0.0, 0.0]],
            "atoms": ["O", "H"],
            "version": "5.0.4",
            "termination_status": "Terminated normally",
        }
        data.update(overrides)
        return data

    def _with(self, **overrides):
        """Reapply parser data and recompute button enablement."""
        self.parser.data = self._data(**overrides)
        self.dlg.update_button_states()
        return self.dlg


# ---------------------------------------------------------------------------
# Button enablement
# ---------------------------------------------------------------------------


class TestButtonStates(_GuiCase):
    def test_everything_is_disabled_without_data(self):
        dlg = self._with()
        for btn in (
            dlg.btn_mo,
            dlg.btn_freq,
            dlg.btn_traj,
            dlg.btn_forces,
            dlg.btn_therm,
            dlg.btn_tddft,
            dlg.btn_dipole,
            dlg.btn_charge,
            dlg.btn_nmr,
            dlg.btn_scf,
        ):
            self.assertFalse(btn.isEnabled())

    def test_mo_coefficients_enable_the_orbital_view(self):
        dlg = self._with(mo_coeffs={"0": {"energy": -1.0}})
        self.assertTrue(dlg.btn_mo.isEnabled())

    def test_orbital_energies_alone_enable_the_orbital_view(self):
        dlg = self._with(orbital_energies=[-1.0, -0.5])
        self.assertTrue(dlg.btn_mo.isEnabled())

    def test_energies_without_coefficients_explain_the_limitation(self):
        dlg = self._with(orbital_energies=[-1.0])
        self.assertIn("no coefficients", dlg.btn_mo.toolTip())

    def test_a_disabled_orbital_button_says_why(self):
        dlg = self._with()
        self.assertEqual(dlg.btn_mo.toolTip(), "No MO data found")

    def test_frequencies_enable_the_vibration_view(self):
        dlg = self._with(frequencies=[{"freq": 1600.0}])
        self.assertTrue(dlg.btn_freq.isEnabled())

    def test_scan_steps_enable_the_trajectory_view(self):
        dlg = self._with(scan_steps=[{"energy": -1.0}])
        self.assertTrue(dlg.btn_traj.isEnabled())

    def test_gradients_enable_the_force_view(self):
        dlg = self._with(gradients=[[0.1, 0.0, 0.0]])
        self.assertTrue(dlg.btn_forces.isEnabled())

    def test_an_optimisation_trajectory_enables_the_force_view(self):
        dlg = self._with(scan_steps=[{"energy": -1.0, "type": "opt_cycle"}])
        self.assertTrue(dlg.btn_forces.isEnabled())


    def test_gradients_enable_forces_even_for_neb(self):
        dlg = self._with(
            scan_steps=[{"energy": -1.0, "type": "neb_image"}],
            gradients=[[0.1, 0.0, 0.0]],
        )
        self.assertTrue(dlg.btn_forces.isEnabled())

    def test_the_force_tooltip_mentions_the_convergence_shortcut(self):
        dlg = self._with(gradients=[[0.1, 0.0, 0.0]])
        self.assertIn("Shift+Click", dlg.btn_forces.toolTip())

    def test_thermal_data_enables_the_thermochemistry_view(self):
        dlg = self._with(thermal={"gibbs": -76.4})
        self.assertTrue(dlg.btn_therm.isEnabled())

    def test_excitations_enable_the_spectrum_view(self):
        dlg = self._with(tddft=[{"state": 1}])
        self.assertTrue(dlg.btn_tddft.isEnabled())

    def test_dipoles_enable_the_dipole_view(self):
        dlg = self._with(dipoles={"magnitude": 1.8})
        self.assertTrue(dlg.btn_dipole.isEnabled())

    def test_charges_enable_the_charge_view(self):
        dlg = self._with(charges={"Mulliken": []})
        self.assertTrue(dlg.btn_charge.isEnabled())

    def test_shieldings_enable_the_nmr_view(self):
        dlg = self._with(nmr_shielding=[{"atom_idx": 0}])
        self.assertTrue(dlg.btn_nmr.isEnabled())

    def test_scf_traces_enable_the_convergence_view(self):
        dlg = self._with(scf_traces=[{"iterations": []}])
        self.assertTrue(dlg.btn_scf.isEnabled())

    def test_a_disabled_scf_button_says_why(self):
        dlg = self._with()
        self.assertEqual(dlg.btn_scf.toolTip(), "No SCF iteration data found")

    def test_an_empty_container_does_not_enable_a_button(self):
        dlg = self._with(frequencies=[], charges={}, scf_traces=[])
        self.assertFalse(dlg.btn_freq.isEnabled())
        self.assertFalse(dlg.btn_charge.isEnabled())
        self.assertFalse(dlg.btn_scf.isEnabled())


# ---------------------------------------------------------------------------
# File info labels
# ---------------------------------------------------------------------------


class TestFileInfoLabels(_GuiCase):
    def test_the_file_name_is_shown(self):
        self.dlg.update_file_info_labels()
        self.assertEqual(self.dlg.lbl_file_path.text(), "job.out")

    def test_the_directory_is_shown_separately(self):
        self.dlg.update_file_info_labels()
        self.assertEqual(self.dlg.lbl_file_dir.text(), self.tmp)

    def test_the_gamess_version_is_shown(self):
        self.dlg.update_file_info_labels()
        self.assertIn("5.0.4", self.dlg.lbl_version.text())

    def test_an_unknown_version_is_labelled(self):
        self.parser.data = self._data(version=None)
        self.parser.data.pop("version")
        self.dlg.update_file_info_labels()
        self.assertIn("Unknown", self.dlg.lbl_version.text())

    def test_the_modification_time_is_shown(self):
        self.dlg.update_file_info_labels()
        self.assertNotIn("---", self.dlg.lbl_updated.text())

    def test_a_missing_file_has_no_modification_time(self):
        self.dlg.file_path = os.path.join(self.tmp, "gone.out")
        self.dlg.update_file_info_labels()
        self.assertIn("---", self.dlg.lbl_updated.text())

    def test_a_normal_termination_is_reported(self):
        self.dlg.update_file_info_labels()
        self.assertIn("Terminated normally", self.dlg.lbl_status.text())

    def test_a_successful_run_is_shown_in_green(self):
        self.dlg.update_file_info_labels()
        self.assertIn("#28a745", self.dlg.lbl_status.styleSheet())

    def test_imaginary_modes_are_flagged_in_the_status(self):
        self.parser.data = self._data(frequencies=[{"freq": -120.0}])
        self.dlg.update_file_info_labels()
        self.assertIn("imaginary mode", self.dlg.lbl_status.text())

    def test_a_converged_run_with_imaginary_modes_is_warned_in_amber(self):
        self.parser.data = self._data(frequencies=[{"freq": -120.0}])
        self.dlg.update_file_info_labels()
        self.assertIn("#fd7e14", self.dlg.lbl_status.styleSheet())

    def test_a_running_job_is_shown_in_amber(self):
        self.parser.data = self._data(termination_status="Running")
        self.dlg.update_file_info_labels()
        self.assertIn("#fd7e14", self.dlg.lbl_status.styleSheet())

    def test_a_failed_run_is_shown_in_red(self):
        self.parser.data = self._data(termination_status="Error terminated")
        self.dlg.update_file_info_labels()
        self.assertIn("#dc3545", self.dlg.lbl_status.styleSheet())

    def test_no_file_is_shown_in_grey(self):
        self.dlg.file_path = ""
        self.dlg.update_file_info_labels()
        self.assertIn("#6c757d", self.dlg.lbl_status.styleSheet())

    def test_labels_are_skipped_before_the_ui_exists(self):
        self.dlg.lbl_file_path = None
        self.dlg.update_file_info_labels()  # must not raise


# ---------------------------------------------------------------------------
# ElidedLabel
# ---------------------------------------------------------------------------


class TestElidedLabel(unittest.TestCase):
    def test_the_full_text_is_retained(self):
        lbl = G.ElidedLabel("/very/long/path/to/a/result/job.out")
        lbl.setText("/another/long/path/job.out")
        self.assertEqual(lbl._full_text, "/another/long/path/job.out")

    def test_short_text_is_shown_untouched(self):
        lbl = G.ElidedLabel("")
        lbl.setFixedWidth(200)
        lbl.setText("short.out")
        self.assertEqual(lbl.text(), "short.out")

    def test_long_text_is_elided(self):
        lbl = G.ElidedLabel("")
        lbl.setFixedWidth(10)
        lbl.setText("/a/very/long/path/that/will/not/fit/job.out")
        self.assertLess(len(lbl.text()), 43)

    def test_elision_keeps_both_ends_of_the_path(self):
        lbl = G.ElidedLabel("")
        lbl.setFixedWidth(12)
        lbl.setText("/start/middle/end.out")
        shown = lbl.text()
        self.assertTrue(shown.startswith("/st"))
        self.assertTrue(shown.endswith(".out"))


# ---------------------------------------------------------------------------
# Directory picker flow
# ---------------------------------------------------------------------------


class TestOpenDirectory(_GuiCase):
    def test_an_empty_directory_reports_that_nothing_was_found(self):
        with patch.object(G, "list_gamess_output_files", return_value=[]):
            with patch.object(G.QMessageBox, "information") as info:
                with patch.object(self.dlg, "load_file") as load:
                    self.dlg._open_directory_path(self.tmp)
        info.assert_called_once()
        load.assert_not_called()

    def test_a_single_output_file_is_opened_without_prompting(self):
        with patch.object(G, "list_gamess_output_files", return_value=["only.out"]):
            with patch.object(self.dlg, "load_file") as load:
                self.dlg._open_directory_path(self.tmp)
        load.assert_called_once_with(os.path.join(self.tmp, "only.out"))

    def test_several_files_offer_a_picker(self):
        chosen = os.path.join(self.tmp, "b.out")
        picker = MagicMock()
        picker.exec.return_value = G.QDialog.DialogCode.Accepted
        picker.selected_path = chosen
        with patch.object(G, "list_gamess_output_files", return_value=["a.out", "b.out"]):
            with patch.object(G, "_DirectoryFilePicker", return_value=picker):
                with patch.object(self.dlg, "load_file") as load:
                    self.dlg._open_directory_path(self.tmp)
        load.assert_called_once_with(chosen)

    def test_cancelling_the_picker_loads_nothing(self):
        picker = MagicMock()
        picker.exec.return_value = "rejected"
        picker.selected_path = None
        with patch.object(G, "list_gamess_output_files", return_value=["a.out", "b.out"]):
            with patch.object(G, "_DirectoryFilePicker", return_value=picker):
                with patch.object(self.dlg, "load_file") as load:
                    self.dlg._open_directory_path(self.tmp)
        load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
