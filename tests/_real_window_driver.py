"""Open every analysis window on real output under real Qt, and press its buttons.

Run in a child process by test_claimed_features.py, because the unit tests replace
Qt with stand-ins. Usage: _real_window_driver.py PACKAGE REQUEST_JSON. The request
maps a fixture name to the main-window slots to call. Prints one JSON report: for
each fixture and slot, whether the main window's button was enabled, the windows
that opened with the controls they show, and any uncaught exception or error box.
Exits 77 when a real Qt or a scientific package is missing.
"""

import faulthandler
import importlib
import json
import os
import sys
import tempfile
import traceback
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYVISTA_OFF_SCREEN", "true")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "tests", "sample_outputs")
sys.path.insert(0, ROOT)

try:
    import matplotlib

    matplotlib.use("QtAgg")
    import numpy  # noqa: F401
    import pyvista  # noqa: F401
    import rdkit  # noqa: F401
    from PySide6.QtCore import QCoreApplication, QEvent, QUrl
    from PySide6.QtGui import QColor, QDesktopServices
    from PySide6.QtWidgets import (
        QAbstractButton,
        QAbstractItemView,
        QApplication,
        QColorDialog,
        QDialog,
        QFileDialog,
        QInputDialog,
        QMainWindow,
        QMessageBox,
        QTabWidget,
        QTableWidget,
    )
except ImportError as error:
    print(f"missing: {error}", file=sys.stderr)
    sys.exit(77)

problems = []


def _hook(kind, value, _trace):
    problems.append(f"{kind.__name__}: {value}")


sys.excepthook = _hook


def _record_box(title):
    def box(_parent=None, heading="", text="", *_args, **_kwargs):
        problems.append(f"{title} box: {heading}: {text}")
        return QMessageBox.StandardButton.Ok

    return box


QMessageBox.critical = staticmethod(_record_box("critical"))
QMessageBox.warning = staticmethod(_record_box("warning"))
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")
QColorDialog.getColor = staticmethod(lambda *a, **k: QColor())
QInputDialog.getText = staticmethod(lambda *a, **k: ("", False))
QInputDialog.getItem = staticmethod(lambda *a, **k: ("", False))
QInputDialog.getInt = staticmethod(lambda *a, **k: (0, False))
QInputDialog.getDouble = staticmethod(lambda *a, **k: (0.0, False))
QDialog.exec = lambda self: 0
QDesktopServices.openUrl = staticmethod(lambda _url=QUrl(): True)


class _Host(QMainWindow):
    """A real parent window whose editor services are all mocks."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        mock = MagicMock(name=name)
        setattr(self, name, mock)
        return mock


def _drain(app):
    for _ in range(3):
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _controls(window):
    """The texts of a window's buttons and tabs, and the rows of its tables."""
    buttons = sorted(
        {b.text().strip() for b in window.findChildren(QAbstractButton) if b.text().strip()}
    )
    tabs = []
    for widget in window.findChildren(QTabWidget):
        tabs.extend(widget.tabText(i) for i in range(widget.count()))
    rows = [t.rowCount() for t in window.findChildren(QTableWidget)]
    return {"buttons": buttons, "tabs": tabs, "rows": rows}


def _select_first_rows(app, window):
    """Pick the first row of every list, so the buttons have something to act on."""
    for view in window.findChildren(QAbstractItemView):
        model = view.model()
        if model is not None and model.rowCount() > 0:
            view.setCurrentIndex(model.index(0, 0))
    _drain(app)


def _press_everything(app, window, known):
    """Click each enabled button once, leaving alone the ones that end the window."""
    _select_first_rows(app, window)
    for button in list(window.findChildren(QAbstractButton)):
        try:
            label = button.text().strip()
            if label in ("Close", "OK", "Cancel") or not button.isEnabled():
                continue
            print("press", type(window).__name__, label, file=sys.stderr, flush=True)
            button.click()
        except RuntimeError:
            continue  # a press replaced part of the window; the widget is gone
        _drain(app)
        for extra in QApplication.topLevelWidgets():
            if extra not in known and extra is not window and extra.isWindow():
                extra.close()
    _drain(app)


def main():
    faulthandler.dump_traceback_later(int(os.environ.get("DRIVER_TIMEOUT", "240")), exit=True)
    package, request = sys.argv[1], json.loads(sys.argv[2])
    app = QApplication([])
    gui = importlib.import_module(package + ".gui")
    parser_module = importlib.import_module(package + ".parser")
    parser_cls = next(
        v
        for k, v in vars(parser_module).items()
        if k.endswith("Parser") and isinstance(v, type) and v.__module__ == parser_module.__name__
    )
    dialog_cls = next(v for k, v in vars(gui).items() if k.endswith("ResultAnalyzerDialog"))
    dialog_cls.load_structure_3d = lambda self, *a, **k: None

    # Dialog settings land in the package folder; keep them out of the checkout.
    settings_home = tempfile.mkdtemp()
    for name in os.listdir(os.path.join(ROOT, package)):
        if not name.endswith(".py"):
            continue
        module_name = f"{package}.{name[:-3]}"
        if name != "__init__.py":
            module = importlib.import_module(module_name)
            module.__file__ = os.path.join(settings_home, name)

    report = {}
    for fixture, slots in request.items():
        path = os.path.join(SAMPLES, fixture + ".out")
        parser = parser_cls()
        with open(path, encoding="utf-8") as handle:
            parser.load_from_memory(handle.read(), path)
        mw = _Host()
        host = dialog_cls(mw, parser, path, context=MagicMock())
        host.show()
        _drain(app)
        report[fixture] = {}
        for slot in slots:
            button = getattr(host, "btn_" + slot.replace("show_", ""), None)
            del problems[:]
            known = set(QApplication.topLevelWidgets())
            getattr(host, slot)()
            _drain(app)
            windows = [
                w
                for w in QApplication.topLevelWidgets()
                if w not in known and isinstance(w, QDialog) and w.isVisible()
            ]
            entry = {
                "enabled": button.isEnabled() if button is not None else None,
                "windows": [],
            }
            for window in windows:
                info = {"class": type(window).__name__, "title": window.windowTitle()}
                info.update(_controls(window))
                entry["windows"].append(info)
                known_now = set(QApplication.topLevelWidgets())
                _press_everything(app, window, known_now)
                window.close()
                _drain(app)
            entry["problems"] = list(problems)
            report[fixture][slot] = entry
        host.close()
        _drain(app)
    print("REPORT:" + json.dumps(report))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # the hook above would hide a failure of the driver itself
        traceback.print_exc()
        os._exit(1)
