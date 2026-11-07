"""
tests/test_load_summary.py

Tests for build_status_suffix() (imaginary-mode suffix appended to the
persistent status label) and its wiring into
GAMESSResultAnalyzerDialog.update_file_info_labels().
"""

import sys
import unittest
from unittest.mock import MagicMock

# Stub Qt (mirrors tests/test_gui_reset_on_load.py's bootstrap so this file is
# self-sufficient regardless of test collection order).
if "PySide6.QtWidgets" in sys.modules:
    qtw = sys.modules["PySide6.QtWidgets"]
else:
    qtw = MagicMock()
    sys.modules["PySide6.QtWidgets"] = qtw

if not hasattr(qtw, "QLabel") or "Mock" in type(getattr(qtw, "QLabel")).__name__:

    class _QLabel:
        def __init__(self, *a, **k):
            pass

        def setText(self, *a):
            pass

        def fontMetrics(self):
            fm = MagicMock()
            fm.elidedText.return_value = ""
            return fm

        def width(self):
            return 100

        def resizeEvent(self, *a):
            pass

        def __getattr__(self, name):
            if name.startswith("_"):
                raise AttributeError(name)
            return MagicMock()

    qtw.QLabel = _QLabel

if (
    not hasattr(qtw, "QPushButton")
    or "Mock" in type(getattr(qtw, "QPushButton")).__name__
):

    class _QPushButton:
        def __init__(self, *a, **k):
            self.clicked = MagicMock()

        def setIcon(self, *a):
            pass

        def setIconSize(self, *a):
            pass

        def setStyleSheet(self, *a):
            pass

        def setEnabled(self, *a):
            pass

        def setToolTip(self, *a):
            pass

        def installEventFilter(self, *a):
            pass

        def __getattr__(self, name):
            if name.startswith("_"):
                raise AttributeError(name)
            return MagicMock()

    qtw.QPushButton = _QPushButton

if not hasattr(qtw, "QDialog") or "Mock" in type(getattr(qtw, "QDialog")).__name__:

    class _QDialog:
        class DialogCode:
            Accepted = 1
            Rejected = 0

        def __init__(self, *a, **k):
            self.menu_bar_mock = MagicMock()

        def menuBar(self):
            return self.menu_bar_mock

        def setWindowTitle(self, *a):
            pass

        def resize(self, *a):
            pass

        def setAcceptDrops(self, *a):
            pass

        def close(self):
            pass

        def __getattr__(self, name):
            if name.startswith("_"):
                raise AttributeError(name)
            return MagicMock()

    qtw.QDialog = _QDialog

if "PySide6" not in sys.modules:
    sys.modules["PySide6"] = MagicMock()
if "PySide6.QtCore" not in sys.modules:
    sys.modules["PySide6.QtCore"] = MagicMock()
qt_core = sys.modules["PySide6.QtCore"]
if (
    not hasattr(qt_core, "QObject")
    or "Mock" in type(getattr(qt_core, "QObject")).__name__
):

    class _QObject:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            if name.startswith("_"):
                raise AttributeError(name)
            return MagicMock()

    qt_core.QObject = _QObject
if "PySide6.QtGui" not in sys.modules:
    sys.modules["PySide6.QtGui"] = MagicMock()

if "matplotlib.backends.backend_qtagg" not in sys.modules:
    sys.modules["matplotlib.backends.backend_qtagg"] = MagicMock()
if "pyvista" not in sys.modules:
    sys.modules["pyvista"] = MagicMock()

from gamess_result_analyzer.gui import (  # noqa: E402
    GAMESSResultAnalyzerDialog,
    build_status_suffix,
)
from gamess_result_analyzer.parser import GAMESSParser  # noqa: E402


class TestBuildStatusSuffix(unittest.TestCase):
    def test_empty_frequencies_list(self):
        suffix, count = build_status_suffix({"frequencies": []})
        self.assertEqual(suffix, "")
        self.assertEqual(count, 0)

    def test_missing_frequencies_key(self):
        suffix, count = build_status_suffix({})
        self.assertEqual(suffix, "")
        self.assertEqual(count, 0)

    def test_all_positive_freqs(self):
        data = {"frequencies": [{"freq": 200.0}, {"freq": 100.0}]}
        suffix, count = build_status_suffix(data)
        self.assertEqual(suffix, "")
        self.assertEqual(count, 0)

    def test_zero_valued_modes_not_imaginary(self):
        data = {
            "frequencies": [
                {"freq": 0.0},
                {"freq": 0.0},
                {"freq": 50.0},
            ]
        }
        suffix, count = build_status_suffix(data)
        self.assertEqual(suffix, "")
        self.assertEqual(count, 0)

    def test_single_imaginary_mode(self):
        data = {"frequencies": [{"freq": 0.0}, {"freq": -123.45}, {"freq": 300.0}]}
        suffix, count = build_status_suffix(data)
        self.assertEqual(suffix, " (1 imaginary mode)")
        self.assertEqual(count, 1)

    def test_multiple_imaginary_modes(self):
        data = {
            "frequencies": [
                {"freq": -450.12},
                {"freq": -20.0},
                {"freq": 300.0},
            ]
        }
        suffix, count = build_status_suffix(data)
        self.assertEqual(suffix, " (2 imaginary modes)")
        self.assertEqual(count, 2)

    def test_freq_key_missing_defaults_to_zero(self):
        data = {"frequencies": [{}, {"freq": 100.0}]}
        suffix, count = build_status_suffix(data)
        self.assertEqual(suffix, "")
        self.assertEqual(count, 0)


class TestStatusLabelSuffix(unittest.TestCase):
    """Drives GAMESSResultAnalyzerDialog.update_file_info_labels() directly to
    check the lbl_status text/stylesheet reflect imaginary-mode counts."""

    def _make_dialog_with_data(self, data):
        parser = GAMESSParser()
        parser.data = data
        dlg = GAMESSResultAnalyzerDialog(None, parser, "job.out", MagicMock())
        dlg.lbl_status = MagicMock()
        return dlg

    def test_imaginary_modes_shown_and_orange(self):
        data = {
            "termination_status": "Terminated normally",
            "frequencies": [{"freq": 0.0}, {"freq": -123.45}],
        }
        dlg = self._make_dialog_with_data(data)
        dlg.update_file_info_labels()
        text = dlg.lbl_status.setText.call_args[0][0]
        style = dlg.lbl_status.setStyleSheet.call_args[0][0]
        self.assertIn("(1 imaginary mode)", text)
        self.assertIn("#fd7e14", style)

    def test_clean_freqs_no_suffix_and_green(self):
        data = {
            "termination_status": "Terminated normally",
            "frequencies": [{"freq": 100.0}, {"freq": 200.0}],
        }
        dlg = self._make_dialog_with_data(data)
        dlg.update_file_info_labels()
        text = dlg.lbl_status.setText.call_args[0][0]
        style = dlg.lbl_status.setStyleSheet.call_args[0][0]
        self.assertNotIn("imaginary", text)
        self.assertEqual(text, "Status: Terminated normally")
        self.assertIn("#28a745", style)


if __name__ == "__main__":
    unittest.main()
