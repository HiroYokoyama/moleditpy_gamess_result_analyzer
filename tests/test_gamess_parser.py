"""GAMESSParser against real GAMESS US output (tests/sample_outputs).

The expected numbers were read from the logs themselves. Two checks are independent
of the parser's own reading: the same level of theory run in Gaussian 16 gives the
same water frequencies, intensities and energies; and coefficients and basis from
the parser, evaluated by the engine on a real-space grid, must give orthonormal
orbitals, which fails if an order, a normalization or a shell assignment is wrong.
"""

import importlib
import os
import sys
import types
import unittest

try:
    import numpy as np
except ImportError:
    np = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_harness  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_outputs")
_PKG = "_gamess_parser_only"
_BOHR = 0.529177249


def _load_module(name):
    """Import one package module without running the plugin's __init__."""
    if _PKG not in sys.modules:
        package = types.ModuleType(_PKG)
        package.__path__ = [os.path.join(_ROOT, "gamess_result_analyzer")]
        package.PLUGIN_VERSION = "test"
        sys.modules[_PKG] = package
    return importlib.import_module(f"{_PKG}.{name}")


def _parse(name):
    parser = _load_module("parser").GAMESSParser()
    with open(os.path.join(_SAMPLES, name + ".out"), encoding="utf-8") as handle:
        parser.load_from_memory(handle.read(), name + ".out")
    return parser.data


class TestBasics(unittest.TestCase):
    def test_version(self):
        self.assertEqual(_parse("water_rhf_mo")["version"], "GAMESS 15 JUL 2024 (R2 Patch 1)")

    def test_scf_energy(self):
        self.assertAlmostEqual(_parse("water_rhf_mo")["scf_energy"], -76.0105049982)

    def test_geometry_is_converted_from_bohr(self):
        data = _parse("water_rhf_mo")
        self.assertEqual(data["atoms"], ["O", "H", "H"])
        self.assertAlmostEqual(data["coords"][1][1], 0.7572, places=3)

    def test_charge_and_multiplicity(self):
        data = _parse("o2_rohf")
        self.assertEqual((data["charge"], data["mult"]), (0, 3))

    def test_normal_termination(self):
        self.assertEqual(_parse("water_rhf_mo")["termination_status"], "Terminated normally")

    def test_a_job_that_stopped_on_an_error_is_reported(self):
        self.assertEqual(_parse("methanol_nmr_abnormal")["termination_status"], "ERROR")


class TestTrajectories(unittest.TestCase):
    def test_optimization_cycles_and_convergence(self):
        data = _parse("water_opt_freq")
        self.assertEqual(len(data["scan_steps"]), 6)
        self.assertTrue(data["converged"])
        self.assertAlmostEqual(data["scan_steps"][-1]["energy"], -76.0107465155, places=7)

    def test_an_optimization_out_of_steps_is_not_converged(self):
        self.assertFalse(_parse("water_failed_opt")["converged"])

    def test_every_step_carries_a_structure(self):
        for step in _parse("benzene_opt_hess")["scan_steps"]:
            self.assertEqual(len(step["atoms"]), 12)
            self.assertEqual(len(step["coords"]), 12)

    def test_gradients_cover_every_atom(self):
        data = _parse("water_gradient")
        self.assertEqual(len(data["gradients"]), 3)
        total = [sum(g["vector"][axis] for g in data["gradients"]) for axis in range(3)]
        for value in total:
            self.assertAlmostEqual(value, 0.0, places=5)


class TestSpectra(unittest.TestCase):
    def test_frequency_count_leaves_out_translation_and_rotation(self):
        self.assertEqual(len(_parse("benzene_opt_hess")["frequencies"]), 30)
        self.assertEqual(len(_parse("water_opt_freq")["frequencies"]), 3)

    def test_modes_have_one_vector_per_atom(self):
        for mode in _parse("water_opt_freq")["frequencies"]:
            self.assertEqual(len(mode["vector"]), 3)

    def test_ir_and_raman_agree_with_gaussian_at_the_same_level(self):
        # RHF/6-31G(d) in Gaussian 16: 1826.56 cm-1, 107.27 km/mol, 5.7237 A**4/amu
        mode = _parse("water_raman")["frequencies"][0]
        self.assertAlmostEqual(mode["freq"], 1826.56, delta=0.1)
        self.assertAlmostEqual(mode["ir"], 107.27, delta=0.1)
        self.assertAlmostEqual(mode["raman"], 5.7237, delta=0.01)

    def test_every_raman_mode_is_read(self):
        modes = _parse("water_raman")["frequencies"]
        self.assertEqual([round(m["raman"], 3) for m in modes], [5.724, 75.517, 39.091])

    def test_a_frequency_job_without_raman_has_zero_activity(self):
        self.assertTrue(all(m["raman"] == 0.0 for m in _parse("water_opt_freq")["frequencies"]))

    def test_a_raman_job_reports_the_energy_without_the_field(self):
        self.assertAlmostEqual(_parse("water_raman")["scf_energy"], -76.0107465155, places=7)

    def test_thermochemistry_is_in_hartree_and_includes_the_zpe(self):
        data = _parse("water_opt_freq")
        thermal = data["thermal"]
        self.assertAlmostEqual(thermal["zpe"], 0.022977, places=5)
        self.assertAlmostEqual(thermal["enthalpy"], -75.983992, places=5)
        self.assertAlmostEqual(thermal["gibbs"], -76.00602, places=5)
        self.assertEqual(thermal["imaginary_freq_count"], 0)

    def test_tddft_states_and_amplitudes(self):
        states = _parse("h2co_tddft")["tddft"]
        self.assertEqual(len(states), 5)
        self.assertAlmostEqual(states[1]["energy_ev"], 9.077)
        self.assertAlmostEqual(states[1]["osc_len"], 0.1567)
        self.assertIn("8 -> 10", " ".join(states[1]["transitions"]))

    def test_a_repeated_tddft_run_keeps_only_its_own_amplitudes(self):
        """An optimization prints a TD-DFT run per geometry; the amplitudes of
        earlier runs used to pile up under the same state numbers."""
        with open(os.path.join(_SAMPLES, "h2co_tddft.out"), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        first = next(i for i, l in enumerate(lines) if "SINGLET EXCITATIONS" in l) - 1
        last = next(i for i, l in enumerate(lines) if "DONE WITH TD-DFT" in l) + 1
        run = lines[first:last]
        # The earlier run gets an amplitude row the later one does not have.
        earlier = [l.replace("8       9    0.999876", "7       9    0.999876") for l in run]
        parser = _load_module("parser").GAMESSParser()
        parser.load_from_memory(
            "\n".join(lines[:first] + earlier + run + lines[last:]), "repeated.out"
        )
        once = _parse("h2co_tddft")["tddft"]
        twice = parser.data["tddft"]
        self.assertEqual(
            [s["transitions"] for s in twice], [s["transitions"] for s in once]
        )
        self.assertNotIn("7 -> 9", " ".join(twice[0]["transitions"]))

    def test_tddft_claims_only_the_length_gauge(self):
        state = _parse("h2co_tddft")["tddft"][0]
        for key in ("osc_vel", "rot_len", "rot_vel", "rotatory_strength"):
            self.assertNotIn(key, state)

    def test_nmr_shieldings(self):
        data = _parse("methanol_nmr")
        self.assertEqual(len(data["nmr_shielding"]), 6)
        self.assertEqual(data["nmr_shielding"][0]["atom_sym"], "C")
        self.assertAlmostEqual(data["nmr_shielding"][0]["shielding"], 152.0486)
        self.assertAlmostEqual(data["nmr_shielding"][0]["anisotropy"], 61.3937)

    def test_nmr_claims_no_couplings(self):
        self.assertNotIn("nmr_couplings", _parse("methanol_nmr"))


class TestProperties(unittest.TestCase):
    def test_dipole(self):
        self.assertAlmostEqual(_parse("water_rhf_mo")["dipole"]["magnitude_debye"], 2.226043)

    def test_dispersion_energy(self):
        self.assertAlmostEqual(_parse("water_d3")["dispersion"], -7.8838e-06)

    def test_spin_contamination(self):
        s2 = _parse("o2_uhf_opt")["spin_s2"]
        self.assertAlmostEqual(s2["actual"], 2.032)
        self.assertAlmostEqual(s2["contamination"], 0.032)

    def test_correlation_energies(self):
        mp2 = {c["label"]: c["value"] for c in _parse("water_mp2")["energy_components"]}
        self.assertAlmostEqual(mp2["MP2 Total"], -76.1966245295)
        self.assertAlmostEqual(mp2["SCS-MP2 Total"], -76.1925685335)
        cc = {c["label"]: c["value"] for c in _parse("water_ccsdt")["energy_components"]}
        self.assertAlmostEqual(cc["CCSD Total"], -76.2056655707)
        self.assertIn("CCSD(T) Total", cc)

    def test_charge_schemes_sum_to_the_total_charge(self):
        for name, schemes in (
            ("water_rhf_mo", {"Mulliken", "Lowdin"}),
            ("water_pdc", {"Mulliken", "Lowdin", "CHELPG"}),
        ):
            charges = _parse(name)["charges"]
            self.assertEqual(set(charges), schemes)
            for scheme, rows in charges.items():
                with self.subTest(fixture=name, scheme=scheme):
                    self.assertEqual(len(rows), 3)
                    self.assertAlmostEqual(sum(r["charge"] for r in rows), 0.0, delta=0.002)

    def test_mayer_bond_orders(self):
        bonds = _parse("benzene_opt_hess")["mayer_bond_orders"]
        self.assertEqual(len(bonds), 15)
        self.assertTrue(all(b["order"] > 0 for b in bonds))

    def test_nbo_is_not_claimed(self):
        data = _parse("water_rhf_mo")
        self.assertNotIn("nbo_orbitals", data)
        self.assertNotIn("nbo_perturbation", data)


class TestOrbitals(unittest.TestCase):
    def test_restricted_mo_table_is_complete(self):
        mos = _parse("water_rhf_mo")["mo_coeffs"]
        self.assertEqual(len(mos), 19)
        self.assertEqual(len(mos["0_restricted"]["coeffs"]), 19)

    def test_every_orbital_energy_is_read(self):
        mos = _parse("water_rhf_mo")["mo_coeffs"]
        energies = [mos[f"{i}_restricted"]["energy"] for i in range(19)]
        self.assertEqual(energies, sorted(energies))
        self.assertNotIn(0.0, energies)
        self.assertAlmostEqual(energies[0], -20.5605)
        self.assertAlmostEqual(energies[4], -0.4979)

    def test_spherical_basis_has_fewer_orbitals_than_coefficients(self):
        mos = _parse("h2co_cct_mo")["mo_coeffs"]
        self.assertEqual(len(mos), 88)
        self.assertEqual(len(mos["0_restricted"]["coeffs"]), 100)

    def test_unrestricted_table_has_both_spins(self):
        mos = _parse("o2_uhf_opt")["mo_coeffs"]
        self.assertEqual(len(mos), 38)
        self.assertEqual(mos["8_alpha"]["occ"], 1.0)
        self.assertEqual(mos["8_beta"]["occ"], 0.0)

    def test_occupations_count_the_electrons(self):
        occupied = sum(m["occ"] for m in _parse("o2_rohf")["mo_coeffs"].values())
        self.assertEqual(occupied, 16.0)

    def test_scf_trace_for_each_reference(self):
        for name, cycles in (("water_rhf_mo", 10), ("o2_uhf_opt", 10), ("o2_rohf", 9)):
            traces = _parse(name)["scf_traces"]
            with self.subTest(fixture=name):
                self.assertEqual(len(traces[0]["iterations"]), cycles)

    def test_only_d_and_higher_shells_choose_a_component_set(self):
        for shell in _parse("water_rhf_mo")["basis_set_shells"]:
            self.assertEqual(shell["cartesian"], shell["l"] >= 2)


def _grid(centers, n_rad=90, n_ang=194):
    """Becke-partitioned atomic grid, in bohr."""
    theta = np.arccos(1 - 2 * (np.arange(n_ang) + 0.5) / n_ang)
    phi = np.pi * (1 + 5**0.5) * np.arange(n_ang)
    sphere = np.c_[
        np.sin(theta) * np.cos(phi), np.sin(theta) * np.sin(phi), np.cos(theta)
    ]
    k = np.arange(1, n_rad + 1)
    x = np.cos(np.pi * k / (n_rad + 1))
    r = (1 + x) / (1 - x)
    wr = (
        (np.pi / (n_rad + 1))
        * np.sin(np.pi * k / (n_rad + 1)) ** 2
        * 2
        * r**2
        / (np.sqrt(1 - x**2) * (1 - x) ** 2)
    )
    points, weights = [], []
    for ia, center in enumerate(centers):
        p = center + (r[:, None, None] * sphere[None]).reshape(-1, 3)
        w = np.repeat(wr, n_ang) * 4 * np.pi / n_ang
        d = np.linalg.norm(p[:, None] - centers[None], axis=2)
        cell = np.ones((len(p), len(centers)))
        for i in range(len(centers)):
            for j in range(len(centers)):
                if i != j:
                    mu = (d[:, i] - d[:, j]) / np.linalg.norm(centers[i] - centers[j])
                    for _ in range(3):
                        mu = 1.5 * mu - 0.5 * mu**3
                    cell[:, i] *= 0.5 * (1 - mu)
        points.append(p)
        weights.append(w * cell[:, ia] / cell.sum(axis=1))
    return np.vstack(points), np.concatenate(weights)


@unittest.skipIf(np is None, "numpy is not installed")
class TestOrthonormalOnAGrid(unittest.TestCase):
    def _overlap(self, name, spin, count, n_ang=194):
        engine_module = gui_harness.load_isolated("mo_engine")
        data = _parse(name)
        shells = [
            {
                "type": s["l"],
                "center": np.array(s["origin"]) / _BOHR,
                "exps": np.array(s["exps"]),
                "coeffs": np.array(s["coeffs"]),
                "cartesian": s["cartesian"],
            }
            for s in data["basis_set_shells"]
        ]
        engine = engine_module.BasisSetEngine(shells)
        points, weights = _grid(np.array(data["coords"]) / _BOHR, n_ang=n_ang)
        values = np.array(
            [
                engine.evaluate_mo_on_grid(
                    i,
                    points,
                    np.array(
                        [c["coeff"] for c in data["mo_coeffs"][f"{i}_{spin}"]["coeffs"]]
                    ),
                )
                for i in range(count)
            ]
        )
        return (values * weights) @ values.T

    def _assert_orthonormal(self, name, spin, count, n_ang=194):
        overlap = self._overlap(name, spin, count, n_ang)
        self.assertLess(np.abs(overlap - np.eye(count)).max(), 0.005)

    def test_cartesian_d(self):
        self._assert_orthonormal("water_rhf_mo", "restricted", 19)

    def test_f_shells_in_a_spherical_run(self):
        self._assert_orthonormal("h2co_cct_mo", "restricted", 88, n_ang=302)

    def test_g_shells_in_a_spherical_run(self):
        self._assert_orthonormal("water_ccq_mo", "restricted", 115, n_ang=434)

    def test_unrestricted_alpha_and_beta(self):
        self._assert_orthonormal("o2_uhf_opt", "alpha", 19)
        self._assert_orthonormal("o2_uhf_opt", "beta", 19)

    def test_an_optimization_shows_the_orbitals_of_the_final_geometry(self):
        self._assert_orthonormal("water_opt_freq", "restricted", 15)

    def test_symmetry_copies_take_the_listed_atoms_basis(self):
        self._assert_orthonormal("water_c2v_mo", "restricted", 15)

    def test_restricted_open_shell(self):
        self._assert_orthonormal("o2_rohf", "restricted", 30)


if __name__ == "__main__":
    unittest.main()
