"""
tests/test_freq_analysis.py
Unit tests for FrequencyDialog frequency list population.

Covers:
  - populate_list(): imaginary modes (raw freq < 0) appear red; real modes use
    default colour; the new Unscaled column always shows the raw value.
  - update_data(): re-applies (or removes) red colour based on the unscaled
    value whenever scaling coefficients change; only the Scaled column (col 1)
    is updated; the Unscaled column (col 2) stays fixed.

PySide6, pyvista, rdkit, numpy, and PIL are stubbed so the tests run headlessly.
"""

import os
import sys
import types
import importlib.util
import unittest
from unittest.mock import MagicMock, patch

_SRC_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


def _install_stubs():
    if "freq_analysis_test_stubs_installed" in sys.modules:
        return

    class _QBase:
        class DialogCode:
            Accepted = 1
            Rejected = 0

        def __init__(self, *a, **kw):
            pass

        def closeEvent(self, event):
            pass

        def __getattr__(self, name):
            return MagicMock()

    class _QDialog(_QBase):
        pass

    class _QWidget(_QBase):
        pass

    class _QTimer:
        def __init__(self, *a, **kw):
            self._interval = 0

        def timeout(self):
            pass

        def start(self, ms=0):
            self._interval = ms

        def stop(self):
            pass

        def isActive(self):
            return False

    class _QColor:
        """Minimal QColor stub that tracks name/invalid state."""

        def __init__(self, name=None):
            self._name = name
            self._valid = name is not None

        def name(self):
            return self._name or ""

        def isValid(self):
            return self._valid

        def red(self):
            return 0

        def green(self):
            return 0

        def blue(self):
            return 0

    # QTreeWidgetItem: stores column texts and foreground colours
    class _QTreeWidgetItem:
        def __init__(self, texts=None):
            self._texts = list(texts) if texts else []
            self._fgs = {}

        def text(self, col):
            return self._texts[col] if col < len(self._texts) else ""

        def setText(self, col, val):
            while len(self._texts) <= col:
                self._texts.append("")
            self._texts[col] = val

        def setForeground(self, col, color):
            self._fgs[col] = color

        def foreground(self, col):
            return self._fgs.get(col, _QColor())

    class _QTreeWidget:
        def __init__(self, *a, **kw):
            self._items = []
            self._header_labels = []
            self._current_item = None

        def setHeaderLabels(self, labels):
            self._header_labels = list(labels)

        def setEditTriggers(self, *a):
            pass

        def currentItemChanged(self):
            return MagicMock()

        def addTopLevelItem(self, item):
            self._items.append(item)

        def clear(self):
            self._items = []

        def invisibleRootItem(self):
            class _Root:
                def __init__(self, items):
                    self._items = items

                def childCount(self):
                    return len(self._items)

                def child(self, i):
                    return self._items[i]

            return _Root(self._items)

        def setCurrentItem(self, item):
            self._current_item = item

        def scrollToItem(self, *a):
            pass

        def clearSelection(self):
            pass

        def selectedItems(self):
            return []

    class _QDoubleSpinBox:
        def __init__(self, *a, **kw):
            self._val = 0.0
            self._enabled = True

        def value(self):
            return self._val

        def setValue(self, v):
            self._val = v

        def setRange(self, *a):
            pass

        def setSingleStep(self, *a):
            pass

        def setDecimals(self, *a):
            pass

        def setEnabled(self, v):
            self._enabled = v

        def setFixedWidth(self, *a):
            pass

        def setSuffix(self, *a):
            pass

        def valueChanged(self):
            return MagicMock()

        def blockSignals(self, *a):
            pass

    class _QSpinBox(_QDoubleSpinBox):
        def __init__(self, *a, **kw):
            super().__init__()
            self._val = 0

    class _QCheckBox:
        def __init__(self, *a, **kw):
            self._checked = False

        def isChecked(self):
            return self._checked

        def setChecked(self, v):
            self._checked = v

        def stateChanged(self):
            return MagicMock()

        def toggled(self):
            return MagicMock()

    class _QSlider:
        def __init__(self, *a, **kw):
            self._val = 0
            self._enabled = True

        def value(self):
            return self._val

        def setValue(self, v):
            self._val = v

        def setRange(self, *a):
            pass

        def setEnabled(self, v):
            self._enabled = v

        def valueChanged(self):
            return MagicMock()

    class _QPushButton:
        def __init__(self, *a, **kw):
            self._enabled = True

        def setEnabled(self, v):
            self._enabled = v

        def isEnabled(self):
            return self._enabled

        def clicked(self):
            return MagicMock()

    class _QLabel:
        def __init__(self, text="", *a, **kw):
            self._text = text

        def setText(self, t):
            self._text = t

        def text(self):
            return self._text

        def setFixedWidth(self, *a):
            pass

        def setAlignment(self, *a):
            pass

    sys.modules.get("PySide6.QtWidgets")
    sys.modules.get("PySide6.QtCore")
    sys.modules.get("PySide6.QtGui")
    sys.modules.get("PySide6")

    _WIDGET_MOCKS = [
        "QVBoxLayout",
        "QHBoxLayout",
        "QFileDialog",
        "QMessageBox",
        "QFormLayout",
        "QDialogButtonBox",
        "QGroupBox",
        "QAbstractItemView",
        "QTreeWidgetItemIterator",
        "QComboBox",
        "QApplication",
        "QColorDialog",
        "QRadioButton",
        "QTabWidget",
    ]
    _WIDGET_CLASSES = {
        "QDialog": _QDialog,
        "QWidget": _QWidget,
        "QTreeWidget": _QTreeWidget,
        "QTreeWidgetItem": _QTreeWidgetItem,
        "QDoubleSpinBox": _QDoubleSpinBox,
        "QSpinBox": _QSpinBox,
        "QCheckBox": _QCheckBox,
        "QSlider": _QSlider,
        "QPushButton": _QPushButton,
        "QLabel": _QLabel,
    }

    # Build and install new stubs, overriding any existing PySide6 modules to avoid
    # constructing real widgets (which requires a QApplication and crashes headlessly).
    _pyside6 = types.ModuleType("PySide6")
    _core = types.ModuleType("PySide6.QtCore")
    _core.Qt = MagicMock()
    _core.QTimer = _QTimer
    _widgets = types.ModuleType("PySide6.QtWidgets")
    for name in _WIDGET_MOCKS:
        setattr(_widgets, name, MagicMock)
    for name, cls in _WIDGET_CLASSES.items():
        setattr(_widgets, name, cls)
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

    # pyvista stub
    _pv = types.ModuleType("pyvista")
    _pv.PolyData = MagicMock
    _pv.Arrow = MagicMock

    # spectrum_widget stub
    _sw = types.ModuleType("gamess_result_analyzer.spectrum_widget")
    _sw.SpectrumWidget = MagicMock

    # utils stub — notify() is pulled in from the real module (no Qt
    # dependency) so its owner-chain resolution is genuinely exercised.
    from gamess_result_analyzer.utils import notify as _real_notify

    _ut = types.ModuleType("gamess_result_analyzer.utils")
    _ut.get_default_export_path = MagicMock(return_value="")
    _ut.clear_atom_color_overrides = MagicMock()
    _ut.save_json_atomic = MagicMock()
    _ut.notify = _real_notify

    # PIL stub — fallback only. This lands in the shared sys.modules for the
    # rest of the session, and matplotlib's Agg backend does
    # `from PIL import features` when it resolves a renderer (figure
    # .tight_layout()), which a stub exposing only .Image cannot satisfy.
    # Keep the real Pillow whenever it is importable.
    try:
        import PIL as _pil  # noqa: F401
    except ImportError:
        _pil = types.ModuleType("PIL")
        _pil.Image = MagicMock

    # rdkit stub (only if not already installed)
    try:
        from rdkit.Geometry import Point3D  # noqa: F401
    except ImportError:

        class _Point3D:
            def __init__(self, x, y, z):
                self.x = x
                self.y = y
                self.z = z

        _rdkit = types.ModuleType("rdkit")
        _rdkit_geom = types.ModuleType("rdkit.Geometry")
        _rdkit_geom.Point3D = _Point3D
        sys.modules["rdkit"] = _rdkit
        sys.modules["rdkit.Geometry"] = _rdkit_geom

    # numpy stub (keep real numpy if available, else stub)
    try:
        import numpy  # noqa: F401
    except ImportError:
        _np = types.ModuleType("numpy")
        _np.array = list
        sys.modules["numpy"] = _np

    sys.modules.update(
        {
            "pyvista": _pv,
            "gamess_result_analyzer.spectrum_widget": _sw,
            "gamess_result_analyzer.utils": _ut,
            "PIL": _pil,
            "freq_analysis_test_stubs_installed": types.ModuleType("marker"),
        }
    )


def _load_freq_analysis():
    _install_stubs()
    path = os.path.join(_SRC_DIR, "gamess_result_analyzer", "freq_analysis.py")
    spec = importlib.util.spec_from_file_location("freq_analysis_mod", path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "gamess_result_analyzer"
    sys.modules["freq_analysis_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


_fa_mod = _load_freq_analysis()
FrequencyDialog = _fa_mod.FrequencyDialog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REAL_COLOR = "#cc0000"  # the red used for imaginary modes


def _color_name(brush_or_color) -> str:
    """Helper to extract hex color name from a QBrush or QColor (stubs or real)."""
    if hasattr(brush_or_color, "color"):
        return brush_or_color.color().name()
    if hasattr(brush_or_color, "name"):
        return brush_or_color.name()
    return ""


def _make_dialog(frequencies):
    """Instantiate FrequencyDialog with minimal mocked parent."""
    mw = MagicMock()
    mw.plotter = MagicMock()
    dlg = FrequencyDialog.__new__(FrequencyDialog)
    # Minimal attribute init (bypass Qt __init__)
    dlg.mw = mw
    dlg.frequencies = frequencies
    dlg.atoms = []
    dlg.base_coords = []
    dlg.is_playing = False
    dlg.vector_color = "orange"
    dlg.vector_res = 20
    dlg.scaling_a = 1.0
    dlg.scaling_b = 0.0
    dlg.vector_actor = None
    dlg.spectrum_win = None
    dlg.default_presets = {"Unscaled": {"a": 1.0, "b": 0.0}}
    dlg.custom_presets = {}

    # Spin boxes
    dlg.spin_sf_a = sys.modules["PySide6.QtWidgets"].QDoubleSpinBox()
    dlg.spin_sf_a._val = 1.0
    dlg.spin_sf_b = sys.modules["PySide6.QtWidgets"].QDoubleSpinBox()
    dlg.spin_sf_b._val = 0.0

    # Tree widget (real stub class)
    dlg.tree = sys.modules["PySide6.QtWidgets"].QTreeWidget()

    # Manual displacement mocks
    dlg.chk_manual_displ = sys.modules["PySide6.QtWidgets"].QCheckBox()
    dlg.slider_displ = sys.modules["PySide6.QtWidgets"].QSlider()
    dlg.lbl_displ_val = sys.modules["PySide6.QtWidgets"].QLabel()
    dlg.spin_amp = sys.modules["PySide6.QtWidgets"].QDoubleSpinBox()
    dlg.spin_amp._val = 1.0

    # Playback button mocks
    dlg.btn_play = sys.modules["PySide6.QtWidgets"].QPushButton()
    dlg.btn_pause = sys.modules["PySide6.QtWidgets"].QPushButton()
    dlg.btn_stop = sys.modules["PySide6.QtWidgets"].QPushButton()
    dlg.btn_gif = sys.modules["PySide6.QtWidgets"].QPushButton()

    # Dipole-change overlay (its own tab, its own settings)
    dlg.dipole_color = "magenta"
    dlg.dipole_actor = None
    dlg.chk_dipole = sys.modules["PySide6.QtWidgets"].QCheckBox()
    dlg.chk_dipole.setChecked(False)
    dlg.lbl_dipole = sys.modules["PySide6.QtWidgets"].QLabel()
    dlg.spin_dip_scale = sys.modules["PySide6.QtWidgets"].QDoubleSpinBox()
    dlg.spin_dip_scale._val = 20.0
    dlg.spin_dip_res = sys.modules["PySide6.QtWidgets"].QSpinBox()
    dlg.spin_dip_res._val = 20
    dlg.spin_dip_alpha = sys.modules["PySide6.QtWidgets"].QDoubleSpinBox()
    dlg.spin_dip_alpha._val = 1.0
    dlg.spin_vec_alpha = sys.modules["PySide6.QtWidgets"].QDoubleSpinBox()
    dlg.spin_vec_alpha._val = 1.0

    return dlg


# ---------------------------------------------------------------------------
# Tests: populate_list
# ---------------------------------------------------------------------------


class TestPopulateListColouring(unittest.TestCase):
    """populate_list() colours imaginary modes red using the unscaled value."""

    def _get_items(self, dlg):
        root = dlg.tree.invisibleRootItem()
        return [root.child(i) for i in range(root.childCount())]

    def test_positive_freq_not_red(self):
        freqs = [{"freq": 3000.0, "ir": 10.0, "raman": 1.0}]
        dlg = _make_dialog(freqs)
        dlg.populate_list()
        items = self._get_items(dlg)
        self.assertEqual(len(items), 1)
        fg = items[0].foreground(0)
        self.assertNotEqual(_color_name(fg), _REAL_COLOR)

    def test_negative_freq_is_red(self):
        freqs = [{"freq": -50.0, "ir": 0.0, "raman": 0.0}]
        dlg = _make_dialog(freqs)
        dlg.populate_list()
        items = self._get_items(dlg)
        self.assertEqual(len(items), 1)
        for col in range(5):
            self.assertEqual(
                _color_name(items[0].foreground(col)),
                _REAL_COLOR,
                f"col {col} not red for imaginary mode",
            )

    def test_zero_freq_not_red(self):
        """Zero is not negative → not imaginary."""
        freqs = [{"freq": 0.0, "ir": 0.0, "raman": 0.0}]
        dlg = _make_dialog(freqs)
        dlg.populate_list()
        items = self._get_items(dlg)
        fg = items[0].foreground(0)
        self.assertNotEqual(_color_name(fg), _REAL_COLOR)

    def test_mixed_modes_colouring(self):
        freqs = [
            {"freq": -120.0, "ir": 0.0, "raman": 0.0},
            {"freq": 500.0, "ir": 5.0, "raman": 0.5},
            {"freq": -30.0, "ir": 0.0, "raman": 0.0},
            {"freq": 1200.0, "ir": 20.0, "raman": 2.0},
        ]
        dlg = _make_dialog(freqs)
        dlg.populate_list()
        items = self._get_items(dlg)
        self.assertEqual(len(items), 4)
        # imaginary
        self.assertEqual(_color_name(items[0].foreground(0)), _REAL_COLOR)
        self.assertEqual(_color_name(items[2].foreground(0)), _REAL_COLOR)
        # real
        self.assertNotEqual(_color_name(items[1].foreground(0)), _REAL_COLOR)
        self.assertNotEqual(_color_name(items[3].foreground(0)), _REAL_COLOR)

    def test_all_five_columns_coloured(self):
        freqs = [{"freq": -75.0, "ir": 0.0, "raman": 0.0}]
        dlg = _make_dialog(freqs)
        dlg.populate_list()
        items = self._get_items(dlg)
        for col in range(5):
            self.assertEqual(
                _color_name(items[0].foreground(col)),
                _REAL_COLOR,
                f"column {col} should be red",
            )


class TestPopulateListUnscaledColumn(unittest.TestCase):
    """Column index 2 always shows the raw (unscaled) frequency value."""

    def _item0(self, dlg):
        return dlg.tree.invisibleRootItem().child(0)

    def test_unscaled_col_equals_raw_freq(self):
        freqs = [{"freq": 3500.5, "ir": 1.0, "raman": 0.0}]
        dlg = _make_dialog(freqs)
        dlg.populate_list()
        item = self._item0(dlg)
        self.assertAlmostEqual(float(item.text(2)), 3500.5, places=1)

    def test_unscaled_col_negative_freq(self):
        freqs = [{"freq": -100.25, "ir": 0.0, "raman": 0.0}]
        dlg = _make_dialog(freqs)
        dlg.populate_list()
        item = self._item0(dlg)
        self.assertAlmostEqual(float(item.text(2)), -100.25, places=1)

    def test_scaled_col_differs_from_unscaled(self):
        """When a!=1, col 1 (scaled) should differ from col 2 (unscaled)."""
        freqs = [{"freq": 1000.0, "ir": 5.0, "raman": 0.0}]
        dlg = _make_dialog(freqs)
        dlg.spin_sf_a._val = 0.96
        dlg.spin_sf_b._val = 10.0
        dlg.populate_list()
        item = self._item0(dlg)
        scaled = float(item.text(1))
        unscaled = float(item.text(2))
        self.assertAlmostEqual(unscaled, 1000.0, places=1)
        self.assertAlmostEqual(scaled, 1000.0 * 0.96 + 10.0, places=1)
        self.assertNotAlmostEqual(scaled, unscaled, places=1)

    def test_column_order_mode_scaled_unscaled_ir_raman(self):
        freqs = [{"freq": 2000.0, "ir": 7.5, "raman": 3.3}]
        dlg = _make_dialog(freqs)
        dlg.spin_sf_a._val = 0.90
        dlg.spin_sf_b._val = 0.0
        dlg.populate_list()
        item = self._item0(dlg)
        # col 0: mode index
        self.assertEqual(item.text(0), "0")
        # col 1: scaled
        self.assertAlmostEqual(float(item.text(1)), 2000.0 * 0.90, places=1)
        # col 2: unscaled
        self.assertAlmostEqual(float(item.text(2)), 2000.0, places=1)
        # col 3: IR
        self.assertAlmostEqual(float(item.text(3)), 7.5, places=1)
        # col 4: Raman
        self.assertAlmostEqual(float(item.text(4)), 3.3, places=1)


# ---------------------------------------------------------------------------
# Tests: update_data
# ---------------------------------------------------------------------------


class TestUpdateDataColouring(unittest.TestCase):
    """update_data() must re-colour rows using the unscaled value."""

    def _populate_then_update(self, freqs, new_a=1.0, new_b=0.0):
        dlg = _make_dialog(freqs)
        dlg.populate_list()
        dlg.spin_sf_a._val = new_a
        dlg.spin_sf_b._val = new_b
        dlg.update_data()
        root = dlg.tree.invisibleRootItem()
        return [root.child(i) for i in range(root.childCount())]

    def test_imaginary_stays_red_after_scaling(self):
        """Even if scaled value becomes positive, raw<0 → stays red."""
        freqs = [{"freq": -50.0, "ir": 0.0, "raman": 0.0}]
        # With a=-2, b=0 the scaled value would be +100, but unscaled=-50 → red
        items = self._populate_then_update(freqs, new_a=-2.0, new_b=0.0)
        for col in range(5):
            self.assertEqual(_color_name(items[0].foreground(col)), _REAL_COLOR)

    def test_real_mode_not_red_after_scaling(self):
        freqs = [{"freq": 1000.0, "ir": 5.0, "raman": 0.0}]
        items = self._populate_then_update(freqs, new_a=0.96, new_b=10.0)
        for col in range(5):
            self.assertNotEqual(_color_name(items[0].foreground(col)), _REAL_COLOR)

    def test_scaled_column_updates(self):
        freqs = [{"freq": 1000.0, "ir": 0.0, "raman": 0.0}]
        items = self._populate_then_update(freqs, new_a=0.95, new_b=5.0)
        self.assertAlmostEqual(float(items[0].text(1)), 1000.0 * 0.95 + 5.0, places=1)

    def test_unscaled_column_unchanged(self):
        """Col 2 must not be altered by update_data()."""
        freqs = [{"freq": 3000.0, "ir": 0.0, "raman": 0.0}]
        dlg = _make_dialog(freqs)
        dlg.populate_list()
        original_unscaled = dlg.tree.invisibleRootItem().child(0).text(2)
        # Apply a very different scaling
        dlg.spin_sf_a._val = 0.5
        dlg.spin_sf_b._val = -100.0
        dlg.update_data()
        updated_unscaled = dlg.tree.invisibleRootItem().child(0).text(2)
        self.assertEqual(original_unscaled, updated_unscaled)


# ---------------------------------------------------------------------------
# Tests: manual_displacement
# ---------------------------------------------------------------------------


class TestManualDisplacement(unittest.TestCase):
    def setUp(self):
        freqs = [{"freq": 1200.0, "vector": [(0.1, 0.0, 0.0), (0.0, 0.2, 0.0)]}]
        self.dlg = _make_dialog(freqs)
        self.dlg.current_mode_idx = 0

        # Setup base coordinates for 2 atoms
        self.dlg.base_coords = [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)]
        self.dlg.atoms = ["C", "H"]

        # Mock conformer and molecule
        self.mock_conf = MagicMock()
        self.mock_mol = MagicMock()
        self.mock_mol.GetConformer.return_value = self.mock_conf

        # Position storage to verify SetAtomPosition calls
        self.positions_set = {}

        def set_atom_pos(idx, pt):
            self.positions_set[idx] = (pt.x, pt.y, pt.z)

        self.mock_conf.SetAtomPosition.side_effect = set_atom_pos
        self.mock_conf.GetNumAtoms.return_value = 2

        # Helper to return displaced positions
        def get_atom_pos(idx):
            class Pt:
                def __init__(self, coords):
                    self.x, self.y, self.z = coords

            coords = self.positions_set.get(idx, self.dlg.base_coords[idx])
            return Pt(coords)

        self.mock_conf.GetAtomPosition.side_effect = get_atom_pos

        self.dlg.mw.current_mol = self.mock_mol
        self.dlg.context = MagicMock()
        self.dlg.chk_vector = sys.modules["PySide6.QtWidgets"].QCheckBox()
        self.dlg.chk_vector.setChecked(False)
        self.dlg.spin_vec_scale = sys.modules["PySide6.QtWidgets"].QDoubleSpinBox()
        self.dlg.spin_vec_scale._val = 2.0

    def test_toggle_manual_displacement(self):
        # By default, manual displacement is not active
        self.dlg.chk_manual_displ.setChecked(False)
        self.dlg.slider_displ.setEnabled(False)

        # 1. Activate manual displacement
        self.dlg.toggle_manual_displacement(True)
        self.assertTrue(self.dlg.slider_displ._enabled)
        self.assertFalse(self.dlg.btn_play._enabled)

        # 2. Change slider to +50 (represents fraction 0.5)
        self.dlg.slider_displ.setValue(50)
        self.dlg.spin_amp.setValue(0.6)  # displacement scale
        self.dlg.on_displacement_slider_changed()

        # factor = 0.5 * 0.6 = 0.3
        # Atom 0: base(1,2,3) + vector(0.1,0,0)*0.3 = (1.03, 2, 3)
        self.assertAlmostEqual(self.positions_set[0][0], 1.03, places=4)
        self.assertAlmostEqual(self.positions_set[1][1], 5.06, places=4)
        self.assertEqual(self.dlg.lbl_displ_val.text(), "+0.30")

        # 3. Deactivate manual displacement
        self.dlg.toggle_manual_displacement(False)
        self.assertFalse(self.dlg.slider_displ._enabled)
        self.assertTrue(self.dlg.btn_play._enabled)
        self.assertEqual(self.dlg.slider_displ.value(), 0)
        # Atom positions should be reset to base_coords
        self.assertAlmostEqual(self.positions_set[0][0], 1.0, places=4)
        self.assertAlmostEqual(self.positions_set[1][1], 5.0, places=4)

    def test_close_event_does_not_reset_geometry_if_manual_active(self):
        # 1. Activate manual displacement and apply displacement
        self.dlg.chk_manual_displ.setChecked(True)
        self.dlg.toggle_manual_displacement(True)
        self.dlg.slider_displ.setValue(50)
        self.dlg.on_displacement_slider_changed()

        # Verify initial displaced coordinates
        self.assertAlmostEqual(self.positions_set[0][0], 1.05, places=4)

        # 2. Call closeEvent
        mock_event = MagicMock()
        self.dlg.closeEvent(mock_event)

        # 3. Assert coordinates were NOT reset (still holds displaced position)
        self.assertAlmostEqual(self.positions_set[0][0], 1.05, places=4)
        self.assertAlmostEqual(self.positions_set[1][1], 5.10, places=4)

    def test_close_event_resets_geometry_if_manual_inactive(self):
        # 1. Start with inactive manual displacement (it should be reset)
        self.dlg.chk_manual_displ.setChecked(False)

        # Apply a displacement first to simulate a state
        self.positions_set[0] = (1.03, 2.0, 3.0)
        self.positions_set[1] = (4.0, 5.06, 6.0)

        # 2. Call closeEvent
        mock_event = MagicMock()
        self.dlg.closeEvent(mock_event)

        # 3. Assert coordinates were reset to base coordinates
        self.assertAlmostEqual(self.positions_set[0][0], 1.0, places=4)
        self.assertAlmostEqual(self.positions_set[1][1], 5.0, places=4)


class _FakePolyData:
    """Stands in for pv.PolyData.

    The module-level stub is `pv.PolyData = MagicMock`, which treats its first
    positional argument as a *spec* -- so the resulting mock rejects
    `point_data` and the drawing code fails before it reaches add_mesh. This
    keeps the two attributes the code actually uses.
    """

    last = None

    def __init__(self, points):
        self.points = points
        self.point_data = {}
        self.glyph_kwargs = None
        _FakePolyData.last = self

    def glyph(self, **kwargs):
        self.glyph_kwargs = kwargs
        return self


class TestDipoleChangeVector(unittest.TestCase):
    """The dipole-change arrow: GAMESS's per-mode transition dipole derivative."""

    def setUp(self):
        self.freqs = [
            {"freq": 1200.0, "ir": 14.83, "dipole_deriv": (0.04, -0.03, 0.0)},
            {"freq": 1800.0, "ir": 0.0},  # no derivative reported for this one
        ]
        self.dlg = _make_dialog(self.freqs)
        self.dlg.current_mode_idx = 0
        self.dlg.base_coords = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0)]
        patcher = patch.object(_fa_mod.pv, "PolyData", _FakePolyData)
        patcher.start()
        self.addCleanup(patcher.stop)
        _FakePolyData.last = None

    def _drawn(self):
        return self.dlg.mw.plotter.add_mesh.called

    def test_nothing_is_drawn_while_the_box_is_unchecked(self):
        self.dlg.chk_dipole.setChecked(False)
        self.dlg.update_dipole_view()
        self.assertFalse(self._drawn())
        self.assertIsNone(self.dlg.dipole_actor)


if __name__ == "__main__":
    unittest.main()
