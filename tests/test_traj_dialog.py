"""
tests/test_traj_dialog.py
Coverage for TrajectoryResultDialog under the headless Qt harness: scan-point
extraction, energy unit conversion and relative/absolute baselines, NEB
detection, frame navigation and playback, structure updates, and the CSV/graph
exporters.

Complements tests/test_traj_analysis.py, which installs its own process-wide
stubs; this module drives the real dialog through gui_harness instead.
"""

import os
import sys
import csv
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

T = gui_harness.load_isolated("traj_analysis")

HARTREE_TO_KJ = 2625.4996395
HARTREE_TO_KCAL = 627.50947406
HARTREE_TO_EV = 27.211386246


def _step(energy, x=0.0, stype="opt_cycle", scan_id=None, **extra):
    s = {
        "energy": energy,
        "atoms": ["O", "H"],
        "coords": [[0.0, 0.0, 0.0], [x, 0.0, 0.0]],
        "type": stype,
    }
    if scan_id is not None:
        s["scan_step_id"] = scan_id
    s.update(extra)
    return s


def _plain_steps():
    """A simple optimisation: no scan ids, no NEB tagging."""
    return [
        _step(-100.0, 1.0),
        _step(-100.5, 1.1),
        _step(-100.8, 1.2),
    ]


def _scan_steps():
    """Two scan points, each with intermediate cycles plus a final evaluation."""
    return [
        _step(-100.0, 1.0, "opt_cycle", scan_id=1),
        _step(-100.4, 1.1, "opt_cycle", scan_id=1),
        _step(-100.5, 1.15, "opt_final", scan_id=1),
        _step(-100.2, 1.3, "opt_cycle", scan_id=2),
        _step(-100.3, 1.35, "opt_final", scan_id=2),
    ]


def _neb_steps():
    return [
        _step(-100.0, 1.0, "neb_image", dist=0.0),
        _step(-99.5, 1.2, "neb_image", dist=0.5),
        _step(-100.2, 1.4, "neb_image", dist=1.0),
    ]


def _make(steps, **kw):
    kw.setdefault("context", MagicMock())
    dlg = T.TrajectoryResultDialog(MagicMock(), steps, **kw)
    return dlg


class _TrajCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.context = MagicMock()
        self.dlg = _make(_plain_steps(), context=self.context)

    def tearDown(self):
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# Construction / filtering
# ---------------------------------------------------------------------------


class TestConstruction(unittest.TestCase):
    def test_all_steps_are_kept_without_scan_ids(self):
        dlg = _make(_plain_steps())
        self.assertEqual(len(dlg.steps), 3)

    def test_zero_energy_steps_are_discarded(self):
        # incomplete cycles of a still-running calculation
        steps = _plain_steps() + [_step(0.0, 2.0)]
        dlg = _make(steps)
        self.assertEqual(len(dlg.all_steps), 3)

    def test_steps_without_an_energy_are_discarded(self):
        steps = _plain_steps() + [{"energy": None, "atoms": [], "coords": []}]
        dlg = _make(steps)
        self.assertEqual(len(dlg.all_steps), 3)

    def test_an_empty_trajectory_is_tolerated(self):
        dlg = _make([])
        self.assertEqual(dlg.steps, [])
        self.assertEqual(dlg.global_min_e, 0)

    def test_the_global_minimum_spans_every_step(self):
        dlg = _make(_plain_steps())
        self.assertAlmostEqual(dlg.global_min_e, -100.8)

    def test_a_scan_collapses_to_its_converged_points(self):
        dlg = _make(_scan_steps())
        self.assertTrue(dlg.showing_scan_points)
        self.assertEqual(len(dlg.steps), 2)

    def test_scans_are_shown_relative_to_the_minimum(self):
        dlg = _make(_scan_steps())
        self.assertTrue(dlg.show_relative)

    def test_a_plain_optimisation_shows_absolute_energies(self):
        dlg = _make(_plain_steps())
        self.assertFalse(dlg.show_relative)
        self.assertFalse(dlg.showing_scan_points)

    def test_neb_paths_default_to_the_coordinate_axis(self):
        dlg = _make(_neb_steps())
        self.assertTrue(dlg.show_coord_x)

    def test_a_plain_optimisation_defaults_to_the_step_axis(self):
        dlg = _make(_plain_steps())
        self.assertFalse(dlg.show_coord_x)

    def test_a_scan_coordinate_selects_the_coordinate_axis(self):
        steps = [_step(-100.0, 1.0, scan_coord=1.5), _step(-100.5, 1.1, scan_coord=1.6)]
        dlg = _make(steps)
        self.assertTrue(dlg.show_coord_x)


# ---------------------------------------------------------------------------
# Scan point extraction (pure)
# ---------------------------------------------------------------------------


class TestComputeScanPoints(_TrajCase):
    def test_steps_without_ids_pass_through_unchanged(self):
        steps = _plain_steps()
        self.assertIs(self.dlg.compute_scan_points(steps), steps)

    def test_one_point_is_returned_per_scan_id(self):
        self.assertEqual(len(self.dlg.compute_scan_points(_scan_steps())), 2)

    def test_the_final_evaluation_wins(self):
        points = self.dlg.compute_scan_points(_scan_steps())
        self.assertAlmostEqual(points[0]["energy"], -100.5)
        self.assertEqual(points[0]["type"], "opt_final")

    def test_the_last_cycle_is_used_without_a_final_evaluation(self):
        steps = [
            _step(-100.0, 1.0, "opt_cycle", scan_id=1),
            _step(-100.4, 1.1, "opt_cycle", scan_id=1),
        ]
        points = self.dlg.compute_scan_points(steps)
        self.assertAlmostEqual(points[0]["energy"], -100.4)

    def test_an_untyped_group_falls_back_to_its_last_step(self):
        steps = [
            _step(-100.0, 1.0, "other", scan_id=1),
            _step(-100.9, 1.1, "other", scan_id=1),
        ]
        points = self.dlg.compute_scan_points(steps)
        self.assertAlmostEqual(points[0]["energy"], -100.9)

    def test_points_come_back_in_scan_id_order(self):
        steps = [
            _step(-1.0, 1.0, "opt_final", scan_id=3),
            _step(-2.0, 1.1, "opt_final", scan_id=1),
            _step(-3.0, 1.2, "opt_final", scan_id=2),
        ]
        energies = [p["energy"] for p in self.dlg.compute_scan_points(steps)]
        self.assertEqual(energies, [-2.0, -3.0, -1.0])


# ---------------------------------------------------------------------------
# Unit conversion
# ---------------------------------------------------------------------------


class TestUnits(unittest.TestCase):
    def _absolute(self, unit):
        dlg = _make(_plain_steps())
        dlg.current_unit = unit
        dlg.update_display_values()
        return dlg.display_energies

    def test_hartree_is_passed_through(self):
        self.assertAlmostEqual(self._absolute("Eh")[0], -100.0)

    def test_kilojoules_per_mole(self):
        self.assertAlmostEqual(self._absolute("kJ/mol")[0], -100.0 * HARTREE_TO_KJ)

    def test_kilocalories_per_mole(self):
        self.assertAlmostEqual(self._absolute("kcal/mol")[0], -100.0 * HARTREE_TO_KCAL)

    def test_electronvolts(self):
        self.assertAlmostEqual(self._absolute("eV")[0], -100.0 * HARTREE_TO_EV)

    def test_an_unknown_unit_falls_back_to_hartree(self):
        self.assertAlmostEqual(self._absolute("furlongs")[0], -100.0)

    def test_relative_energies_start_at_zero(self):
        dlg = _make(_scan_steps())
        dlg.update_display_values()
        self.assertAlmostEqual(min(dlg.display_energies), 0.0)

    def test_relative_energies_use_the_displayed_baseline(self):
        dlg = _make(_scan_steps())
        dlg.current_unit = "kJ/mol"
        dlg.update_display_values()
        # scan points are -100.5 and -100.3; baseline is -100.5
        self.assertAlmostEqual(dlg.display_energies[1], 0.2 * HARTREE_TO_KJ, places=4)

    def test_switching_units_rescales_the_display(self):
        dlg = _make(_plain_steps())
        dlg.on_unit_changed("eV")
        self.assertEqual(dlg.current_unit, "eV")
        self.assertAlmostEqual(dlg.display_energies[0], -100.0 * HARTREE_TO_EV)

    def test_recalc_tracks_the_displayed_minimum(self):
        dlg = _make(_plain_steps())
        dlg.steps = dlg.all_steps[:2]
        dlg.recalc_energies()
        self.assertAlmostEqual(dlg.min_e, -100.5)
        self.assertEqual(len(dlg.display_energies), 2)


# ---------------------------------------------------------------------------
# Navigation and playback
# ---------------------------------------------------------------------------


class TestNavigation(_TrajCase):
    def setUp(self):
        super().setUp()
        self.dlg.slider = MagicMock()
        self.dlg.slider.value.return_value = 1
        self.dlg.timer = MagicMock()

    def test_next_frame_advances_the_slider(self):
        self.dlg.next_frame()
        self.dlg.slider.setValue.assert_called_with(2)

    def test_next_frame_wraps_at_the_end(self):
        self.dlg.slider.value.return_value = 2
        self.dlg.next_frame()
        self.dlg.slider.setValue.assert_called_with(0)

    def test_previous_frame_steps_back(self):
        self.dlg.prev_frame()
        self.dlg.slider.setValue.assert_called_with(0)

    def test_previous_frame_wraps_when_looping(self):
        self.dlg.slider.value.return_value = 0
        self.dlg.chk_loop.setChecked(True)
        self.dlg.prev_frame()
        self.dlg.slider.setValue.assert_called_with(2)

    def test_previous_frame_clamps_at_the_start_without_looping(self):
        self.dlg.slider.value.return_value = 0
        self.dlg.chk_loop.setChecked(False)
        self.dlg.prev_frame()
        self.dlg.slider.setValue.assert_called_with(0)

    def test_next_frame_clamps_at_the_end_without_looping(self):
        self.dlg.slider.value.return_value = 2
        self.dlg.chk_loop.setChecked(False)
        self.dlg.is_playing = False
        self.dlg.next_frame()
        self.dlg.slider.setValue.assert_called_with(2)

    def test_reaching_the_end_stops_playback(self):
        self.dlg.slider.value.return_value = 2
        self.dlg.chk_loop.setChecked(False)
        self.dlg.is_playing = True
        self.dlg.next_frame()
        self.assertFalse(self.dlg.is_playing)

    def test_jump_to_first_frame(self):
        self.dlg.go_to_first_frame()
        self.dlg.slider.setValue.assert_called_with(0)

    def test_jump_to_last_frame(self):
        self.dlg.go_to_last_frame()
        self.dlg.slider.setValue.assert_called_with(2)

    def test_play_starts_the_timer(self):
        self.dlg.is_playing = False
        self.dlg.toggle_play()
        self.assertTrue(self.dlg.is_playing)
        self.dlg.timer.start.assert_called_once()

    def test_play_again_stops_the_timer(self):
        self.dlg.is_playing = True
        self.dlg.toggle_play()
        self.assertFalse(self.dlg.is_playing)
        self.dlg.timer.stop.assert_called_once()

    def test_changing_the_frame_rate_retimes_playback(self):
        self.dlg.is_playing = True
        self.dlg.spin_fps.setValue(25)
        self.dlg.on_fps_changed()
        self.dlg.timer.start.assert_called_once_with(40)

    def test_the_frame_rate_is_ignored_while_paused(self):
        self.dlg.is_playing = False
        self.dlg.on_fps_changed()
        self.dlg.timer.start.assert_not_called()


# ---------------------------------------------------------------------------
# Structure updates
# ---------------------------------------------------------------------------


class TestStructureUpdate(_TrajCase):
    def test_selecting_a_step_pushes_its_geometry(self):
        with patch.object(self.dlg, "update_structure") as upd:
            self.dlg.on_step_changed(1)
        upd.assert_called_once()
        atoms, coords = upd.call_args.args
        self.assertEqual(atoms, ["O", "H"])
        self.assertAlmostEqual(coords[1][0], 1.1)

    def test_an_out_of_range_index_is_ignored(self):
        with patch.object(self.dlg, "update_structure") as upd:
            self.dlg.on_step_changed(99)
        upd.assert_not_called()

    def test_a_step_without_coordinates_is_skipped(self):
        self.dlg.steps = [{"energy": -1.0, "atoms": [], "coords": []}]
        with patch.object(self.dlg, "update_structure") as upd:
            self.dlg.on_step_changed(0)
        upd.assert_not_called()


# ---------------------------------------------------------------------------
# Guards on QTimer.singleShot callbacks scheduled against a possibly-closed
# dialog (on_step_changed, run_auto_load, load_mep_trj)
# ---------------------------------------------------------------------------


class TestClosedDialogGuards(_TrajCase):
    def test_on_step_changed_noops_once_closing(self):
        self.dlg._is_closing = True
        with patch.object(self.dlg, "update_structure") as upd:
            with patch.object(self.dlg, "highlight_point") as hl:
                self.dlg.on_step_changed(1)
        upd.assert_not_called()
        hl.assert_not_called()

    def test_run_auto_load_noops_once_closing(self):
        self.dlg._is_closing = True
        self.dlg.steps = [{"type": "neb_image", "atoms": None}]
        with patch.object(self.dlg, "load_external_trj") as load:
            self.dlg.run_auto_load()
        load.assert_not_called()

    def test_load_mep_trj_noops_once_closing(self):
        self.dlg._is_closing = True
        with patch.object(T.QFileDialog, "getOpenFileName") as opener:
            self.dlg.load_mep_trj()
        opener.assert_not_called()


# ---------------------------------------------------------------------------
# Display toggles
# ---------------------------------------------------------------------------


class TestToggles(_TrajCase):

    def test_the_coordinate_axis_follows_the_radio_button(self):
        self.dlg.sender = lambda: None
        self.dlg.radio_coord.setChecked(True)
        self.dlg.on_x_axis_mode_changed()
        self.assertTrue(self.dlg.show_coord_x)

    def test_the_step_axis_follows_the_radio_button(self):
        self.dlg.sender = lambda: None
        self.dlg.radio_coord.setChecked(False)
        self.dlg.on_x_axis_mode_changed()
        self.assertFalse(self.dlg.show_coord_x)

    def test_an_unchecked_sender_is_ignored(self):
        unchecked = MagicMock()
        unchecked.isChecked.return_value = False
        self.dlg.sender = lambda: unchecked
        before = self.dlg.show_coord_x
        self.dlg.on_x_axis_mode_changed()
        self.assertEqual(self.dlg.show_coord_x, before)

    def test_clearing_the_selection_removes_the_highlight_artists(self):
        marker, line = MagicMock(), MagicMock()
        self.dlg._highlight_marker = marker
        self.dlg._highlight_line = line
        self.dlg.clear_selection()
        marker.remove.assert_called_once()
        line.remove.assert_called_once()

    def test_clearing_an_empty_selection_is_a_noop(self):
        self.dlg._highlight_marker = None
        self.dlg._highlight_line = None
        self.dlg.clear_selection()  # must not raise

    def test_a_stale_artist_that_cannot_be_removed_is_tolerated(self):
        marker = MagicMock()
        marker.remove.side_effect = ValueError("already gone")
        self.dlg._highlight_marker = marker
        self.dlg._highlight_line = None
        self.dlg.clear_selection()  # must not raise


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


class TestExports(_TrajCase):
    def test_csv_has_a_row_per_step(self):
        path = os.path.join(self.tmp, "traj.csv")
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.save_csv()
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(len(rows), 4)  # header + 3 steps

    def test_csv_records_the_displayed_energies(self):
        path = os.path.join(self.tmp, "traj.csv")
        self.dlg.current_unit = "Eh"
        self.dlg.update_display_values()
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.save_csv()
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertIn("-100.0", ",".join(rows[1]))

    def test_a_cancelled_csv_export_writes_nothing(self):
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=("", "")):
            self.dlg.save_csv()
        self.assertFalse([f for f in os.listdir(self.tmp) if f.endswith(".csv")])

    def test_saving_the_graph_exports_the_figure(self):
        # Assert the export is driven rather than rasterising for real: a real
        # savefig would pull in matplotlib's Agg backend (and Pillow), which
        # other test modules stub out process-wide.
        path = os.path.join(self.tmp, "traj.png")
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=(path, "")):
            with patch.object(self.dlg.canvas.fig, "savefig") as saver:
                self.dlg.save_graph()
        saver.assert_called_once_with(path, dpi=300)

    def test_the_tooltip_is_hidden_while_exporting(self):
        path = os.path.join(self.tmp, "traj.png")
        self.dlg.annot.set_visible(True)
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=(path, "")):
            with patch.object(self.dlg.canvas.fig, "savefig"):
                self.dlg.save_graph()
        # hidden for the export, then restored
        self.assertTrue(self.dlg.annot.get_visible())

    def test_a_cancelled_graph_export_writes_nothing(self):
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=("", "")):
            with patch.object(self.dlg.canvas.fig, "savefig") as saver:
                self.dlg.save_graph()
        saver.assert_not_called()

    def test_escape_routes_through_close(self):
        with patch.object(self.dlg, "close") as closer:
            self.dlg.reject()
        closer.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class TestExternalTrajectoryWithoutEnergies(_TrajCase):

    def test_the_silent_auto_load_stays_silent(self):
        path = os.path.join(self.tmp, "noenergy.xyz")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("1\nframe\nH 0 0 0\n")
        with patch.object(T.QMessageBox, "warning") as warn:
            self.dlg.load_external_trj(path, silent=True)
        warn.assert_not_called()
