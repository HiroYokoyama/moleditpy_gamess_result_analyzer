"""Every feature the README claims, against real GAMESS output.

The table below is the list of claims. Parsing a job that has the result must
enable that feature's button; a job without it must leave the button off. The
windows themselves are opened under real Qt by test_real_windows.py.
"""

import importlib
import os
import sys
import types
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_harness  # noqa: E402
from claimed_features import BUTTONS, DISABLED  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SAMPLES = os.path.join(_ROOT, "tests", "sample_outputs")
_PKG = "_gamess_claims"
G = gui_harness.load_isolated("gui")


def _parse(name):
    """Parser data from one fixture, read without running the plugin's __init__."""
    if _PKG not in sys.modules:
        package = types.ModuleType(_PKG)
        package.__path__ = [os.path.join(_ROOT, "gamess_result_analyzer")]
        package.PLUGIN_VERSION = "test"
        sys.modules[_PKG] = package
    parser = importlib.import_module(f"{_PKG}.parser").GAMESSParser()
    path = os.path.join(_SAMPLES, name + ".out")
    with open(path, encoding="utf-8") as handle:
        parser.load_from_memory(handle.read(), path)
    return parser.data


class TestButtonsFollowTheOutput(unittest.TestCase):
    def _dialog(self, name):
        parser = MagicMock()
        parser.data = _parse(name)
        parser.filename = name + ".out"
        dlg = G.GAMESSResultAnalyzerDialog(
            MagicMock(), parser, name + ".out", context=MagicMock()
        )
        dlg.update_button_states()
        return dlg

    def test_a_job_with_the_result_enables_its_button(self):
        for name, button in BUTTONS:
            with self.subTest(fixture=name, button=button):
                self.assertTrue(getattr(self._dialog(name), button).isEnabled())

    def test_a_job_without_the_result_leaves_its_button_off(self):
        for name, button in DISABLED:
            with self.subTest(fixture=name, button=button):
                self.assertFalse(getattr(self._dialog(name), button).isEnabled())


if __name__ == "__main__":
    unittest.main()
