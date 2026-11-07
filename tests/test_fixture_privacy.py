"""Every committed fixture must be free of the machine, the account and the paths."""

import os
import re
import unittest

SAMPLES = os.path.join(os.path.dirname(__file__), "sample_outputs")

_FORBIDDEN = {
    "Windows path": re.compile(r"[A-Za-z]:\\[A-Za-z0-9_.]"),
    "Unix path": re.compile(r"/(?:home|Users|scratch|work|tmp|opt|usr)/"),
    "run script": re.compile(r"GAMESS execution script|ddikick|rungms|This job is running on host"),
    "host note": re.compile(r"\b(?:ON HOST|NODE \d+ HOST|hostname)\b", re.I),
    "date": re.compile(r"\b\d\d:\d\d:\d\d \d\d-[A-Z]{3}-\d{4}\b"),
    "weekday date": re.compile(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "),
    "timing": re.compile(r"TOTAL WALL CLOCK TIME|STEP CPU TIME"),
    "private address": re.compile(r"\b(?:192\.168|10\.\d+\.\d+)\.\d+\b"),
    "mail": re.compile(r"[\w.-]+@[\w-]+\.[\w.]+"),
}
_SAFE_MOLECULES = {"benzene", "h2co", "methanol", "o2", "water"}


def _samples():
    return sorted(f for f in os.listdir(SAMPLES) if f.endswith((".log", ".out")))


def _read(name):
    with open(os.path.join(SAMPLES, name), encoding="utf-8") as handle:
        return handle.read()


class TestFixturePrivacy(unittest.TestCase):
    def test_there_are_fixtures_to_check(self):
        self.assertGreater(len(_samples()), 10)

    def test_no_identifying_text(self):
        for name in _samples():
            text = _read(name)
            for label, pattern in _FORBIDDEN.items():
                with self.subTest(fixture=name, kind=label):
                    self.assertIsNone(pattern.search(text))

    def test_only_textbook_molecules(self):
        for name in _samples():
            with self.subTest(fixture=name):
                self.assertIn(name.split("_")[0], _SAFE_MOLECULES)

    def test_the_log_starts_at_the_program_banner(self):
        for name in _samples():
            with self.subTest(fixture=name):
                self.assertIn("GAMESS VERSION =", "".join(_read(name).splitlines(True)[:6]))


if __name__ == "__main__":
    unittest.main()
