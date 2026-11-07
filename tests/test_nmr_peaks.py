"""
tests/test_nmr_peaks.py
Second NMR pass: the peak model that plotting and selection are built on
(_get_current_peaks), J-coupling formatting, per-nucleus default axis ranges,
reference-standard management, label handling and the spectrum CSV export.

Kept separate from tests/test_nmr_dialog.py, which covers the shift table and
merge persistence.
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

N = gui_harness.load_isolated("nmr_analysis")
NE = sys.modules[f"{N.__package__}.nmr_export"]  # export_* now live here


def _data():
    """One carbon plus three methyl protons."""
    return [
        {"atom_idx": 0, "atom_sym": "C", "shielding": 150.0},
        {"atom_idx": 1, "atom_sym": "H", "shielding": 30.0},
        {"atom_idx": 2, "atom_sym": "H", "shielding": 30.5},
        {"atom_idx": 3, "atom_sym": "H", "shielding": 31.0},
    ]


def _couplings():
    return [
        {"atom_idx1": 1, "atom_idx2": 0, "coupling": 7.4},
        {"atom_idx1": 2, "atom_idx2": 0, "coupling": 7.6},
        {"atom_idx1": 3, "atom_idx2": 0, "coupling": 7.5},
        {"atom_idx1": 1, "atom_idx2": 2, "coupling": 12.0},  # intra-methyl
        {"atom_idx1": 1, "atom_idx2": 3, "coupling": 0.05},  # negligible
    ]


class _NMRCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        self.context = MagicMock()
        self.host = MagicMock()
        self.host.context = self.context
        self.host.file_path = os.path.join(self.tmp, "job.out")

        self.dlg = N.NMRDialog(self.host, _data())
        self.dlg.parent_dlg = self.host
        self.dlg.settings_file = os.path.join(self.tmp, "settings.json")
        self.dlg.merged_peaks_file = os.path.join(self.tmp, "merges.json")
        self.dlg.merged_peaks = []
        self.dlg.delta_ref = 0.0
        self.dlg.sigma_ref = 0.0

        # Redrawing pulls in a matplotlib renderer (Agg -> Pillow), which other
        # test modules stub out; these tests are about the peak model.
        patcher = patch.object(self.dlg, "plot_spectrum")
        patcher.start()
        self.addCleanup(patcher.stop)


# ---------------------------------------------------------------------------
# Peak model
# ---------------------------------------------------------------------------


class TestCurrentPeaks(_NMRCase):
    def test_one_peak_per_nucleus_when_nothing_is_merged(self):
        self.assertEqual(len(self.dlg._get_current_peaks()), 4)

    def test_peaks_come_back_in_descending_shift_order(self):
        shifts = [p[0] for p in self.dlg._get_current_peaks()]
        self.assertEqual(shifts, sorted(shifts, reverse=True))

    def test_an_unmerged_peak_has_unit_intensity(self):
        self.assertTrue(all(p[1] == 1.0 for p in self.dlg._get_current_peaks()))

    def test_an_unmerged_peak_is_not_flagged_as_merged(self):
        self.assertTrue(all(p[2] is False for p in self.dlg._get_current_peaks()))

    def test_each_unmerged_peak_names_its_atom(self):
        atoms = sorted(p[3][0] for p in self.dlg._get_current_peaks())
        self.assertEqual(atoms, [0, 1, 2, 3])

    def test_the_shift_follows_the_reference(self):
        self.dlg.delta_ref = 0.0
        self.dlg.sigma_ref = 31.8
        peak = next(p for p in self.dlg._get_current_peaks() if p[3] == [1])
        self.assertAlmostEqual(peak[0], 31.8 - 30.0)

    def test_a_merged_group_collapses_to_one_peak(self):
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        self.assertEqual(len(self.dlg._get_current_peaks()), 2)

    def test_a_merged_peak_averages_the_shielding(self):
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        peak = next(p for p in self.dlg._get_current_peaks() if p[2])
        self.assertAlmostEqual(peak[0], -(30.0 + 30.5 + 31.0) / 3)

    def test_a_merged_peak_integrates_to_its_atom_count(self):
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        peak = next(p for p in self.dlg._get_current_peaks() if p[2])
        self.assertEqual(peak[1], 3.0)

    def test_a_merged_peak_is_flagged(self):
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        self.assertTrue(any(p[2] for p in self.dlg._get_current_peaks()))

    def test_merged_atoms_are_not_also_listed_individually(self):
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        singles = [p for p in self.dlg._get_current_peaks() if not p[2]]
        self.assertEqual([p[3] for p in singles], [[0]])

    def test_a_group_outside_the_current_filter_is_dropped(self):
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        self.dlg.displayed_data = [d for d in self.dlg.data if d["atom_sym"] == "C"]
        peaks = self.dlg._get_current_peaks()
        self.assertEqual(len(peaks), 1)
        self.assertFalse(peaks[0][2])

    def test_a_group_naming_unknown_atoms_is_skipped(self):
        self.dlg.merged_peaks = [{"indices": [98, 99]}]
        self.assertEqual(len(self.dlg._get_current_peaks()), 4)

    def test_filtering_narrows_the_peak_list(self):
        self.dlg.displayed_data = [d for d in self.dlg.data if d["atom_sym"] == "H"]
        self.assertEqual(len(self.dlg._get_current_peaks()), 3)


# ---------------------------------------------------------------------------
# J-coupling formatting
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Default axis ranges per nucleus
# ---------------------------------------------------------------------------


class TestXRangeDefaults(_NMRCase):
    def _range(self, nucleus):
        self.dlg.update_x_range_defaults(nucleus)
        return self.dlg.spin_x_max.value(), self.dlg.spin_x_min.value()

    def test_proton_uses_a_narrow_window(self):
        self.assertEqual(self._range("H"), (12, -1))

    def test_carbon_uses_a_wider_window(self):
        self.assertEqual(self._range("C"), (220, -10))

    def test_nitrogen_spans_negative_shifts(self):
        self.assertEqual(self._range("N"), (500, -400))

    def test_fluorine_is_entirely_negative(self):
        self.assertEqual(self._range("F"), (0, -200))

    def test_an_isotope_label_resolves_to_the_same_window(self):
        self.assertEqual(self._range("13C"), self._range("C"))

    def test_a_two_letter_element_resolves_to_its_own_preset(self):
        # get_nucleus_key("Pt") -> "195Pt", which is not itself a preset key, so
        # the element is recovered from it. Stripping lowercase would leave "P"
        # and hand platinum phosphorus's window.
        x_max, x_min = self._range("Pt")
        self.assertEqual(x_max, 1000)
        # The preset asks for -5000 but the spin box is bounded at -2000.
        self.assertEqual(x_min, -2000)

    def test_platinum_does_not_inherit_the_phosphorus_window(self):
        self.assertNotEqual(self._range("Pt"), self._range("P"))

    def test_an_unknown_nucleus_gets_a_generic_window(self):
        self.assertEqual(self._range("Xx"), (500, -500))

    def test_it_is_skipped_before_the_spin_boxes_exist(self):
        self.dlg.spin_x_max = None
        self.dlg.update_x_range_defaults("H")  # must not raise


# ---------------------------------------------------------------------------
# Reference standards
# ---------------------------------------------------------------------------


class TestReferences(_NMRCase):
    def test_the_builtin_standards_are_available(self):
        self.assertIn("TMS", self.dlg.reference_standards["1H"])

    def test_selecting_a_standard_sets_both_reference_values(self):
        self.dlg.current_nucleus = "H"
        self.dlg.update_reference_combo()
        self.dlg.combo_ref.setCurrentText("TMS")
        self.dlg.on_ref_change()
        self.assertAlmostEqual(self.dlg.sigma_ref, 31.8)
        self.assertAlmostEqual(self.dlg.delta_ref, 0.0)

    def test_a_solvent_standard_carries_its_own_offset(self):
        self.dlg.current_nucleus = "H"
        self.dlg.update_reference_combo()
        self.dlg.combo_ref.setCurrentText("CDCl3")
        self.dlg.on_ref_change()
        self.assertAlmostEqual(self.dlg.delta_ref, 7.26)

    def test_choosing_no_reference_zeroes_the_offsets(self):
        self.dlg.current_nucleus = "H"
        self.dlg.update_reference_combo()
        self.dlg.combo_ref.setCurrentText("No Reference")
        self.dlg.on_ref_change()
        self.assertAlmostEqual(self.dlg.sigma_ref, 0.0)
        self.assertAlmostEqual(self.dlg.delta_ref, 0.0)

    def test_a_custom_reference_is_saved_and_offered(self):
        self.dlg.current_nucleus = "H"
        picker = MagicMock()
        picker.exec.return_value = True
        picker.get_reference_data.return_value = (
            "MyRef",
            {"1H": {"delta_ref": 1.5, "sigma_ref": 30.0}},
        )
        with patch.object(N, "CustomReferenceDialog", return_value=picker):
            with patch.object(N.QDialog.DialogCode, "Accepted", True):
                self.dlg.add_custom_reference()
        self.assertIn("MyRef", self.dlg.reference_standards["1H"])

    def test_a_cancelled_custom_reference_is_not_saved(self):
        picker = MagicMock()
        picker.exec.return_value = False
        with patch.object(N, "CustomReferenceDialog", return_value=picker):
            self.dlg.add_custom_reference()
        self.assertNotIn("MyRef", self.dlg.reference_standards.get("1H", {}))

    def test_custom_references_survive_a_settings_round_trip(self):
        self.dlg.reference_standards.setdefault("1H", {})["MyRef"] = {
            "delta_ref": 1.5,
            "sigma_ref": 30.0,
        }
        self.dlg.save_settings()
        saved = json.load(open(self.dlg.settings_file, encoding="utf-8"))
        self.assertIn("MyRef", json.dumps(saved))

    def test_saving_preserves_unrelated_sections(self):
        with open(self.dlg.settings_file, "w", encoding="utf-8") as fh:
            json.dump({"mo_settings": {"last_preset": "Default"}}, fh)
        self.dlg.save_settings()
        saved = json.load(open(self.dlg.settings_file, encoding="utf-8"))
        self.assertEqual(saved["mo_settings"], {"last_preset": "Default"})

    def test_save_preserves_several_other_dialogs_sections(self):
        others = {
            "mo_settings": {"iso": 0.02},
            "dipole_settings": {"res": 20},
            "thermal_settings": {"show_details": True},
        }
        with open(self.dlg.settings_file, "w", encoding="utf-8") as fh:
            json.dump(others, fh)

        self.dlg.linewidth = 2.5
        self.dlg.save_settings()

        with open(self.dlg.settings_file, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        for key, val in others.items():
            self.assertEqual(on_disk[key], val)
        self.assertAlmostEqual(on_disk["nmr_settings"]["spectrum_linewidth"], 2.5)

    def test_round_trips_through_the_shared_helpers(self):
        S = gui_harness.load_isolated("settings")
        self.dlg.linewidth = 3.5
        self.dlg.save_settings()

        section = S.load_section(self.dlg.settings_file, "nmr_settings")
        self.assertAlmostEqual(section["spectrum_linewidth"], 3.5)

    def test_malformed_custom_references_do_not_crash_load(self):
        # A non-dict value for a nucleus's refs makes refs.items() raise
        # AttributeError; the narrowed handler must swallow it, not crash.
        with open(self.dlg.settings_file, "w", encoding="utf-8") as fh:
            json.dump({"nmr_settings": {"custom_references": {"1H": "not_a_dict"}}}, fh)
        self.dlg.load_settings()  # must not raise


# ---------------------------------------------------------------------------
# Selection and labels
# ---------------------------------------------------------------------------


class TestSelectionAndLabels(_NMRCase):
    def test_clearing_the_selection_empties_it(self):
        self.dlg.selected_peak_indices = {0, 1}
        self.dlg.clear_peak_selection()
        self.assertEqual(self.dlg.selected_peak_indices, set())

    def test_clearing_removes_the_highlight_artists(self):
        artist = MagicMock()
        self.dlg.highlight_artists = [artist]
        self.dlg.clear_peak_selection()
        artist.remove.assert_called_once()
        self.assertEqual(self.dlg.highlight_artists, [])

    def test_a_stale_artist_that_cannot_be_removed_is_tolerated(self):
        artist = MagicMock()
        artist.remove.side_effect = ValueError("gone")
        self.dlg.highlight_artists = [artist]
        self.dlg.clear_peak_selection()  # must not raise
        self.assertEqual(self.dlg.highlight_artists, [])

    def test_clearing_also_drops_the_3d_selection(self):
        e3d = self.host.mw.edit_3d_manager
        self.dlg.clear_peak_selection()
        e3d.selected_atoms_3d.clear.assert_called_once()

    def test_clearing_the_atom_labels_empties_the_list(self):
        self.dlg._atom_labels = [MagicMock(), MagicMock()]
        self.dlg.clear_atom_labels()
        self.assertEqual(self.dlg._atom_labels, [])

    def test_selecting_peaks_by_atom_updates_the_selection(self):
        self.dlg.peaks_metadata = [
            (100.0, 1.0, False, [0]),
            (1.8, 1.0, False, [1]),
        ]
        with patch.object(self.dlg, "highlight_selected_peaks"):
            with patch.object(self.dlg, "update_selected_labels"):
                self.dlg.select_peaks_by_atom_indices({1})
        self.assertEqual(self.dlg.selected_peak_indices, {1})

    def test_reselecting_the_same_peaks_does_not_redraw(self):
        self.dlg.peaks_metadata = [(100.0, 1.0, False, [0])]
        self.dlg.selected_peak_indices = {0}
        with patch.object(self.dlg, "highlight_selected_peaks") as hl:
            self.dlg.select_peaks_by_atom_indices({0})
        hl.assert_not_called()


# ---------------------------------------------------------------------------
# Spectrum export
# ---------------------------------------------------------------------------


class TestSpectrumExport(_NMRCase):
    def test_the_csv_is_written(self):
        path = os.path.join(self.tmp, "spec.csv")
        with patch.object(NE.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_spectrum_csv()
        self.assertTrue(os.path.exists(path))

    def test_the_stick_csv_records_shift_intensity_and_atoms(self):
        path = os.path.join(self.tmp, "spec.csv")
        with patch.object(NE.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_spectrum_csv()
        with open(path, encoding="utf-8") as fh:
            rows = [r for r in csv.reader(fh) if r]
        self.assertEqual(rows[0], ["Chemical Shift (ppm)", "Intensity", "AtomIndices"])

    def test_the_stick_csv_has_a_row_per_peak(self):
        # peaks_metadata is normally filled in by plot_spectrum, which is
        # patched out here, so publish the peak model directly.
        self.dlg.peaks_metadata = self.dlg._get_current_peaks()
        path = os.path.join(self.tmp, "spec.csv")
        with patch.object(NE.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_spectrum_csv()
        with open(path, encoding="utf-8") as fh:
            rows = [r for r in csv.reader(fh) if r]
        self.assertEqual(len(rows), 5)  # header + 4 nuclei

    def test_a_merged_peak_exports_all_of_its_atoms(self):
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        self.dlg.peaks_metadata = self.dlg._get_current_peaks()
        path = os.path.join(self.tmp, "spec.csv")
        with patch.object(NE.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_spectrum_csv()
        self.assertIn("1;2;3", open(path, encoding="utf-8").read())

    def test_without_peak_data_the_csv_says_so(self):
        self.dlg.peaks_metadata = []
        path = os.path.join(self.tmp, "spec.csv")
        with patch.object(NE.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_spectrum_csv()
        self.assertIn("No peak data", open(path, encoding="utf-8").read())

    def test_a_cancelled_export_writes_nothing(self):
        with patch.object(NE.QFileDialog, "getSaveFileName", return_value=("", "")):
            self.dlg.export_spectrum_csv()
        self.assertFalse([f for f in os.listdir(self.tmp) if f.endswith(".csv")])


if __name__ == "__main__":
    unittest.main()
