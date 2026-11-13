"""Real orbitals are orthonormal over the basis the renderer evaluates.

A mis-normalized or misordered basis function still draws a plausible orbital,
so the picture cannot catch it. Here the overlap matrix of the engine's own
basis functions is built analytically and C^T S C must be the identity for
every orbital the program printed: this checks the parsed shells, their
contraction coefficients, the Cartesian/spherical component definitions and
their order against the program itself. The tolerance is the rounding of the
printed coefficients.
"""

import importlib.util
import os
import sys
import types
import unittest
from math import comb, pi, sqrt
from unittest.mock import MagicMock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _parser_loader import load_standalone_parser

_PKG = "gamess_result_analyzer"
_PARSER = "GAMESSParser"
_SAMPLES = {
    # sample: what it adds
    "water_rhf_mo": "s/p, RHF",
    "h2co_cct_mo": "cc-pVTZ: d and f",
    "water_ccq_mo": "cc-pVQZ: d, f and g",
    "o2_uhf_opt": "alpha and beta sets",
    "h2s_ecp_mo": "an ECP basis",
}
_TOLERANCE = 2e-5
_BOHR = 0.529177249
_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _load_engine():
    """mo_engine with a throwaway QtCore stub, restored afterwards."""

    class _QThread:
        def __init__(self, *a, **k):
            pass

    core = types.ModuleType("PySide6.QtCore")
    core.QThread = _QThread
    core.Signal = lambda *a, **k: MagicMock()
    pyside6 = types.ModuleType("PySide6")
    pyside6.QtCore = core
    package = types.ModuleType("_mo_orthonormality_pkg")
    package.__path__ = [os.path.join(_ROOT, _PKG)]
    package.PLUGIN_VERSION = "test"
    names = ("PySide6", "PySide6.QtCore", package.__name__)
    saved = {k: sys.modules.get(k) for k in names}
    sys.modules.update(
        {"PySide6": pyside6, "PySide6.QtCore": core, package.__name__: package}
    )
    try:
        spec = importlib.util.spec_from_file_location(
            f"{package.__name__}.mo_engine", os.path.join(_ROOT, _PKG, "mo_engine.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _odd_double_factorial(n):
    result = 1
    while n > 1:
        result *= n
        n -= 2
    return result


def _overlap_1d(la, lb, a, b, p, centre):
    """One Cartesian factor of a primitive overlap (binomial expansion)."""
    total = 0.0
    for i in range(la + 1):
        for j in range(lb + 1):
            if (i + j) % 2 == 0:
                total += (
                    comb(la, i) * comb(lb, j) * (centre - a) ** (la - i)
                    * (centre - b) ** (lb - j)
                    * _odd_double_factorial(i + j - 1) / (2 * p) ** ((i + j) // 2)
                )
    return total * sqrt(pi / p)


def _primitive_overlap(alpha, powers_a, a, beta, powers_b, b):
    p = alpha + beta
    centre = (alpha * a + beta * b) / p
    value = np.exp(-alpha * beta / p * float(np.sum((a - b) ** 2)))
    for k in range(3):
        value *= _overlap_1d(powers_a[k], powers_b[k], a[k], b[k], p, centre[k])
    return value


def _basis_functions(engine):
    """Each basis function as (coefficient, exponent, powers, centre) terms."""
    functions = []
    for shell in engine.shells:
        for components in shell["basis_data"]:
            functions.append(
                [
                    (c["coeffs"][k], alpha, (c["l"], c["m"], c["n"]), shell["center"])
                    for c in components
                    for k, alpha in enumerate(shell["exps"])
                ]
            )
    return functions


def _overlap_matrix(functions):
    n = len(functions)
    s = np.zeros((n, n))
    for i in range(n):
        for j in range(i, n):
            s[i, j] = s[j, i] = sum(
                ca * cb * _primitive_overlap(alpha, pa, a, beta, pb, b)
                for ca, alpha, pa, a in functions[i]
                for cb, beta, pb, b in functions[j]
            )
    return s


def _coefficients(mo):
    return [c["coeff"] if isinstance(c, dict) else c for c in mo["coeffs"]]


ENGINE = _load_engine()


class TestRealOrbitalsAreOrthonormal(unittest.TestCase):
    def _check(self, sample):
        parser = getattr(load_standalone_parser(), _PARSER)()
        path = os.path.join(_ROOT, "tests", "sample_outputs", sample + ".out")
        with open(path, encoding="utf-8") as handle:
            parser.load_from_memory(handle.read(), sample + ".out")
        data = parser.data
        shells = [
            {
                "type": s["l"],
                "center": np.array(s["origin"], float) / _BOHR,
                "exps": np.array(s["exps"], float),
                "coeffs": np.array(s["coeffs"], float),
                "cartesian": bool(s.get("cartesian", False)),
            }
            for s in data["basis_set_shells"]
        ]
        engine = ENGINE.BasisSetEngine(shells)
        overlap = _overlap_matrix(_basis_functions(engine))
        mos = list(data["mo_coeffs"].values())
        self.assertTrue(mos)
        for spin in sorted({mo.get("spin", "") for mo in mos}):
            orbitals = [mo for mo in mos if mo.get("spin", "") == spin]
            c = np.array([_coefficients(mo) for mo in orbitals]).T
            self.assertEqual(c.shape[0], engine.n_basis, f"{sample} {spin}")
            deviation = np.abs(c.T @ overlap @ c - np.eye(c.shape[1])).max()
            self.assertLess(deviation, _TOLERANCE, f"{sample} {spin}")

    def test_samples(self):
        for sample, what in _SAMPLES.items():
            with self.subTest(sample=sample, covers=what):
                self._check(sample)


if __name__ == "__main__":
    unittest.main()
