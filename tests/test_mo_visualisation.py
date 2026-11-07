"""
tests/test_mo_visualisation.py
Covers the visualisation side of MODialog: rendering a cube as an isosurface
with the current appearance settings, and building the orbital energy diagram
(spin separation, RHF/UHF detection, and locating the cube directory).
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

M = gui_harness.load_isolated("mo_analysis")


def _restricted():
    return {
        str(i): {"id": i, "energy": e, "occ": occ, "spin": "restricted"}
        for i, (e, occ) in enumerate([(-1.0, 2.0), (-0.5, 2.0), (0.2, 0.0)])
    }


def _unrestricted():
    mos = {}
    for i, (e, occ) in enumerate([(-1.0, 1.0), (-0.4, 1.0), (0.3, 0.0)]):
        mos[f"{i}_alpha"] = {"id": i, "energy": e, "occ": occ, "spin": "alpha"}
    for i, (e, occ) in enumerate([(-0.9, 1.0), (-0.3, 0.0), (0.4, 0.0)]):
        mos[f"{i}_beta"] = {"id": i, "energy": e, "occ": occ, "spin": "beta"}
    return mos


class _MOCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        saved = M.__file__
        M.__file__ = os.path.join(self.tmp, "mo_analysis.py")
        self.addCleanup(lambda: setattr(M, "__file__", saved))

        self.out = os.path.join(self.tmp, "job.out")
        self.host = MagicMock()
        self.host.parser.filename = self.out
        self.host.file_path = self.out

        self.dlg = self._make(_restricted())

    def _make(self, mos):
        dlg = M.MODialog(self.host, mos, result_dir=self.tmp)
        dlg.parent_dlg = self.host
        dlg.mw = MagicMock()
        return dlg

    def _cube(self, name="job_MO_1.cube"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("cube")
        return path


# ---------------------------------------------------------------------------
# Isosurface rendering
# ---------------------------------------------------------------------------


class TestShowCube(_MOCase):
    def test_a_cube_is_loaded_and_rendered(self):
        path = self._cube()
        with patch.object(M, "CubeVisualizer") as vis_cls:
            vis_cls.return_value.load_file.return_value = True
            self.dlg.show_cube(path)
        vis_cls.return_value.load_file.assert_called_once_with(path)
        vis_cls.return_value.show_iso.assert_called_once()

    def test_the_rendered_cube_is_remembered(self):
        path = self._cube()
        with patch.object(M, "CubeVisualizer") as vis_cls:
            vis_cls.return_value.load_file.return_value = True
            self.dlg.show_cube(path)
        self.assertEqual(self.dlg.last_cube_path, path)

    def test_the_isosurface_uses_the_current_appearance(self):
        self.dlg.spin_iso.setValue(0.035)
        self.dlg.spin_opacity.setValue(0.65)
        self.dlg.set_btn_color(self.dlg.btn_color_p, "#112233")
        self.dlg.set_btn_color(self.dlg.btn_color_n, "#445566")
        with patch.object(M, "CubeVisualizer") as vis_cls:
            vis_cls.return_value.load_file.return_value = True
            self.dlg.show_cube(self._cube())
        args, kwargs = vis_cls.return_value.show_iso.call_args
        self.assertAlmostEqual(args[0], 0.035)
        self.assertAlmostEqual(kwargs["opacity"], 0.65)
        self.assertEqual(kwargs["color_p"].lower(), "#112233")
        self.assertEqual(kwargs["color_n"].lower(), "#445566")

    def test_an_unreadable_cube_is_not_remembered(self):
        with patch.object(M, "CubeVisualizer") as vis_cls:
            vis_cls.return_value.load_file.return_value = False
            self.dlg.show_cube(self._cube())
        vis_cls.return_value.show_iso.assert_not_called()
        self.assertIsNone(self.dlg.last_cube_path)

    def test_without_a_visualizer_the_user_is_told(self):
        with patch.object(M, "CubeVisualizer", None):
            with patch.object(M.QMessageBox, "warning") as warn:
                self.dlg.show_cube(self._cube())
        warn.assert_called_once()

    def test_without_a_main_window_nothing_is_drawn(self):
        self.dlg.mw = None
        with patch.object(M, "CubeVisualizer") as vis_cls:
            self.dlg.show_cube(self._cube())
        vis_cls.assert_not_called()

    def test_loading_by_path_renders_an_existing_cube(self):
        path = self._cube()
        with patch.object(self.dlg, "show_cube") as show:
            self.dlg.load_file_by_path(path)
        show.assert_called_once_with(path)

    def test_loading_by_path_ignores_a_missing_cube(self):
        with patch.object(self.dlg, "show_cube") as show:
            self.dlg.load_file_by_path(os.path.join(self.tmp, "nope.cube"))
        show.assert_not_called()


# ---------------------------------------------------------------------------
# Energy diagram
# ---------------------------------------------------------------------------


class TestMoDiagram(_MOCase):
    def _open(self, dlg=None):
        dlg = dlg or self.dlg
        with patch.object(M, "EnergyDiagramDialog") as diag:
            dlg.show_mo_diagram()
        return diag

    def test_a_restricted_calculation_is_reported_as_rhf(self):
        data = self._open().call_args.args[0]
        self.assertEqual(data["type"], "RHF")

    def test_restricted_energies_are_passed_as_a_flat_list(self):
        data = self._open().call_args.args[0]
        self.assertEqual(data["energies"], [-1.0, -0.5, 0.2])

    def test_restricted_occupations_are_passed_as_a_flat_list(self):
        data = self._open().call_args.args[0]
        self.assertEqual(data["occupations"], [2.0, 2.0, 0.0])

    def test_an_unrestricted_calculation_is_reported_as_uhf(self):
        data = self._open(self._make(_unrestricted())).call_args.args[0]
        self.assertEqual(data["type"], "UHF")

    def test_unrestricted_energies_are_split_by_spin(self):
        data = self._open(self._make(_unrestricted())).call_args.args[0]
        self.assertEqual(data["energies"][0], [-1.0, -0.4, 0.3])
        self.assertEqual(data["energies"][1], [-0.9, -0.3, 0.4])

    def test_unrestricted_occupations_are_split_by_spin(self):
        data = self._open(self._make(_unrestricted())).call_args.args[0]
        self.assertEqual(data["occupations"][0], [1.0, 1.0, 0.0])
        self.assertEqual(data["occupations"][1], [1.0, 0.0, 0.0])

    def test_alpha_only_data_is_treated_as_restricted(self):
        mos = {
            f"{i}_alpha": {"id": i, "energy": e, "occ": occ, "spin": "alpha"}
            for i, (e, occ) in enumerate([(-1.0, 2.0), (0.5, 0.0)])
        }
        data = self._open(self._make(mos)).call_args.args[0]
        self.assertEqual(data["type"], "RHF")
        self.assertEqual(data["energies"], [-1.0, 0.5])

    def test_energies_are_converted_from_electronvolts(self):
        mos = {"0": {"id": 0, "energy_ev": -27.2114, "occ": 2.0, "spin": "restricted"}}
        data = self._open(self._make(mos)).call_args.args[0]
        self.assertAlmostEqual(data["energies"][0], -1.0, places=5)

    def test_a_missing_energy_becomes_zero(self):
        mos = {"0": {"id": 0, "occ": 2.0, "spin": "restricted"}}
        data = self._open(self._make(mos)).call_args.args[0]
        self.assertEqual(data["energies"], [0.0])

    def test_the_diagram_is_pointed_at_the_result_directory(self):
        diag = self._open()
        self.assertEqual(diag.call_args.kwargs["result_dir"], self.tmp)

    def test_a_cube_subfolder_is_preferred_when_present(self):
        cube_dir = os.path.join(self.tmp, "job_cubes")
        os.makedirs(cube_dir, exist_ok=True)
        diag = self._open()
        self.assertEqual(diag.call_args.kwargs["result_dir"], cube_dir)

    def test_reopening_replaces_the_previous_diagram(self):
        previous = MagicMock()
        self.dlg.energy_dlg = previous
        self._open()
        previous.close.assert_called_once()

    def test_the_diagram_is_shown(self):
        diag = self._open()
        diag.return_value.show.assert_called_once()

    def test_without_the_diagram_module_nothing_happens(self):
        with patch.object(M, "EnergyDiagramDialog", None):
            self.dlg.show_mo_diagram()  # must not raise


if __name__ == "__main__":
    unittest.main()
