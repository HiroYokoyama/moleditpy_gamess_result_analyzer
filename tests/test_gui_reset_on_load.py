"""
tests/test_gui_reset_on_load.py

Tests that loading a *new* GAMESS result into an already-open analyzer window
(Select File / Reload / drag-drop) clears stale 3D atom-color overrides
left by the Atomic Charges view, so colors keyed to a previous molecule's
atom indices never bleed onto a differently-indexed one.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

# Stub Qt (mirrors tests/test_about_menu.py's bootstrap so this file is
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


class TestLoadFileClearsAtomColorOverrides(unittest.TestCase):
    def _make_dialog(self):
        parser = GAMESSParser()
        ctx = MagicMock()
        dlg = GAMESSResultAnalyzerDialog(None, parser, "", ctx)
        # Avoid exercising the rest of load_file's file-parsing machinery —
        # only the reset-on-load wiring at the top of the method matters here.
        dlg.close_all_sub_dialogs = MagicMock()
        dlg.update_file_info_labels = MagicMock()
        dlg.load_structure_3d = MagicMock()
        dlg.update_button_states = MagicMock()
        return dlg

    def test_load_file_calls_clear_atom_color_overrides(self):
        dlg = self._make_dialog()
        with _patch_gui("clear_atom_color_overrides") as mock_clear:
            # Path need not exist: the reset call happens before file I/O,
            # and any read failure is caught internally by load_file().
            dlg.load_file("/nonexistent/path/calc.out")
            mock_clear.assert_called_once_with(dlg.mw)

    def test_load_file_clears_overrides_even_on_read_failure(self):
        """A missing/unreadable file must not skip the override reset."""
        dlg = self._make_dialog()
        with _patch_gui("clear_atom_color_overrides") as mock_clear:
            try:
                dlg.load_file("/definitely/does/not/exist.out")
            except Exception as exc:  # load_file must swallow read errors
                self.fail(f"load_file raised unexpectedly: {exc}")
            mock_clear.assert_called_once()

    def test_load_file_clears_before_closing_sub_dialogs_is_also_called(self):
        """Both cleanup steps must run on every load, in the same call."""
        dlg = self._make_dialog()
        with _patch_gui("clear_atom_color_overrides") as mock_clear:
            dlg.load_file("/nonexistent/path/calc.out")
            dlg.close_all_sub_dialogs.assert_called_once()
            mock_clear.assert_called_once()


class TestCloseDeregistersTheWindow(unittest.TestCase):
    """Closing must drop the window from the host registry.

    Plotter picking is installed once, in __init__, and removed in
    closeEvent. A window left registered gets re-shown by the Extensions
    menu instead of rebuilt, so its event filter is never reinstalled and
    atom clicks stay dead for the rest of the session.
    """

    def _make_dialog(self, ctx):
        return GAMESSResultAnalyzerDialog(None, GAMESSParser(), "", ctx)

    def test_close_event_deregisters(self):
        ctx = MagicMock()
        dlg = self._make_dialog(ctx)
        dlg.closeEvent(MagicMock())
        ctx.register_window.assert_called_with("analyzer", None)

    def test_close_event_still_removes_the_picking_filter(self):
        dlg = self._make_dialog(MagicMock())
        dlg._disable_plotter_picking = MagicMock()
        dlg.closeEvent(MagicMock())
        dlg._disable_plotter_picking.assert_called_once()

    def test_close_event_accepts_the_event(self):
        dlg = self._make_dialog(MagicMock())
        evt = MagicMock()
        dlg.closeEvent(evt)
        evt.accept.assert_called_once()

    def test_a_registry_failure_does_not_block_the_close(self):
        ctx = MagicMock()
        ctx.register_window.side_effect = RuntimeError("registry gone")
        dlg = self._make_dialog(ctx)
        evt = MagicMock()
        dlg.closeEvent(evt)  # must not raise
        evt.accept.assert_called_once()

    def test_close_without_a_context_is_harmless(self):
        dlg = self._make_dialog(None)
        evt = MagicMock()
        dlg.closeEvent(evt)
        evt.accept.assert_called_once()

    def test_the_registry_reads_back_empty_after_a_close(self):
        """The precondition _open_gamess_analyzer_empty() branches on."""
        registry = {}
        ctx = MagicMock()
        ctx.register_window.side_effect = lambda k, w: registry.__setitem__(k, w)
        ctx.get_window.side_effect = lambda k: registry.get(k)

        dlg = self._make_dialog(ctx)
        ctx.register_window("analyzer", dlg)
        self.assertIs(ctx.get_window("analyzer"), dlg)

        dlg.closeEvent(MagicMock())
        self.assertIsNone(ctx.get_window("analyzer"))


if __name__ == "__main__":
    unittest.main()
