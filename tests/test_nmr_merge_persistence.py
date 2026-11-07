"""
tests/test_nmr_merge_persistence.py
Regression tests for the NMR merged-peaks persistence fixes:

  - save_merged_peaks writes atomically (temp file + os.replace) so an
    interrupted save can no longer truncate the previous file.
  - A corrupt/unreadable merge file is moved aside as *.corrupt instead of
    being silently replaced by [] on the next save.
  - load_merged_peaks validates entries: only dicts with a non-empty list
    of int indices survive.
  - merge_selected_peaks refuses a selection that resolves to zero atoms
    (stale peaks_metadata) instead of storing an empty {"indices": []}.
  - selected_peak_indices are remapped through their atoms whenever
    peaks_metadata is rebuilt, so Merge/Unmerge can no longer act on the
    wrong peaks after a filter/reference change.

Reuses the headless Qt/matplotlib/pyvista stubs from test_nmr_mo_fixes.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from tests.test_nmr_mo_fixes import NMRDialog, _make_nmr_dialog, _nmr_mod  # noqa: F401

_merge_mod = sys.modules["gamess_result_analyzer.nmr_merge"]


def _dialog_with_file(tmpdir):
    dlg = _make_nmr_dialog()
    dlg.merged_peaks_file = os.path.join(tmpdir, "job-nmr_peak_info.json")
    dlg.merged_peaks = []
    return dlg


class TestAtomicSave(unittest.TestCase):
    def test_save_writes_json_and_leaves_no_tmp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dlg = _dialog_with_file(tmpdir)
            dlg.merged_peaks = [{"indices": [1, 2]}, {"indices": [5, 6, 7]}]
            NMRDialog.save_merged_peaks(dlg)

            with open(dlg.merged_peaks_file, encoding="utf-8") as f:
                self.assertEqual(json.load(f), dlg.merged_peaks)
            self.assertFalse(os.path.exists(dlg.merged_peaks_file + ".tmp"))

    def test_failed_save_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dlg = _dialog_with_file(tmpdir)
            dlg.merged_peaks = [{"indices": [1, 2]}]
            NMRDialog.save_merged_peaks(dlg)

            dlg.merged_peaks = [{"indices": [3, 4]}]
            with patch.object(
                _merge_mod, "save_json_atomic", side_effect=OSError("disk full")
            ):
                NMRDialog.save_merged_peaks(dlg)  # must not raise

            with open(dlg.merged_peaks_file, encoding="utf-8") as f:
                self.assertEqual(json.load(f), [{"indices": [1, 2]}])


class TestLoadHardening(unittest.TestCase):
    def test_corrupt_file_is_backed_up_not_wiped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dlg = _dialog_with_file(tmpdir)
            with open(dlg.merged_peaks_file, "w", encoding="utf-8") as f:
                f.write('{"indices": [1, 2')  # truncated JSON

            NMRDialog.load_merged_peaks(dlg)

            self.assertEqual(dlg.merged_peaks, [])
            self.assertFalse(os.path.exists(dlg.merged_peaks_file))
            backup = dlg.merged_peaks_file + ".corrupt"
            self.assertTrue(os.path.exists(backup))
            with open(backup, encoding="utf-8") as f:
                self.assertEqual(f.read(), '{"indices": [1, 2')

            # A subsequent save must not touch the backup.
            dlg.merged_peaks = [{"indices": [9, 10]}]
            NMRDialog.save_merged_peaks(dlg)
            self.assertTrue(os.path.exists(backup))

    def test_missing_file_gives_empty_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dlg = _dialog_with_file(tmpdir)
            dlg.merged_peaks = [{"indices": [1]}]  # stale in-memory state
            NMRDialog.load_merged_peaks(dlg)
            self.assertEqual(dlg.merged_peaks, [])

    def test_invalid_entries_are_dropped_valid_kept(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dlg = _dialog_with_file(tmpdir)
            raw = [
                {"indices": [1, 2]},  # valid
                {"indices": []},  # empty — dropped
                {"indices": ["a", "b"]},  # non-int — dropped
                {"indices": [True, False]},  # bools — dropped
                {"other": 1},  # no indices — dropped
                "not-a-dict",  # dropped
                {"indices": [3], "note": "extra keys survive"},  # valid
            ]
            with open(dlg.merged_peaks_file, "w", encoding="utf-8") as f:
                json.dump(raw, f)

            NMRDialog.load_merged_peaks(dlg)

            self.assertEqual(
                dlg.merged_peaks,
                [
                    {"indices": [1, 2]},
                    {"indices": [3], "note": "extra keys survive"},
                ],
            )

    def test_non_list_top_level_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dlg = _dialog_with_file(tmpdir)
            with open(dlg.merged_peaks_file, "w", encoding="utf-8") as f:
                json.dump({"indices": [1, 2]}, f)
            NMRDialog.load_merged_peaks(dlg)
            self.assertEqual(dlg.merged_peaks, [])


class TestMergeGuards(unittest.TestCase):
    def _merge_dialog(self):
        dlg = _make_nmr_dialog()
        dlg.data = [
            {"atom_idx": i, "atom_sym": "H", "shielding": 30.0 - i} for i in range(8)
        ]
        dlg.displayed_data = list(dlg.data)
        dlg.merged_peaks = []
        dlg.clear_peak_selection = MagicMock()
        dlg.recalc = MagicMock()
        return dlg

    def test_stale_selection_resolving_to_no_atoms_is_rejected(self):
        dlg = self._merge_dialog()
        dlg.merged_peaks = [{"indices": [5, 6]}]
        # Selection points past the (stale, shorter) metadata.
        dlg.peaks_metadata = [(1.0, 1.0, False, [0])]
        dlg.selected_peak_indices = {3, 4}

        with patch.object(_merge_mod, "QMessageBox") as mb:
            NMRDialog.merge_selected_peaks(dlg)
            mb.warning.assert_called_once()
            mb.question.assert_not_called()

        # No empty group stored, existing groups untouched, nothing dirtied.
        self.assertEqual(dlg.merged_peaks, [{"indices": [5, 6]}])
        self.assertFalse(dlg._merged_dirty)

    def test_merge_of_unrelated_peaks_keeps_existing_groups(self):
        dlg = self._merge_dialog()
        dlg.merged_peaks = [{"indices": [5, 6]}]
        dlg.peaks_metadata = [
            (1.0, 1.0, False, [0]),
            (2.0, 1.0, False, [1]),
        ]
        dlg.selected_peak_indices = {0, 1}

        with patch.object(_merge_mod, "QMessageBox") as mb:
            NMRDialog.merge_selected_peaks(dlg)
            mb.question.assert_not_called()  # no false conflict

        self.assertIn({"indices": [5, 6]}, dlg.merged_peaks)
        self.assertIn({"indices": [0, 1]}, dlg.merged_peaks)
        self.assertTrue(dlg._merged_dirty)

    def test_unmerge_with_unset_metadata_does_not_crash(self):
        dlg = self._merge_dialog()
        dlg.merged_peaks = [{"indices": [5, 6]}]
        dlg.peaks_metadata = None
        dlg.selected_peak_indices = {0}
        NMRDialog.unmerge_selected_peaks(dlg)  # must not raise
        self.assertEqual(dlg.merged_peaks, [{"indices": [5, 6]}])


class TestMergedPeaksPathKeying(unittest.TestCase):
    """Pathless results must not share one merged-peaks file."""

    _DATA_A = [{"atom_idx": 0, "atom_sym": "H", "shielding": 30.0}]
    _DATA_B = [{"atom_idx": 0, "atom_sym": "C", "shielding": 180.0}]

    def test_file_path_gives_sibling_json(self):
        path = NMRDialog._merged_peaks_path(
            os.path.join("some", "dir", "job.out"), self._DATA_A
        )
        self.assertEqual(path, os.path.join("some", "dir", "job-nmr_peak_info.json"))

    def test_pathless_results_get_distinct_files(self):
        path_a = NMRDialog._merged_peaks_path(None, self._DATA_A)
        path_b = NMRDialog._merged_peaks_path(None, self._DATA_B)
        self.assertNotEqual(path_a, path_b)

    def test_pathless_same_data_is_stable(self):
        self.assertEqual(
            NMRDialog._merged_peaks_path(None, self._DATA_A),
            NMRDialog._merged_peaks_path(None, list(self._DATA_A)),
        )

    def test_pathless_empty_data_does_not_crash(self):
        path = NMRDialog._merged_peaks_path(None, None)
        self.assertTrue(os.path.basename(path).startswith("nmr_merged_peaks-"))


class TestEscGoesThroughClose(unittest.TestCase):
    def test_reject_routes_to_close(self):
        """Esc (QDialog.reject) must trigger closeEvent — otherwise the
        unsaved-merge prompt is skipped and sel_timer polls a hidden
        dialog forever."""
        dlg = _make_nmr_dialog()
        dlg.close = MagicMock()
        NMRDialog.reject(dlg)
        dlg.close.assert_called_once()


class TestSelectionRemap(unittest.TestCase):
    def test_selection_follows_atoms_across_rebuild(self):
        dlg = _make_nmr_dialog()
        old = [
            (1.0, 1.0, False, [0]),
            (2.0, 1.0, False, [1]),
            (3.0, 1.0, False, [2]),
        ]
        # After a rebuild (e.g. new reference reverses the shift order)
        # the same atoms sit at different positions.
        dlg.peaks_metadata = [
            (3.0, 1.0, False, [2]),
            (2.0, 1.0, False, [1]),
            (1.0, 1.0, False, [0]),
        ]
        dlg.selected_peak_indices = {0}  # was atom 0

        NMRDialog._remap_selection_to_new_peaks(dlg, old)

        self.assertEqual(dlg.selected_peak_indices, {2})  # still atom 0

    def test_selection_of_vanished_peak_is_dropped(self):
        dlg = _make_nmr_dialog()
        old = [(1.0, 1.0, False, [0])]
        dlg.peaks_metadata = [(2.0, 1.0, False, [1])]  # atom 0 filtered out
        dlg.selected_peak_indices = {0}

        NMRDialog._remap_selection_to_new_peaks(dlg, old)

        self.assertEqual(dlg.selected_peak_indices, set())

    def test_out_of_range_old_indices_are_ignored(self):
        dlg = _make_nmr_dialog()
        dlg.peaks_metadata = [(1.0, 1.0, False, [0])]
        dlg.selected_peak_indices = {5}

        NMRDialog._remap_selection_to_new_peaks(dlg, [])

        self.assertEqual(dlg.selected_peak_indices, set())

    def test_empty_selection_is_untouched(self):
        dlg = _make_nmr_dialog()
        dlg.peaks_metadata = [(1.0, 1.0, False, [0])]
        dlg.selected_peak_indices = set()
        NMRDialog._remap_selection_to_new_peaks(dlg, [(1.0, 1.0, False, [0])])
        self.assertEqual(dlg.selected_peak_indices, set())

    def test_merged_peak_selection_survives_merge_of_others(self):
        dlg = _make_nmr_dialog()
        old = [
            (1.0, 2.0, True, [0, 1]),
            (2.0, 1.0, False, [2]),
            (3.0, 1.0, False, [3]),
        ]
        # Atoms 2+3 got merged; the [0,1] group moved to index 1.
        dlg.peaks_metadata = [
            (2.5, 2.0, True, [2, 3]),
            (1.0, 2.0, True, [0, 1]),
        ]
        dlg.selected_peak_indices = {0}  # the [0,1] merged peak

        NMRDialog._remap_selection_to_new_peaks(dlg, old)

        self.assertEqual(dlg.selected_peak_indices, {1})


if __name__ == "__main__":
    unittest.main()
