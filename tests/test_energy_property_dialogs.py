"""
tests/test_energy_property_dialogs.py
Instantiation coverage for the two small table dialogs: EnergyComponentsDialog
and PropertiesDialog. Both bury all their logic in __init__, so simply
constructing them under the headless Qt harness exercises the row builders.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

EnergyComponentsDialog = gui_harness.load_isolated(
    "energy_analysis"
).EnergyComponentsDialog
PropertiesDialog = gui_harness.load_isolated("property_analysis").PropertiesDialog


class TestEnergyComponentsDialog(unittest.TestCase):
    def test_with_dimensioned_and_dimensionless_rows(self):
        data = {
            "energy_components": [
                {"label": "MP2 correlation", "value": -0.512345678},
                {"label": "T1 diagnostic", "value": 0.0123, "dimensionless": True},
            ]
        }
        EnergyComponentsDialog(None, data)  # must not raise

    def test_empty_shows_hf_dft_message(self):
        EnergyComponentsDialog(None, {})

    def test_missing_key_defaults_to_empty(self):
        EnergyComponentsDialog(None, {"other": 1})


class TestPropertiesDialog(unittest.TestCase):
    def test_full_property_set(self):
        data = {
            "scf_energy": -76.12345678,
            "charge": -1,
            "mult": 2,
            "version": "5.0.4",
            "converged": True,
            "dispersion": -0.0123,
            "spin_s2": {"actual": 0.7621, "ideal": 0.75, "contamination": 0.0121},
        }
        PropertiesDialog(None, data)

    def test_minimal_defaults(self):
        PropertiesDialog(None, {})

    def test_not_converged_and_no_s2(self):
        PropertiesDialog(None, {"scf_energy": -1.0, "converged": False})

    def test_s2_actual_without_ideal(self):
        PropertiesDialog(None, {"spin_s2": {"actual": 0.75}})


if __name__ == "__main__":
    unittest.main()
