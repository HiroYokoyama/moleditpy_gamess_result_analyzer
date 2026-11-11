"""Layouts the fixtures do not show, made by editing a real log in memory."""

import importlib
import os
import re
import sys
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_outputs")
_PKG = "_gamess_parser_audit"


def _module(name):
    if _PKG not in sys.modules:
        package = types.ModuleType(_PKG)
        package.__path__ = [os.path.join(_ROOT, "gamess_result_analyzer")]
        package.PLUGIN_VERSION = "test"
        sys.modules[_PKG] = package
    return importlib.import_module(f"{_PKG}.{name}")


def _text(name):
    with open(os.path.join(_SAMPLES, name + ".out"), encoding="utf-8") as handle:
        return handle.read()


def _parse_text(text):
    parser = _module("parser").GAMESSParser()
    parser.load_from_memory(text, "edited.out")
    return parser.data


def _rename_hydrogens(text, name):
    """The C2v water log with its hydrogens called *name* in $DATA."""
    text = re.sub(r"(?m)^ H( +)1\.0 ", lambda m: f" {name}{' ' * max(1, len(m.group(1)) + 1 - len(name))}1.0 ", text)
    return re.sub(r"(?m)^ H$", f" {name}", text)


class TestNamedAtomsUnderSymmetry(unittest.TestCase):
    """Under symmetry the basis is listed once per unique atom, by its $DATA name."""

    def test_the_hydrogens_find_their_shells_by_name(self):
        plain = _parse_text(_text("water_c2v_mo"))
        named = _parse_text(_rename_hydrogens(_text("water_c2v_mo"), "HYDROGEN"))
        self.assertGreater(len(plain["basis_set_shells"]), 0)
        self.assertEqual(named["atoms"], plain["atoms"])
        self.assertEqual(
            [(s["atom_idx"], s["l"], s["exps"]) for s in named["basis_set_shells"]],
            [(s["atom_idx"], s["l"], s["exps"]) for s in plain["basis_set_shells"]],
        )

    def test_a_name_with_punctuation_is_still_an_atom(self):
        plain = _parse_text(_text("water_c2v_mo"))
        named = _parse_text(_rename_hydrogens(_text("water_c2v_mo"), "H-A"))
        self.assertEqual(named["atoms"], ["O", "H", "H"])
        self.assertEqual(named["coords"], plain["coords"])
        self.assertEqual(len(named["basis_set_shells"]), len(plain["basis_set_shells"]))


class TestNumericalHessian(unittest.TestCase):
    """A numerical hessian runs one SCF per displaced geometry after the job's own."""

    def test_the_energy_is_that_of_the_reference_geometry(self):
        text = _text("nh3_planar_hessian")
        reference = _parse_text(text)["scf_energy"]
        final = next(l for l in text.splitlines() if "FINAL RHF ENERGY IS" in l)
        displaced = final.replace(f"{reference:.10f}", f"{reference + 0.0012:.10f}")
        edited = text.replace("METHOD=ANALYTIC", "METHOD=SEMINUM ").replace(
            final, final + "\n" + displaced + "\n" + displaced, 1
        )
        self.assertEqual(edited.count("FINAL RHF ENERGY IS"), 3)
        self.assertAlmostEqual(_parse_text(edited)["scf_energy"], reference)

    def test_an_analytic_hessian_is_unchanged(self):
        text = _text("nh3_planar_hessian")
        final = next(l for l in text.splitlines() if "FINAL RHF ENERGY IS" in l)
        self.assertAlmostEqual(_parse_text(text)["scf_energy"], float(final.split()[4]))


class TestLogStillBeingWritten(unittest.TestCase):
    """Opening the log of a running job reads what is there; it never fails."""

    def _cut_inside(self, name, marker, offset):
        text = _text(name)
        start = text.rfind(marker)
        self.assertGreater(start, 0, marker)
        return text[: start + offset]

    def test_cut_inside_a_mode_row(self):
        text = _text("nh3_planar_hessian")
        start = text.rfind("FREQUENCY:")
        row = text.find(" X ", start)
        data = _parse_text(text[: row + 12])
        for mode in data["frequencies"]:
            self.assertIn(len(mode["vector"]), (0, 4))

    def test_cut_inside_a_frequency_row(self):
        text = _text("nh3_planar_hessian")
        start = text.rfind("FREQUENCY:")
        data = _parse_text(text[: start + 30])
        self.assertIsInstance(data["frequencies"], list)

    def test_cut_inside_the_thermochemistry(self):
        for name in ("water_opt_freq", "water_raman", "nh3_planar_hessian"):
            text = _text(name)
            start = text.rfind("ZERO POINT ENERGY")
            for offset in (18, 40, 900, 1500):
                data = _parse_text(text[: start + offset])
                self.assertIsInstance(data["thermal"], dict)

    def test_complete_logs_keep_their_thermochemistry(self):
        for name in ("water_opt_freq", "water_raman", "nh3_planar_hessian"):
            self.assertIn("gibbs", _parse_text(_text(name))["thermal"], name)


if __name__ == "__main__":
    unittest.main()
