"""Open every claimed window on real output under real Qt, and press every button.

Skipped without PySide6, numpy, matplotlib, rdkit and pyvista, unless
REQUIRE_REAL_QT=1, which the Real Qt workflow sets.
"""

import json
import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from claimed_features import CLAIMS, CONTROLS, TABS  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
#: Refusals a press is meant to get; anything else in a message box is a failure.
_EXPECTED = re.compile(
    r"Cannot delete Default preset|built-in standard and cannot be deleted"
    r"|Please select at least 2 peaks|No orbital|Please select|No .* selected"
)


def _request():
    request = {}
    for name, slot, _cls in CLAIMS:
        request.setdefault(name, [])
        if slot not in request[name]:
            request[name].append(slot)
    return request


class TestRealWindows(unittest.TestCase):
    report = None

    @classmethod
    def setUpClass(cls):
        result = subprocess.run(
            [
                sys.executable,
                os.path.join(_HERE, "_real_window_driver.py"),
                "gamess_result_analyzer",
                json.dumps(_request()),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
        )
        if result.returncode == 77 and os.environ.get("REQUIRE_REAL_QT") != "1":
            raise unittest.SkipTest("real Qt and the scientific packages are not installed")
        if result.returncode != 0 or "REPORT:" not in result.stdout:
            raise AssertionError(result.stdout[-2000:] + result.stderr[-4000:])
        cls.report = json.loads(result.stdout.split("REPORT:", 1)[1])

    def _windows(self, name, slot):
        return self.report[name][slot]["windows"]

    def test_every_claimed_window_opens(self):
        for name, slot, cls in CLAIMS:
            with self.subTest(fixture=name, slot=slot):
                self.assertIn(cls, [w["class"] for w in self._windows(name, slot)])

    def test_no_button_raises_or_reports_an_error(self):
        for name, slots in self.report.items():
            for slot, entry in slots.items():
                unexpected = [p for p in entry["problems"] if not _EXPECTED.search(p)]
                with self.subTest(fixture=name, slot=slot):
                    self.assertEqual(unexpected, [])

    def test_the_program_decides_which_controls_exist(self):
        for name, slot, present, absent in CONTROLS:
            shown = set(self._windows(name, slot)[0]["buttons"])
            with self.subTest(fixture=name, slot=slot):
                self.assertEqual(present - shown, set())
                self.assertEqual(absent & shown, set())

    def test_the_program_decides_which_tabs_exist(self):
        for name, slot, present, absent in TABS:
            tabs = " ".join(self._windows(name, slot)[0]["tabs"])
            with self.subTest(fixture=name, slot=slot):
                self.assertIn(present, tabs)
                self.assertNotIn(absent, tabs)


if __name__ == "__main__":
    unittest.main()
