"""
tests/test_force_dialogs.py
Coverage for force_analysis under the headless Qt harness: the convergence
graph dialog (metric selection and the CSV export of the threshold table) and
ForceViewerDialog (vector scaling, visualisation toggle, trajectory
navigation and settings).

Complements tests/test_force_analysis.py, which installs its own process-wide
stubs; this module drives the real dialogs through gui_harness.
"""

import os
import sys
import csv
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

F = gui_harness.load_isolated("force_analysis")


def _conv(value, tol, converged):
    # The parser stores the raw GAMESS verdict string ("YES"/"NO"), not a bool.
    return {"value": value, "tolerance": tol, "converged": "YES" if converged else "NO"}


def _steps(n=3):
    steps = []
    for i in range(n):
        done = i == n - 1
        steps.append(
            {
                "energy": -76.0 - i * 0.01,
                "atoms": ["O", "H"],
                "coords": [[0.0, 0.0, 0.0], [0.96 + i * 0.01, 0.0, 0.0]],
                "convergence": {
                    "rms gradient": _conv(0.01 / (i + 1), 0.0001, done),
                    "max gradient": _conv(0.02 / (i + 1), 0.0003, done),
                    "rms step": _conv(0.05 / (i + 1), 0.002, done),
                    "max step": _conv(0.08 / (i + 1), 0.004, done),
                    "energy change": _conv(-0.01 / (i + 1), 0.000005, done),
                },
            }
        )
    return steps


def _gradients():
    return [
        {"atom_idx": 0, "atom_sym": "O", "grad": [0.01, 0.0, 0.0]},
        {"atom_idx": 1, "atom_sym": "H", "grad": [-0.01, 0.002, 0.0]},
    ]


# ---------------------------------------------------------------------------
# ConvergenceGraphDialog
# ---------------------------------------------------------------------------


class _ConvCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.host = MagicMock()
        self.dlg = F.ConvergenceGraphDialog(self.host, _steps(), current_idx=1)

    def _export(self, path):
        """QFileDialog is imported lazily in the method; QMessageBox at module level."""
        file_dialog = MagicMock()
        file_dialog.getSaveFileName.return_value = (path, "")
        message_box = MagicMock()
        with gui_harness.qt_available(QFileDialog=file_dialog):
            with patch.object(F, "QMessageBox", message_box):
                self.dlg.export_csv()
        return message_box


class TestConvergenceGraph(_ConvCase):
    def test_the_steps_are_retained(self):
        self.assertEqual(len(self.dlg.traj_steps), 3)

    def test_every_metric_is_offered_plus_an_all_option(self):
        self.assertEqual(self.dlg.metric_combo.count(), 6)

    def test_all_is_the_default_metric(self):
        self.assertEqual(self.dlg.metric_combo.currentText(), "All")

    def test_selecting_one_metric_redraws(self):
        self.dlg.metric_combo.setCurrentText("RMS Grad")
        self.dlg.redraw_graph()  # must not raise

    def test_an_empty_metric_selection_falls_back_to_all(self):
        self.dlg.metric_combo.clear()
        self.dlg.redraw_graph()  # must not raise

    def test_a_trajectory_without_convergence_data_still_draws(self):
        dlg = F.ConvergenceGraphDialog(self.host, [{"energy": -76.0}])
        dlg.redraw_graph()  # must not raise

    def test_the_csv_has_a_row_per_step(self):
        path = os.path.join(self.tmp, "conv.csv")
        self._export(path)
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(len(rows), 4)  # header + 3 steps

    def test_the_csv_records_value_tolerance_and_verdict_per_metric(self):
        path = os.path.join(self.tmp, "conv.csv")
        self._export(path)
        with open(path, encoding="utf-8") as fh:
            header = next(csv.reader(fh))
        for label in ("RMS Grad", "MAX Grad", "RMS Step", "MAX Step", "Energy Change"):
            self.assertIn(f"{label} Value", header)
            self.assertIn(f"{label} Tolerance", header)
            self.assertIn(f"{label} Converged", header)

    def test_the_csv_numbers_steps_from_one(self):
        path = os.path.join(self.tmp, "conv.csv")
        self._export(path)
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual([r["Step"] for r in rows], ["1", "2", "3"])

    def test_a_missing_metric_leaves_blank_cells(self):
        steps = _steps(1)
        del steps[0]["convergence"]["rms step"]
        dlg = F.ConvergenceGraphDialog(self.host, steps)
        self.dlg = dlg
        path = os.path.join(self.tmp, "conv.csv")
        self._export(path)
        with open(path, encoding="utf-8") as fh:
            row = next(csv.DictReader(fh))
        self.assertEqual(row["RMS Step Value"], "")

    def test_a_non_numeric_value_is_blanked(self):
        steps = _steps(1)
        steps[0]["convergence"]["rms gradient"]["value"] = "n/a"
        dlg = F.ConvergenceGraphDialog(self.host, steps)
        self.dlg = dlg
        path = os.path.join(self.tmp, "conv.csv")
        self._export(path)
        with open(path, encoding="utf-8") as fh:
            row = next(csv.DictReader(fh))
        self.assertEqual(row["RMS Grad Value"], "")

    def test_exporting_without_convergence_data_warns(self):
        self.dlg = F.ConvergenceGraphDialog(self.host, [{"energy": -76.0}])
        box = self._export(os.path.join(self.tmp, "conv.csv"))
        box.warning.assert_called_once()

    def test_a_cancelled_export_writes_nothing(self):
        self._export("")
        self.assertFalse([f for f in os.listdir(self.tmp) if f.endswith(".csv")])


# ---------------------------------------------------------------------------
# ForceViewerDialog
# ---------------------------------------------------------------------------


class _ForceCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        saved = F.__file__
        F.__file__ = os.path.join(self.tmp, "force_analysis.py")
        self.addCleanup(lambda: setattr(F, "__file__", saved))

        self.plotter = MagicMock()
        self.host = MagicMock()
        self.host.mw.plotter = self.plotter
        # clear_vectors resolves the main window through the plugin context
        # first, so point that at the same mock 3D view.
        self.host.context.get_main_window.return_value = self.host.mw
        self.host.file_path = os.path.join(self.tmp, "job.out")

        self.parser = MagicMock()
        self.parser.data = {
            "scan_steps": _steps(),
            "coords": [[0.0, 0.0, 0.0], [0.96, 0.0, 0.0]],
            "atoms": ["O", "H"],
            # With a trajectory present the dialog opens on the "final" slot and
            # re-reads the gradients from the parser rather than the argument.
            "gradients": _gradients(),
        }

        self.dlg = F.ForceViewerDialog(self.host, _gradients(), parser=self.parser)
        self.dlg.parent = lambda: self.host


class TestForceViewer(_ForceCase):
    def test_the_gradients_are_retained(self):
        self.assertEqual(len(self.dlg.gradients), 2)

    def test_the_trajectory_is_picked_up_from_the_parser(self):
        self.assertEqual(len(self.dlg.traj_steps), 3)

    def test_it_starts_on_the_final_structure(self):
        # The slot just past the last trajectory step is "Current (Final)".
        self.assertEqual(self.dlg.current_step_idx, len(self.dlg.traj_steps))

    def test_no_vectors_are_drawn_initially(self):
        self.assertEqual(self.dlg.actors, [])

    def test_a_parser_without_a_trajectory_is_fine(self):
        parser = MagicMock()
        parser.data = {}
        dlg = F.ForceViewerDialog(self.host, _gradients(), parser=parser)
        self.assertEqual(dlg.traj_steps, [])

    def _viewer(self, gradients):
        """A viewer whose *parser* carries `gradients` — with a trajectory
        present the dialog re-reads them from there, not from the argument."""
        parser = MagicMock()
        parser.data = dict(self.parser.data, gradients=gradients)
        return F.ForceViewerDialog(self.host, gradients, parser=parser)

    def test_auto_scale_picks_a_scale_from_the_largest_force(self):
        self.dlg.auto_scale()
        # largest gradient magnitude is ~0.0102, target arrow length 2.0
        self.assertGreater(self.dlg.spin_scale.value(), 0)

    def test_auto_scale_shortens_the_arrows_for_larger_forces(self):
        gentle = self._viewer([{"atom_idx": 0, "atom_sym": "O", "grad": [0.01, 0, 0]}])
        strong = self._viewer([{"atom_idx": 0, "atom_sym": "O", "grad": [1.0, 0, 0]}])
        gentle.auto_scale()
        strong.auto_scale()
        self.assertLess(strong.spin_scale.value(), gentle.spin_scale.value())

    def test_auto_scale_without_gradients_does_nothing(self):
        dlg = self._viewer([])
        before = dlg.spin_scale.value()
        dlg.auto_scale()
        self.assertEqual(dlg.spin_scale.value(), before)

    def test_auto_scale_ignores_vanishing_forces(self):
        dlg = self._viewer([{"atom_idx": 0, "atom_sym": "O", "grad": [0.0, 0.0, 0.0]}])
        before = dlg.spin_scale.value()
        dlg.auto_scale()
        self.assertEqual(dlg.spin_scale.value(), before)

    def test_the_vector_alias_is_understood(self):
        dlg = self._viewer([{"atom_idx": 0, "atom_sym": "O", "vector": [0.5, 0, 0]}])
        dlg.spin_scale.setValue(1.0)
        dlg.auto_scale()
        self.assertAlmostEqual(dlg.spin_scale.value(), 4.0)

    def test_enabling_visualisation_draws_the_vectors(self):
        self.dlg.btn_visualize.setChecked(True)
        with patch.object(self.dlg, "update_vectors") as draw:
            self.dlg.toggle_visualization()
        draw.assert_called_once()
        self.assertEqual(self.dlg.btn_visualize.text(), "Clear Forces")

    def test_disabling_visualisation_clears_the_vectors(self):
        self.dlg.btn_visualize.setChecked(False)
        with patch.object(self.dlg, "clear_vectors") as clear:
            self.dlg.toggle_visualization()
        clear.assert_called_once()
        self.assertEqual(self.dlg.btn_visualize.text(), "Visualize Forces")

    def test_clearing_removes_every_actor(self):
        self.dlg.actors = [object(), object()]
        self.dlg.clear_vectors()
        self.assertEqual(self.dlg.actors, [])
        self.assertEqual(self.plotter.remove_actor.call_count, 2)

    def test_a_stale_actor_that_cannot_be_removed_is_tolerated(self):
        self.plotter.remove_actor.side_effect = ValueError("gone")
        self.dlg.actors = [object()]
        self.dlg.clear_vectors()  # must not raise
        self.assertEqual(self.dlg.actors, [])

    def test_the_force_table_has_a_row_per_atom(self):
        self.dlg.populate_force_table()
        self.assertEqual(self.dlg.force_table.rowCount(), 2)

    def test_the_force_table_reports_when_there_is_nothing_to_show(self):
        self.dlg.gradients = []
        self.dlg.populate_force_table()
        self.assertEqual(self.dlg.force_table.rowCount(), 1)

    def test_the_last_step_carrying_forces_is_found(self):
        steps = _steps()
        steps[2].pop("convergence")
        dlg = F.ForceViewerDialog(self.host, _gradients(), parser=self.parser)
        dlg.traj_steps = steps
        self.assertIsNotNone(dlg.get_last_force_containing_step_idx())

    def test_escape_routes_through_close(self):
        with patch.object(self.dlg, "close") as closer:
            self.dlg.reject()
        closer.assert_called_once()

    def test_the_colour_setting_survives_a_round_trip(self):
        self.dlg.force_color = "#00ff00"
        self.dlg.save_settings()

        fresh = F.ForceViewerDialog(self.host, _gradients(), parser=self.parser)
        fresh.load_settings()
        self.assertEqual(fresh.force_color, "#00ff00")

    def test_corrupt_settings_are_ignored(self):
        with open(self.dlg.settings_file, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.dlg.load_settings()  # must not raise

    def test_reopening_the_convergence_graph_closes_the_previous_one(self):
        with patch.object(F, "ConvergenceGraphDialog") as graph:
            self.dlg.show_convergence_graph()
            first = graph.return_value
            self.dlg.show_convergence_graph()
        first.close.assert_called_once()

    def test_the_convergence_graph_is_told_the_current_step(self):
        with patch.object(F, "ConvergenceGraphDialog") as graph:
            self.dlg.show_convergence_graph()
        self.assertEqual(graph.call_args.args[2], self.dlg.current_step_idx)

    def test_a_trajectory_free_run_has_no_convergence_graph(self):
        parser = MagicMock()
        parser.data = {"gradients": _gradients(), "atoms": ["O", "H"]}
        dlg = F.ForceViewerDialog(self.host, _gradients(), parser=parser)
        with patch.object(F.QMessageBox, "warning") as warn:
            with patch.object(F, "ConvergenceGraphDialog") as graph:
                dlg.show_convergence_graph()
        warn.assert_called_once()
        graph.assert_not_called()

    def test_save_preserves_several_other_dialogs_sections(self):
        import json

        others = {
            "mo_settings": {"iso": 0.02},
            "nmr_settings": {"ref": "TMS"},
            "thermal_settings": {"show_details": True},
        }
        with open(self.dlg.settings_file, "w", encoding="utf-8") as fh:
            json.dump(others, fh)

        self.dlg.force_color = "#abcdef"
        self.dlg.save_settings()

        with open(self.dlg.settings_file, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        for key, val in others.items():
            self.assertEqual(on_disk[key], val)
        self.assertEqual(on_disk["force_settings"]["force_color"], "#abcdef")

    def test_round_trips_through_the_shared_helpers(self):
        S = gui_harness.load_isolated("settings")
        self.dlg.force_color = "#112233"
        self.dlg.save_settings()

        section = S.load_section(self.dlg.settings_file, "force_settings")
        self.assertEqual(section["force_color"], "#112233")

    def test_update_vectors_noops_once_closing(self):
        """QTimer.singleShot(100, self.update_vectors) can fire after the
        dialog is closed; the guard must return before touching the plotter."""
        self.dlg._is_closing = True
        with patch.object(self.dlg, "clear_vectors") as clear:
            self.dlg.update_vectors()
        clear.assert_not_called()
        self.plotter.remove_actor.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class TestUnavailableCycleEnergy(_ForceCase):
    def test_missing_energy_is_explicit_and_geometry_still_updates(self):
        self.dlg.traj_steps[0]["energy"] = None
        with patch.object(self.dlg, "update_structure") as structure:
            self.dlg.on_trajectory_change(0)
        self.assertEqual(self.dlg.traj_info.text(), "Energy: not available")
        structure.assert_called_once_with(self.dlg.traj_steps[0]["atoms"], self.dlg.traj_steps[0]["coords"])

    def test_zero_is_a_valid_energy_when_explicitly_present(self):
        self.dlg.traj_steps[0]["energy"] = 0.0
        self.dlg.on_trajectory_change(0)
        self.assertEqual(self.dlg.traj_info.text(), "Energy: 0.00000000 Eh")
