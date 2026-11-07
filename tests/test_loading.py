"""
tests/test_loading.py
Covers gamess_result_analyzer/loading.py: the encoding-tolerant reader and the
progress-dialog-driven load that keeps a large .out from looking like a hang.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

M = gui_harness.load_isolated("loading")

OUT_TEXT = """
          *      GAMESS VERSION = 15 JUL 2024 (R2 Patch 1)          *

 ATOM      ATOMIC                      COORDINATES (BOHR)
           CHARGE         X                   Y                   Z
 O           8.0     0.0000000000        0.0000000000        0.0000000000
 H           1.0     1.8141000000        0.0000000000        0.0000000000

 FINAL RHF ENERGY IS      -76.0105049982 AFTER  10 ITERATIONS
 EXECUTION OF GAMESS TERMINATED NORMALLY
"""


class _Signal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self):
        for slot in list(self._slots):
            slot()


class FakeProgressDialog:
    """Records what the loader drives, and can fire Cancel on demand."""

    instances = []

    def __init__(self, label, cancel_text, low, high, parent):
        self.labels = [label]
        self.values = []
        self.cancel_text = cancel_text
        self.range = (low, high)
        self.parent = parent
        self.title = None
        self.minimum_duration = None
        self.closed = False
        self.canceled = _Signal()
        #: set by a test — fires Cancel once the bar reaches this value
        self.cancel_at = None
        FakeProgressDialog.instances.append(self)

    def setWindowTitle(self, title):
        self.title = title

    def setWindowModality(self, _mode):
        pass

    def setMinimumDuration(self, ms):
        self.minimum_duration = ms

    def setAutoClose(self, _flag):
        pass

    def setAutoReset(self, _flag):
        pass

    def setLabelText(self, text):
        self.labels.append(text)

    def setValue(self, value):
        self.values.append(value)
        if self.cancel_at is not None and value >= self.cancel_at:
            self.cancel_at = None
            self.canceled.emit()

    def close(self):
        self.closed = True

    def hide(self):
        pass

    def deleteLater(self):
        pass


class _LoadingCase(unittest.TestCase):
    def setUp(self):
        FakeProgressDialog.instances = []
        patcher = patch.object(M, "QProgressDialog", FakeProgressDialog)
        patcher.start()
        self.addCleanup(patcher.stop)

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name, text, encoding="utf-8"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding=encoding) as fh:
            fh.write(text)
        return path

    @property
    def dialog(self):
        return FakeProgressDialog.instances[-1]


class TestReadGAMESSText(_LoadingCase):
    def test_a_utf8_file_is_read(self):
        path = self._write("a.out", OUT_TEXT)
        self.assertIn("TERMINATED NORMALLY", M.read_gamess_text(path))

    def test_a_utf16_file_is_read(self):
        path = self._write("a.out", OUT_TEXT, encoding="utf-16")
        self.assertIn("TERMINATED NORMALLY", M.read_gamess_text(path))

    def test_undecodable_bytes_are_replaced_rather_than_failing(self):
        path = os.path.join(self.tmp, "b.out")
        with open(path, "wb") as fh:
            fh.write(OUT_TEXT.encode("utf-8") + b"\xff\xfe\x00rubbish")
        self.assertIn("TERMINATED NORMALLY", M.read_gamess_text(path))

    def test_a_bomless_8bit_file_is_not_read_as_utf16(self):
        # Even-length cp1252 bytes decode "successfully" as UTF-16 into CJK
        # garbage; that used to win before the 8-bit codecs were tried.
        path = self._write("c.out", "Å TERMINATED\n", encoding="cp1252")
        self.assertEqual(M.read_gamess_text(path), "Å TERMINATED\n")

    def test_crlf_line_endings_are_normalized(self):
        path = os.path.join(self.tmp, "d.out")
        with open(path, "wb") as fh:
            fh.write(b"line1\r\nline2\r\n")
        self.assertEqual(M.read_gamess_text(path), "line1\nline2\n")

    def test_a_missing_file_raises(self):
        with self.assertRaises(OSError):
            M.read_gamess_text(os.path.join(self.tmp, "nope.out"))


class TestLoadGAMESSParser(_LoadingCase):
    def test_the_file_is_parsed(self):
        parser = M.load_gamess_parser(self._write("a.out", OUT_TEXT), parent=None)
        self.assertEqual(parser.data.get("version"), "GAMESS 15 JUL 2024 (R2 Patch 1)")

    def test_a_progress_dialog_is_shown(self):
        M.load_gamess_parser(self._write("a.out", OUT_TEXT), parent=None)
        self.assertEqual(len(FakeProgressDialog.instances), 1)

    def test_progress_runs_from_zero_to_full(self):
        M.load_gamess_parser(self._write("a.out", OUT_TEXT), parent=None)
        self.assertEqual(self.dialog.values[0], 0)
        self.assertEqual(self.dialog.values[-1], 100)

    def test_progress_never_goes_backwards(self):
        M.load_gamess_parser(self._write("a.out", OUT_TEXT), parent=None)
        values = self.dialog.values
        self.assertEqual(values, sorted(values))

    def test_each_parse_step_is_named_for_the_user(self):
        M.load_gamess_parser(self._write("a.out", OUT_TEXT), parent=None)
        joined = " ".join(self.dialog.labels)
        self.assertIn("Reading vibrational frequencies", joined)
        self.assertIn("Reading NMR data", joined)

    def test_the_dialog_is_closed_afterwards(self):
        M.load_gamess_parser(self._write("a.out", OUT_TEXT), parent=None)
        self.assertTrue(self.dialog.closed)

    def test_the_dialog_does_not_flash_for_a_fast_load(self):
        M.load_gamess_parser(self._write("a.out", OUT_TEXT), parent=None)
        self.assertGreater(self.dialog.minimum_duration, 0)

    def test_cancelling_abandons_the_load(self):
        path = self._write("a.out", OUT_TEXT)

        def arm(*args, **kwargs):
            dlg = FakeProgressDialog(*args, **kwargs)
            dlg.cancel_at = 30  # part-way through parsing
            return dlg

        with patch.object(M, "QProgressDialog", arm):
            self.assertIsNone(M.load_gamess_parser(path, parent=None))

    def test_cancelling_still_closes_the_dialog(self):
        path = self._write("a.out", OUT_TEXT)

        def arm(*args, **kwargs):
            dlg = FakeProgressDialog(*args, **kwargs)
            dlg.cancel_at = 30
            return dlg

        with patch.object(M, "QProgressDialog", arm):
            M.load_gamess_parser(path, parent=None)
        self.assertTrue(self.dialog.closed)

    def test_cancelling_stops_the_remaining_parse_steps(self):
        path = self._write("a.out", OUT_TEXT)

        def arm(*args, **kwargs):
            dlg = FakeProgressDialog(*args, **kwargs)
            dlg.cancel_at = 30
            return dlg

        with patch.object(M, "QProgressDialog", arm):
            M.load_gamess_parser(path, parent=None)
        self.assertLess(self.dialog.values[-1], 100)

    def test_a_read_error_propagates_to_the_caller(self):
        with self.assertRaises(OSError):
            M.load_gamess_parser(os.path.join(self.tmp, "nope.out"), parent=None)

    def test_a_read_error_still_closes_the_dialog(self):
        with self.assertRaises(OSError):
            M.load_gamess_parser(os.path.join(self.tmp, "nope.out"), parent=None)
        self.assertTrue(self.dialog.closed)


if __name__ == "__main__":
    unittest.main()


class TestReadGAMESSTextFallbacks(_LoadingCase):
    def test_a_utf16_big_endian_bom_is_honoured(self):
        path = os.path.join(self.tmp, "be.out")
        with open(path, "wb") as fh:
            fh.write(b"\xfe\xff" + "GAMESS TERMINATED\r\n".encode("utf-16-be"))
        self.assertEqual(M.read_gamess_text(path), "GAMESS TERMINATED\n")

    def test_the_replacement_fallback_is_used_when_every_codec_fails(self):
        path = self._write("e.out", "TERMINATED\n")
        with patch.object(M, "ENCODINGS", ("ascii",)):
            with open(path, "ab") as fh:
                fh.write(b"\xff")
            self.assertIn("TERMINATED", M.read_gamess_text(path))
