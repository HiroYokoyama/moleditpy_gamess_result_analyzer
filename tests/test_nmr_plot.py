"""
tests/test_nmr_plot.py
Coverage for _NMRPlotMixin: peak building (_get_current_peaks,
_calculate_peak_selection_from_atoms, _remap_selection_to_new_peaks),
highlight helpers, external-selection polling, label-shift toggle, and the
clear/toggle-all-labels helpers.

All state-holding methods are exercised directly on the mixin class via a
minimal stand-in that carries only the attributes the mixin reads.  3D-viewer
and Qt-drawing calls are replaced with MagicMocks so the test stays headless.

NMR plot-rendering paths (plot_spectrum, plot_real_spectrum,
highlight_selected_peaks) are exercised at the matplotlib-object level only;
the canvas/renderer is not involved.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

# Load the nmr_analysis module through the harness (which also loads the
# nmr_plot and nmr_export siblings as package members).
N = gui_harness.load_isolated("nmr_analysis")
NMRDialog = N.NMRDialog

# ------------------------------------------------------------------
# Minimal stand-in for the dialog state _NMRPlotMixin reads
# ------------------------------------------------------------------


def _data():
    return [
        {"atom_idx": 0, "atom_sym": "C", "shielding": 150.0},
        {"atom_idx": 1, "atom_sym": "H", "shielding": 30.0},
        {"atom_idx": 2, "atom_sym": "H", "shielding": 30.5},
        {"atom_idx": 3, "atom_sym": "H", "shielding": 31.0},
    ]


def _couplings():
    return [
        {"atom_idx1": 1, "atom_idx2": 2, "coupling": 7.5},
        {"atom_idx1": 1, "atom_idx2": 3, "coupling": 1.2},
    ]


class _DialogBase(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name
        self.addCleanup(self._tmpdir.cleanup)

        host = MagicMock()
        host.file_path = os.path.join(self.tmp, "job.out")
        self.dlg = NMRDialog(host, _data(), file_path=None)
        self.dlg.parent_dlg = host
        self.dlg.settings_file = os.path.join(self.tmp, "settings.json")
        self.dlg.merged_peaks_file = os.path.join(self.tmp, "merges.json")
        self.dlg.merged_peaks = []

        self._pp = patch.object(self.dlg, "plot_spectrum")
        self._pp.start()
        self.addCleanup(self._pp.stop)


# ===========================================================================
# _get_current_peaks
# ===========================================================================


class TestGetCurrentPeaks(_DialogBase):
    def setUp(self):
        super().setUp()
        self.dlg.delta_ref = 0.0
        self.dlg.sigma_ref = 31.8
        self.dlg.displayed_data = list(_data())

    def test_returns_one_peak_per_displayed_atom_when_no_merges(self):
        peaks = self.dlg._get_current_peaks()
        self.assertEqual(len(peaks), 4)

    def test_peaks_are_sorted_descending_by_shift(self):
        peaks = self.dlg._get_current_peaks()
        shifts = [p[0] for p in peaks]
        self.assertEqual(shifts, sorted(shifts, reverse=True))

    def test_shift_formula_delta_ref_plus_sigma_ref_minus_shielding(self):
        peaks = self.dlg._get_current_peaks()
        all_shifts = {round(p[0], 2) for p in peaks}
        self.assertIn(-118.2, all_shifts)
        self.assertIn(1.8, all_shifts)

    def test_individual_peak_has_intensity_one(self):
        peaks = self.dlg._get_current_peaks()
        for p in peaks:
            if not p[2]:
                self.assertEqual(p[1], 1.0)

    def test_individual_peak_is_not_marked_merged(self):
        peaks = self.dlg._get_current_peaks()
        for p in peaks:
            self.assertFalse(p[2])

    def test_merged_group_collapses_into_one_peak(self):
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        peaks = self.dlg._get_current_peaks()
        self.assertEqual(len(peaks), 2)

    def test_merged_peak_intensity_equals_group_size(self):
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        peaks = self.dlg._get_current_peaks()
        merged = [p for p in peaks if p[2]]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0][1], 3.0)

    def test_merged_peak_is_flagged_as_merged(self):
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        peaks = self.dlg._get_current_peaks()
        merged = [p for p in peaks if p[2]]
        self.assertTrue(merged[0][2])

    def test_merged_peak_shift_is_average_of_group_shieldings(self):
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        peaks = self.dlg._get_current_peaks()
        merged = [p for p in peaks if p[2]][0]
        self.assertAlmostEqual(merged[0], 1.3, places=5)

    def test_group_with_atoms_absent_from_data_is_skipped(self):
        self.dlg.merged_peaks = [{"indices": [98, 99]}]
        peaks = self.dlg._get_current_peaks()
        self.assertEqual([p for p in peaks if p[2]], [])

    def test_group_not_in_displayed_data_is_excluded(self):
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        self.dlg.displayed_data = [d for d in _data() if d["atom_sym"] == "C"]
        peaks = self.dlg._get_current_peaks()
        self.assertEqual([p for p in peaks if p[2]], [])

    def test_group_partially_in_displayed_data_is_still_included(self):
        self.dlg.merged_peaks = [{"indices": [1, 2, 3]}]
        self.dlg.displayed_data = [d for d in _data() if d["atom_idx"] in {0, 1, 2}]
        peaks = self.dlg._get_current_peaks()
        self.assertEqual(len([p for p in peaks if p[2]]), 1)

    def test_empty_displayed_data_returns_no_peaks(self):
        self.dlg.displayed_data = []
        self.assertEqual(self.dlg._get_current_peaks(), [])

    def test_each_peak_carries_a_list_of_atom_indices(self):
        peaks = self.dlg._get_current_peaks()
        for p in peaks:
            self.assertIsInstance(p[3], list)
            self.assertTrue(all(isinstance(i, int) for i in p[3]))


# ===========================================================================
# _calculate_peak_selection_from_atoms
# ===========================================================================


class TestCalculatePeakSelectionFromAtoms(_DialogBase):
    def _metadata(self):
        return [
            (100.0, 1.0, False, [0]),
            (1.8, 3.0, True, [1, 2, 3]),
        ]

    def test_empty_peaks_metadata_returns_empty_set(self):
        self.dlg.peaks_metadata = []
        self.assertEqual(self.dlg._calculate_peak_selection_from_atoms({0}), set())

    def test_absent_peaks_metadata_returns_empty_set(self):
        self.dlg.peaks_metadata = None
        self.assertEqual(self.dlg._calculate_peak_selection_from_atoms({0}), set())

    def test_matching_single_atom_selects_its_peak(self):
        self.dlg.peaks_metadata = self._metadata()
        self.assertEqual(self.dlg._calculate_peak_selection_from_atoms({0}), {0})

    def test_atom_in_merged_group_selects_that_group_peak(self):
        self.dlg.peaks_metadata = self._metadata()
        self.assertEqual(self.dlg._calculate_peak_selection_from_atoms({2}), {1})

    def test_atoms_spanning_two_peaks_select_both(self):
        self.dlg.peaks_metadata = self._metadata()
        self.assertEqual(self.dlg._calculate_peak_selection_from_atoms({0, 3}), {0, 1})

    def test_unknown_atom_selects_nothing(self):
        self.dlg.peaks_metadata = self._metadata()
        self.assertEqual(self.dlg._calculate_peak_selection_from_atoms({99}), set())

    def test_string_index_is_coerced_to_int(self):
        self.dlg.peaks_metadata = self._metadata()
        self.assertEqual(self.dlg._calculate_peak_selection_from_atoms({"2"}), {1})

    def test_empty_target_set_selects_nothing(self):
        self.dlg.peaks_metadata = self._metadata()
        self.assertEqual(self.dlg._calculate_peak_selection_from_atoms(set()), set())


# ===========================================================================
# _remap_selection_to_new_peaks
# ===========================================================================


class TestRemapSelectionToNewPeaks(_DialogBase):
    def _meta(self):
        return [
            (100.0, 1.0, False, [0]),
            (1.8, 1.0, True, [1, 2, 3]),
        ]

    def test_remap_empty_selection_is_noop(self):
        old = self._meta()
        self.dlg.peaks_metadata = old
        self.dlg.selected_peak_indices = set()
        self.dlg._remap_selection_to_new_peaks(old)
        self.assertEqual(self.dlg.selected_peak_indices, set())

    def test_remap_follows_atoms_when_rows_reordered(self):
        old = self._meta()
        self.dlg.peaks_metadata = old
        self.dlg.selected_peak_indices = {1}
        self.dlg.peaks_metadata = [old[1], old[0]]
        self.dlg._remap_selection_to_new_peaks(old)
        self.assertEqual(self.dlg.selected_peak_indices, {0})

    def test_remap_drops_peaks_that_disappeared(self):
        old = self._meta()
        self.dlg.selected_peak_indices = {1}
        self.dlg.peaks_metadata = [old[0]]
        self.dlg._remap_selection_to_new_peaks(old)
        self.assertEqual(self.dlg.selected_peak_indices, set())

    def test_remap_ignores_out_of_range_old_indices(self):
        old = self._meta()
        self.dlg.peaks_metadata = old
        self.dlg.selected_peak_indices = {99}
        self.dlg._remap_selection_to_new_peaks(old)
        self.assertEqual(self.dlg.selected_peak_indices, set())


# ===========================================================================
# _shift_labels_enabled
# ===========================================================================


class TestShiftLabelsEnabled(_DialogBase):
    def test_default_stub_checkbox_returns_false(self):
        # The harness supplies a _CheckBox stub (unchecked by default)
        self.assertFalse(self.dlg._shift_labels_enabled())

    def test_checked_checkbox_returns_true(self):
        chk = MagicMock()
        chk.isChecked.return_value = True
        self.dlg.chk_label_shifts = chk
        self.assertTrue(self.dlg._shift_labels_enabled())

    def test_unchecked_checkbox_returns_false(self):
        chk = MagicMock()
        chk.isChecked.return_value = False
        self.dlg.chk_label_shifts = chk
        self.assertFalse(self.dlg._shift_labels_enabled())

    def test_checkbox_raising_runtime_error_returns_false(self):
        chk = MagicMock()
        chk.isChecked.side_effect = RuntimeError("deleted")
        self.dlg.chk_label_shifts = chk
        self.assertFalse(self.dlg._shift_labels_enabled())


# ===========================================================================
# on_label_shifts_toggled
# ===========================================================================


class TestOnLabelShiftsToggled(_DialogBase):
    def test_calls_update_selected_labels_when_selection_nonempty(self):
        self.dlg.selected_peak_indices = {0}
        with patch.object(self.dlg, "update_selected_labels") as upd:
            self.dlg.on_label_shifts_toggled()
        upd.assert_called_once_with(is_external_sync=True)

    def test_does_nothing_when_selection_empty(self):
        self.dlg.selected_peak_indices = set()
        with patch.object(self.dlg, "update_selected_labels") as upd:
            self.dlg.on_label_shifts_toggled()
        upd.assert_not_called()


# ===========================================================================
# select_peaks_by_atom_indices
# ===========================================================================


class TestSelectPeaksByAtomIndices(_DialogBase):
    def setUp(self):
        super().setUp()
        self.dlg.peaks_metadata = [
            (100.0, 1.0, False, [0]),
            (1.8, 3.0, True, [1, 2, 3]),
        ]
        self.dlg.selected_peak_indices = set()
        self.dlg.highlight_selected_peaks = MagicMock()
        self.dlg.update_selected_labels = MagicMock()

    def test_matching_atom_updates_selection_and_highlights(self):
        self.dlg.select_peaks_by_atom_indices({1})
        self.assertEqual(self.dlg.selected_peak_indices, {1})
        self.dlg.highlight_selected_peaks.assert_called_once()

    def test_no_change_skips_redraw(self):
        self.dlg.selected_peak_indices = {1}
        self.dlg.select_peaks_by_atom_indices({1})
        self.dlg.highlight_selected_peaks.assert_not_called()

    def test_unknown_atom_clears_selection(self):
        self.dlg.selected_peak_indices = {0}
        self.dlg.select_peaks_by_atom_indices({99})
        self.assertEqual(self.dlg.selected_peak_indices, set())


# ===========================================================================
# _check_external_selection
# ===========================================================================


class TestCheckExternalSelection(_DialogBase):
    def _host_with_atoms(self, selected_3d=(), meas_atoms=()):
        host = MagicMock()
        e3d = MagicMock()
        e3d.selected_atoms_3d = set(selected_3d)
        e3d.selected_atoms_for_measurement = list(meas_atoms)
        e3d.measurement_mode = False
        host.mw.edit_3d_manager = e3d
        return host

    def setUp(self):
        super().setUp()
        self.dlg.peaks_metadata = [
            (100.0, 1.0, False, [0]),
            (1.8, 3.0, True, [1, 2, 3]),
        ]
        self.dlg.selected_peak_indices = set()
        self.dlg._last_synced_mw_selection = None
        self.dlg.highlight_selected_peaks = MagicMock()
        self.dlg.update_selected_labels = MagicMock()
        self.dlg.clear_peak_selection = MagicMock()

    def test_no_mw_attribute_is_a_noop(self):
        host = MagicMock(spec=[])
        self.dlg.parent_dlg = host
        self.dlg._check_external_selection()

    def test_unchanged_3d_selection_is_a_noop(self):
        host = self._host_with_atoms(selected_3d={1})
        self.dlg.parent_dlg = host
        self.dlg._last_synced_mw_selection = frozenset({1})
        self.dlg._check_external_selection()
        self.dlg.highlight_selected_peaks.assert_not_called()

    def test_new_atom_selection_updates_peak_selection(self):
        host = self._host_with_atoms(selected_3d={0})
        self.dlg.parent_dlg = host
        self.dlg._last_synced_mw_selection = frozenset()
        self.dlg._check_external_selection()
        self.assertEqual(self.dlg.selected_peak_indices, {0})

    def test_cleared_3d_selection_calls_clear_peak_selection(self):
        host = self._host_with_atoms(selected_3d=set())
        self.dlg.parent_dlg = host
        self.dlg.selected_peak_indices = {0}
        self.dlg._last_synced_mw_selection = frozenset({0})
        self.dlg._check_external_selection()
        self.dlg.clear_peak_selection.assert_called_once()

    def test_measurement_atoms_are_included_in_selection(self):
        host = self._host_with_atoms(selected_3d=set(), meas_atoms=[2])
        self.dlg.parent_dlg = host
        self.dlg._last_synced_mw_selection = frozenset()
        self.dlg._check_external_selection()
        self.assertEqual(self.dlg.selected_peak_indices, {1})


# ===========================================================================
# clear_peak_selection
# ===========================================================================


class TestClearPeakSelection(_DialogBase):
    def setUp(self):
        super().setUp()
        self.dlg.selected_peak_indices = {0, 1}
        self.dlg.highlight_artists = []
        self.dlg._atom_labels = []
        self.dlg._nmr_label_names = []
        self.dlg._nmr_sphere_actors = []
        self.dlg.clear_atom_labels = MagicMock()

    def test_clears_selected_indices(self):
        self.dlg.clear_peak_selection()
        self.assertEqual(len(self.dlg.selected_peak_indices), 0)

    def test_calls_clear_atom_labels(self):
        self.dlg.clear_peak_selection()
        self.dlg.clear_atom_labels.assert_called_once()

    def test_calls_canvas_draw_idle_when_canvas_present(self):
        canvas = MagicMock()
        self.dlg.canvas = canvas
        self.dlg.clear_peak_selection()
        canvas.draw_idle.assert_called()

    def test_tolerates_failing_highlight_artist_remove(self):
        artist = MagicMock()
        artist.remove.side_effect = RuntimeError("already gone")
        self.dlg.highlight_artists = [artist]
        self.dlg.clear_peak_selection()
        self.assertEqual(self.dlg.highlight_artists, [])

    def test_syncs_3d_selection_clear_when_mw_present(self):
        host = MagicMock()
        e3d = MagicMock()
        e3d.selected_atoms_3d = {1}
        host.mw.edit_3d_manager = e3d
        self.dlg.parent_dlg = host
        self.dlg.clear_peak_selection()
        self.assertEqual(len(e3d.selected_atoms_3d), 0)


# ===========================================================================
# toggle_all_labels
# ===========================================================================


class TestToggleAllLabels(_DialogBase):
    def setUp(self):
        super().setUp()
        self.dlg.peaks_metadata = [
            (1.0, 1.0, False, [1]),
            (2.0, 1.0, False, [2]),
        ]
        self.dlg.selected_peak_indices = set()
        self.dlg.highlight_selected_peaks = MagicMock()
        self.dlg.update_selected_labels = MagicMock()
        self.dlg.clear_peak_selection = MagicMock()

    def _checked_chk(self, checked):
        chk = MagicMock()
        chk.isChecked.return_value = checked
        return chk

    def test_show_all_true_sets_show_all_mode(self):
        self.dlg.chk_show_all_labels = self._checked_chk(True)
        self.dlg.toggle_all_labels()
        self.assertTrue(self.dlg.show_all_mode)

    def test_show_all_true_selects_every_peak(self):
        self.dlg.chk_show_all_labels = self._checked_chk(True)
        self.dlg.toggle_all_labels()
        self.assertEqual(self.dlg.selected_peak_indices, {0, 1})

    def test_show_all_false_clears_selection(self):
        self.dlg.chk_show_all_labels = self._checked_chk(False)
        self.dlg.show_all_mode = True
        self.dlg.toggle_all_labels()
        self.assertFalse(self.dlg.show_all_mode)
        self.dlg.clear_peak_selection.assert_called_once()

    def test_show_all_falls_back_to_displayed_data_when_no_peaks_metadata(self):
        self.dlg.peaks_metadata = None
        self.dlg.displayed_data = [{"atom_idx": 0}, {"atom_idx": 1}, {"atom_idx": 2}]
        self.dlg.chk_show_all_labels = self._checked_chk(True)
        self.dlg.toggle_all_labels()
        self.assertEqual(self.dlg.selected_peak_indices, {0, 1, 2})


# ===========================================================================
# plot_spectrum — smoke test via a real matplotlib Figure (no renderer)
# ===========================================================================


class TestPlotSpectrumSmoke(_DialogBase):
    def _figure(self):
        import matplotlib.figure as mf

        return mf.Figure()

    def setUp(self):
        super().setUp()
        # Stop the patch so the real plot_spectrum runs
        self._pp.stop()

        self.dlg.delta_ref = 0.0
        self.dlg.sigma_ref = 31.8
        self.dlg.merged_peaks = []
        self.dlg.displayed_data = list(_data())
        self.dlg.selected_peak_indices = set()
        self.dlg.highlight_artists = []
        self.dlg.peaks_metadata = []
        self.dlg.current_shifts = []
        self.dlg.show_all_mode = False
        self.dlg.figure = self._figure()
        self.dlg.canvas = MagicMock()

        chk_real = MagicMock()
        chk_real.isChecked.return_value = False
        self.dlg.chk_real_spectrum = chk_real

        chk_auto = MagicMock()
        chk_auto.isChecked.return_value = True
        self.dlg.chk_auto_x = chk_auto

    def tearDown(self):
        # Re-start the patch so _DialogBase.tearDown does not error
        try:
            self._pp.start()
        except RuntimeError:
            pass
        super().tearDown()

    def test_stick_spectrum_populates_axes(self):
        self.dlg.plot_spectrum()
        self.assertEqual(len(self.dlg.figure.axes), 1)

    def test_no_peaks_writes_no_data_text(self):
        self.dlg.displayed_data = []
        self.dlg.plot_spectrum()
        ax = self.dlg.figure.axes[0]
        texts = [t.get_text() for t in ax.texts]
        self.assertTrue(any("No data" in t for t in texts))

    def test_peaks_produce_stem_lines(self):
        self.dlg.plot_spectrum()
        ax = self.dlg.figure.axes[0]
        self.assertGreater(len(ax.lines) + len(ax.collections), 0)

    def test_title_includes_nmr(self):
        self.dlg.plot_spectrum()
        ax = self.dlg.figure.axes[0]
        self.assertIn("NMR", ax.get_title())

    def test_x_axis_is_reversed_in_auto_mode(self):
        self.dlg.plot_spectrum()
        ax = self.dlg.figure.axes[0]
        xleft, xright = ax.get_xlim()
        self.assertGreater(xleft, xright)


# ===========================================================================
# Coupling simulation — worker thread and cancel
# ===========================================================================


def _nmrsim_installed():
    try:
        import nmrsim  # noqa: F401  pylint: disable=unused-import,import-outside-toplevel
    except ImportError:
        return False
    return True


# ===========================================================================
# on_peak_click
# ===========================================================================


class TestOnPeakClick(_DialogBase):
    def setUp(self):
        super().setUp()
        self.dlg.current_shifts = [5.0, 3.0, 1.0]
        self.dlg.selected_peak_indices = set()
        self.dlg._last_highlight_atoms = set()
        self.dlg.highlight_selected_peaks = MagicMock()
        self.dlg.update_selected_labels = MagicMock()
        # QApplication is imported at module level in nmr_plot.py, not nmr_analysis
        self._NP = sys.modules[N.__package__ + ".nmr_plot"]

    def _event(self, button=1, xdata=5.0, xlim=(10.0, 0.0)):
        ev = MagicMock()
        ev.button = button
        ev.xdata = xdata
        ev.inaxes.get_xlim.return_value = xlim
        return ev

    def test_non_left_click_is_ignored(self):
        ev = self._event(button=3)
        self.dlg.on_peak_click(ev)
        self.dlg.highlight_selected_peaks.assert_not_called()

    def test_click_outside_axes_xdata_none_is_ignored(self):
        ev = self._event(xdata=None)
        self.dlg.on_peak_click(ev)
        self.dlg.highlight_selected_peaks.assert_not_called()

    def test_click_far_from_any_peak_is_ignored(self):
        ev = self._event(xdata=9.9)
        self.dlg.on_peak_click(ev)
        self.dlg.highlight_selected_peaks.assert_not_called()

    def test_click_on_unselected_peak_selects_it(self):
        with patch.object(self._NP.QApplication, "keyboardModifiers", return_value=0):
            self.dlg.on_peak_click(self._event(xdata=5.0))
        self.assertEqual(self.dlg.selected_peak_indices, {0})

    def test_click_on_only_selected_peak_deselects_it(self):
        self.dlg.selected_peak_indices = {0}
        with patch.object(self._NP.QApplication, "keyboardModifiers", return_value=0):
            self.dlg.on_peak_click(self._event(xdata=5.0))
        self.assertEqual(self.dlg.selected_peak_indices, set())

    def test_ctrl_click_adds_to_selection(self):
        self.dlg.selected_peak_indices = {0}
        ctrl = self._NP.Qt.KeyboardModifier.ControlModifier
        with patch.object(
            self._NP.QApplication, "keyboardModifiers", return_value=ctrl
        ):
            self.dlg.on_peak_click(self._event(xdata=3.0))
        self.assertIn(1, self.dlg.selected_peak_indices)

    def test_ctrl_click_on_selected_peak_removes_it(self):
        self.dlg.selected_peak_indices = {0, 1}
        ctrl = self._NP.Qt.KeyboardModifier.ControlModifier
        with patch.object(
            self._NP.QApplication, "keyboardModifiers", return_value=ctrl
        ):
            self.dlg.on_peak_click(self._event(xdata=5.0))
        self.assertNotIn(0, self.dlg.selected_peak_indices)


if __name__ == "__main__":
    unittest.main()


