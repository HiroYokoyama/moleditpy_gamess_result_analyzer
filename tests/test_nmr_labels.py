"""
tests/test_nmr_labels.py
Covers the 3D side of NMRDialog: per-atom labels for the selected peaks
(including the optional chemical-shift line and its merged-peak form) and the
highlight spheres drawn into the viewer.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

N = gui_harness.load_isolated("nmr_analysis")


def _data():
    return [
        {"atom_idx": 0, "atom_sym": "C", "shielding": 150.0},
        {"atom_idx": 1, "atom_sym": "H", "shielding": 30.0},
        {"atom_idx": 2, "atom_sym": "H", "shielding": 30.5},
        {"atom_idx": 3, "atom_sym": "H", "shielding": 31.0},
    ]


class _LabelCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        self.plotter = MagicMock()
        self.v3d = MagicMock()
        self.v3d.plotter = self.plotter
        self.v3d.atom_positions_3d = np.array(
            [
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
            ]
        )

        self.host = MagicMock()
        self.host.mw.view_3d_manager = self.v3d
        self.host.parser.data = {"coords": [[0.0, 0.0, 0.0]] * 4}
        self.host.file_path = os.path.join(self.tmp, "job.out")

        self.dlg = N.NMRDialog(self.host, _data())
        self.dlg.parent_dlg = self.host
        self.dlg.merged_peaks_file = os.path.join(self.tmp, "merges.json")
        self.dlg.delta_ref = 0.0
        self.dlg.sigma_ref = 31.8
        self.dlg.peaks_metadata = [
            (-118.2, 1.0, False, [0]),
            (1.8, 3.0, True, [1, 2, 3]),
        ]

        patcher = patch.object(self.dlg, "plot_spectrum")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _labels(self):
        """(text, position) for every label pushed to the viewer."""
        out = []
        for call in self.plotter.add_point_labels.call_args_list:
            positions, texts = call.args[0], call.args[1]
            out.append((texts[0], positions[0]))
        return out


class TestAtomLabels(_LabelCase):
    def test_a_label_carries_the_symbol_and_index(self):
        self.dlg.add_atom_label(1, "H")
        self.assertEqual(self._labels()[0][0], "H1")

    def test_a_label_sits_above_the_atom(self):
        self.dlg.add_atom_label(1, "H")
        _, pos = self._labels()[0]
        self.assertAlmostEqual(pos[2], 0.4)

    def test_a_label_uses_the_viewer_position(self):
        self.dlg.add_atom_label(1, "H")
        _, pos = self._labels()[0]
        self.assertAlmostEqual(pos[0], 1.0)

    def test_a_shift_line_is_appended_when_given(self):
        self.dlg.add_atom_label(1, "H", "δ 1.80")
        self.assertEqual(self._labels()[0][0], "H1\nδ 1.80")

    def test_labels_are_tracked_for_later_removal(self):
        self.dlg.add_atom_label(1, "H")
        self.assertEqual(len(self.dlg._atom_labels), 1)

    def test_it_falls_back_to_the_parsed_geometry(self):
        del self.v3d.atom_positions_3d
        self.dlg.add_atom_label(1, "H")
        self.assertEqual(len(self._labels()), 1)

    def test_an_unknown_atom_is_skipped(self):
        del self.v3d.atom_positions_3d
        self.host.parser.data = {"coords": []}
        self.dlg.add_atom_label(9, "H")
        self.plotter.add_point_labels.assert_not_called()

    def test_without_a_viewer_nothing_is_drawn(self):
        self.host.mw.view_3d_manager = None
        self.dlg.add_atom_label(1, "H")
        self.plotter.add_point_labels.assert_not_called()

    def test_clearing_drops_the_tracked_labels(self):
        self.dlg.add_atom_label(1, "H")
        self.dlg.clear_atom_labels()
        self.assertEqual(self.dlg._atom_labels, [])


class TestSelectedLabels(_LabelCase):
    def test_selecting_a_peak_labels_its_atom(self):
        self.dlg.selected_peak_indices = {0}
        self.dlg.update_selected_labels()
        self.assertEqual([t for t, _ in self._labels()], ["C0"])

    def test_a_merged_peak_labels_every_atom_in_the_group(self):
        self.dlg.selected_peak_indices = {1}
        self.dlg.update_selected_labels()
        self.assertEqual([t for t, _ in self._labels()], ["H1", "H2", "H3"])

    def test_selecting_several_peaks_labels_all_of_them(self):
        self.dlg.selected_peak_indices = {0, 1}
        self.dlg.update_selected_labels()
        self.assertEqual(len(self._labels()), 4)

    def test_updating_clears_the_previous_labels(self):
        self.dlg.selected_peak_indices = {0}
        self.dlg.update_selected_labels()
        self.dlg.update_selected_labels()
        self.assertEqual(len(self.dlg._atom_labels), 1)

    def test_shift_labels_are_off_by_default(self):
        self.dlg.selected_peak_indices = {0}
        self.dlg.update_selected_labels()
        self.assertNotIn("δ", self._labels()[0][0])

    def test_an_enabled_shift_label_shows_the_atom_shift(self):
        self.dlg.selected_peak_indices = {0}
        with patch.object(self.dlg, "_shift_labels_enabled", return_value=True):
            self.dlg.update_selected_labels()
        # 0.0 + (31.8 - 150.0)
        self.assertIn("-118.20", self._labels()[0][0])

    def test_a_merged_peak_shows_both_its_own_and_the_averaged_shift(self):
        self.dlg.selected_peak_indices = {1}
        with patch.object(self.dlg, "_shift_labels_enabled", return_value=True):
            self.dlg.update_selected_labels()
        # atom 1: 31.8 - 30.0 = 1.80, merged peak sits at 1.80
        self.assertIn("→", self._labels()[0][0])

    def test_an_out_of_range_peak_is_ignored(self):
        self.dlg.selected_peak_indices = {99}
        self.dlg.update_selected_labels()
        self.plotter.add_point_labels.assert_not_called()

    def test_no_selection_leaves_the_viewer_clean(self):
        self.dlg.selected_peak_indices = set()
        self.dlg.update_selected_labels()
        self.plotter.add_point_labels.assert_not_called()


class TestHighlightSpheres(_LabelCase):
    def test_highlights_are_drawn_for_the_given_atoms(self):
        self.dlg.draw_custom_nmr_highlights_3d([1, 2])
        self.plotter.add_mesh.assert_called()

    def test_previous_highlights_are_removed_first(self):
        self.dlg.draw_custom_nmr_highlights_3d([1])
        self.plotter.remove_actor.assert_any_call("nmr_selection_highlights")

    def test_an_empty_selection_only_clears(self):
        self.dlg.draw_custom_nmr_highlights_3d([])
        self.plotter.add_mesh.assert_not_called()
        self.plotter.render.assert_called()

    def test_out_of_range_atoms_are_dropped(self):
        self.dlg.draw_custom_nmr_highlights_3d([99])
        self.plotter.add_mesh.assert_not_called()

    def test_without_a_viewer_nothing_is_drawn(self):
        self.host.mw.view_3d_manager = None
        self.dlg.draw_custom_nmr_highlights_3d([1])
        self.plotter.add_mesh.assert_not_called()

    def test_the_actor_tracker_is_reset_each_time(self):
        self.dlg._nmr_sphere_actors = [object(), object()]
        self.dlg.draw_custom_nmr_highlights_3d([])
        self.assertEqual(self.dlg._nmr_sphere_actors, [])


if __name__ == "__main__":
    unittest.main()
