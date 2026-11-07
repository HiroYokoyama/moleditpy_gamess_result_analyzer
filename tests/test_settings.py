"""
tests/test_settings.py
Covers gamess_result_analyzer/settings.py: the shared read-merge-atomic-write
that keeps one dialog's save from discarding every other dialog's section.
"""

import json
import os
import sys
import tempfile
import shutil
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

M = gui_harness.load_isolated("settings")


class _SettingsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "settings.json")

    def write(self, obj):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)

    def read(self):
        with open(self.path, "r", encoding="utf-8") as fh:
            return json.load(fh)


class TestLoadAll(_SettingsCase):
    def test_missing_file_is_empty(self):
        self.assertEqual(M.load_all(self.path), {})

    def test_reads_a_mapping(self):
        self.write({"a": {"x": 1}})
        self.assertEqual(M.load_all(self.path), {"a": {"x": 1}})

    def test_malformed_json_is_empty_and_logged(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        with self.assertLogs(level="WARNING") as cm:
            self.assertEqual(M.load_all(self.path), {})
        self.assertTrue(any("Could not read settings" in m for m in cm.output))

    def test_a_json_list_is_rejected_not_returned(self):
        self.write([1, 2, 3])
        with self.assertLogs(level="WARNING") as cm:
            self.assertEqual(M.load_all(self.path), {})
        self.assertTrue(any("not a JSON object" in m for m in cm.output))


class TestLoadSection(_SettingsCase):
    def test_absent_section_is_empty(self):
        self.write({"other": {"k": 1}})
        self.assertEqual(M.load_section(self.path, "mine"), {})

    def test_returns_the_section(self):
        self.write({"mine": {"k": 1}})
        self.assertEqual(M.load_section(self.path, "mine"), {"k": 1})

    def test_non_mapping_section_falls_back_to_default(self):
        self.write({"mine": "corrupt"})
        self.assertEqual(M.load_section(self.path, "mine"), {})
        self.assertEqual(M.load_section(self.path, "mine", {"d": 2}), {"d": 2})


class TestSaveSection(_SettingsCase):
    def test_creates_the_file(self):
        self.assertTrue(M.save_section(self.path, "mine", {"k": 1}))
        self.assertEqual(self.read(), {"mine": {"k": 1}})

    def test_preserves_other_dialogs_sections(self):
        """The whole point: one dialog's save must not wipe the others."""
        self.write({"mo_settings": {"iso": 0.02}, "nmr_settings": {"ref": "TMS"}})
        M.save_section(self.path, "thermal_settings", {"show_details": True})
        self.assertEqual(
            self.read(),
            {
                "mo_settings": {"iso": 0.02},
                "nmr_settings": {"ref": "TMS"},
                "thermal_settings": {"show_details": True},
            },
        )

    def test_replaces_only_its_own_section(self):
        self.write({"mine": {"old": 1}, "other": {"keep": 2}})
        M.save_section(self.path, "mine", {"new": 3})
        self.assertEqual(self.read(), {"mine": {"new": 3}, "other": {"keep": 2}})

    def test_a_corrupt_file_does_not_block_the_save(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        with self.assertLogs(level="WARNING"):
            self.assertTrue(M.save_section(self.path, "mine", {"k": 1}))
        self.assertEqual(self.read(), {"mine": {"k": 1}})

    def test_unwritable_path_is_reported_not_raised(self):
        # A path under a file can never be created; avoids patching open(),
        # which breaks pytest's own I/O.
        blocker = os.path.join(self.tmp, "blocker")
        with open(blocker, "w", encoding="utf-8") as fh:
            fh.write("x")
        target = os.path.join(blocker, "settings.json")
        with self.assertLogs(level="WARNING") as cm:
            self.assertFalse(M.save_section(target, "mine", {"k": 1}))
        self.assertTrue(any("Could not save mine settings" in m for m in cm.output))

    def test_unserialisable_value_is_reported_not_raised(self):
        with self.assertLogs(level="WARNING"):
            self.assertFalse(M.save_section(self.path, "mine", {"k": object()}))

    def test_writes_atomically(self):
        with patch.object(M, "save_json_atomic") as atomic:
            M.save_section(self.path, "mine", {"k": 1})
        atomic.assert_called_once()
        self.assertEqual(atomic.call_args[0][0], self.path)
        self.assertEqual(atomic.call_args[0][1], {"mine": {"k": 1}})


if __name__ == "__main__":
    unittest.main()
