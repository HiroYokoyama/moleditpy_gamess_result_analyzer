"""
tests/test_mo_engine.py
Unit tests for mo_engine.py: CubeWriter file format and BasisSetEngine
GTO evaluation. Previously this module had no direct tests.

mo_engine imports numpy (real) and PySide6.QtCore (stubbed: QThread must be an
inheritable class for CalcWorker's class definition to succeed).
"""

import importlib.util
import math
import os
import re
import sys
import types
import unittest
from unittest.mock import MagicMock

import numpy as np

_SRC = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__), "..", "gamess_result_analyzer", "mo_engine.py"
    )
)


def _plugin_version():
    """PLUGIN_VERSION read from the package source, without importing it."""
    init = os.path.join(os.path.dirname(_SRC), "__init__.py")
    with open(init, encoding="utf-8") as fh:
        m = re.search(r"""PLUGIN_VERSION\s*=\s*['"](.+?)['"]""", fh.read())
    return m.group(1)


PLUGIN_VERSION = _plugin_version()


def _load_mo_engine():
    """Load mo_engine with a temporary PySide6.QtCore stub.

    The stub is removed from sys.modules afterwards (originals restored) so
    the minimal QtCore — it only has QThread/Signal — cannot leak into
    other test modules that install their own, richer PySide6 stubs.
    """

    class _QThread:
        def __init__(self, *a, **k):
            pass

    core = types.ModuleType("PySide6.QtCore")
    core.QThread = _QThread
    core.Signal = lambda *a, **k: MagicMock()
    pyside6 = types.ModuleType("PySide6")
    pyside6.QtCore = core

    saved = {k: sys.modules.get(k) for k in ("PySide6", "PySide6.QtCore")}
    sys.modules["PySide6"] = pyside6
    sys.modules["PySide6.QtCore"] = core

    # mo_engine stamps PLUGIN_VERSION into every cube header via `from . import
    # PLUGIN_VERSION`. Give it a package to resolve that against, so the test
    # covers the real import branch rather than the flat-load fallback.
    pkg = sys.modules.get("gamess_result_analyzer")
    if pkg is None:
        pkg = types.ModuleType("gamess_result_analyzer")
        sys.modules["gamess_result_analyzer"] = pkg
        saved["gamess_result_analyzer"] = None
    if not hasattr(pkg, "PLUGIN_VERSION"):
        pkg.PLUGIN_VERSION = PLUGIN_VERSION

    try:
        spec = importlib.util.spec_from_file_location("mo_engine_standalone", _SRC)
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "gamess_result_analyzer"
        sys.modules["mo_engine_standalone"] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


_mod = _load_mo_engine()
CubeWriter = _mod.CubeWriter
read_generation_settings = _mod.read_generation_settings
BasisSetEngine = _mod.BasisSetEngine
BasisSetEngineErrors = _mod.UnsupportedBasisError

ANG_TO_BOHR = 1.0 / 0.529177249


# ---------------------------------------------------------------------------
# CubeWriter
# ---------------------------------------------------------------------------


class TestCubeWriter(unittest.TestCase):
    def _write(self, tmpdir, data=None, atoms_sym=(8, 1, 1)):
        # Atomic numbers (ints) avoid any rdkit dependency in to_z().
        if data is None:
            data = np.arange(2 * 2 * 2, dtype=float).reshape(2, 2, 2)
        path = os.path.join(tmpdir, "out.cube")
        CubeWriter.write(
            path,
            list(atoms_sym),
            [(0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)][: len(atoms_sym)],
            origin=(-1.0, -2.0, -3.0),
            vectors=np.diag([0.5, 0.6, 0.7]),
            data=data,
            comment="MO 5",
        )
        with open(path, encoding="utf-8") as f:
            return f.read().splitlines()

    def test_comment_lines(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            lines = self._write(td)
        self.assertIn("MO 5", lines[0])
        self.assertTrue(lines[1])

    def test_second_comment_line_stamps_the_plugin_version(self):
        """Cube provenance must be readable from the file.

        The f/g normalization fix changed the values in every cube built from
        a basis carrying those shells, so "which version wrote this" cannot
        be answered by looking at the numbers.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            lines = self._write(td)
        self.assertIn("MoleditPy GAMESS Result Analyzer", lines[1])
        self.assertIn(f"v{PLUGIN_VERSION}", lines[1])
        # The fallback must not reach a written file.
        self.assertNotIn("unknown", lines[1])

    def test_the_generation_settings_ride_on_the_second_comment_line(self):
        """The cube format allows exactly two comment lines, so the grid and
        margin cannot have one of their own."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "s.cube")
            CubeWriter.write(
                path,
                [8],
                [(0.0, 0.0, 0.0)],
                origin=(0.0, 0.0, 0.0),
                vectors=np.diag([0.5, 0.5, 0.5]),
                data=np.zeros((2, 2, 2)),
                comment="MO 1",
                grid_points=40,
                margin=4.0,
            )
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        self.assertIn("grid=40", lines[1])
        self.assertIn("margin=4.00", lines[1])
        # Line 3 must still be the atom-count record, not another comment.
        self.assertEqual(int(lines[2].split()[0]), 1)

    def test_the_settings_read_back(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "s.cube")
            CubeWriter.write(
                path,
                [8],
                [(0.0, 0.0, 0.0)],
                origin=(0.0, 0.0, 0.0),
                vectors=np.diag([0.5, 0.5, 0.5]),
                data=np.zeros((2, 2, 2)),
                comment="MO 1",
                grid_points=60,
                margin=5.5,
            )
            info = read_generation_settings(path)
        self.assertEqual(info["grid"], 60)
        self.assertAlmostEqual(info["margin"], 5.5)
        self.assertEqual(info["version"], PLUGIN_VERSION)

    def test_a_cube_without_settings_reports_them_missing(self):
        """Cubes written before the stamp, or by another program, must not be
        described with the current spin-box values."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "old.cube")
            CubeWriter.write(
                path,
                [8],
                [(0.0, 0.0, 0.0)],
                origin=(0.0, 0.0, 0.0),
                vectors=np.diag([0.5, 0.5, 0.5]),
                data=np.zeros((2, 2, 2)),
                comment="MO 1",
            )
            info = read_generation_settings(path)
        self.assertIsNone(info["grid"])
        self.assertIsNone(info["margin"])
        self.assertEqual(info["version"], PLUGIN_VERSION)

    def test_a_foreign_cube_reports_everything_unknown(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "foreign.cube")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("GAMESS cube\nSCF density\n 1 0.0 0.0 0.0\n")
            info = read_generation_settings(path)
        self.assertEqual(info, {"version": None, "grid": None, "margin": None})

    def test_an_unreadable_cube_reports_everything_unknown(self):
        info = read_generation_settings(os.path.join("no", "such", "file.cube"))
        self.assertEqual(info, {"version": None, "grid": None, "margin": None})

    def test_version_line_does_not_disturb_the_grid_header(self):
        """The atom/grid block still starts on line 3 (cube format is positional)."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            lines = self._write(td)
        self.assertEqual(int(lines[2].split()[0]), 3)

    def test_atom_count_and_origin(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            lines = self._write(td)
        parts = lines[2].split()
        self.assertEqual(int(parts[0]), 3)
        self.assertAlmostEqual(float(parts[1]), -1.0, places=5)
        self.assertAlmostEqual(float(parts[3]), -3.0, places=5)

    def test_grid_vector_lines(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            lines = self._write(td)
        nx = lines[3].split()
        ny = lines[4].split()
        nz = lines[5].split()
        self.assertEqual([int(nx[0]), int(ny[0]), int(nz[0])], [2, 2, 2])
        self.assertAlmostEqual(float(nx[1]), 0.5, places=5)
        self.assertAlmostEqual(float(ny[2]), 0.6, places=5)
        self.assertAlmostEqual(float(nz[3]), 0.7, places=5)

    def test_atom_lines_z_charge_and_bohr_coords(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            lines = self._write(td)
        first = lines[6].split()
        self.assertEqual(int(first[0]), 8)
        self.assertAlmostEqual(float(first[1]), 8.0, places=5)
        # Second atom at (0, 0, 1.0 Angstrom) -> z in Bohr
        second = lines[7].split()
        self.assertAlmostEqual(float(second[4]), ANG_TO_BOHR, places=5)

    def test_data_six_values_per_line_and_total(self):
        import tempfile

        data = np.arange(2 * 2 * 2, dtype=float).reshape(2, 2, 2)
        with tempfile.TemporaryDirectory() as td:
            lines = self._write(td, data=data)
        data_lines = lines[9:]  # 2 comments + 4 header + 3 atoms
        vals = [float(v) for ln in data_lines for v in ln.split()]
        self.assertEqual(len(vals), 8)
        self.assertEqual(vals, sorted(vals))  # arange order preserved (C-flatten)
        self.assertTrue(all(len(ln.split()) <= 6 for ln in data_lines))

    def test_creates_missing_directory(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            nested = os.path.join(td, "a", "b")
            path = os.path.join(nested, "out.cube")
            CubeWriter.write(
                path,
                [1],
                [(0.0, 0.0, 0.0)],
                origin=(0, 0, 0),
                vectors=np.eye(3),
                data=np.zeros((2, 2, 2)),
            )
            self.assertTrue(os.path.exists(path))


# ---------------------------------------------------------------------------
# BasisSetEngine
# ---------------------------------------------------------------------------


def _s_shell(alpha=1.0, coeff=1.0, center=(0.0, 0.0, 0.0)):
    return {
        "type": 0,
        "center": np.array(center, dtype=float),
        "exps": np.array([alpha]),
        "coeffs": np.array([coeff]),
    }


class TestBasisSetEngine(unittest.TestCase):
    def test_n_basis_counts_components(self):
        shells = [
            _s_shell(),
            {**_s_shell(), "type": 1},  # P -> 3
            {**_s_shell(), "type": 2},  # D (spherical) -> 5
            {**_s_shell(), "type": 3},  # F (spherical) -> 7
            {**_s_shell(), "type": 4},  # G (spherical) -> 9
        ]
        eng = BasisSetEngine(shells)
        self.assertEqual(eng.n_basis, 1 + 3 + 5 + 7 + 9)

    def test_unsupported_shell_type_is_rejected(self):
        """Superseded the old "skipped" behaviour — see
        TestUnsupportedShellsAreRejected for why skipping was unsafe."""
        with self.assertRaises(BasisSetEngineErrors):
            BasisSetEngine([_s_shell(), {**_s_shell(), "type": 99}])

    def test_s_orbital_value_at_center(self):
        eng = BasisSetEngine([_s_shell(alpha=1.0, coeff=1.0)])
        val = eng.evaluate_mo_on_grid(0, np.zeros((1, 3)), np.array([1.0]))
        expected = (2.0 / math.pi) ** 0.75  # GTO normalization prefactor at r=0
        self.assertAlmostEqual(val[0], expected, places=8)

    def test_s_orbital_gamess_decay(self):
        alpha = 0.8
        eng = BasisSetEngine([_s_shell(alpha=alpha)])
        pts = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.5]])
        vals = eng.evaluate_mo_on_grid(0, pts, np.array([1.0]))
        self.assertAlmostEqual(vals[1] / vals[0], math.exp(-alpha * 1.5**2), places=8)

    def test_p_shell_order_px_py_pz(self):
        eng = BasisSetEngine([{**_s_shell(), "type": 1}])
        pt_x = np.array([[0.7, 0.0, 0.0]])
        pt_z = np.array([[0.0, 0.0, 0.7]])
        # Component 0 = px: nonzero along x, zero along z
        c_px = np.array([1.0, 0.0, 0.0])
        self.assertGreater(eng.evaluate_mo_on_grid(0, pt_x, c_px)[0], 0.0)
        self.assertAlmostEqual(eng.evaluate_mo_on_grid(0, pt_z, c_px)[0], 0.0)
        # Component 2 = pz: nonzero along z, zero along x
        c_pz = np.array([0.0, 0.0, 1.0])
        self.assertGreater(eng.evaluate_mo_on_grid(0, pt_z, c_pz)[0], 0.0)
        self.assertAlmostEqual(eng.evaluate_mo_on_grid(0, pt_x, c_pz)[0], 0.0)

    def test_p_orbital_antisymmetric(self):
        eng = BasisSetEngine([{**_s_shell(), "type": 1}])
        pts = np.array([[0.9, 0.0, 0.0], [-0.9, 0.0, 0.0]])
        vals = eng.evaluate_mo_on_grid(0, pts, np.array([1.0, 0.0, 0.0]))
        self.assertAlmostEqual(vals[0], -vals[1], places=10)

    def test_shell_centered_off_origin(self):
        center = (1.0, -2.0, 0.5)
        eng = BasisSetEngine([_s_shell(alpha=1.0, center=center)])
        vals = eng.evaluate_mo_on_grid(
            0, np.array([center, (0.0, 0.0, 0.0)]), np.array([1.0])
        )
        self.assertGreater(vals[0], vals[1])
        self.assertAlmostEqual(vals[0], (2.0 / math.pi) ** 0.75, places=8)

    def test_too_few_coeffs_raises(self):
        eng = BasisSetEngine([{**_s_shell(), "type": 1}])  # needs 3
        with self.assertRaises(ValueError):
            eng.evaluate_mo_on_grid(0, np.zeros((1, 3)), np.array([1.0]))

    def test_near_zero_mo_coeff_skipped(self):
        eng = BasisSetEngine([_s_shell()])
        vals = eng.evaluate_mo_on_grid(0, np.zeros((1, 3)), np.array([1e-12]))
        self.assertEqual(vals[0], 0.0)

    def test_normalization_prefactor_s_and_p(self):
        eng = BasisSetEngine([_s_shell()])
        alpha = 1.3
        n_s = eng._normalization_prefactor(alpha, 0, 0, 0)
        self.assertAlmostEqual(n_s, (2 * alpha / math.pi) ** 0.75, places=10)
        # P: extra sqrt(8*alpha * 1!/2!) = sqrt(4*alpha)
        n_p = eng._normalization_prefactor(alpha, 1, 0, 0)
        self.assertAlmostEqual(
            n_p, (2 * alpha / math.pi) ** 0.75 * math.sqrt(4 * alpha), places=10
        )


class TestUnsupportedShellsAreRejected(unittest.TestCase):
    """A shell the engine cannot evaluate must block, not be skipped.

    Skipping left start_idx unset and current_idx unadvanced, so every shell
    after it read the wrong slice of the MO coefficient vector — a silently
    wrong orbital with nothing on screen to say so.
    """

    @staticmethod
    def _shells(*types):
        return [{**_s_shell(), "type": t} for t in types]

    def test_an_h_shell_is_rejected(self):
        with self.assertRaises(BasisSetEngineErrors):
            BasisSetEngine(self._shells(0, 5))

    def test_the_message_names_the_shell_letter(self):
        with self.assertRaises(BasisSetEngineErrors) as ctx:
            BasisSetEngine(self._shells(5))
        self.assertIn("H shell", str(ctx.exception))

    def test_an_out_of_range_type_is_rejected(self):
        with self.assertRaises(BasisSetEngineErrors):
            BasisSetEngine(self._shells(99))

    def test_s_through_g_are_all_accepted(self):
        eng = BasisSetEngine(self._shells(0, 1, 2, 3, 4))
        self.assertEqual(eng.n_basis, 1 + 3 + 5 + 7 + 9)

    def test_later_shells_keep_their_offsets(self):
        """The reason the skip mattered: indices after it must stay right."""
        eng = BasisSetEngine(self._shells(0, 2, 0))
        self.assertEqual([sh["start_idx"] for sh in eng.shells], [0, 1, 6])


class TestSphericalShellNormalization(unittest.TestCase):
    """d/f/g components must be unit normalized and have the right shape.

    The hand-typed angular prefactors these replaced were wrong two ways:
    the f-shell fed raw polynomial coefficients to components with different
    Cartesian normalizations (putting the f(z^3) nodal cone at 29 deg instead
    of 39.2), and the g-shell prefactors disagreed between components (g2 came
    out ~2x the norm of g1), so g lobes had the wrong relative sizes.
    """

    @staticmethod
    def _eng(shell_type):
        return BasisSetEngine([{**_s_shell(alpha=1.0, coeff=1.0), "type": shell_type}])

    def _gram_matrix(self, shell_type):
        """<phi_i|phi_j> for every pair of components in one shell.

        Gauss-Legendre in cos(theta), uniform in phi, and a fine trapezoid in
        r — the GAMESS decays fast enough that a plain radial grid is well
        inside the tolerance asserted below.
        """
        eng = self._eng(shell_type)
        n_r, n_t, n_phi = 400, 80, 80
        r = np.linspace(1e-6, 6.0, n_r)
        ct, wt = np.polynomial.legendre.leggauss(n_t)
        phi = np.linspace(0.0, 2 * np.pi, n_phi, endpoint=False)
        dr = r[1] - r[0]
        dphi = 2 * np.pi / n_phi

        R, CT, PH = np.meshgrid(r, ct, phi, indexing="ij")
        ST = np.sqrt(1.0 - CT**2)
        pts = np.stack(
            [
                (R * ST * np.cos(PH)).ravel(),
                (R * ST * np.sin(PH)).ravel(),
                (R * CT).ravel(),
            ],
            axis=1,
        )
        weights = (R**2 * np.broadcast_to(wt[None, :, None], R.shape)).ravel()
        weights = weights * dr * dphi

        values = []
        for i in range(eng.n_basis):
            c = np.zeros(eng.n_basis)
            c[i] = 1.0
            values.append(eng.evaluate_mo_on_grid(0, pts, c))
        values = np.array(values)
        return (values * weights) @ values.T

    def _component_norms(self, shell_type):
        return list(np.diag(self._gram_matrix(shell_type)))

    def test_d_components_are_unit_normalized(self):
        for n in self._component_norms(2):
            self.assertAlmostEqual(n, 1.0, places=4)

    def test_f_components_are_unit_normalized(self):
        for n in self._component_norms(3):
            self.assertAlmostEqual(n, 1.0, places=4)

    def test_g_components_are_unit_normalized(self):
        for n in self._component_norms(4):
            self.assertAlmostEqual(n, 1.0, places=4)

    def test_components_of_a_shell_are_mutually_orthogonal(self):
        """Real solid harmonics of one l are orthogonal — so the Gram matrix
        off-diagonal must vanish, not merely the diagonal be 1.

        This is what catches a wrong *shape*. _build_sph_defs renormalizes
        whatever polynomial it is handed, so a corrupted component still comes
        back unit-norm and the tests above stay green; what it cannot fix is
        the admixture of other harmonics the corruption introduces, which
        shows up here as overlap with a sibling component. Verified by
        perturbing g2's polynomial: the norms remained 1.0 while this
        off-diagonal went from ~1e-16 to 7e-2.

        Only the m=0 components (f z^3, g z^4) have their shape pinned
        individually, by the nodal-cone tests below; this covers all of them.
        """
        for shell_type, letter in ((2, "d"), (3, "f"), (4, "g")):
            with self.subTest(shell=letter):
                gram = self._gram_matrix(shell_type)
                off_diagonal = gram - np.diag(np.diag(gram))
                worst = np.unravel_index(
                    np.argmax(np.abs(off_diagonal)), off_diagonal.shape
                )
                self.assertLess(
                    np.abs(off_diagonal).max(),
                    1e-6,
                    f"{letter} components {worst[0]} and {worst[1]} overlap by "
                    f"{off_diagonal[worst]:.2e}; one of them has the wrong shape",
                )

    def _first_node_deg(self, shell_type, component, lo=0.5, hi=89.5):
        eng = self._eng(shell_type)
        c = np.zeros(eng.n_basis)
        c[component] = 1.0
        thetas = np.linspace(lo, hi, 20000)
        pts = np.stack(
            [
                np.sin(np.radians(thetas)),
                np.zeros_like(thetas),
                np.cos(np.radians(thetas)),
            ],
            axis=1,
        )
        v = eng.evaluate_mo_on_grid(0, pts, c)
        crossings = thetas[:-1][np.sign(v[:-1]) != np.sign(v[1:])]
        return list(crossings)

    def test_f_z3_nodal_cone(self):
        """f(z^3) changes sign at cos^2(theta) = 3/5."""
        exact = math.degrees(math.acos(math.sqrt(0.6)))
        nodes = self._first_node_deg(3, 0)
        self.assertEqual(len(nodes), 1)
        self.assertAlmostEqual(nodes[0], exact, places=1)

    def test_g_z4_nodal_cones(self):
        """g(z^4) has two cones, at cos^2(theta) = (3 +/- 2*sqrt(6/5))/7."""
        exact = sorted(
            math.degrees(math.acos(math.sqrt(x)))
            for x in (
                (3 + 2 * math.sqrt(1.2)) / 7,
                (3 - 2 * math.sqrt(1.2)) / 7,
            )
        )
        nodes = sorted(self._first_node_deg(4, 0))
        self.assertEqual(len(nodes), 2)
        for got, want in zip(nodes, exact):
            self.assertAlmostEqual(got, want, places=1)

    def test_f_xyz_is_sign_alternating_across_octants(self):
        eng = self._eng(3)
        c = np.zeros(eng.n_basis)
        c[4] = 1.0  # f(-2) = xyz
        pts = np.array([[0.7, 0.7, 0.7], [-0.7, 0.7, 0.7]])
        v = eng.evaluate_mo_on_grid(0, pts, c)
        self.assertAlmostEqual(v[0], -v[1], places=10)


if __name__ == "__main__":
    unittest.main()


class TestCalcWorkerGridGuard(unittest.TestCase):
    """v3.9.1: n_points < 2 used to divide span by zero — numpy silently
    produced inf grid vectors and a corrupt cube file was written."""

    def _make_worker(self, n_points, output_path=None):
        worker = _mod.CalcWorker(
            engine=MagicMock(),
            mo_idx=0,
            n_points=n_points,
            margin=3.0,
            atoms_sym=["H"],
            atoms_coords=[[0.0, 0.0, 0.0]],
            mo_coeffs=[1.0],
            # Guard tests never reach the writer; the success-path test
            # passes a real temp path instead.
            output_path=output_path or os.path.join("unused_dir", "x.cube"),
        )
        worker.finished_sig = MagicMock()
        worker.progress_sig = MagicMock()
        return worker

    def test_single_point_grid_rejected_not_silently_written(self):
        worker = self._make_worker(n_points=1)
        worker.run()
        success, msg = worker.finished_sig.emit.call_args[0]
        self.assertFalse(success)
        self.assertIn("at least 2 points", msg)
        worker.engine.evaluate_mo_on_grid.assert_not_called()

    def test_zero_points_also_rejected(self):
        worker = self._make_worker(n_points=0)
        worker.run()
        success, _ = worker.finished_sig.emit.call_args[0]
        self.assertFalse(success)

    def test_two_points_passes_the_guard(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            worker = self._make_worker(
                n_points=2, output_path=os.path.join(tmp, "x.cube")
            )
            worker.engine.evaluate_mo_on_grid.return_value = np.zeros(8)
            worker.run()
        success, _ = worker.finished_sig.emit.call_args[0]
        # Guard passed: either full success (cube written) or a failure from a
        # later stage — but never the grid-resolution message.
        args = worker.finished_sig.emit.call_args[0]
        self.assertNotIn("at least 2 points", str(args[1]))


class TestCalcWorkerCancel(unittest.TestCase):
    def test_a_cancelled_worker_reports_back_instead_of_going_silent(self):
        worker = _mod.CalcWorker(
            engine=MagicMock(),
            mo_idx=0,
            n_points=2,
            margin=3.0,
            atoms_sym=["H"],
            atoms_coords=[[0.0, 0.0, 0.0]],
            mo_coeffs=[1.0],
            output_path=os.path.join("unused_dir", "x.cube"),
        )
        worker.finished_sig = MagicMock()
        worker.progress_sig = MagicMock()
        worker.cancel()
        worker.run()
        worker.finished_sig.emit.assert_called_once_with(False, _mod.CANCELLED)
        worker.engine.evaluate_mo_on_grid.assert_not_called()


class TestCubeWriterGhostAtoms(unittest.TestCase):
    def test_a_dummy_atom_is_written_as_z_zero(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ghost.cube")
            _mod.CubeWriter.write(
                path,
                ["X", "H"],
                [[0.0, 0.0, 0.0], [0.0, 0.0, 0.74]],
                np.zeros(3),
                np.eye(3),
                np.zeros((2, 2, 2)),
            )
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        # Line 7 is the first atom; a GetAtomicNumber("X") failure used to
        # abort the write before any file existed.
        self.assertEqual(lines[6].split()[0], "0")
