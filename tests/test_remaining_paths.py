"""
tests/test_remaining_paths.py
Final gap-filling across three modules:

  * EnergyDiagramDialog — hover tooltips over orbital levels and drag-to-zoom
  * ChargeDialog        — loading persisted colour schemes at construction
  * GAMESSResultAnalyzerDialog — the remaining analysis launchers (trajectory,
    forces, the Shift+click convergence shortcut, properties, bonds)
"""

import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

E = gui_harness.load_isolated("energy_diag")
C = gui_harness.load_isolated("charge_analysis")
G = gui_harness.load_isolated("gui")


# ---------------------------------------------------------------------------
# Energy diagram: hover and drag-to-zoom
# ---------------------------------------------------------------------------


class _Point:
    def __init__(self, x, y):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y

    def toPoint(self):
        return self


class _Rect:
    def __init__(self, x, y, w, h):
        self._x, self._y, self._w, self._h = x, y, w, h

    def contains(self, p):
        return (
            self._x <= p.x() <= self._x + self._w
            and self._y <= p.y() <= self._y + self._h
        )


def _move_event(x, y):
    ev = MagicMock()
    ev.position.return_value = _Point(x, y)
    ev.globalPosition.return_value = _Point(x, y)
    return ev


class TestDiagramHover(unittest.TestCase):
    def setUp(self):
        data = {
            "type": "RHF",
            "energies": [-1.0, -0.5, 0.2, 0.6],
            "occupations": [2, 2, 0, 0],
        }
        self.dlg = E.EnergyDiagramDialog(data, parent=None, result_dir=None)
        self.dlg.width = lambda: 500
        self.dlg.height = lambda: 600
        self.dlg.dragging = False
        self.dlg.hit_zones = [
            (_Rect(100, 100, 80, 14), 1, "HOMO", ""),
            (_Rect(100, 200, 80, 14), 2, "LUMO", "_A"),
        ]

    def test_hovering_a_level_shows_its_index(self):
        with patch.object(E.QToolTip, "showText") as tip:
            self.dlg.mouseMoveEvent(_move_event(120, 105))
        self.assertIn("Index: 1", tip.call_args.args[1])

    def test_the_tooltip_names_the_orbital(self):
        with patch.object(E.QToolTip, "showText") as tip:
            self.dlg.mouseMoveEvent(_move_event(120, 105))
        self.assertIn("HOMO", tip.call_args.args[1])

    def test_the_tooltip_marks_the_spin_channel(self):
        with patch.object(E.QToolTip, "showText") as tip:
            self.dlg.mouseMoveEvent(_move_event(120, 205))
        self.assertIn("(A)", tip.call_args.args[1])

    def test_moving_off_the_levels_hides_the_tooltip(self):
        with patch.object(E.QToolTip, "hideText") as hide:
            self.dlg.mouseMoveEvent(_move_event(400, 400))
        hide.assert_called_once()

    def test_hovering_a_level_offers_a_clickable_cursor(self):
        with patch.object(E.QToolTip, "showText"):
            with patch.object(self.dlg, "setCursor") as cursor:
                self.dlg.mouseMoveEvent(_move_event(120, 105))
        cursor.assert_called_with(E.Qt.CursorShape.PointingHandCursor)

    def test_empty_space_restores_the_normal_cursor(self):
        with patch.object(E.QToolTip, "hideText"):
            with patch.object(self.dlg, "setCursor") as cursor:
                self.dlg.mouseMoveEvent(_move_event(400, 400))
        cursor.assert_called_with(E.Qt.CursorShape.ArrowCursor)


class TestDiagramDragZoom(TestDiagramHover):
    def setUp(self):
        super().setUp()
        self.dlg.dragging = True
        self.dlg.last_mouse_y = 300
        self.dlg.current_min, self.dlg.current_max = -1.0, 1.0

    # the hover assertions above do not apply while dragging
    test_hovering_a_level_shows_its_index = None
    test_the_tooltip_names_the_orbital = None
    test_the_tooltip_marks_the_spin_channel = None
    test_moving_off_the_levels_hides_the_tooltip = None
    test_hovering_a_level_offers_a_clickable_cursor = None
    test_empty_space_restores_the_normal_cursor = None

    def test_dragging_down_zooms_out(self):
        self.dlg.mouseMoveEvent(_move_event(0, 350))
        self.assertGreater(self.dlg.current_max - self.dlg.current_min, 2.0)

    def test_dragging_up_zooms_in(self):
        self.dlg.mouseMoveEvent(_move_event(0, 250))
        self.assertLess(self.dlg.current_max - self.dlg.current_min, 2.0)

    def test_zooming_preserves_the_centre(self):
        self.dlg.mouseMoveEvent(_move_event(0, 350))
        self.assertAlmostEqual((self.dlg.current_min + self.dlg.current_max) / 2, 0.0)

    def test_the_drag_origin_follows_the_cursor(self):
        self.dlg.mouseMoveEvent(_move_event(0, 350))
        self.assertEqual(self.dlg.last_mouse_y, 350)

    def test_a_violent_drag_is_clamped(self):
        self.dlg.mouseMoveEvent(_move_event(0, 100000))
        # factor is capped at 10x
        self.assertLessEqual(self.dlg.current_max - self.dlg.current_min, 20.0 + 1e-9)


# ---------------------------------------------------------------------------
# Charge dialog: persisted colour schemes
# ---------------------------------------------------------------------------


def _charges():
    return {
        "Mulliken": [
            {"atom_idx": 0, "atom_sym": "O", "charge": -0.42, "spin": 0.0},
            {"atom_idx": 1, "atom_sym": "H", "charge": 0.21, "spin": 0.0},
        ]
    }


class TestChargeSettingsLoad(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        saved = C.__file__
        C.__file__ = os.path.join(self.tmp, "charge_analysis.py")
        self.addCleanup(lambda: setattr(C, "__file__", saved))

        self.host = MagicMock()
        self.host.mw.view_3d_manager._plugin_color_overrides = {}
        self.host.parser.data = {"coords": [[0.0, 0.0, 0.0], [0.96, 0.0, 0.0]]}
        self.host.file_path = os.path.join(self.tmp, "job.out")

    def _write_settings(self, payload):
        with open(os.path.join(self.tmp, "settings.json"), "w", encoding="utf-8") as fh:
            json.dump({"charge_settings": payload}, fh)

    def test_a_saved_custom_scheme_is_restored(self):
        self._write_settings(
            {"custom_color_schemes": [{"name": "Mine", "colors": ["#000", "#fff"]}]}
        )
        dlg = C.ChargeDialog(self.host, _charges())
        self.assertEqual(dlg.schemes["Custom: Mine"], ["#000", "#fff"])

    def test_the_last_used_scheme_is_restored(self):
        self._write_settings({"last_charge_scheme": "Red(-) - Blue(+)"})
        dlg = C.ChargeDialog(self.host, _charges())
        self.assertEqual(dlg.current_scheme, "Red(-) - Blue(+)")

    def test_a_nameless_scheme_is_skipped(self):
        self._write_settings(
            {"custom_color_schemes": [{"name": "", "colors": ["#000", "#fff"]}]}
        )
        dlg = C.ChargeDialog(self.host, _charges())
        self.assertNotIn("Custom: ", dlg.schemes)

    def test_a_colourless_scheme_is_skipped(self):
        self._write_settings(
            {"custom_color_schemes": [{"name": "Empty", "colors": []}]}
        )
        dlg = C.ChargeDialog(self.host, _charges())
        self.assertNotIn("Custom: Empty", dlg.schemes)

    def test_corrupt_settings_fall_back_to_the_defaults(self):
        with open(os.path.join(self.tmp, "settings.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        dlg = C.ChargeDialog(self.host, _charges())
        self.assertEqual(dlg.current_scheme, "Red(-) - White - Blue(+)")

    def test_no_settings_file_falls_back_to_the_defaults(self):
        dlg = C.ChargeDialog(self.host, _charges())
        self.assertEqual(dlg.current_scheme, "Red(-) - White - Blue(+)")


# ---------------------------------------------------------------------------
# Remaining analysis launchers
# ---------------------------------------------------------------------------


def _conv_steps():
    return [
        {"energy": -76.0, "convergence": {"rms gradient": {"value": 0.01}}},
        {"energy": -76.1, "convergence": {"rms gradient": {"value": 0.001}}},
        {"energy": -76.2},
    ]


class TestRemainingLaunchers(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        self.path = os.path.join(self.tmp, "job.out")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("GAMESS output\n")

        self.parser = MagicMock()
        self.parser.filename = self.path
        self.parser.data = {}
        self.context = MagicMock()

        self.dlg = G.GAMESSResultAnalyzerDialog(
            MagicMock(), self.parser, self.path, context=self.context
        )
        patcher = patch.object(self.dlg, "load_structure_3d")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_trajectory_window_gets_the_result_context(self):
        self.parser.data = {"scan_steps": _conv_steps(), "neb_trj_file": "job_MEP.xyz"}
        self.dlg.traj_dlg = None
        with patch.object(G, "TrajectoryResultDialog") as traj:
            self.dlg.show_trajectory()
        kwargs = traj.call_args.kwargs
        self.assertEqual(kwargs["base_dir"], self.tmp)
        self.assertEqual(kwargs["output_path"], self.path)

    def test_the_trajectory_window_refuses_without_steps(self):
        self.parser.data = {}
        with patch.object(G, "TrajectoryResultDialog") as traj:
            with patch.object(G.QMessageBox, "warning") as warn:
                self.dlg.show_trajectory()
        traj.assert_not_called()
        warn.assert_called_once()

    def test_the_convergence_shortcut_opens_the_graph(self):
        self.parser.data = {"scan_steps": _conv_steps()}
        self.dlg.conv_graph_dlg = None
        with patch.object(G, "ConvergenceGraphDialog") as graph:
            self.dlg.show_convergence_graph_direct()
        graph.assert_called_once()
        graph.return_value.show.assert_called_once()

    def test_the_shortcut_starts_on_the_last_converged_step(self):
        self.parser.data = {"scan_steps": _conv_steps()}
        self.dlg.conv_graph_dlg = None
        with patch.object(G, "ConvergenceGraphDialog") as graph:
            self.dlg.show_convergence_graph_direct()
        # step 2 carries no convergence block, so step 1 is the latest with data
        self.assertEqual(graph.call_args.args[2], 1)

    def test_the_shortcut_refuses_without_a_trajectory(self):
        self.parser.data = {}
        with patch.object(G, "ConvergenceGraphDialog") as graph:
            with patch.object(G.QMessageBox, "warning") as warn:
                self.dlg.show_convergence_graph_direct()
        graph.assert_not_called()
        warn.assert_called_once()

    def test_reopening_the_shortcut_replaces_the_previous_graph(self):
        self.parser.data = {"scan_steps": _conv_steps()}
        previous = MagicMock()
        self.dlg.conv_graph_dlg = previous
        with patch.object(G, "ConvergenceGraphDialog"):
            self.dlg.show_convergence_graph_direct()
        previous.close.assert_called_once()

    def test_a_graph_that_refuses_to_close_is_tolerated(self):
        self.parser.data = {"scan_steps": _conv_steps()}
        stubborn = MagicMock()
        stubborn.close.side_effect = RuntimeError("destroyed")
        self.dlg.conv_graph_dlg = stubborn
        with patch.object(G, "ConvergenceGraphDialog"):
            self.dlg.show_convergence_graph_direct()  # must not raise

    def _no_modifiers(self):
        """show_forces branches to the convergence graph on Shift+click."""
        mods = MagicMock()
        mods.__and__ = lambda self, other: 0
        return patch.object(G.QApplication, "keyboardModifiers", return_value=mods)

    def _lazy_dialog(self, submodule, name):
        """Patch a dialog that show_* imports lazily from a sibling module."""
        stub = MagicMock()
        module = MagicMock()
        setattr(module, name, stub)
        key = f"{G.__package__}.{submodule}"
        return patch.dict(sys.modules, {key: module}), stub

    def test_the_force_window_opens_with_gradients(self):
        self.parser.data = {"gradients": [{"atom_idx": 0, "grad": [0.1, 0, 0]}]}
        self.dlg.forces_dlg = None
        with patch.object(G, "ForceViewerDialog") as forces:
            with self._no_modifiers():
                self.dlg.show_forces()
        forces.assert_called_once()

    def test_shift_clicking_forces_opens_the_convergence_graph(self):
        mods = MagicMock()
        mods.__and__ = lambda self, other: 1
        self.parser.data = {"scan_steps": _conv_steps()}
        with patch.object(G.QApplication, "keyboardModifiers", return_value=mods):
            with patch.object(self.dlg, "show_convergence_graph_direct") as graph:
                with patch.object(G, "ForceViewerDialog") as forces:
                    self.dlg.show_forces()
        graph.assert_called_once()
        forces.assert_not_called()

    def test_the_bond_window_opens_with_bond_data(self):
        self.parser.data = {"mayer_bond_orders": [{"atom_idx1": 0, "atom_idx2": 1}]}
        self.dlg.bond_dlg = None
        patcher, bonds = self._lazy_dialog("bond_analysis", "BondAnalysisDialog")
        with patcher:
            self.dlg.show_bond_analysis()
        bonds.assert_called_once()


    def test_the_bond_window_refuses_without_data(self):
        self.parser.data = {}
        patcher, bonds = self._lazy_dialog("bond_analysis", "BondAnalysisDialog")
        with patcher:
            with patch.object(G.QMessageBox, "warning") as warn:
                self.dlg.show_bond_analysis()
        bonds.assert_not_called()
        warn.assert_called_once()

    def test_the_energy_components_window_refuses_without_data(self):
        self.parser.data = {}
        patcher, energy = self._lazy_dialog("energy_analysis", "EnergyComponentsDialog")
        with patcher:
            with patch.object(G.QMessageBox, "information") as info:
                self.dlg.show_energy_components()
        energy.assert_not_called()
        info.assert_called_once()


if __name__ == "__main__":
    unittest.main()
