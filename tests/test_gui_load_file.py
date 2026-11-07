"""
tests/test_gui_load_file.py
Covers GAMESSResultAnalyzerDialog.load_file: encoding fallback and the state
reset that has to happen when a new result replaces the previous one.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

G = gui_harness.load_isolated("gui")

OUT_TEXT = """
          *      GAMESS VERSION = 15 JUL 2024 (R2 Patch 1)          *

 ATOM      ATOMIC                      COORDINATES (BOHR)
           CHARGE         X                   Y                   Z
 O           8.0     0.0000000000        0.0000000000        0.0000000000
 H           1.0     1.8141000000        0.0000000000        0.0000000000

 FINAL RHF ENERGY IS      -76.0105049982 AFTER  10 ITERATIONS
 EXECUTION OF GAMESS TERMINATED NORMALLY
"""


class _LoadCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        self.first = self._write("first.out", OUT_TEXT)

        self.context = MagicMock()
        self.mw = MagicMock()
        self.parser = MagicMock()
        self.parser.filename = self.first
        self.parser.data = {}

        self.dlg = G.GAMESSResultAnalyzerDialog(
            self.mw, self.parser, self.first, context=self.context
        )
        # Drawing needs a live 3D viewer; covered separately.
        patcher = patch.object(self.dlg, "load_structure_3d")
        self.draw = patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, name, text, encoding="utf-8"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding=encoding) as fh:
            fh.write(text)
        return path


class TestLoadFile(_LoadCase):
    def test_a_rejected_child_close_keeps_the_current_result(self):
        child = MagicMock()
        child.close.return_value = False
        self.dlg.nmr_dlg = child
        before = self.dlg.file_path
        with patch.object(G, "load_gamess_parser") as load:
            self.dlg.load_file(self._write("second.out", OUT_TEXT))
        load.assert_not_called()
        self.assertEqual(self.dlg.file_path, before)
        self.assertIs(self.dlg.nmr_dlg, child)

    def test_a_rejected_child_close_keeps_the_analyzer_open(self):
        child = MagicMock()
        child.close.return_value = False
        self.dlg.nmr_dlg = child
        event = MagicMock()
        self.dlg.closeEvent(event)
        event.ignore.assert_called_once()
        event.accept.assert_not_called()

    def test_the_file_becomes_the_current_result(self):
        path = self._write("second.out", OUT_TEXT)
        self.dlg.load_file(path)
        self.assertEqual(self.dlg.file_path, path)

    def test_the_output_is_parsed(self):
        path = self._write("second.out", OUT_TEXT)
        self.dlg.load_file(path)
        self.assertEqual(self.dlg.parser.data.get("version"), "GAMESS 15 JUL 2024 (R2 Patch 1)")

    def test_the_geometry_is_parsed(self):
        path = self._write("second.out", OUT_TEXT)
        self.dlg.load_file(path)
        self.assertEqual(len(self.dlg.parser.data.get("coords", [])), 2)

    def test_the_structure_is_reframed_for_a_new_result(self):
        path = self._write("second.out", OUT_TEXT)
        self.dlg.load_file(path)
        self.draw.assert_called_once_with(fit_camera=True)

    def test_open_analysis_windows_are_closed_first(self):
        stale = MagicMock()
        self.dlg.mo_dlg = stale
        self.dlg.load_file(self._write("second.out", OUT_TEXT))
        stale.close.assert_called_once()

    def test_atom_colours_from_the_previous_result_are_cleared(self):
        with patch.object(G, "clear_atom_color_overrides") as clear:
            self.dlg.load_file(self._write("second.out", OUT_TEXT))
        clear.assert_called_once_with(self.dlg.mw)

    def test_the_main_window_title_is_synced(self):
        path = self._write("second.out", OUT_TEXT)
        self.dlg.load_file(path)
        self.assertEqual(self.mw.init_manager.current_file_path, path)

    def test_the_main_window_title_is_actually_redrawn(self):
        path = self._write("second.out", OUT_TEXT)
        self.dlg.load_file(path)
        self.mw.state_manager.update_window_title.assert_called()

    def test_cancelling_the_load_keeps_the_previous_result(self):
        before = self.dlg.file_path
        with patch.object(G, "load_gamess_parser", return_value=None):
            self.dlg.load_file(self._write("second.out", OUT_TEXT))
        self.assertEqual(self.dlg.file_path, before)

    def test_cancelling_the_load_is_reported(self):
        with patch.object(G, "load_gamess_parser", return_value=None):
            self.dlg.load_file(self._write("second.out", OUT_TEXT))
        self.assertTrue(
            any(
                "cancelled" in str(c.args[0]).lower()
                for c in self.context.show_status_message.call_args_list
            )
        )

    def test_success_is_reported(self):
        self.dlg.load_file(self._write("second.out", OUT_TEXT))
        self.assertTrue(
            any(
                "Successfully loaded" in str(c.args[0])
                for c in self.context.show_status_message.call_args_list
            )
        )

    def test_a_utf16_output_is_read(self):
        path = self._write("utf16.out", OUT_TEXT, encoding="utf-16")
        self.dlg.load_file(path)
        self.assertEqual(self.dlg.parser.data.get("version"), "GAMESS 15 JUL 2024 (R2 Patch 1)")

    def test_undecodable_bytes_do_not_stop_the_load(self):
        path = os.path.join(self.tmp, "binary.out")
        with open(path, "wb") as fh:
            fh.write(OUT_TEXT.encode("utf-8") + b"\xff\xfe\x00rubbish")
        self.dlg.load_file(path)
        self.assertEqual(self.dlg.file_path, path)

    def test_a_missing_file_is_reported(self):
        with patch.object(G.QMessageBox, "critical") as crit:
            self.dlg.load_file(os.path.join(self.tmp, "nope.out"))
        crit.assert_called_once()

    def test_a_failed_load_keeps_the_previous_result(self):
        before = self.dlg.file_path
        with patch.object(G.QMessageBox, "critical"):
            self.dlg.load_file(os.path.join(self.tmp, "nope.out"))
        self.assertEqual(self.dlg.file_path, before)


if __name__ == "__main__":
    unittest.main()
