"""
Tests for the Help > About menu action.
"""

import sys
import unittest
from unittest.mock import MagicMock

# Stub Qt
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

# Stub matplotlib and pyvista backends if not present
if "matplotlib.backends.backend_qtagg" not in sys.modules:
    sys.modules["matplotlib.backends.backend_qtagg"] = MagicMock()
if "pyvista" not in sys.modules:
    sys.modules["pyvista"] = MagicMock()

from gamess_result_analyzer.gui import GAMESSResultAnalyzerDialog  # noqa: E402
from gamess_result_analyzer.parser import GAMESSParser  # noqa: E402


# Keep patches attached to the module captured by this dialog class. Other
# test modules temporarily replace package/module attributes during collection.
_GUI_MODULE = sys.modules[GAMESSResultAnalyzerDialog.__module__]


def _patch_gui(target, *args, **kwargs):
    owner = _GUI_MODULE
    parts = target.split(".")
    for name in parts[:-1]:
        owner = getattr(owner, name)
    return unittest.mock.patch.object(owner, parts[-1], *args, **kwargs)


class TestAboutMenu(unittest.TestCase):
    @_patch_gui("QMessageBox.about", create=True)
    def test_show_about(self, mock_about):
        """Test that show_about calls QMessageBox.about."""
        parser = GAMESSParser()
        # Mock Context
        ctx = MagicMock()

        # Instantiate Dialog
        dlg = GAMESSResultAnalyzerDialog(None, parser, "", ctx)

        # Call show_about directly
        dlg.show_about()

        # Verify it called QMessageBox.about
        mock_about.assert_called_once()
        args = mock_about.call_args[0]

        self.assertIs(args[0], dlg)
        self.assertEqual(args[1], "About GAMESS Result Analyzer")
        self.assertIn("Author:</b> Hiromichi Yokoyama", args[2])
        self.assertIn("GitHub:", args[2])

    def test_open_file_no_shift(self):
        """Test that open_file calls getOpenFileName when Shift is not pressed."""
        from unittest.mock import patch
        from gamess_result_analyzer.gui import Qt

        no_mod = Qt.KeyboardModifier.NoModifier

        if hasattr(no_mod, "mock_add_spec") or "Mock" in type(no_mod).__name__:
            no_mod.__and__ = MagicMock(return_value=0)

        parser = GAMESSParser()
        ctx = MagicMock()
        dlg = GAMESSResultAnalyzerDialog(None, parser, "", ctx)

        dlg.open_directory = MagicMock()

        with _patch_gui(
            "QApplication.keyboardModifiers",
            return_value=no_mod,
        ):
            with _patch_gui(
                "QFileDialog.getOpenFileName",
                return_value=("", ""),
            ) as mock_get:
                dlg.open_file()
                mock_get.assert_called_once()
                dlg.open_directory.assert_not_called()

    def test_open_file_with_shift(self):
        """Test that open_file calls open_directory instead when Shift is pressed."""
        from unittest.mock import patch
        from gamess_result_analyzer.gui import Qt

        shift_mod = Qt.KeyboardModifier.ShiftModifier

        if hasattr(shift_mod, "mock_add_spec") or "Mock" in type(shift_mod).__name__:
            shift_mod.__and__ = MagicMock(return_value=1)

        parser = GAMESSParser()
        ctx = MagicMock()
        dlg = GAMESSResultAnalyzerDialog(None, parser, "", ctx)

        dlg.open_directory = MagicMock()

        with _patch_gui(
            "QApplication.keyboardModifiers",
            return_value=shift_mod,
        ):
            with _patch_gui(
                "QFileDialog.getOpenFileName",
                return_value=("", ""),
            ) as mock_get:
                dlg.open_file()
                mock_get.assert_not_called()
                dlg.open_directory.assert_called_once()

    def test_show_convergence_graph_direct(self):
        """Test that show_convergence_graph_direct opens the graph dialog."""
        from unittest.mock import patch

        parser = GAMESSParser()
        parser.data["scan_steps"] = [{"convergence": {"rms gradient": 0.001}}]
        ctx = MagicMock()
        dlg = GAMESSResultAnalyzerDialog(None, parser, "", ctx)

        with _patch_gui("ConvergenceGraphDialog") as mock_graph_dlg:
            dlg.show_convergence_graph_direct()
            mock_graph_dlg.assert_called_once()
            mock_graph_dlg.return_value.show.assert_called_once()

    def test_show_forces_no_shift(self):
        """Test that show_forces opens ForceViewerDialog when Shift is not pressed."""
        from unittest.mock import patch
        from gamess_result_analyzer.gui import Qt

        no_mod = Qt.KeyboardModifier.NoModifier

        if hasattr(no_mod, "mock_add_spec") or "Mock" in type(no_mod).__name__:
            no_mod.__and__ = MagicMock(return_value=0)

        parser = GAMESSParser()
        parser.data["gradients"] = [{"atom_idx": 0, "vector": [0.0, 0.0, 0.0]}]
        ctx = MagicMock()
        dlg = GAMESSResultAnalyzerDialog(None, parser, "", ctx)

        with _patch_gui(
            "QApplication.keyboardModifiers",
            return_value=no_mod,
        ):
            with _patch_gui("ForceViewerDialog") as mock_force_dlg:
                dlg.show_forces()
                mock_force_dlg.assert_called_once()

    def test_show_forces_with_shift(self):
        """Test that show_forces opens ConvergenceGraphDialog directly when Shift is pressed."""
        from unittest.mock import patch
        from gamess_result_analyzer.gui import Qt

        shift_mod = Qt.KeyboardModifier.ShiftModifier

        if hasattr(shift_mod, "mock_add_spec") or "Mock" in type(shift_mod).__name__:
            shift_mod.__and__ = MagicMock(return_value=1)

        parser = GAMESSParser()
        parser.data["gradients"] = [{"atom_idx": 0, "vector": [0.0, 0.0, 0.0]}]
        parser.data["scan_steps"] = [{"convergence": {"rms gradient": 0.001}}]
        ctx = MagicMock()
        dlg = GAMESSResultAnalyzerDialog(None, parser, "", ctx)

        with _patch_gui(
            "QApplication.keyboardModifiers",
            return_value=shift_mod,
        ):
            with _patch_gui(
                "ConvergenceGraphDialog"
            ) as mock_graph_dlg:
                dlg.show_forces()
                mock_graph_dlg.assert_called_once()

    def test_open_directory_path_single_file(self):
        """Test that _open_directory_path loads the file directly if only one is found."""
        from unittest.mock import patch
        import os

        parser = GAMESSParser()
        ctx = MagicMock()
        dlg = GAMESSResultAnalyzerDialog(None, parser, "", ctx)
        dlg.load_file = MagicMock()

        with _patch_gui(
            "list_gamess_output_files",
            return_value=["only_one.out"],
        ):
            with _patch_gui("_DirectoryFilePicker") as mock_picker:
                dlg._open_directory_path("/mock/dir")
                expected_path = os.path.join("/mock/dir", "only_one.out")
                dlg.load_file.assert_called_once_with(expected_path)
                mock_picker.assert_not_called()

    def test_open_directory_path_multiple_files(self):
        """Test that _open_directory_path opens the picker if multiple files are found."""
        from unittest.mock import patch
        from PySide6.QtWidgets import QDialog

        parser = GAMESSParser()
        ctx = MagicMock()
        dlg = GAMESSResultAnalyzerDialog(None, parser, "", ctx)
        dlg.load_file = MagicMock()

        with _patch_gui(
            "list_gamess_output_files",
            return_value=["one.out", "two.out"],
        ):
            with _patch_gui("_DirectoryFilePicker") as mock_picker:
                mock_inst = mock_picker.return_value
                mock_inst.exec.return_value = QDialog.DialogCode.Accepted
                mock_inst.selected_path = "/mock/dir/one.out"

                dlg._open_directory_path("/mock/dir")
                mock_picker.assert_called_once()
                dlg.load_file.assert_called_once_with("/mock/dir/one.out")


if __name__ == "__main__":
    unittest.main()
