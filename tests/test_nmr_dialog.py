"""
tests/test_nmr_dialog.py
Coverage for NMRDialog under the headless Qt harness: chemical-shift
recalculation against a reference standard, peak merging (isotope validation,
conflict handling, persistence), selection remapping across table rebuilds,
nucleus filtering, and the CSV exporters.

Complements tests/test_nmr_merge_persistence.py and tests/test_nmr_mo_fixes.py,
which install their own process-wide stubs; this module drives the real dialog
through gui_harness instead.

Both ``settings_file`` and ``merged_peaks_file`` default to paths inside the
installed package, so every test redirects them into a temp dir — the suite
must never write to the source tree.
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
NM = sys.modules[f"{N.__package__}.nmr_merge"]
NE = sys.modules[f"{N.__package__}.nmr_export"]  # export_table_csv now lives here


class _FakeItem:
    def __init__(self, text=""):
        self._text = "" if text is None else str(text)

    def text(self):
        return self._text

    def setText(self, t):
        self._text = t

    def __getattr__(self, name):
        return MagicMock()


class _FakeTable:
    """QTableWidget stand-in that actually stores its cells."""

    def __init__(self):
        self._rows = 0
        self._cells = {}

    HEADERS = ["Atom", "Nucleus", "Shielding", "Shift", "J"]

    def setRowCount(self, n):
        self._rows = n
        self._cells = {k: v for k, v in self._cells.items() if k[0] < n}

    def horizontalHeaderItem(self, c):
        return _FakeItem(self.HEADERS[c])

    def rowCount(self):
        return self._rows

    def columnCount(self):
        return 5

    def setItem(self, r, c, item):
        self._cells[(r, c)] = item

    def item(self, r, c):
        return self._cells.get((r, c))

    def cell(self, r, c):
        it = self._cells.get((r, c))
        return it.text() if it is not None else None

    def column(self, c):
        return [self.cell(r, c) for r in range(self._rows)]

    def __getattr__(self, name):
        return MagicMock()


def _data():
    """Ethanol-ish: one carbon plus three equivalent methyl protons."""
    return [
        {"atom_idx": 0, "atom_sym": "C", "shielding": 150.0},
        {"atom_idx": 1, "atom_sym": "H", "shielding": 30.0},
        {"atom_idx": 2, "atom_sym": "H", "shielding": 30.5},
        {"atom_idx": 3, "atom_sym": "H", "shielding": 31.0},
    ]


class _NMRCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

        self.context = MagicMock()
        self.host = MagicMock()
        self.host.context = self.context
        self.host.file_path = os.path.join(self.tmp, "job.out")

        self.dlg = N.NMRDialog(self.host, _data(), file_path=None)
        self.dlg.parent_dlg = self.host
        self.dlg.settings_file = os.path.join(self.tmp, "settings.json")
        self.dlg.merged_peaks_file = os.path.join(self.tmp, "merges.json")
        self.dlg.merged_peaks = []

        self.table = _FakeTable()
        self.dlg.table = self.table

        self._saved_item = N.QTableWidgetItem
        N.QTableWidgetItem = _FakeItem

        # recalc() also redraws the spectrum, and figure.tight_layout() pulls in
        # a matplotlib renderer (Agg -> Pillow). These tests are about the table
        # and the shift arithmetic, so keep them off the rendering stack.
        self._plot_patch = patch.object(self.dlg, "plot_spectrum")
        self.plot_spectrum = self._plot_patch.start()
        self.addCleanup(self._plot_patch.stop)

    def tearDown(self):
        N.QTableWidgetItem = self._saved_item
        self._tmp.cleanup()

    def _set_reference(self, delta_ref=0.0, sigma_ref=0.0):
        self.dlg.delta_ref = delta_ref
        self.dlg.sigma_ref = sigma_ref


# ---------------------------------------------------------------------------
# Merged-peaks file location (pure/static)
# ---------------------------------------------------------------------------


class TestMergedPeaksPath(unittest.TestCase):
    def test_path_is_derived_from_the_result_file(self):
        path = N.NMRDialog._merged_peaks_path("/results/job.out", _data())
        self.assertEqual(path, os.path.join("/results", "job-nmr_peak_info.json"))

    def test_extension_is_replaced_not_appended(self):
        path = N.NMRDialog._merged_peaks_path("/r/calc.property.txt", _data())
        self.assertTrue(path.endswith("calc.property-nmr_peak_info.json"))

    def test_pathless_results_are_keyed_by_data_fingerprint(self):
        a = N.NMRDialog._merged_peaks_path(None, _data())
        b = N.NMRDialog._merged_peaks_path(None, _data())
        self.assertEqual(a, b)

    def test_different_molecules_get_different_fallback_files(self):
        other = _data()
        other[0]["shielding"] = 999.0
        a = N.NMRDialog._merged_peaks_path(None, _data())
        b = N.NMRDialog._merged_peaks_path(None, other)
        self.assertNotEqual(a, b)

    def test_empty_data_still_yields_a_path(self):
        self.assertTrue(N.NMRDialog._merged_peaks_path(None, []).endswith(".json"))

    def test_fingerprint_ignores_dict_key_order(self):
        reordered = [
            {
                "atom_sym": d["atom_sym"],
                "shielding": d["shielding"],
                "atom_idx": d["atom_idx"],
            }
            for d in _data()
        ]
        self.assertEqual(
            N.NMRDialog._merged_peaks_path(None, _data()),
            N.NMRDialog._merged_peaks_path(None, reordered),
        )


# ---------------------------------------------------------------------------
# Nucleus mapping
# ---------------------------------------------------------------------------


class TestNucleusKey(_NMRCase):
    def test_hydrogen_maps_to_proton(self):
        self.assertEqual(self.dlg.get_nucleus_key("H"), "1H")

    def test_carbon_maps_to_carbon_13(self):
        self.assertEqual(self.dlg.get_nucleus_key("C"), "13C")

    def test_nitrogen_maps_to_nitrogen_15(self):
        self.assertEqual(self.dlg.get_nucleus_key("N"), "15N")

    def test_digits_in_the_symbol_are_stripped(self):
        self.assertEqual(self.dlg.get_nucleus_key("C13"), "13C")

    def test_unknown_elements_pass_through(self):
        self.assertEqual(self.dlg.get_nucleus_key("Xx"), "Xx")


# ---------------------------------------------------------------------------
# Chemical shift recalculation
# ---------------------------------------------------------------------------


class TestRecalc(_NMRCase):
    def test_one_row_per_nucleus(self):
        self._set_reference()
        self.dlg.recalc()
        self.assertEqual(self.table.rowCount(), 4)

    def test_shift_is_reference_minus_shielding(self):
        # delta = delta_ref + (sigma_ref - sigma)
        self._set_reference(delta_ref=0.0, sigma_ref=31.8)
        self.dlg.recalc()
        # atom 1: 0.0 + (31.8 - 30.0) = 1.80
        self.assertIn("1.80", self.table.column(3))

    def test_reference_offset_shifts_every_peak(self):
        self._set_reference(delta_ref=7.26, sigma_ref=31.8)
        self.dlg.recalc()
        # atom 1: 7.26 + (31.8 - 30.0) = 9.06
        self.assertIn("9.06", self.table.column(3))

    def test_shielding_column_is_the_raw_value(self):
        self._set_reference(delta_ref=7.26, sigma_ref=31.8)
        self.dlg.recalc()
        self.assertIn("150.00", self.table.column(2))

    def test_merged_group_reports_the_averaged_shielding(self):
        self._set_reference()
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        self.dlg.recalc()
        # (30.0 + 30.5 + 31.0) / 3 = 30.50
        self.assertIn("30.50", self.table.column(2))

    def test_merged_group_collapses_into_one_row(self):
        self._set_reference()
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        self.dlg.recalc()
        # 3 protons merged into 1 row, plus the lone carbon
        self.assertEqual(self.table.rowCount(), 2)

    def test_merged_row_lists_its_atom_indices(self):
        self._set_reference()
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        self.dlg.recalc()
        self.assertIn("[1, 2, 3]", self.table.column(0))

    def test_merged_row_shows_the_multiplicity(self):
        self._set_reference()
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        self.dlg.recalc()
        self.assertIn("3H", self.table.column(1))

    def test_merged_average_respects_the_reference(self):
        self._set_reference(delta_ref=0.0, sigma_ref=31.8)
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        self.dlg.recalc()
        # 0.0 + (31.8 - 30.5) = 1.30
        self.assertIn("1.30", self.table.column(3))

    def test_a_group_naming_absent_atoms_is_skipped(self):
        self._set_reference()
        self.dlg.merged_peaks = [{"indices": [98, 99]}]
        self.dlg.recalc()
        self.assertEqual(self.table.rowCount(), 4)

    def test_filtered_out_groups_are_not_shown(self):
        self._set_reference()
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        self.dlg.displayed_data = [d for d in self.dlg.data if d["atom_sym"] == "C"]
        self.dlg.recalc()
        self.assertEqual(self.table.rowCount(), 1)


# ---------------------------------------------------------------------------
# Peak selection
# ---------------------------------------------------------------------------


class TestPeakSelection(_NMRCase):
    def _metadata(self):
        # (shift, intensity, is_merged, atom_indices)
        return [
            (100.0, 1.0, False, [0]),
            (1.8, 1.0, True, [1, 2, 3]),
        ]

    def test_no_metadata_selects_nothing(self):
        self.dlg.peaks_metadata = []
        self.assertEqual(self.dlg._calculate_peak_selection_from_atoms({1}), set())

    def test_a_peak_is_selected_when_any_atom_matches(self):
        self.dlg.peaks_metadata = self._metadata()
        self.assertEqual(self.dlg._calculate_peak_selection_from_atoms({2}), {1})

    def test_atoms_from_several_peaks_select_both(self):
        self.dlg.peaks_metadata = self._metadata()
        self.assertEqual(self.dlg._calculate_peak_selection_from_atoms({0, 3}), {0, 1})

    def test_unknown_atoms_select_nothing(self):
        self.dlg.peaks_metadata = self._metadata()
        self.assertEqual(self.dlg._calculate_peak_selection_from_atoms({77}), set())

    def test_string_atom_indices_are_accepted(self):
        self.dlg.peaks_metadata = self._metadata()
        self.assertEqual(self.dlg._calculate_peak_selection_from_atoms({"2"}), {1})

    def test_remap_follows_the_atoms_when_rows_are_reordered(self):
        old = self._metadata()
        self.dlg.peaks_metadata = old
        self.dlg.selected_peak_indices = {1}  # the proton group
        # Rebuild puts the proton group first
        self.dlg.peaks_metadata = [old[1], old[0]]
        self.dlg._remap_selection_to_new_peaks(old)
        self.assertEqual(self.dlg.selected_peak_indices, {0})

    def test_remap_of_an_empty_selection_is_a_noop(self):
        old = self._metadata()
        self.dlg.peaks_metadata = old
        self.dlg.selected_peak_indices = set()
        self.dlg._remap_selection_to_new_peaks(old)
        self.assertEqual(self.dlg.selected_peak_indices, set())

    def test_remap_drops_peaks_that_no_longer_exist(self):
        old = self._metadata()
        self.dlg.selected_peak_indices = {1}
        self.dlg.peaks_metadata = [old[0]]  # proton group filtered away
        self.dlg._remap_selection_to_new_peaks(old)
        self.assertEqual(self.dlg.selected_peak_indices, set())

    def test_remap_ignores_out_of_range_indices(self):
        old = self._metadata()
        self.dlg.peaks_metadata = old
        self.dlg.selected_peak_indices = {99}
        self.dlg._remap_selection_to_new_peaks(old)
        self.assertEqual(self.dlg.selected_peak_indices, set())


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------


class TestMerge(_NMRCase):
    def setUp(self):
        super().setUp()
        self.dlg.peaks_metadata = [
            (100.0, 1.0, False, [0]),
            (1.8, 1.0, False, [1]),
            (1.9, 1.0, False, [2]),
            (2.0, 1.0, False, [3]),
        ]
        self.dlg.recalc = MagicMock()
        self.dlg.clear_peak_selection = MagicMock()

    def test_merging_fewer_than_two_peaks_warns(self):
        self.dlg.selected_peak_indices = {1}
        with patch.object(N.QMessageBox, "warning") as warn:
            self.dlg.merge_selected_peaks()
        warn.assert_called_once()
        self.assertEqual(self.dlg.merged_peaks, [])

    def test_merging_two_peaks_creates_a_group(self):
        self.dlg.selected_peak_indices = {1, 2}
        self.dlg.merge_selected_peaks()
        self.assertEqual(self.dlg.merged_peaks, [{"indices": [1, 2]}])

    def test_merged_indices_are_sorted_and_deduplicated(self):
        self.dlg.peaks_metadata[1] = (1.8, 1.0, False, [3, 1, 1])
        self.dlg.selected_peak_indices = {1, 2}
        self.dlg.merge_selected_peaks()
        self.assertEqual(self.dlg.merged_peaks[0]["indices"], [1, 2, 3])

    def test_different_nuclei_cannot_be_merged(self):
        self.dlg.selected_peak_indices = {0, 1}  # C and H
        with patch.object(N.QMessageBox, "critical") as crit:
            self.dlg.merge_selected_peaks()
        crit.assert_called_once()
        self.assertEqual(self.dlg.merged_peaks, [])

    def test_a_missing_symbol_does_not_count_as_a_second_nucleus(self):
        self.dlg.data[2].pop("atom_sym")
        self.dlg.selected_peak_indices = {1, 2}
        with patch.object(N.QMessageBox, "critical") as crit:
            self.dlg.merge_selected_peaks()
        crit.assert_not_called()
        self.assertEqual(self.dlg.merged_peaks, [{"indices": [1, 2]}])

    def test_a_stale_selection_resolving_to_no_atoms_warns(self):
        self.dlg.peaks_metadata = [(1.0, 1.0, False, []), (2.0, 1.0, False, [])]
        self.dlg.selected_peak_indices = {0, 1}
        with patch.object(N.QMessageBox, "warning") as warn:
            self.dlg.merge_selected_peaks()
        warn.assert_called_once()
        self.assertEqual(self.dlg.merged_peaks, [])

    def test_merging_marks_the_state_dirty(self):
        self.dlg.selected_peak_indices = {1, 2}
        self.dlg.merge_selected_peaks()
        self.assertTrue(self.dlg._merged_dirty)

    def test_merging_does_not_write_to_disk(self):
        self.dlg.selected_peak_indices = {1, 2}
        self.dlg.merge_selected_peaks()
        self.assertFalse(os.path.exists(self.dlg.merged_peaks_file))

    def test_a_conflicting_merge_replaces_the_old_group_when_confirmed(self):
        self.dlg.merged_peaks = [{"indices": [1, 2]}]
        self.dlg.selected_peak_indices = {2, 3}
        yes = N.QMessageBox.StandardButton.Yes
        with patch.object(N.QMessageBox, "question", return_value=yes):
            self.dlg.merge_selected_peaks()
        self.assertEqual(self.dlg.merged_peaks, [{"indices": [2, 3]}])

    def test_a_conflicting_merge_is_abandoned_when_declined(self):
        self.dlg.merged_peaks = [{"indices": [1, 2]}]
        self.dlg.selected_peak_indices = {2, 3}
        no = N.QMessageBox.StandardButton.No
        with patch.object(N.QMessageBox, "question", return_value=no):
            self.dlg.merge_selected_peaks()
        self.assertEqual(self.dlg.merged_peaks, [{"indices": [1, 2]}])

    def test_a_non_conflicting_merge_keeps_existing_groups(self):
        self.dlg.merged_peaks = [{"indices": [0]}]
        self.dlg.selected_peak_indices = {1, 2}
        self.dlg.merge_selected_peaks()
        self.assertIn({"indices": [0]}, self.dlg.merged_peaks)
        self.assertIn({"indices": [1, 2]}, self.dlg.merged_peaks)

    def test_unmerging_removes_the_group(self):
        self.dlg.merged_peaks = [{"indices": [1, 2]}]
        self.dlg.peaks_metadata = [(1.8, 1.0, True, [1, 2])]
        self.dlg.selected_peak_indices = {0}
        self.dlg.unmerge_selected_peaks()
        self.assertEqual(self.dlg.merged_peaks, [])

    def test_unmerging_leaves_individual_peaks_alone(self):
        self.dlg.merged_peaks = [{"indices": [1, 2]}]
        self.dlg.peaks_metadata = [(100.0, 1.0, False, [0])]
        self.dlg.selected_peak_indices = {0}
        self.dlg.unmerge_selected_peaks()
        self.assertEqual(self.dlg.merged_peaks, [{"indices": [1, 2]}])

    def test_unmerging_without_a_selection_is_a_noop(self):
        self.dlg.merged_peaks = [{"indices": [1, 2]}]
        self.dlg.selected_peak_indices = set()
        self.dlg.unmerge_selected_peaks()
        self.assertEqual(self.dlg.merged_peaks, [{"indices": [1, 2]}])


# ---------------------------------------------------------------------------
# Merge persistence
# ---------------------------------------------------------------------------


class TestMergePersistence(_NMRCase):
    def test_failed_explicit_save_keeps_changes_dirty(self):
        self.dlg.merged_peaks = [{"indices": [1, 2]}]
        self.dlg._merged_dirty = True
        self.dlg.btn_save_merge.setEnabled(True)
        # A parent that is a file makes the destination genuinely unwritable.
        blocker = os.path.join(self.tmp, "blocker")
        with open(blocker, "w", encoding="utf-8") as fh:
            fh.write("file")
        self.dlg.merged_peaks_file = os.path.join(blocker, "merges.json")
        with patch.object(N.QMessageBox, "warning") as warning, patch.object(NM, "notify") as note:
            self.dlg.save_merges_clicked()
        self.assertTrue(self.dlg._merged_dirty)
        self.assertTrue(self.dlg.btn_save_merge.isEnabled())
        warning.assert_called_once()
        note.assert_not_called()

    def test_saving_writes_the_groups(self):
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        self.dlg.save_merged_peaks()
        saved = json.load(open(self.dlg.merged_peaks_file, encoding="utf-8"))
        self.assertEqual(saved, [{"indices": [1, 2, 3]}])

    def test_groups_survive_a_round_trip(self):
        self.dlg.merged_peaks = [{"indices": [1, 2]}, {"indices": [3]}]
        self.dlg.save_merged_peaks()
        self.dlg.merged_peaks = []
        self.dlg.load_merged_peaks()
        self.assertEqual(self.dlg.merged_peaks, [{"indices": [1, 2]}, {"indices": [3]}])

    def test_a_missing_file_loads_as_empty(self):
        self.dlg.merged_peaks = [{"indices": [1]}]
        self.dlg.load_merged_peaks()
        self.assertEqual(self.dlg.merged_peaks, [])

    def test_explicit_save_clears_the_dirty_flag(self):
        self.dlg.merged_peaks = [{"indices": [1, 2]}]
        self.dlg._merged_dirty = True
        self.dlg.save_merges_clicked()
        self.assertFalse(self.dlg._merged_dirty)

    def test_a_corrupt_file_is_moved_aside(self):
        with open(self.dlg.merged_peaks_file, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.dlg.load_merged_peaks()
        self.assertEqual(self.dlg.merged_peaks, [])
        # moved aside so the next save cannot silently replace real merges
        self.assertTrue(os.path.exists(self.dlg.merged_peaks_file + ".corrupt"))

    def test_a_non_list_payload_is_ignored(self):
        with open(self.dlg.merged_peaks_file, "w", encoding="utf-8") as fh:
            json.dump({"indices": [1, 2]}, fh)
        self.dlg.load_merged_peaks()
        self.assertEqual(self.dlg.merged_peaks, [])

    def test_malformed_groups_are_dropped(self):
        with open(self.dlg.merged_peaks_file, "w", encoding="utf-8") as fh:
            json.dump(
                [
                    {"indices": [1, 2]},  # good
                    {"indices": []},  # empty
                    {"indices": "12"},  # not a list
                    {"nope": [1]},  # wrong key
                    "garbage",  # not a dict
                    {"indices": [1, True]},  # bools are not atom indices
                ],
                fh,
            )
        self.dlg.load_merged_peaks()
        self.assertEqual(self.dlg.merged_peaks, [{"indices": [1, 2]}])


# ---------------------------------------------------------------------------
# Filtering and exports
# ---------------------------------------------------------------------------


class TestFilterAndExport(_NMRCase):
    def test_filtering_narrows_the_displayed_nuclei(self):
        self.dlg.current_nucleus = "H"
        self.dlg.apply_filter()
        self.assertEqual([d["atom_idx"] for d in self.dlg.displayed_data], [1, 2, 3])

    def test_the_all_filter_shows_every_nucleus(self):
        self.dlg.current_nucleus = "All"
        self.dlg.apply_filter()
        self.assertEqual(len(self.dlg.displayed_data), 4)

    def test_filtering_rebuilds_the_table(self):
        self.dlg.current_nucleus = "C"
        self.dlg.apply_filter()
        self.assertEqual(self.table.rowCount(), 1)

    def test_export_table_writes_a_row_per_peak(self):
        self._set_reference(delta_ref=0.0, sigma_ref=31.8)
        self.dlg.recalc()
        path = os.path.join(self.tmp, "table.csv")
        with patch.object(NE.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_table_csv()
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(len(rows), 5)  # header + 4 nuclei

    def test_export_table_cancelled_writes_nothing(self):
        with patch.object(NE.QFileDialog, "getSaveFileName", return_value=("", "")):
            self.dlg.export_table_csv()
        self.assertFalse([f for f in os.listdir(self.tmp) if f.endswith(".csv")])

    def test_escape_routes_through_close(self):
        with patch.object(self.dlg, "close") as closer:
            self.dlg.reject()
        closer.assert_called_once()


if __name__ == "__main__":
    unittest.main()
