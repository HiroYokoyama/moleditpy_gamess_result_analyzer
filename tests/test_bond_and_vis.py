"""
tests/test_bond_and_vis.py
Coverage for bond_analysis (Mayer bond orders / NBO tables and their 3D
highlighting) and vis.CubeVisualizer (cube parsing and grid construction).

Complements tests/test_bond_analysis.py and tests/test_cube_and_mo.py, which
exercise the parsing helpers; this module drives the dialog and visualizer
themselves through gui_harness.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

B = gui_harness.load_isolated("bond_analysis")
V = gui_harness.load_isolated("vis")

BOHR_TO_ANG = 0.529177249


def _bond_data():
    return {
        "mayer_bond_orders": [
            {
                "atom_idx1": 0,
                "atom_sym1": "O",
                "atom_idx2": 1,
                "atom_sym2": "H",
                "order": 0.9123,
            },
            {
                "atom_idx1": 0,
                "atom_sym1": "O",
                "atom_idx2": 2,
                "atom_sym2": "H",
                "order": 0.9098,
            },
        ],
        "nbo_orbitals": [
            {
                "index": 1,
                "type": "BD",
                "atoms": "O  1   H  2",
                "occupancy": 1.99852,
                "energy": -0.73211,
                # Shaped like parser.parse_nbo_hybrids output.
                "hybrids": [
                    {
                        "atom_sym": "O",
                        "atom_idx": 0,
                        "s_pct": 30.1,
                        "p_pct": 69.8,
                        "d_pct": 0.1,
                        "label": "sp2.32",
                        "raw": "",
                    },
                    {
                        "atom_sym": "H",
                        "atom_idx": 1,
                        "s_pct": 99.9,
                        "p_pct": 0.1,
                        "d_pct": 0.0,
                        "label": "s",
                        "raw": "",
                    },
                ],
                "atom_indices": [0, 1],
            },
        ],
        "nbo_perturbation": [
            {
                "donor": "LP ( 1) O 1",
                "acceptor": "BD*( 1) O 1 - H 2",
                "e2_kcal": 1.23,
                "e_diff": 0.85,
                "fock": 0.029,
            },
        ],
    }


class _BondCase(unittest.TestCase):
    def setUp(self):
        # The highlight helpers do `import pyvista as pv` inside the function
        # body, so they resolve it from sys.modules at call time — where other
        # test modules leave a partial stub without Line/Sphere. Install a
        # MagicMock for the duration; the meshes go straight to a mock plotter.
        self._pv_patch = patch.dict(sys.modules, {"pyvista": MagicMock()})
        self._pv_patch.start()
        self.addCleanup(self._pv_patch.stop)

        self.plotter = MagicMock()
        self.host = MagicMock()
        self.host.mw.plotter = self.plotter
        self.host.parser.data = {
            "coords": [[0.0, 0.0, 0.0], [0.76, 0.59, 0.0], [-0.76, 0.59, 0.0]],
            "atoms": ["O", "H", "H"],
        }
        self.dlg = B.BondAnalysisDialog(self.host, _bond_data())

    def _table(self, rows):
        """A table stand-in reporting `rows` as its selected rows."""
        table = MagicMock()
        table.selectionModel.return_value.selectedRows.return_value = [
            MagicMock(**{"row.return_value": r}) for r in rows
        ]
        return table


# ---------------------------------------------------------------------------
# Hybridization formatting (pure)
# ---------------------------------------------------------------------------


def _hyb(sym, s, p, d=0.0):
    return {"atom_sym": sym, "s_pct": s, "p_pct": p, "d_pct": d}


class TestVdwRadius(unittest.TestCase):
    def test_known_elements_have_a_radius(self):
        self.assertGreater(B._vdw("O"), 0)

    def test_unknown_elements_fall_back(self):
        self.assertGreater(B._vdw("Xx"), 0)


# ---------------------------------------------------------------------------
# Dialog construction
# ---------------------------------------------------------------------------


class TestBondDialog(_BondCase):
    def test_the_dialog_keeps_the_bond_orders(self):
        self.assertEqual(len(self.dlg._mbo), 2)


    def test_no_highlight_is_active_initially(self):
        self.assertEqual(self.dlg._actors, [])

    def test_a_dialog_without_data_still_builds(self):
        dlg = B.BondAnalysisDialog(self.host, {})
        self.assertEqual(dlg._mbo, [])

    def test_coordinates_come_from_the_parser(self):
        self.assertEqual(len(self.dlg._coords()), 3)

    def test_missing_parser_yields_no_coordinates(self):
        host = MagicMock()
        host.parser = None
        dlg = B.BondAnalysisDialog(host, _bond_data())
        self.assertEqual(dlg._coords(), [])

    def test_the_halo_scales_with_the_element(self):
        # Pin the radius source: RDKit is optional, and without it _vdw()
        # returns the same fallback for every element, so a bare O-vs-H
        # comparison would tie on a machine that lacks it.
        table = MagicMock()
        table.GetRvdw.side_effect = lambda sym: {"O": 1.52, "H": 1.20}[sym]
        with patch.object(B, "_PERIODIC_TABLE", table):
            atoms = ["O", "H", "H"]
            self.assertGreater(
                self.dlg._halo_radius(0, atoms), self.dlg._halo_radius(1, atoms)
            )

    def test_the_halo_is_a_fraction_of_the_vdw_radius(self):
        table = MagicMock()
        table.GetRvdw.return_value = 1.52
        with patch.object(B, "_PERIODIC_TABLE", table):
            self.assertAlmostEqual(
                self.dlg._halo_radius(0, ["O"]), 1.52 * B._VDW_FRACTION
            )

    def test_a_radius_lookup_failure_falls_back(self):
        table = MagicMock()
        table.GetRvdw.side_effect = RuntimeError("unknown element")
        with patch.object(B, "_PERIODIC_TABLE", table):
            self.assertAlmostEqual(B._vdw("Xx"), 1.70)

    def test_without_rdkit_every_element_uses_the_default(self):
        with patch.object(B, "_PERIODIC_TABLE", None):
            self.assertAlmostEqual(B._vdw("O"), 1.70)
            self.assertAlmostEqual(B._vdw("H"), 1.70)

    def test_an_out_of_range_atom_gets_a_default_halo(self):
        self.assertGreater(self.dlg._halo_radius(99, ["O"]), 0)


# ---------------------------------------------------------------------------
# 3D highlighting
# ---------------------------------------------------------------------------


class TestHighlighting(_BondCase):
    def test_highlighting_a_bond_draws_a_tube_and_two_halos(self):
        self.dlg._highlight_bond(0, 1)
        self.assertEqual(self.plotter.add_mesh.call_count, 3)
        self.assertEqual(len(self.dlg._actors), 3)

    def test_highlighting_a_bond_clears_the_previous_one(self):
        self.dlg._highlight_bond(0, 1)
        self.plotter.reset_mock()
        self.dlg._highlight_bond(0, 2)
        self.assertEqual(self.plotter.remove_actor.call_count, 3)

    def test_an_out_of_range_bond_draws_nothing(self):
        self.dlg._highlight_bond(0, 99)
        self.plotter.add_mesh.assert_not_called()

    def test_highlighting_atoms_draws_one_halo_each(self):
        self.dlg._highlight_atoms([0, 1])
        self.assertEqual(self.plotter.add_mesh.call_count, 2)

    def test_out_of_range_atoms_are_skipped(self):
        self.dlg._highlight_atoms([0, 99])
        self.assertEqual(self.plotter.add_mesh.call_count, 1)

    def test_clearing_removes_every_actor(self):
        self.dlg._highlight_bond(0, 1)
        self.dlg._clear_highlight()
        self.assertEqual(self.dlg._actors, [])
        self.assertEqual(self.plotter.remove_actor.call_count, 3)

    def test_clearing_without_a_plotter_still_drops_the_actors(self):
        host = MagicMock(spec=[])  # no .mw at all
        dlg = B.BondAnalysisDialog(self.host, _bond_data())
        dlg.parent_dlg = host
        dlg._actors = [object(), object()]
        dlg._clear_highlight()
        self.assertEqual(dlg._actors, [])

    def test_a_stale_actor_that_cannot_be_removed_is_tolerated(self):
        self.plotter.remove_actor.side_effect = ValueError("gone")
        self.dlg._actors = [object()]
        self.dlg._clear_highlight()  # must not raise
        self.assertEqual(self.dlg._actors, [])

    def test_highlighting_without_coordinates_draws_nothing(self):
        self.host.parser.data = {"coords": []}
        self.dlg._highlight_bond(0, 1)
        self.plotter.add_mesh.assert_not_called()


# ---------------------------------------------------------------------------
# NBO detail popup and clipboard copy
# ---------------------------------------------------------------------------


class TestCopySelection(unittest.TestCase):
    def _table(self, cells):
        """cells: {(row, col): text} -> a table reporting them as selected."""
        table = B._CopyableTable()
        items = []
        for (row, col), text in sorted(cells.items()):
            item = MagicMock()
            item.row.return_value = row
            item.column.return_value = col
            item.text.return_value = text
            items.append(item)
        table.selectedItems = lambda: items
        return table

    def _copied(self, table):
        clipboard = MagicMock()
        with patch.object(B.QApplication, "clipboard", return_value=clipboard):
            table._copy_selection()
        if not clipboard.setText.call_args_list:
            return None
        return clipboard.setText.call_args.args[0]

    def test_a_single_cell_is_copied(self):
        self.assertEqual(self._copied(self._table({(0, 0): "O"})), "O")

    def test_a_row_is_copied_as_tab_separated_text(self):
        table = self._table({(0, 0): "O", (0, 1): "H", (0, 2): "0.9123"})
        self.assertEqual(self._copied(table), "O\tH\t0.9123")

    def test_several_rows_are_copied_as_lines(self):
        table = self._table({(0, 0): "O", (0, 1): "H", (1, 0): "O", (1, 1): "H2"})
        self.assertEqual(self._copied(table), "O\tH\nO\tH2")

    def test_columns_are_ordered_regardless_of_selection_order(self):
        table = self._table({(0, 2): "third", (0, 0): "first", (0, 1): "second"})
        self.assertEqual(self._copied(table), "first\tsecond\tthird")

    def test_an_empty_selection_copies_nothing(self):
        table = B._CopyableTable()
        table.selectedItems = lambda: []
        self.assertIsNone(self._copied(table))


# ---------------------------------------------------------------------------
# Table selection -> highlight
# ---------------------------------------------------------------------------


class TestSelection(_BondCase):
    def test_a_single_selected_row_is_reported(self):
        self.assertEqual(B.BondAnalysisDialog._single_selected_row(self._table([2])), 2)

    def test_a_multi_row_selection_is_ambiguous(self):
        self.assertIsNone(
            B.BondAnalysisDialog._single_selected_row(self._table([0, 1]))
        )

    def test_an_empty_selection_is_reported_as_none(self):
        self.assertIsNone(B.BondAnalysisDialog._single_selected_row(self._table([])))

    def test_selecting_a_bond_row_highlights_that_bond(self):
        with patch.object(self.dlg, "_highlight_bond") as hl:
            self.dlg._on_mayer_selected(self._table([1]))
        hl.assert_called_once_with(0, 2)

    def test_deselecting_clears_the_highlight(self):
        with patch.object(self.dlg, "_clear_highlight") as clear:
            self.dlg._on_mayer_selected(self._table([]))
        clear.assert_called_once()

    def test_a_multi_row_selection_clears_the_highlight(self):
        with patch.object(self.dlg, "_clear_highlight") as clear:
            self.dlg._on_mayer_selected(self._table([0, 1]))
        clear.assert_called_once()

    def test_an_out_of_range_bond_row_is_ignored(self):
        with patch.object(self.dlg, "_highlight_bond") as hl:
            self.dlg._on_mayer_selected(self._table([99]))
        hl.assert_not_called()


# ---------------------------------------------------------------------------
# CubeVisualizer
# ---------------------------------------------------------------------------


def _cube_text(n_atoms=2, dset_ids=None, dims=(2, 2, 2), angstrom=False):
    nx, ny, nz = dims
    if angstrom:
        # GAMESS cube convention: a negative voxel count on the NX line
        # means the grid is already in Angstrom.
        nx = -nx
    lines = [
        "Cube file",
        "Density",
        f"{n_atoms:5d}    0.000000    0.000000    0.000000",
        f"{nx:5d}    0.100000    0.000000    0.000000",
        f"{ny:5d}    0.000000    0.100000    0.000000",
        f"{nz:5d}    0.000000    0.000000    0.100000",
    ]
    for i in range(abs(n_atoms)):
        lines.append(f"    1    1.000000    {float(i):.6f}    0.000000    0.000000")
    if dset_ids is not None:
        lines.append("    " + "    ".join(str(x) for x in dset_ids))
    values = [f"{v:.5E}" for v in range(abs(nx) * ny * nz)]
    for i in range(0, len(values), 6):
        lines.append(" " + " ".join(values[i : i + 6]))
    return "\n".join(lines) + "\n"


class _VisCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.mw = MagicMock()
        self.vis = V.CubeVisualizer(self.mw)

    def _write(self, text, name="test.cube"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path


class TestCubeParsing(_VisCase):
    def test_dimensions_are_read(self):
        meta = self.vis._parse_cube(self._write(_cube_text(dims=(2, 3, 4))))
        self.assertEqual(meta["dims"], (2, 3, 4))

    def test_the_origin_is_read(self):
        meta = self.vis._parse_cube(self._write(_cube_text()))
        self.assertEqual(list(meta["origin"]), [0.0, 0.0, 0.0])

    def test_the_axis_vectors_are_read(self):
        meta = self.vis._parse_cube(self._write(_cube_text()))
        self.assertAlmostEqual(meta["vectors"][0][0], 0.1)

    def test_every_voxel_is_read(self):
        meta = self.vis._parse_cube(self._write(_cube_text(dims=(2, 2, 2))))
        self.assertEqual(len(meta["data"]), 8)

    def test_atom_records_are_skipped(self):
        meta = self.vis._parse_cube(self._write(_cube_text(n_atoms=5)))
        self.assertEqual(len(meta["data"]), 8)

    def test_an_mo_cube_skips_its_dset_ids(self):
        # A negative atom count flags an MO cube: a DSET_IDS block sits
        # between the atom records and the volumetric data.
        text = _cube_text(n_atoms=-2, dset_ids=[1, 7])
        meta = self.vis._parse_cube(self._write(text))
        self.assertEqual(len(meta["data"]), 8)

    def test_a_wrapped_dset_id_block_is_skipped(self):
        text = _cube_text(n_atoms=-2, dset_ids=[3, 1, 2, 3])
        meta = self.vis._parse_cube(self._write(text))
        self.assertEqual(len(meta["data"]), 8)

    def test_loading_reports_success(self):
        with patch.object(V, "pv") as pv:
            pv.StructuredGrid.return_value = MagicMock()
            self.assertTrue(self.vis.load_file(self._write(_cube_text())))

    def test_loading_a_missing_file_reports_failure(self):
        self.assertFalse(self.vis.load_file(os.path.join(self.tmp, "nope.cube")))

    def test_loading_a_malformed_file_reports_failure(self):
        self.assertFalse(self.vis.load_file(self._write("not a cube\n")))


class TestGridBuilding(_VisCase):
    def test_the_grid_is_sized_from_the_cube(self):
        meta = self.vis._parse_cube(self._write(_cube_text(dims=(2, 2, 2))))
        grid = MagicMock()
        with patch.object(V, "pv") as pv:
            pv.StructuredGrid.return_value = grid
            self.vis._build_grid(meta)
        self.assertEqual(grid.dimensions, [2, 2, 2])

    def test_points_are_converted_from_bohr_to_angstrom(self):
        meta = self.vis._parse_cube(self._write(_cube_text(dims=(2, 2, 2))))
        grid = MagicMock()
        with patch.object(V, "pv") as pv:
            pv.StructuredGrid.return_value = grid
            self.vis._build_grid(meta)
        # second grid point is one step along x: 0.1 Bohr in Angstrom
        self.assertAlmostEqual(grid.points[1][0], 0.1 * BOHR_TO_ANG, places=6)

    def test_an_angstrom_cube_is_not_converted_again(self):
        """A negative NX flags Angstrom units; converting anyway inflated the
        whole isosurface by 1/0.529 = 1.89x."""
        meta = self.vis._parse_cube(
            self._write(_cube_text(dims=(2, 2, 2), angstrom=True))
        )
        self.assertTrue(meta["is_angstrom"])
        grid = MagicMock()
        with patch.object(V, "pv") as pv:
            pv.StructuredGrid.return_value = grid
            self.vis._build_grid(meta)
        self.assertAlmostEqual(grid.points[1][0], 0.1, places=6)

    def test_an_angstrom_cube_still_reports_positive_dims(self):
        meta = self.vis._parse_cube(
            self._write(_cube_text(dims=(2, 2, 2), angstrom=True))
        )
        self.assertEqual(meta["dims"], (2, 2, 2))
        self.assertEqual(len(meta["data"]), 8)

    def test_every_voxel_reaches_the_grid(self):
        meta = self.vis._parse_cube(self._write(_cube_text(dims=(2, 2, 2))))
        grid = MagicMock()
        store = {}
        grid.point_data = store
        with patch.object(V, "pv") as pv:
            pv.StructuredGrid.return_value = grid
            self.vis._build_grid(meta)
        self.assertEqual(len(store["values"]), 8)

    def _grid(self, positive_points=10, negative_points=10):
        """A loaded grid whose contours report the given point counts."""
        grid = MagicMock()

        def contour(levels, scalars=None):
            surface = MagicMock()
            surface.n_points = positive_points if levels[0] > 0 else negative_points
            return surface

        grid.contour.side_effect = contour
        self.vis.current_grid = grid
        return grid

    def test_both_lobes_are_drawn(self):
        self._grid()
        self.vis.show_iso(0.02)
        names = [c.kwargs["name"] for c in self.mw.plotter.add_mesh.call_args_list]
        self.assertEqual(names, ["mo_iso_p", "mo_iso_n"])

    def test_the_lobes_are_contoured_at_opposite_signs(self):
        grid = self._grid()
        self.vis.show_iso(0.03)
        levels = [c.args[0][0] for c in grid.contour.call_args_list]
        self.assertEqual(levels, [0.03, -0.03])

    def test_the_lobe_colours_are_applied(self):
        self._grid()
        self.vis.show_iso(0.02, color_p="#ff0000", color_n="#0000ff")
        colours = [c.kwargs["color"] for c in self.mw.plotter.add_mesh.call_args_list]
        self.assertEqual(colours, ["#ff0000", "#0000ff"])

    def test_the_appearance_settings_are_applied(self):
        self._grid()
        self.vis.show_iso(0.02, opacity=0.35, style="wireframe", smooth_shading=False)
        kwargs = self.mw.plotter.add_mesh.call_args_list[0].kwargs
        self.assertAlmostEqual(kwargs["opacity"], 0.35)
        self.assertEqual(kwargs["style"], "wireframe")
        self.assertFalse(kwargs["smooth_shading"])

    def test_an_empty_lobe_is_not_drawn(self):
        self._grid(negative_points=0)
        self.vis.show_iso(0.02)
        names = [c.kwargs["name"] for c in self.mw.plotter.add_mesh.call_args_list]
        self.assertEqual(names, ["mo_iso_p"])

    def test_previous_isosurfaces_are_cleared_first(self):
        self._grid()
        self.vis.show_iso(0.02)
        self.mw.plotter.remove_actor.assert_any_call("mo_iso_p")
        self.mw.plotter.remove_actor.assert_any_call("mo_iso_n")

    def test_nothing_is_drawn_without_a_loaded_grid(self):
        self.vis.current_grid = None
        self.vis.show_iso(0.02)
        self.mw.plotter.add_mesh.assert_not_called()

    def test_a_contouring_failure_is_tolerated(self):
        grid = MagicMock()
        grid.contour.side_effect = RuntimeError("bad scalars")
        self.vis.current_grid = grid
        self.vis.show_iso(0.02)  # must not raise
        self.mw.plotter.add_mesh.assert_not_called()

    def test_clearing_removes_both_isosurface_actors(self):
        self.vis.clear()
        removed = [c.args[0] for c in self.mw.plotter.remove_actor.call_args_list]
        self.assertEqual(removed, ["mo_iso_p", "mo_iso_n"])

    def test_clearing_redraws_the_scene(self):
        self.vis.clear()
        self.mw.plotter.render.assert_called_once()


if __name__ == "__main__":
    unittest.main()
