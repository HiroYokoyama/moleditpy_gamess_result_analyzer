"""
tests/test_nmr_mo_fixes.py
Unit tests for the bug-fixes applied in v2.6.0:

  NMR:
    - sel_timer is stopped in closeEvent so it cannot fire on a dead widget.
    - reset_selection() exists and stops/restarts the timer while clearing state.

  MO:
    - apply_preset() blocks signals on all vis widgets so only ONE show_cube()
      call is made per preset application (no intermediate phantom renders).

PySide6, pyvista, rdkit, numpy, matplotlib, PIL and all related sub-modules are
fully stubbed so tests run headlessly.
"""

import os
import sys
import types
import importlib.util
import unittest
from unittest.mock import MagicMock

_SRC_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Shared stub helpers
# ---------------------------------------------------------------------------


def _make_qt_base():
    """Return a base class stub for QDialog/QWidget with noop Qt methods."""

    class _Base:
        class DialogCode:
            Accepted = 1
            Rejected = 0

        def __init__(self, *a, **kw):
            pass

        def closeEvent(self, event):
            pass

        def __getattr__(self, name):
            return MagicMock()

    return _Base


class _QColor:
    def __init__(self, name=None):
        self._name = name

    def name(self):
        return self._name or ""

    def isValid(self):
        return self._name is not None

    def red(self):
        return 0

    def green(self):
        return 0

    def blue(self):
        return 0


class _SpinBox:
    def __init__(self, val=0.0):
        self._val = val
        self._blocked = False
        self._signals_blocked = False

    def value(self):
        return self._val

    def setValue(self, v):
        self._val = v

    def blockSignals(self, v):
        self._signals_blocked = v

    def setRange(self, *a):
        pass

    def setSingleStep(self, *a):
        pass

    def setDecimals(self, *a):
        pass

    def setEnabled(self, *a):
        pass

    def setFixedWidth(self, *a):
        pass

    def setSuffix(self, *a):
        pass

    def setChecked(self, v):
        self._val = bool(v)

    def isChecked(self):
        return bool(self._val)

    def stateChanged(self):
        return MagicMock()

    def valueChanged(self):
        return MagicMock()

    def toggled(self):
        return MagicMock()

    def currentTextChanged(self):
        return MagicMock()


class _QComboBox:
    def __init__(self):
        self._items = []
        self._idx = 0
        self._blocked = False

    def addItem(self, t):
        self._items.append(t)

    def addItems(self, ts):
        self._items.extend(ts)

    def findText(self, t):
        try:
            return self._items.index(t)
        except ValueError:
            return -1

    def setCurrentIndex(self, i):
        self._idx = i

    def currentText(self):
        return self._items[self._idx] if self._items else ""

    def currentIndex(self):
        return self._idx

    def blockSignals(self, v):
        self._blocked = v

    def currentTextChanged(self):
        return MagicMock()

    def currentIndexChanged(self):
        return MagicMock()

    def clear(self):
        self._items = []
        self._idx = 0


class _RecordingTimer:
    """QTimer stub that records stop()/start() calls."""

    def __init__(self, *a, **kw):
        self.calls = []
        self._active = False

    def start(self, ms=200):
        self.calls.append(("start", ms))
        self._active = True

    def stop(self):
        self.calls.append(("stop",))
        self._active = False

    def isActive(self):
        return self._active

    def timeout(self):
        return MagicMock()


# ---------------------------------------------------------------------------
# NMR stubs + loader
# ---------------------------------------------------------------------------


def _install_nmr_stubs():
    """Install stubs for headless NMR loading.

    When the full suite runs, real PySide6 may already be in sys.modules from
    earlier tests.  Replacing it wholesale breaks the test runner.  Instead:
      - If QtWidgets is already present, patch only the names nmr_analysis
        needs that are missing (MagicMock stand-ins).
      - If PySide6 is absent, install a complete stub module.
    numpy / matplotlib / pyvista stubs are always installed under their real
    names so nmr_analysis can import them without a display.
    """
    marker = "nmr_fix_stubs_installed"
    if marker in sys.modules:
        return

    _Base = _make_qt_base()

    # All names nmr_analysis.py imports from QtWidgets
    _WIDGET_NAMES = [
        "QDialog",
        "QVBoxLayout",
        "QHBoxLayout",
        "QLabel",
        "QComboBox",
        "QDoubleSpinBox",
        "QTableWidget",
        "QTableWidgetItem",
        "QHeaderView",
        "QPushButton",
        "QApplication",
        "QGroupBox",
        "QMessageBox",
        "QFileDialog",
        "QCheckBox",
        "QButtonGroup",
        "QAbstractItemView",
        "QTreeWidget",
        "QTreeWidgetItem",
        "QFormLayout",
        "QDialogButtonBox",
        "QSplitter",
        "QFrame",
        "QScrollArea",
        "QSizePolicy",
        "QWidget",
        "QSpinBox",
    ]

    existing_widgets = sys.modules.get("PySide6.QtWidgets")
    existing_core = sys.modules.get("PySide6.QtCore")

    if existing_widgets is not None:
        # Real PySide6 already loaded — only patch missing names
        for name in _WIDGET_NAMES:
            if not hasattr(existing_widgets, name):
                setattr(existing_widgets, name, MagicMock)
        if existing_core is not None and not hasattr(existing_core, "QTimer"):
            existing_core.QTimer = _RecordingTimer
    else:
        # No PySide6 yet — build and install full stub
        _pyside6 = types.ModuleType("PySide6")
        _core = types.ModuleType("PySide6.QtCore")
        _core.Qt = MagicMock()
        _core.QTimer = _RecordingTimer
        _widgets = types.ModuleType("PySide6.QtWidgets")
        for name in _WIDGET_NAMES:
            setattr(_widgets, name, MagicMock)
        _widgets.QDialog = _Base
        _pyside6.QtWidgets = _widgets
        _pyside6.QtCore = _core
        _gui = types.ModuleType("PySide6.QtGui")
        _gui.QColor = _QColor
        _gui.QBrush = MagicMock
        _pyside6.QtGui = _gui
        sys.modules.update(
            {
                "PySide6": _pyside6,
                "PySide6.QtWidgets": _widgets,
                "PySide6.QtCore": _core,
                "PySide6.QtGui": _gui,
            }
        )

    # Numeric/plotting stubs are a *fallback only*. These go into the shared
    # sys.modules for the rest of the session, so installing a crippled numpy
    # (or a matplotlib whose Figure is a MagicMock) over a working one breaks
    # every later test that does real numerics — including matplotlib itself,
    # which consults sys.modules["numpy"] from its C extensions at call time.
    # Prefer the genuine packages whenever they are importable.
    try:
        import numpy as _np  # noqa: F401
    except ImportError:
        _np = types.ModuleType("numpy")
        _np.array = list
        _np.zeros_like = lambda a: [0] * len(a)

    try:
        import matplotlib as _mpl  # noqa: F401
        from matplotlib import figure as _mpl_fig  # noqa: F401
        from matplotlib import ticker as _mpl_tick  # noqa: F401
    except ImportError:
        _mpl = types.ModuleType("matplotlib")
        _mpl.use = MagicMock()
        _mpl_fig = types.ModuleType("matplotlib.figure")
        _mpl_fig.Figure = MagicMock
        _mpl_tick = types.ModuleType("matplotlib.ticker")
        _mpl_tick.MaxNLocator = MagicMock

    # The Qt-agg backend always stays stubbed: importing the real one needs a
    # live Qt binding, which CI deliberately does not install. The *parent*
    # backends package must stay real when it can, though — matplotlib imports
    # backend_agg through it on demand (figure.tight_layout resolves a renderer
    # that way), and a bare stub has no __path__ for that import to traverse.
    try:
        from matplotlib import backends as _mpl_back  # noqa: F401
    except ImportError:
        _mpl_back = types.ModuleType("matplotlib.backends")

    _mpl_back_qt = types.ModuleType("matplotlib.backends.backend_qtagg")
    _mpl_back_qt.FigureCanvasQTAgg = _Base
    _mpl_back_qt.NavigationToolbar2QT = MagicMock

    _pv = types.ModuleType("pyvista")
    _pv.PolyData = MagicMock
    _pv.Sphere = MagicMock

    _utils_mod = types.ModuleType("gamess_result_analyzer.utils")
    _utils_mod.get_default_export_path = MagicMock(return_value="")
    _utils_mod.clear_atom_color_overrides = MagicMock()

    def _save_json_atomic(path, data, indent=2):
        import json as _json

        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(data, f, indent=indent)
        os.replace(tmp, path)

    _utils_mod.save_json_atomic = _save_json_atomic

    def _notify(owner, message, timeout=3000):
        candidate = owner
        for _ in range(4):
            context = getattr(candidate, "context", None)
            if context is not None and hasattr(context, "show_status_message"):
                context.show_status_message(message, timeout)
                return True
            candidate = getattr(candidate, "parent_dlg", None)
            if candidate is None:
                break
        return False

    _utils_mod.notify = _notify

    _custom_ref = types.ModuleType("gamess_result_analyzer.nmr_custom_ref_dialog")
    _custom_ref.CustomReferenceDialog = MagicMock

    # nmr_analysis only needs PLUGIN_VERSION off the package. If the real
    # package is already imported, annotate it rather than replacing it —
    # overwriting the entry strips its submodule attributes (.gui and friends),
    # which breaks any later test that patches "gamess_result_analyzer.gui.X".
    _ver_mod = sys.modules.get("gamess_result_analyzer")
    if _ver_mod is None:
        _ver_mod = types.ModuleType("gamess_result_analyzer")
    if not hasattr(_ver_mod, "PLUGIN_VERSION"):
        _ver_mod.PLUGIN_VERSION = "2.6.0"

    sys.modules.update(
        {
            "numpy": _np,
            "matplotlib": _mpl,
            "matplotlib.figure": _mpl_fig,
            "matplotlib.backends": _mpl_back,
            "matplotlib.backends.backend_qtagg": _mpl_back_qt,
            "matplotlib.ticker": _mpl_tick,
            "pyvista": _pv,
            "gamess_result_analyzer": _ver_mod,
            "gamess_result_analyzer.utils": _utils_mod,
            "gamess_result_analyzer.nmr_custom_ref_dialog": _custom_ref,
            marker: types.ModuleType(marker),
        }
    )


def _load_sibling(name):
    """Load gamess_result_analyzer/<name>.py as gamess_result_analyzer.<name>."""
    mod_name = f"gamess_result_analyzer.{name}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    path = os.path.join(_SRC_DIR, "gamess_result_analyzer", f"{name}.py")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "gamess_result_analyzer"
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_nmr():
    _install_nmr_stubs()
    for _name in ("settings", "nmr_merge", "nmr_plot", "nmr_export"):
        _load_sibling(_name)
    path = os.path.join(_SRC_DIR, "gamess_result_analyzer", "nmr_analysis.py")
    spec = importlib.util.spec_from_file_location("nmr_analysis_fix_mod", path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "gamess_result_analyzer"
    sys.modules["nmr_analysis_fix_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


_nmr_mod = _load_nmr()
NMRDialog = _nmr_mod.NMRDialog


# ---------------------------------------------------------------------------
# NMR test helpers
# ---------------------------------------------------------------------------


def _make_nmr_dialog():
    """Build a minimal NMRDialog instance without running Qt __init__."""
    parent_dlg = MagicMock()
    parent_dlg.mw = MagicMock()

    dlg = NMRDialog.__new__(NMRDialog)
    dlg.parent_dlg = parent_dlg
    dlg.data = [{"atom_idx": 0, "atom_sym": "H", "shielding": 30.0}]
    dlg.couplings = []
    dlg.displayed_data = list(dlg.data)
    dlg.selected_peak_indices = set()
    dlg.highlight_artists = []
    dlg.show_all_mode = False
    dlg._atom_labels = []
    dlg._nmr_sphere_actors = []
    dlg._nmr_label_names = []
    dlg._last_synced_mw_selection = frozenset()
    dlg.peaks_metadata = None
    # The Qt stub base auto-creates truthy MagicMocks for missing attributes,
    # so the unsaved-merge flag and the shift-label checkbox must be set
    # explicitly on fakes.
    dlg._merged_dirty = False
    dlg.chk_label_shifts = None  # default: shift values hidden

    # Real recording timer
    timer = _RecordingTimer()
    dlg.sel_timer = timer

    return dlg


# ---------------------------------------------------------------------------
# NMR Tests
# ---------------------------------------------------------------------------


class TestNMRCloseEventStopsTimer(unittest.TestCase):
    """closeEvent() must stop sel_timer before it fires on a dead dialog."""

    def test_close_event_stops_timer(self):
        dlg = _make_nmr_dialog()
        dlg.sel_timer.start(200)  # simulate running timer
        self.assertTrue(dlg.sel_timer.isActive())

        # Patch save_settings and clear_atom_labels (they touch Qt)
        dlg.save_settings = MagicMock()
        dlg.clear_atom_labels = MagicMock()

        event = MagicMock()
        # Call the real closeEvent
        NMRDialog.closeEvent(dlg, event)

        # Timer must be stopped
        self.assertFalse(dlg.sel_timer.isActive())
        stop_calls = [c for c in dlg.sel_timer.calls if c[0] == "stop"]
        self.assertGreater(len(stop_calls), 0, "sel_timer.stop() was never called")

    def test_close_event_stop_before_save(self):
        """stop() must precede save_settings() to avoid mid-close timer fires."""
        dlg = _make_nmr_dialog()
        call_order = []
        dlg.sel_timer.stop = lambda: call_order.append("stop")
        dlg.save_settings = lambda: call_order.append("save")
        dlg.clear_atom_labels = MagicMock()
        NMRDialog.closeEvent(dlg, MagicMock())
        self.assertLess(call_order.index("stop"), call_order.index("save"))


class TestNMRResetSelection(unittest.TestCase):
    """reset_selection() stops the timer, clears state, then restarts."""

    def test_reset_selection_exists(self):
        self.assertTrue(hasattr(NMRDialog, "reset_selection"))
        self.assertTrue(callable(NMRDialog.reset_selection))

    def test_reset_selection_stops_and_restarts_timer(self):
        dlg = _make_nmr_dialog()
        dlg.sel_timer.start(200)
        dlg.clear_peak_selection = MagicMock()

        NMRDialog.reset_selection(dlg)

        ops = [c[0] for c in dlg.sel_timer.calls]
        self.assertIn("stop", ops)
        self.assertIn("start", ops)
        # stop must come before start
        last_start = len(ops) - 1 - list(reversed(ops)).index("start")
        self.assertLess(ops.index("stop"), last_start)

    def test_reset_selection_clears_sync_tracker(self):
        dlg = _make_nmr_dialog()
        dlg._last_synced_mw_selection = frozenset({1, 2, 3})
        dlg.clear_peak_selection = MagicMock()
        NMRDialog.reset_selection(dlg)
        self.assertEqual(dlg._last_synced_mw_selection, frozenset())

    def test_reset_selection_calls_clear_peak_selection(self):
        dlg = _make_nmr_dialog()
        dlg.clear_peak_selection = MagicMock()
        NMRDialog.reset_selection(dlg)
        dlg.clear_peak_selection.assert_called_once()


# ---------------------------------------------------------------------------
# MO stubs + loader
# ---------------------------------------------------------------------------


def _install_mo_stubs():
    marker = "mo_fix_stubs_installed"
    if marker in sys.modules:
        return

    _Base = _make_qt_base()

    _MO_WIDGET_NAMES = [
        "QVBoxLayout",
        "QHBoxLayout",
        "QPushButton",
        "QLabel",
        "QTreeWidget",
        "QTreeWidgetItem",
        "QAbstractItemView",
        "QTreeWidgetItemIterator",
        "QFileDialog",
        "QMessageBox",
        "QGroupBox",
        "QHeaderView",
        "QProgressDialog",
        "QFormLayout",
        "QInputDialog",
        "QApplication",
        "QColorDialog",
        "QWidget",
        "QDialog",
        "QDoubleSpinBox",
        "QSpinBox",
        "QCheckBox",
        "QComboBox",
        "QMenu",
    ]

    existing_widgets = sys.modules.get("PySide6.QtWidgets")

    if existing_widgets is not None:
        # Patch whatever is already there
        for name in _MO_WIDGET_NAMES:
            if not hasattr(existing_widgets, name):
                if name in ["QWidget", "QDialog"]:
                    setattr(existing_widgets, name, _Base)
                else:
                    setattr(existing_widgets, name, MagicMock)
    else:
        _pyside6_mo = types.ModuleType("PySide6")
        _core_mo = types.ModuleType("PySide6.QtCore")
        _core_mo.Qt = MagicMock()
        _widgets_mo = types.ModuleType("PySide6.QtWidgets")
        for name in _MO_WIDGET_NAMES:
            if name in ["QWidget", "QDialog"]:
                setattr(_widgets_mo, name, _Base)
            else:
                setattr(_widgets_mo, name, MagicMock)
        _pyside6_mo.QtWidgets = _widgets_mo
        _pyside6_mo.QtCore = _core_mo
        _gui_mo = types.ModuleType("PySide6.QtGui")
        _gui_mo.QColor = _QColor
        _gui_mo.QBrush = MagicMock
        _pyside6_mo.QtGui = _gui_mo
        sys.modules.update(
            {
                "PySide6": _pyside6_mo,
                "PySide6.QtWidgets": _widgets_mo,
                "PySide6.QtCore": _core_mo,
                "PySide6.QtGui": _gui_mo,
            }
        )

    _utils_mo = types.ModuleType("gamess_result_analyzer.utils")
    _utils_mo.get_default_export_path = MagicMock(return_value="")
    _utils_mo.clear_atom_color_overrides = MagicMock()
    sys.modules.setdefault("gamess_result_analyzer.utils", _utils_mo)

    sys.modules[marker] = types.ModuleType(marker)


def _load_mo():
    _install_mo_stubs()
    # Stub heavy deps before loading mo_analysis
    for mod_name in [
        "gamess_result_analyzer.mo_engine",
        "gamess_result_analyzer.vis",
        "gamess_result_analyzer.energy_diag",
    ]:
        sys.modules.setdefault(mod_name, types.ModuleType(mod_name))
    path = os.path.join(_SRC_DIR, "gamess_result_analyzer", "mo_analysis.py")
    spec = importlib.util.spec_from_file_location("mo_analysis_fix_mod", path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "gamess_result_analyzer"
    sys.modules["mo_analysis_fix_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


_mo_mod = _load_mo()
MODialog = _mo_mod.MODialog


# ---------------------------------------------------------------------------
# MO test helpers
# ---------------------------------------------------------------------------


def _make_mo_dialog():
    dlg = MODialog.__new__(MODialog)
    dlg.mw = MagicMock()
    dlg.parent_dlg = MagicMock()
    dlg.mo_data = {}
    dlg.mos = {}
    dlg.last_cube_path = None
    dlg.generation_queue = []
    dlg.energy_dlg = None
    dlg.settings_file = os.path.join(_SRC_DIR, "gamess_result_analyzer", "settings.json")

    # Vis control widgets (recording spins/combos/checkboxes)
    dlg.spin_iso = _SpinBox(0.02)
    dlg.spin_opacity = _SpinBox(0.5)
    dlg.combo_style = _QComboBox()
    dlg.combo_style.addItems(["Surface", "Wireframe", "Points"])
    dlg.check_smooth = _SpinBox(1.0)  # isChecked() → True

    dlg.btn_color_p = MagicMock()
    dlg.btn_color_n = MagicMock()
    dlg.btn_color_p.styleSheet.return_value = "background-color: #ff0000;"
    dlg.btn_color_n.styleSheet.return_value = "background-color: #0000ff;"

    dlg.combo_presets = _QComboBox()
    dlg.combo_presets.addItem("Default")

    dlg.presets = {
        "Default": {
            "iso": 0.02,
            "opacity": 0.5,
            "style": "Surface",
            "color_p": "#ff0000",
            "color_n": "#0000ff",
            "smooth_shading": True,
        },
        "MyPreset": {
            "iso": 0.05,
            "opacity": 0.7,
            "style": "Wireframe",
            "color_p": "#00ff00",
            "color_n": "#ff00ff",
            "smooth_shading": False,
        },
    }
    dlg.combo_presets.addItem("MyPreset")

    return dlg


# ---------------------------------------------------------------------------
# MO Tests
# ---------------------------------------------------------------------------


class TestMOApplyPresetSignalBlock(unittest.TestCase):
    """apply_preset() must block all widget signals so only one
    update_vis_only() is fired at the end, not one per widget change."""

    def test_update_vis_only_called_exactly_once(self):
        dlg = _make_mo_dialog()
        dlg.update_vis_only = MagicMock()
        dlg.save_settings = MagicMock()
        dlg.set_btn_color = MagicMock()

        MODialog.apply_preset(dlg, "MyPreset")

        dlg.update_vis_only.assert_called_once()

    def test_spin_iso_blocked_during_apply(self):
        """spin_iso.blockSignals(True) must be called before setValue."""
        dlg = _make_mo_dialog()
        dlg.update_vis_only = MagicMock()
        dlg.save_settings = MagicMock()
        dlg.set_btn_color = MagicMock()

        block_calls = []

        def recording_bs(v):
            block_calls.append(v)

        dlg.spin_iso.blockSignals = recording_bs

        MODialog.apply_preset(dlg, "MyPreset")

        self.assertIn(True, block_calls, "blockSignals(True) never called on spin_iso")
        self.assertIn(
            False, block_calls, "blockSignals(False) never called on spin_iso"
        )

    def test_values_applied_from_preset(self):
        dlg = _make_mo_dialog()
        dlg.update_vis_only = MagicMock()
        dlg.save_settings = MagicMock()
        dlg.set_btn_color = MagicMock()

        MODialog.apply_preset(dlg, "MyPreset")

        self.assertAlmostEqual(dlg.spin_iso.value(), 0.05)
        self.assertAlmostEqual(dlg.spin_opacity.value(), 0.7)

    def test_signals_unblocked_after_apply(self):
        """Even if an exception occurs during apply, signals must be unblocked."""
        dlg = _make_mo_dialog()
        dlg.update_vis_only = MagicMock()
        dlg.save_settings = MagicMock()
        dlg.set_btn_color = MagicMock()

        # Force an error mid-apply by making combo_style.findText raise
        dlg.combo_style.findText = MagicMock(side_effect=RuntimeError("oops"))

        try:
            MODialog.apply_preset(dlg, "MyPreset")
        except RuntimeError:
            pass

        # After the try/finally, signals must be unblocked
        self.assertFalse(dlg.spin_iso._signals_blocked)
        self.assertFalse(dlg.spin_opacity._signals_blocked)
        self.assertFalse(dlg.check_smooth._signals_blocked)

    def test_unknown_preset_is_noop(self):
        dlg = _make_mo_dialog()
        dlg.update_vis_only = MagicMock()
        MODialog.apply_preset(dlg, "DoesNotExist")
        dlg.update_vis_only.assert_not_called()

    def test_show_cube_updates_last_cube_path(self):
        dlg = _make_mo_dialog()
        dlg.mw = MagicMock()
        dlg.get_color_hex = MagicMock(return_value="#ffffff")
        dlg.combo_style = MagicMock()
        dlg.combo_style.currentText = MagicMock(return_value="Surface")
        dlg.spin_iso = MagicMock()
        dlg.spin_iso.value = MagicMock(return_value=0.02)
        dlg.spin_opacity = MagicMock()
        dlg.spin_opacity.value = MagicMock(return_value=0.5)
        dlg.check_smooth = MagicMock()
        dlg.check_smooth.isChecked = MagicMock(return_value=True)

        mock_vis = MagicMock()
        mock_vis.load_file = MagicMock(return_value=True)
        _mo_mod.CubeVisualizer = MagicMock(return_value=mock_vis)

        dlg.last_cube_path = "old_path.cube"
        MODialog.show_cube(dlg, "new_path.cube")

        self.assertEqual(dlg.last_cube_path, "new_path.cube")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# merge_selected_peaks: nuclei validation with missing atom symbols
# ---------------------------------------------------------------------------


def _make_merge_dialog(data, metadata, selected):
    dlg = _make_nmr_dialog()
    dlg.data = data
    dlg.peaks_metadata = metadata
    dlg.selected_peak_indices = set(selected)
    dlg.merged_peaks = []
    dlg._merged_dirty = False
    dlg.save_merged_peaks = MagicMock()
    dlg.clear_peak_selection = MagicMock()
    dlg.recalc = MagicMock()
    return dlg


class TestNMRMergeSelectedPeaksSymbols(unittest.TestCase):
    """v3.9.1: an entry without atom_sym must not count as a distinct nucleus
    (previously None entered the set and ', '.join(...) raised TypeError)."""

    def setUp(self):
        self._merge_mod = sys.modules["gamess_result_analyzer.nmr_merge"]
        self._orig_msgbox = self._merge_mod.QMessageBox
        self._merge_mod.QMessageBox = MagicMock()

    def tearDown(self):
        self._merge_mod.QMessageBox = self._orig_msgbox

    def test_missing_symbol_does_not_block_merge_or_crash(self):
        data = [
            {"atom_idx": 0, "atom_sym": "C", "shielding": 100.0},
            {"atom_idx": 1, "shielding": 101.0},  # no atom_sym key
        ]
        metadata = [(0, 0, 0, [0]), (0, 0, 0, [1])]
        dlg = _make_merge_dialog(data, metadata, {0, 1})

        dlg.merge_selected_peaks()  # crashed with TypeError before the fix

        self._merge_mod.QMessageBox.critical.assert_not_called()
        self.assertEqual(dlg.merged_peaks, [{"indices": [0, 1]}])
        # v3.9.1: merges are no longer auto-saved — only flagged dirty
        dlg.save_merged_peaks.assert_not_called()
        self.assertTrue(dlg._merged_dirty)
        dlg.recalc.assert_called_once()

    def test_truly_mixed_nuclei_still_rejected_with_readable_message(self):
        data = [
            {"atom_idx": 0, "atom_sym": "H", "shielding": 30.0},
            {"atom_idx": 1, "atom_sym": "C", "shielding": 100.0},
        ]
        metadata = [(0, 0, 0, [0]), (0, 0, 0, [1])]
        dlg = _make_merge_dialog(data, metadata, {0, 1})

        dlg.merge_selected_peaks()

        self._merge_mod.QMessageBox.critical.assert_called_once()
        msg = self._merge_mod.QMessageBox.critical.call_args[0][2]
        self.assertIn("H", msg)
        self.assertIn("C", msg)
        self.assertEqual(dlg.merged_peaks, [])

    def test_all_symbols_missing_merge_allowed(self):
        data = [
            {"atom_idx": 0, "shielding": 30.0},
            {"atom_idx": 1, "shielding": 31.0},
        ]
        metadata = [(0, 0, 0, [0]), (0, 0, 0, [1])]
        dlg = _make_merge_dialog(data, metadata, {0, 1})

        dlg.merge_selected_peaks()

        self._merge_mod.QMessageBox.critical.assert_not_called()
        self.assertEqual(dlg.merged_peaks, [{"indices": [0, 1]}])


# ---------------------------------------------------------------------------
# v3.9.1: explicit merge saving (no auto-save) + shift values in 3D labels
# ---------------------------------------------------------------------------


class TestNMRMergeExplicitSave(unittest.TestCase):
    def setUp(self):
        self._merge_mod = sys.modules["gamess_result_analyzer.nmr_merge"]
        self._orig_msgbox = self._merge_mod.QMessageBox
        self._merge_mod.QMessageBox = MagicMock()

    def tearDown(self):
        self._merge_mod.QMessageBox = self._orig_msgbox

    def _dirty_dialog(self):
        data = [
            {"atom_idx": 0, "atom_sym": "H", "shielding": 30.0},
            {"atom_idx": 1, "atom_sym": "H", "shielding": 31.0},
        ]
        metadata = [(0, 0, 0, [0]), (0, 0, 0, [1])]
        dlg = _make_merge_dialog(data, metadata, {0, 1})
        dlg.merge_selected_peaks()
        return dlg

    def test_merge_marks_dirty_without_saving(self):
        dlg = self._dirty_dialog()
        self.assertTrue(dlg._merged_dirty)
        dlg.save_merged_peaks.assert_not_called()

    def test_unmerge_marks_dirty_without_saving(self):
        dlg = _make_merge_dialog(
            [{"atom_idx": 0, "atom_sym": "H", "shielding": 30.0}],
            [(0, 0, True, [0, 1])],
            {0},
        )
        dlg.merged_peaks = [{"indices": [0, 1]}]
        dlg.unmerge_selected_peaks()
        self.assertEqual(dlg.merged_peaks, [])
        self.assertTrue(dlg._merged_dirty)
        dlg.save_merged_peaks.assert_not_called()

    def test_save_merges_clicked_saves_and_clears_dirty(self):
        dlg = self._dirty_dialog()
        dlg.btn_save_merge = MagicMock()
        dlg.save_merges_clicked()
        dlg.save_merged_peaks.assert_called_once()
        self.assertFalse(dlg._merged_dirty)
        dlg.btn_save_merge.setEnabled.assert_called_with(False)

    def test_merge_enables_save_button(self):
        data = [
            {"atom_idx": 0, "atom_sym": "H", "shielding": 30.0},
            {"atom_idx": 1, "atom_sym": "H", "shielding": 31.0},
        ]
        dlg = _make_merge_dialog(data, [(0, 0, 0, [0]), (0, 0, 0, [1])], {0, 1})
        dlg.btn_save_merge = MagicMock()
        dlg.merge_selected_peaks()
        dlg.btn_save_merge.setEnabled.assert_called_with(True)


class TestNMRCloseEventSavePrompt(unittest.TestCase):
    def setUp(self):
        self._orig_msgbox = _nmr_mod.QMessageBox
        self.msgbox = MagicMock()
        # Distinct sentinels so Save/Discard comparisons behave
        self.msgbox.StandardButton.Save = 4
        self.msgbox.StandardButton.Discard = 8
        _nmr_mod.QMessageBox = self.msgbox

    def tearDown(self):
        _nmr_mod.QMessageBox = self._orig_msgbox

    def _closing_dialog(self, dirty):
        dlg = _make_nmr_dialog()
        dlg._merged_dirty = dirty
        dlg.merged_peaks = [{"indices": [0, 1]}]
        dlg.save_merged_peaks = MagicMock()
        dlg.save_settings = MagicMock()
        dlg.clear_atom_labels = MagicMock()
        return dlg

    def test_dirty_close_save_choice_saves(self):
        dlg = self._closing_dialog(dirty=True)
        self.msgbox.question.return_value = 4
        dlg.closeEvent(MagicMock())
        self.msgbox.question.assert_called_once()
        dlg.save_merged_peaks.assert_called_once()
        self.assertFalse(dlg._merged_dirty)

    def test_failed_save_prevents_close_and_keeps_changes_dirty(self):
        dlg = self._closing_dialog(dirty=True)
        dlg.save_merged_peaks.return_value = False
        self.msgbox.question.return_value = 4
        event = MagicMock()
        dlg.closeEvent(event)
        self.assertTrue(dlg._merged_dirty)
        event.ignore.assert_called_once()
        event.accept.assert_not_called()
        dlg.clear_atom_labels.assert_not_called()
        self.assertTrue(dlg.sel_timer.isActive())

    def test_dirty_close_discard_choice_does_not_save(self):
        dlg = self._closing_dialog(dirty=True)
        self.msgbox.question.return_value = 8
        dlg.closeEvent(MagicMock())
        dlg.save_merged_peaks.assert_not_called()

    def test_clean_close_never_prompts(self):
        dlg = self._closing_dialog(dirty=False)
        dlg.closeEvent(MagicMock())
        self.msgbox.question.assert_not_called()
        dlg.save_merged_peaks.assert_not_called()


class TestNMRShiftLabels(unittest.TestCase):
    """3D atom labels must show the chemical shift; merged peaks show both
    the atom's original value and the merged (averaged) value."""

    def _label_recording_dialog(self):
        dlg = _make_nmr_dialog()
        dlg.delta_ref = 0.0
        dlg.sigma_ref = 31.8  # TMS-like: delta = sigma_ref - shielding
        dlg.data = [
            {"atom_idx": 0, "atom_sym": "H", "shielding": 24.6},  # δ 7.20
            {"atom_idx": 1, "atom_sym": "H", "shielding": 24.4},  # δ 7.40
        ]
        dlg.clear_atom_labels = MagicMock()
        dlg.draw_custom_nmr_highlights_3d = MagicMock()
        dlg.add_atom_label = MagicMock()
        chk = MagicMock()
        chk.isChecked.return_value = True  # opt in: show shift values
        dlg.chk_label_shifts = chk
        return dlg

    def test_individual_peak_label_has_own_shift(self):
        dlg = self._label_recording_dialog()
        dlg.peaks_metadata = [(7.20, 1.0, False, [0])]
        dlg.selected_peak_indices = {0}
        dlg.update_selected_labels(is_external_sync=True)
        args = dlg.add_atom_label.call_args[0]
        self.assertEqual(args[0], 0)
        self.assertEqual(args[2], "δ 7.20")

    def test_merged_peak_label_shows_original_and_merged(self):
        dlg = self._label_recording_dialog()
        dlg.peaks_metadata = [(7.30, 2.0, True, [0, 1])]  # merged average
        dlg.selected_peak_indices = {0}
        dlg.update_selected_labels(is_external_sync=True)
        texts = [c[0][2] for c in dlg.add_atom_label.call_args_list]
        self.assertEqual(texts, ["δ 7.20 → 7.30", "δ 7.40 → 7.30"])

    def test_add_atom_label_appends_shift_line(self):
        dlg = _make_nmr_dialog()
        v3d = MagicMock()
        v3d.atom_positions_3d = [[0.0, 0.0, 0.0]]
        dlg.parent_dlg.mw.view_3d_manager = v3d
        dlg._nmr_label_names = []
        dlg._atom_labels = []
        dlg.add_atom_label(0, "H", "δ 7.26")
        label_arg = v3d.plotter.add_point_labels.call_args[0][1]
        self.assertEqual(label_arg, ["H0\nδ 7.26"])

    def test_add_atom_label_without_shift_keeps_plain_text(self):
        dlg = _make_nmr_dialog()
        v3d = MagicMock()
        v3d.atom_positions_3d = [[0.0, 0.0, 0.0]]
        dlg.parent_dlg.mw.view_3d_manager = v3d
        dlg._nmr_label_names = []
        dlg._atom_labels = []
        dlg.add_atom_label(0, "H")
        label_arg = v3d.plotter.add_point_labels.call_args[0][1]
        self.assertEqual(label_arg, ["H0"])


class TestNMRShiftLabelsToggle(unittest.TestCase):
    """Shift values on 3D labels are opt-in; the default (checkbox absent or
    unchecked) keeps the compact SymbolIndex-only labels."""

    def _dialog(self, checkbox):
        dlg = _make_nmr_dialog()
        dlg.delta_ref = 0.0
        dlg.sigma_ref = 31.8
        dlg.data = [{"atom_idx": 0, "atom_sym": "H", "shielding": 24.6}]
        dlg.peaks_metadata = [(7.20, 1.0, False, [0])]
        dlg.selected_peak_indices = {0}
        dlg.clear_atom_labels = MagicMock()
        dlg.draw_custom_nmr_highlights_3d = MagicMock()
        dlg.add_atom_label = MagicMock()
        dlg.chk_label_shifts = checkbox
        return dlg

    def test_unchecked_hides_shift(self):
        chk = MagicMock()
        chk.isChecked.return_value = False
        dlg = self._dialog(chk)
        dlg.update_selected_labels(is_external_sync=True)
        self.assertIsNone(dlg.add_atom_label.call_args[0][2])

    def test_missing_checkbox_defaults_to_hidden(self):
        dlg = self._dialog(None)
        dlg.update_selected_labels(is_external_sync=True)
        self.assertIsNone(dlg.add_atom_label.call_args[0][2])

    def test_checked_shows_shift(self):
        chk = MagicMock()
        chk.isChecked.return_value = True
        dlg = self._dialog(chk)
        dlg.update_selected_labels(is_external_sync=True)
        self.assertEqual(dlg.add_atom_label.call_args[0][2], "δ 7.20")

    def test_toggle_refreshes_labels_without_clearing_3d_selection(self):
        dlg = self._dialog(MagicMock())
        dlg.update_selected_labels = MagicMock()
        dlg.on_label_shifts_toggled()
        dlg.update_selected_labels.assert_called_once_with(is_external_sync=True)

    def test_toggle_with_no_selection_is_noop(self):
        dlg = self._dialog(MagicMock())
        dlg.selected_peak_indices = set()
        dlg.update_selected_labels = MagicMock()
        dlg.on_label_shifts_toggled()
        dlg.update_selected_labels.assert_not_called()
