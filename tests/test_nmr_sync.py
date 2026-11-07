"""
tests/test_nmr_sync.py
Covers NMRDialog's two-way link with the 3D viewer and the remaining table
controls: the poller that mirrors a 3D atom selection onto the spectrum
(including the echo-suppression that stops it fighting its own updates),
show-all-labels mode, the clipboard copy, and the simulation control gating.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

N = gui_harness.load_isolated("nmr_analysis")
NE = sys.modules[f"{N.__package__}.nmr_export"]  # copy_table now lives here


def _data():
    return [
        {"atom_idx": 0, "atom_sym": "C", "shielding": 150.0},
        {"atom_idx": 1, "atom_sym": "H", "shielding": 30.0},
        {"atom_idx": 2, "atom_sym": "H", "shielding": 30.5},
        {"atom_idx": 3, "atom_sym": "H", "shielding": 31.0},
    ]


class _SyncCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        self.e3d = MagicMock()
        self.e3d.selected_atoms_3d = set()
        self.e3d.selected_atoms_for_measurement = []

        self.context = MagicMock()
        self.host = MagicMock()
        self.host.context = self.context
        self.host.mw.edit_3d_manager = self.e3d
        self.host.file_path = os.path.join(self.tmp, "job.out")

        self.dlg = N.NMRDialog(self.host, _data())
        self.dlg.parent_dlg = self.host
        self.dlg.settings_file = os.path.join(self.tmp, "settings.json")
        self.dlg.merged_peaks_file = os.path.join(self.tmp, "merges.json")
        self.dlg.peaks_metadata = [
            (-118.2, 1.0, False, [0]),
            (1.8, 3.0, True, [1, 2, 3]),
        ]
        self.dlg._last_synced_mw_selection = frozenset()

        for name in (
            "plot_spectrum",
            "highlight_selected_peaks",
            "update_selected_labels",
        ):
            patcher = patch.object(self.dlg, name)
            patcher.start()
            self.addCleanup(patcher.stop)


# ---------------------------------------------------------------------------
# 3D selection -> spectrum
# ---------------------------------------------------------------------------


class TestExternalSelection(_SyncCase):
    def test_selecting_an_atom_in_3d_selects_its_peak(self):
        self.e3d.selected_atoms_3d = {2}
        self.dlg._check_external_selection()
        self.assertEqual(self.dlg.selected_peak_indices, {1})

    def test_selecting_atoms_from_two_peaks_selects_both(self):
        self.e3d.selected_atoms_3d = {0, 2}
        self.dlg._check_external_selection()
        self.assertEqual(self.dlg.selected_peak_indices, {0, 1})

    def test_a_measurement_selection_also_counts(self):
        self.e3d.selected_atoms_for_measurement = [2]
        self.dlg._check_external_selection()
        self.assertEqual(self.dlg.selected_peak_indices, {1})

    def test_non_integer_measurement_entries_are_ignored(self):
        self.e3d.selected_atoms_for_measurement = [("distance", 1, 2)]
        self.dlg._check_external_selection()
        self.assertEqual(self.dlg.selected_peak_indices, set())

    def test_an_unchanged_3d_selection_is_not_reapplied(self):
        self.e3d.selected_atoms_3d = {2}
        self.dlg._check_external_selection()
        self.dlg.highlight_selected_peaks.reset_mock()
        # polling again with the same 3D state must do nothing
        self.dlg._check_external_selection()
        self.dlg.highlight_selected_peaks.assert_not_called()

    def test_clearing_the_3d_selection_clears_the_spectrum(self):
        self.e3d.selected_atoms_3d = {2}
        self.dlg._check_external_selection()
        self.e3d.selected_atoms_3d = set()
        with patch.object(self.dlg, "clear_peak_selection") as clear:
            self.dlg._check_external_selection()
        clear.assert_called_once()

    def test_the_sync_does_not_write_back_to_the_3d_selection(self):
        # is_external_sync=True stops the update echoing into the viewer
        self.e3d.selected_atoms_3d = {2}
        self.dlg._check_external_selection()
        self.dlg.update_selected_labels.assert_called_once_with(is_external_sync=True)

    def test_a_selection_hitting_the_same_peak_is_not_reapplied(self):
        self.e3d.selected_atoms_3d = {1}
        self.dlg._check_external_selection()
        self.dlg.highlight_selected_peaks.reset_mock()
        # atoms 1 and 2 belong to the same merged peak, so the peak selection
        # is unchanged even though the 3D selection differs
        self.e3d.selected_atoms_3d = {1, 2}
        self.dlg._check_external_selection()
        self.dlg.highlight_selected_peaks.assert_not_called()

    def test_a_host_without_a_viewer_is_tolerated(self):
        host = MagicMock(spec=[])
        self.dlg.parent_dlg = host
        self.dlg._check_external_selection()  # must not raise


# ---------------------------------------------------------------------------
# Show all labels
# ---------------------------------------------------------------------------


class TestShowAllLabels(_SyncCase):
    def test_enabling_selects_every_peak(self):
        self.dlg.show_all_mode = False
        self.dlg.chk_show_all_labels.setChecked(True)
        self.dlg.toggle_all_labels()
        self.assertEqual(self.dlg.selected_peak_indices, {0, 1})
        self.assertTrue(self.dlg.show_all_mode)

    def test_enabling_labels_them_in_3d(self):
        self.dlg.chk_show_all_labels.setChecked(True)
        self.dlg.toggle_all_labels()
        self.dlg.update_selected_labels.assert_called_once()

    def test_disabling_clears_the_selection(self):
        self.dlg.show_all_mode = True
        self.dlg.chk_show_all_labels.setChecked(False)
        with patch.object(self.dlg, "clear_peak_selection") as clear:
            self.dlg.toggle_all_labels()
        self.assertFalse(self.dlg.show_all_mode)
        clear.assert_called_once()

    def test_it_falls_back_to_the_displayed_rows_without_peak_data(self):
        self.dlg.peaks_metadata = None
        self.dlg.chk_show_all_labels.setChecked(True)
        self.dlg.toggle_all_labels()
        self.assertEqual(len(self.dlg.selected_peak_indices), 4)


# ---------------------------------------------------------------------------
# Table controls
# ---------------------------------------------------------------------------


class TestTableControls(_SyncCase):
    def test_copying_puts_the_table_on_the_clipboard(self):
        clipboard = MagicMock()
        with patch.object(NE.QApplication, "clipboard", return_value=clipboard):
            self.dlg.copy_table()
        text = clipboard.setText.call_args.args[0]
        self.assertTrue(text.startswith("Idx\tNucleus\tShielding\tShift\n"))

    def test_copying_is_reported(self):
        with patch.object(NE.QApplication, "clipboard", return_value=MagicMock()):
            self.dlg.copy_table()
        self.context.show_status_message.assert_called()


    def test_it_is_skipped_before_the_control_exists(self):
        self.dlg.chk_real_spectrum = None
        self.dlg.toggle_simulation_controls()  # must not raise


if __name__ == "__main__":
    unittest.main()
