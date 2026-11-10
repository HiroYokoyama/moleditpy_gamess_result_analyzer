"""Parser cases found by running GAMESS's own example inputs through it.

Each fixture is a small job written for this suite that shows the same output
layout: atoms named in words, an effective core potential, a saddle point.
"""

import importlib
import os
import sys
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_outputs")
_PKG = "_gamess_parser_cases"


def _module(name):
    if _PKG not in sys.modules:
        package = types.ModuleType(_PKG)
        package.__path__ = [os.path.join(_ROOT, "gamess_result_analyzer")]
        package.PLUGIN_VERSION = "test"
        sys.modules[_PKG] = package
    return importlib.import_module(f"{_PKG}.{name}")


def _parse(name):
    parser = _module("parser").GAMESSParser()
    with open(os.path.join(_SAMPLES, name + ".out"), encoding="utf-8") as handle:
        parser.load_from_memory(handle.read(), name + ".out")
    return parser.data


class TestAtomsNamedInWords(unittest.TestCase):
    """$DATA may call an atom OXYGEN or N1; most tables print that name."""

    def test_the_fixture_prints_the_names(self):
        with open(os.path.join(_SAMPLES, "water_named_hessian.out"), encoding="utf-8") as f:
            self.assertIn("1 OXYGEN ", f.read())

    def test_gradient(self):
        gradients = _parse("water_named_hessian")["gradients"]
        self.assertEqual([g["atom_sym"] for g in gradients], ["O", "H", "H"])
        self.assertAlmostEqual(gradients[0]["vector"][2], -0.060161565)

    def test_mulliken_and_lowdin_charges(self):
        charges = _parse("water_named_hessian")["charges"]
        self.assertEqual([c["atom_sym"] for c in charges["Mulliken"]], ["O", "H", "H"])
        self.assertAlmostEqual(charges["Mulliken"][0]["charge"], -0.365058)
        self.assertEqual(len(charges["Lowdin"]), 3)

    def test_fitted_charges_carry_the_element(self):
        rows = _parse("water_named_pdc")["charges"]["CHELPG"]
        self.assertEqual([r["atom_sym"] for r in rows], ["O", "H", "H"])
        self.assertAlmostEqual(rows[0]["charge"], -0.9362)

    def test_normal_modes_have_their_vectors(self):
        modes = _parse("water_named_hessian")["frequencies"]
        self.assertEqual([round(m["freq"], 2) for m in modes], [2049.6, 4489.64, 4788.56])
        for mode in modes:
            self.assertEqual(len(mode["vector"]), 3)
            self.assertGreater(sum(abs(v) for row in mode["vector"] for v in row), 0.1)

    def test_optimization_steps_carry_their_gradients(self):
        steps = _parse("water_named_opt")["scan_steps"]
        self.assertEqual(len(steps), 7)
        for step in steps:
            self.assertEqual(len(step["gradients"]), 3)

    def test_nmr_shieldings(self):
        rows = _parse("water_named_nmr")["nmr_shielding"]
        self.assertEqual([r["atom_sym"] for r in rows], ["O", "H", "H"])
        self.assertAlmostEqual(rows[0]["shielding"], 326.7525)
        self.assertAlmostEqual(rows[1]["shielding"], 32.7256)

    def test_names_ending_in_a_number(self):
        data = _parse("nh3_planar_hessian")
        self.assertEqual([g["atom_sym"] for g in data["gradients"]], ["N", "H", "H", "H"])
        self.assertEqual(len(data["charges"]["Mulliken"]), 4)


class TestSaddlePointModes(unittest.TestCase):
    """An imaginary mode comes first, so the rotations are modes 2 to 7, not 1 to 6."""

    def test_only_the_vibrations_remain(self):
        modes = _parse("nh3_planar_hessian")["frequencies"]
        self.assertEqual(
            [round(m["freq"], 2) for m in modes],
            [-1117.93, 1852.15, 1852.15, 4086.21, 4427.9, 4427.91],
        )

    def test_every_mode_moves_every_atom_row(self):
        for mode in _parse("nh3_planar_hessian")["frequencies"]:
            self.assertEqual(len(mode["vector"]), 4)


class TestEffectiveCorePotential(unittest.TestCase):
    """The orbitals hold only the electrons the potential keeps."""

    def test_restricted_occupations(self):
        orbitals = _parse("h2s_ecp_mo")["mo_coeffs"]
        occupations = [orbitals[f"{i}_restricted"]["occ"] for i in range(len(orbitals))]
        self.assertEqual(occupations[:5], [2.0, 2.0, 2.0, 2.0, 0.0])
        self.assertEqual(sum(occupations), 8.0)

    def test_the_homo_is_the_fourth_orbital(self):
        energies = _parse("h2s_ecp_mo")["orbital_energies"]
        occupied = [o for o in energies if o["type"] == "occupied"]
        self.assertEqual(len(occupied), 4)
        self.assertAlmostEqual(occupied[-1]["energy"], -0.3891)

    def test_unrestricted_occupations(self):
        orbitals = _parse("sh_ecp_uhf")["mo_coeffs"]
        alpha = sum(o["occ"] for key, o in orbitals.items() if key.endswith("_alpha"))
        beta = sum(o["occ"] for key, o in orbitals.items() if key.endswith("_beta"))
        self.assertEqual((alpha, beta), (4.0, 3.0))


class TestOrbitalEnergyColumns(unittest.TestCase):
    """Energies are F11.4: large ones touch and larger ones print as asterisks."""

    def setUp(self):
        self.read = _module("parser_electronic")._orbital_energies

    def test_ordinary_row(self):
        row = "                  -10.5650    -0.7114    -0.4428     0.2820     0.4001"
        self.assertEqual(self.read(row, 5), [-10.565, -0.7114, -0.4428, 0.282, 0.4001])

    def test_values_that_touch(self):
        row = "                65902.3036133655.2718281698.9341652464.5063***********"
        values = self.read(row, 5)
        self.assertEqual(values[:4], [65902.3036, 133655.2718, 281698.9341, 652464.5063])
        self.assertGreaterEqual(values[4], 999999.9999)

    def test_trailing_spaces_and_a_short_last_block(self):
        self.assertEqual(self.read("                   -0.5932     0.1584  ", 2), [-0.5932, 0.1584])

    def test_a_real_table_keeps_its_energies(self):
        orbitals = _parse("h2s_ecp_mo")["mo_coeffs"]
        energies = [orbitals[f"{i}_restricted"]["energy"] for i in range(len(orbitals))]
        self.assertEqual(energies, sorted(energies))
        self.assertAlmostEqual(energies[0], -1.005)


class TestBondOrderTable(unittest.TestCase):
    """The table ends at the valence heading, which spans two lines."""

    def test_only_the_printed_pairs_are_read(self):
        bonds = _parse("water_rhf_mo")["mayer_bond_orders"]
        self.assertEqual(
            [(b["atom_idx1"], b["atom_idx2"]) for b in bonds], [(0, 1), (0, 2)]
        )

    def test_pairs_outside_the_structure_are_dropped(self):
        parser = _module("parser").GAMESSParser()
        parser.lines = [
            "          BOND ORDER AND VALENCE ANALYSIS     BOND ORDER THRESHOLD=0.050",
            "          -------------------------------",
            "  ATOM PAIR DIST  ORDER      ATOM PAIR DIST  ORDER",
            "    1   2  0.957  0.942        4   5  0.957  0.957",
            "",
            "                       TOTAL       BONDED        FREE",
            "      ATOM            VALENCE     VALENCE     VALENCE",
            "    1 O                 1.934       1.934      -0.000",
            "    7   0  1.000  1.000",
        ]
        parser.data["atoms"] = ["O", "H", "H"]
        parser.parse_mayer_bond_orders()
        self.assertEqual(len(parser.data["mayer_bond_orders"]), 1)


if __name__ == "__main__":
    unittest.main()
