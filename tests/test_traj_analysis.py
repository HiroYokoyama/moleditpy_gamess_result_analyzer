"""
tests/test_traj_analysis.py
Unit tests for pure-logic methods in traj_analysis.TrajectoryResultDialog.

PySide6, matplotlib Qt backends, rdkit, and PIL are fully stubbed at module
level so that the tests run headlessly without any display server or Qt
installation.  Only compute_scan_points and update_display_values are tested
here — both are self-contained data-transformation methods.
"""

import os
import sys
import types
import importlib.util
import unittest
from unittest.mock import MagicMock

_SRC_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


def _install_stubs(force=False):
    """Install minimal stubs for Qt and matplotlib so traj_analysis can be loaded."""
    if "traj_analysis_test_stubs_installed" in sys.modules and not force:
        return

    # --- PySide6 ---
    # QDialog and FigureCanvasQTAgg must be real inheritable classes, not
    # MagicMock() instances, because traj_analysis inherits from them.
    # All other Qt widgets are only instantiated, so MagicMock() is fine.

    class _BaseWidget:
        """Stub base class for Qt widgets used as base classes."""

        class DialogCode:
            Accepted = 1
            Rejected = 0

        def __init__(self, *args, **kwargs):
            pass

        def closeEvent(self, event):
            pass

        def __getattr__(self, name):
            return lambda *a, **kw: None

    class _Qt:
        class Orientation:
            Horizontal = 1

        class WindowType:
            Window = 1

    existing_widgets = sys.modules.get("PySide6.QtWidgets")
    existing_core = sys.modules.get("PySide6.QtCore")
    existing_gui = sys.modules.get("PySide6.QtGui")
    existing_pyside6 = sys.modules.get("PySide6")

    _TRAJ_WIDGET_MOCKS = [
        "QVBoxLayout",
        "QHBoxLayout",
        "QLabel",
        "QSlider",
        "QRadioButton",
        "QComboBox",
        "QPushButton",
        "QSpinBox",
        "QFormLayout",
        "QDialogButtonBox",
        "QCheckBox",
        "QFileDialog",
        "QMessageBox",
        "QApplication",
        "QButtonGroup",
    ]

    if existing_widgets is not None:
        # Patch
        if not hasattr(existing_widgets, "QDialog"):
            existing_widgets.QDialog = _BaseWidget
        if not hasattr(existing_widgets, "QWidget"):
            existing_widgets.QWidget = _BaseWidget
        for name in _TRAJ_WIDGET_MOCKS:
            if not hasattr(existing_widgets, name):
                setattr(existing_widgets, name, MagicMock())
        if existing_core is not None:
            if not hasattr(existing_core, "Qt"):
                existing_core.Qt = _Qt
            if not hasattr(existing_core, "QTimer"):
                existing_core.QTimer = MagicMock()
        if existing_gui is not None:
            if not hasattr(existing_gui, "QColor"):
                existing_gui.QColor = MagicMock()
    else:
        _pyside6 = existing_pyside6 or types.ModuleType("PySide6")
        _core = existing_core or types.ModuleType("PySide6.QtCore")
        _widgets = types.ModuleType("PySide6.QtWidgets")
        _gui = existing_gui or types.ModuleType("PySide6.QtGui")

        _core.Qt = _Qt
        _core.QTimer = MagicMock()
        _widgets.QDialog = _BaseWidget
        _widgets.QWidget = _BaseWidget
        for name in _TRAJ_WIDGET_MOCKS:
            setattr(_widgets, name, MagicMock())
        _gui.QColor = MagicMock()

        _pyside6.QtCore = _core
        _pyside6.QtWidgets = _widgets
        _pyside6.QtGui = _gui

        sys.modules.update(
            {
                "PySide6": _pyside6,
                "PySide6.QtCore": _core,
                "PySide6.QtWidgets": _widgets,
                "PySide6.QtGui": _gui,
            }
        )

    # --- matplotlib Qt backends ---
    # FigureCanvasQTAgg is inherited by MplCanvas → must be a real class
    class _BaseCanvas:
        def __init__(self, *args, **kwargs):
            pass

        def __getattr__(self, name):
            return lambda *a, **kw: None

    _mpl_qtagg = types.ModuleType("matplotlib.backends.backend_qtagg")
    _mpl_qtagg.FigureCanvasQTAgg = _BaseCanvas
    _mpl_qtagg.NavigationToolbar2QT = MagicMock()
    sys.modules["matplotlib.backends.backend_qtagg"] = _mpl_qtagg

    # --- gamess_result_analyzer package stubs (for relative imports) ---
    _gamess_pkg = types.ModuleType("gamess_result_analyzer")
    _gamess_pkg.__path__ = [os.path.join(_SRC_DIR, "gamess_result_analyzer")]
    _gamess_pkg.__package__ = "gamess_result_analyzer"

    _gamess_utils = types.ModuleType("gamess_result_analyzer.utils")
    _gamess_utils.get_default_export_path = (
        lambda base, suffix="_analyzed", extension="": ""
    )
    _gamess_utils.normalize_atom_symbol = (
        lambda raw: raw.strip().split(":")[0].capitalize()
    )
    _gamess_utils.determine_bonds_without_dummies = (
        lambda mol, charge=0, bond_orders=True: None  # no-op stub
    )
    _gamess_utils.list_gamess_output_files = lambda directory: []  # no-op stub
    _gamess_utils.clear_atom_color_overrides = lambda mw: None  # no-op stub
    _gamess_utils.notify = lambda owner, message, timeout=3000: False  # no-op stub

    _gamess_spectrum = types.ModuleType("gamess_result_analyzer.spectrum_widget")
    _gamess_spectrum.SpectrumWidget = MagicMock()

    sys.modules.update(
        {
            "gamess_result_analyzer": _gamess_pkg,
            "gamess_result_analyzer.utils": _gamess_utils,
            "gamess_result_analyzer.spectrum_widget": _gamess_spectrum,
        }
    )

    # rdkit and PIL: both have try/except ImportError guards in traj_analysis.py.
    # Do NOT stub them here — matplotlib imports PIL internally and will break
    # if a fake PIL module is injected into sys.modules before matplotlib loads.

    sys.modules["traj_analysis_test_stubs_installed"] = types.ModuleType("_sentinel")


def _load_traj_module():
    _install_stubs(force=True)
    src = os.path.normpath(
        os.path.join(_SRC_DIR, "gamess_result_analyzer", "traj_analysis.py")
    )
    spec = importlib.util.spec_from_file_location(
        "gamess_result_analyzer.traj_analysis", src
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "gamess_result_analyzer"
    sys.modules["gamess_result_analyzer.traj_analysis"] = mod
    spec.loader.exec_module(mod)
    return mod


_traj_mod = _load_traj_module()
TrajectoryResultDialog = _traj_mod.TrajectoryResultDialog


# ---------------------------------------------------------------------------
# Minimal fake object for calling unbound logic methods with custom self
# ---------------------------------------------------------------------------


class _FakeDialog:
    """Minimal stand-in for TrajectoryResultDialog in pure-logic tests."""

    def __init__(self, steps, unit="kJ/mol", relative=True):
        self.steps = steps
        self.all_steps = steps
        self.energies = [s["energy"] for s in steps]
        self.min_e = min(self.energies) if self.energies else 0.0
        self.current_unit = unit
        self.show_relative = relative
        self.display_energies = []

    def update_display_values(self):
        TrajectoryResultDialog.update_display_values(self)


# ---------------------------------------------------------------------------
# TestComputeScanPoints
# ---------------------------------------------------------------------------


class TestComputeScanPoints(unittest.TestCase):
    """compute_scan_points does not use self — call with None."""

    def test_no_scan_ids_returns_all_steps(self):
        steps = [{"energy": -100.0}, {"energy": -101.0}]
        result = TrajectoryResultDialog.compute_scan_points(None, steps)
        self.assertEqual(result, steps)

    def test_empty_steps_returns_empty(self):
        result = TrajectoryResultDialog.compute_scan_points(None, [])
        self.assertEqual(result, [])

    def test_groups_by_scan_step_id_picks_last(self):
        steps = [
            {"scan_step_id": 1, "type": "opt_cycle", "energy": -100.0},
            {"scan_step_id": 1, "type": "opt_cycle", "energy": -100.5},  # last → kept
            {"scan_step_id": 2, "type": "opt_cycle", "energy": -101.0},
        ]
        result = TrajectoryResultDialog.compute_scan_points(None, steps)
        self.assertEqual(len(result), 2)
        self.assertAlmostEqual(result[0]["energy"], -100.5)
        self.assertAlmostEqual(result[1]["energy"], -101.0)

    def test_scan_ids_sorted_by_id(self):
        steps = [
            {"scan_step_id": 3, "type": "opt_cycle", "energy": -103.0},
            {"scan_step_id": 1, "type": "opt_cycle", "energy": -101.0},
            {"scan_step_id": 2, "type": "opt_cycle", "energy": -102.0},
        ]
        result = TrajectoryResultDialog.compute_scan_points(None, steps)
        ids = [s["scan_step_id"] for s in result]
        self.assertEqual(ids, [1, 2, 3])

    def test_non_opt_cycle_group_falls_back_to_last(self):
        """If no opt_cycle in a group, the last step in the group is used."""
        steps = [
            {"scan_step_id": 1, "type": "scan_step", "energy": -100.0},
            {"scan_step_id": 1, "type": "scan_step", "energy": -100.8},
        ]
        result = TrajectoryResultDialog.compute_scan_points(None, steps)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["energy"], -100.8)

    def test_opt_cycle_preferred_over_other_types(self):
        steps = [
            {"scan_step_id": 1, "type": "scan_step", "energy": -99.0},
            {"scan_step_id": 1, "type": "opt_cycle", "energy": -100.5},
            {
                "scan_step_id": 1,
                "type": "opt_cycle",
                "energy": -100.8,
            },  # last opt_cycle
        ]
        result = TrajectoryResultDialog.compute_scan_points(None, steps)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["energy"], -100.8)

    def test_single_step_per_group(self):
        steps = [
            {"scan_step_id": 1, "type": "opt_cycle", "energy": -100.0},
            {"scan_step_id": 2, "type": "opt_cycle", "energy": -101.0},
        ]
        result = TrajectoryResultDialog.compute_scan_points(None, steps)
        self.assertEqual(len(result), 2)


# ---------------------------------------------------------------------------
# TestUpdateDisplayValues
# ---------------------------------------------------------------------------

_EH_TO_KJ = 2625.4996395
_EH_TO_KCAL = 627.50947406
_EH_TO_EV = 27.211386246


class TestUpdateDisplayValues(unittest.TestCase):
    def test_relative_kj_per_mol(self):
        steps = [{"energy": -100.00}, {"energy": -100.01}, {"energy": -99.99}]
        fake = _FakeDialog(steps, unit="kJ/mol", relative=True)
        # min_e = -100.01
        fake.min_e = -100.01
        TrajectoryResultDialog.update_display_values(fake)
        expected = [
            (-100.00 - (-100.01)) * _EH_TO_KJ,
            0.0,
            (-99.99 - (-100.01)) * _EH_TO_KJ,
        ]
        for got, exp in zip(fake.display_energies, expected):
            self.assertAlmostEqual(got, exp, places=3)

    def test_relative_kcal_per_mol(self):
        steps = [{"energy": -100.0}, {"energy": -100.001}]
        fake = _FakeDialog(steps, unit="kcal/mol", relative=True)
        fake.min_e = -100.001
        TrajectoryResultDialog.update_display_values(fake)
        delta = (-100.0 - (-100.001)) * _EH_TO_KCAL
        self.assertAlmostEqual(fake.display_energies[0], delta, places=3)
        self.assertAlmostEqual(fake.display_energies[1], 0.0, places=6)

    def test_relative_ev(self):
        steps = [{"energy": -100.0}, {"energy": -100.001}]
        fake = _FakeDialog(steps, unit="eV", relative=True)
        fake.min_e = -100.001
        TrajectoryResultDialog.update_display_values(fake)
        delta = (-100.0 - (-100.001)) * _EH_TO_EV
        self.assertAlmostEqual(fake.display_energies[0], delta, places=3)

    def test_absolute_kj_per_mol(self):
        steps = [{"energy": -1.0}, {"energy": -2.0}]
        fake = _FakeDialog(steps, unit="kJ/mol", relative=False)
        TrajectoryResultDialog.update_display_values(fake)
        self.assertAlmostEqual(fake.display_energies[0], -1.0 * _EH_TO_KJ, places=3)
        self.assertAlmostEqual(fake.display_energies[1], -2.0 * _EH_TO_KJ, places=3)

    def test_absolute_eh_factor_one(self):
        """Unknown unit falls back to factor=1.0 (Eh)."""
        steps = [{"energy": -76.5}]
        fake = _FakeDialog(steps, unit="Eh", relative=False)
        TrajectoryResultDialog.update_display_values(fake)
        self.assertAlmostEqual(fake.display_energies[0], -76.5, places=6)

    def test_relative_minimum_is_zero(self):
        steps = [{"energy": -100.0}, {"energy": -100.5}, {"energy": -99.5}]
        fake = _FakeDialog(steps, unit="kJ/mol", relative=True)
        TrajectoryResultDialog.update_display_values(fake)
        self.assertAlmostEqual(min(fake.display_energies), 0.0, places=6)

    def test_single_step_relative_is_zero(self):
        steps = [{"energy": -76.23456}]
        fake = _FakeDialog(steps, unit="kJ/mol", relative=True)
        TrajectoryResultDialog.update_display_values(fake)
        self.assertAlmostEqual(fake.display_energies[0], 0.0, places=10)


# ---------------------------------------------------------------------------
# TestRecalcEnergies
# ---------------------------------------------------------------------------


class _FakeDialogRecalc:
    """Minimal stand-in for recalc_energies tests."""

    def __init__(self, steps, unit="kJ/mol", relative=True):
        self.steps = steps
        self.all_steps = steps
        self.current_unit = unit
        self.show_relative = relative
        self.energies = []
        self.min_e = 0.0
        self.display_energies = []

    def recalc_energies(self):
        TrajectoryResultDialog.recalc_energies(self)

    def update_display_values(self):
        TrajectoryResultDialog.update_display_values(self)


class TestRecalcEnergies(unittest.TestCase):
    def test_energies_extracted_from_steps(self):
        steps = [{"energy": -100.0}, {"energy": -100.5}, {"energy": -99.5}]
        fake = _FakeDialogRecalc(steps)
        fake.recalc_energies()
        self.assertEqual(fake.energies, [-100.0, -100.5, -99.5])

    def test_min_e_set_correctly(self):
        steps = [{"energy": -100.0}, {"energy": -100.5}, {"energy": -99.5}]
        fake = _FakeDialogRecalc(steps)
        fake.recalc_energies()
        self.assertAlmostEqual(fake.min_e, -100.5, places=6)

    def test_display_energies_populated(self):
        steps = [{"energy": -100.0}, {"energy": -100.5}]
        fake = _FakeDialogRecalc(steps, unit="kJ/mol", relative=True)
        fake.recalc_energies()
        self.assertEqual(len(fake.display_energies), 2)

    def test_minimum_display_energy_is_zero_relative(self):
        steps = [{"energy": -100.0}, {"energy": -100.5}, {"energy": -99.5}]
        fake = _FakeDialogRecalc(steps, unit="kJ/mol", relative=True)
        fake.recalc_energies()
        self.assertAlmostEqual(min(fake.display_energies), 0.0, places=6)

    def test_empty_steps_min_e_zero(self):
        fake = _FakeDialogRecalc([])
        fake.recalc_energies()
        self.assertEqual(fake.energies, [])
        self.assertAlmostEqual(fake.min_e, 0.0, places=6)

    def test_single_step_display_energy_zero(self):
        steps = [{"energy": -76.23456}]
        fake = _FakeDialogRecalc(steps, unit="kJ/mol", relative=True)
        fake.recalc_energies()
        self.assertAlmostEqual(fake.display_energies[0], 0.0, places=10)


class TestOnPick(unittest.TestCase):
    def test_on_pick_left_click(self):
        steps = [{"energy": -100.0, "atoms": [1, 2]}]
        fake = _FakeDialog(steps)
        fake.slider = MagicMock()

        # Mock event with left click mouseevent
        event = MagicMock()
        event.artist = MagicMock()
        event.ind = [0]
        event.mouseevent = MagicMock()
        event.mouseevent.button = 1

        TrajectoryResultDialog.on_pick(fake, event)
        fake.slider.setValue.assert_called_once_with(0)

    def test_on_pick_right_click_ignored(self):
        steps = [{"energy": -100.0, "atoms": [1, 2]}]
        fake = _FakeDialog(steps)
        fake.slider = MagicMock()

        # Mock event with right click mouseevent (button == 3)
        event = MagicMock()
        event.artist = MagicMock()
        event.ind = [0]
        event.mouseevent = MagicMock()
        event.mouseevent.button = 3

        TrajectoryResultDialog.on_pick(fake, event)
        fake.slider.setValue.assert_not_called()

    def test_on_pick_scroll_ignored(self):
        steps = [{"energy": -100.0, "atoms": [1, 2]}]
        fake = _FakeDialog(steps)
        fake.slider = MagicMock()

        # Mock event with scroll event (button == 'up')
        event = MagicMock()
        event.artist = MagicMock()
        event.ind = [0]
        event.mouseevent = MagicMock()
        event.mouseevent.button = "up"

        TrajectoryResultDialog.on_pick(fake, event)
        fake.slider.setValue.assert_not_called()

    def test_on_pick_missing_mouseevent_ignored(self):
        steps = [{"energy": -100.0, "atoms": [1, 2]}]
        fake = _FakeDialog(steps)
        fake.slider = MagicMock()

        # Mock event without mouseevent
        event = MagicMock()
        event.artist = MagicMock()
        event.ind = [0]
        if hasattr(event, "mouseevent"):
            del event.mouseevent

        TrajectoryResultDialog.on_pick(fake, event)
        fake.slider.setValue.assert_not_called()

    def test_on_pick_neb_summary_no_atoms_ignored(self):
        steps = [{"energy": -100.0}]  # no "atoms" key
        fake = _FakeDialog(steps)
        fake.slider = MagicMock()

        event = MagicMock()
        event.artist = MagicMock()
        event.ind = [0]
        event.mouseevent = MagicMock()
        event.mouseevent.button = 1

        TrajectoryResultDialog.on_pick(fake, event)
        fake.slider.setValue.assert_not_called()


class TestEmptyStepsGuards(unittest.TestCase):
    def test_highlight_point_empty_guards(self):
        fake = _FakeDialog([])
        fake.canvas = MagicMock()

        # Should return early
        TrajectoryResultDialog.highlight_point(fake, 0)
        fake.canvas.axes.plot.assert_not_called()

        # Should return early for out of bounds index
        fake.display_energies = [1.2]
        fake.steps = [{"energy": 1.2}]
        TrajectoryResultDialog.highlight_point(fake, 1)
        fake.canvas.axes.plot.assert_not_called()

    def test_on_step_changed_empty_guards(self):
        fake = _FakeDialog([])
        fake.highlight_point = MagicMock()

        # Should return early
        TrajectoryResultDialog.on_step_changed(fake, 0)
        fake.highlight_point.assert_not_called()

    def test_on_hover_empty_guards(self):
        fake = _FakeDialog([])
        fake.scatter = None
        event = MagicMock()

        # Should return early when scatter is None
        TrajectoryResultDialog.on_hover(fake, event)
        event.inaxes = MagicMock()
        TrajectoryResultDialog.on_hover(fake, event)


# ---------------------------------------------------------------------------
# TestGoToFirstLastFrame — "|<" / ">|" navigation buttons
# ---------------------------------------------------------------------------


class _FakeNavDialog:
    """Minimal stand-in exposing just what go_to_first/last_frame touch."""

    def __init__(self, steps, is_playing=False):
        self.steps = steps
        self.is_playing = is_playing
        self.slider = MagicMock()
        self.toggle_play_calls = 0

    def toggle_play(self):
        self.toggle_play_calls += 1
        self.is_playing = not self.is_playing


class TestGoToFirstLastFrame(unittest.TestCase):
    def test_go_to_first_frame_sets_slider_to_zero(self):
        fake = _FakeNavDialog(steps=[{"energy": 1.0}, {"energy": 2.0}, {"energy": 3.0}])
        TrajectoryResultDialog.go_to_first_frame(fake)
        fake.slider.setValue.assert_called_once_with(0)

    def test_go_to_last_frame_sets_slider_to_last_index(self):
        fake = _FakeNavDialog(steps=[{"energy": 1.0}, {"energy": 2.0}, {"energy": 3.0}])
        TrajectoryResultDialog.go_to_last_frame(fake)
        fake.slider.setValue.assert_called_once_with(2)

    def test_go_to_last_frame_single_step(self):
        fake = _FakeNavDialog(steps=[{"energy": 1.0}])
        TrajectoryResultDialog.go_to_last_frame(fake)
        fake.slider.setValue.assert_called_once_with(0)

    def test_go_to_first_frame_stops_playback_first(self):
        fake = _FakeNavDialog(steps=[{"energy": 1.0}], is_playing=True)
        TrajectoryResultDialog.go_to_first_frame(fake)
        self.assertEqual(fake.toggle_play_calls, 1)
        fake.slider.setValue.assert_called_once_with(0)

    def test_go_to_last_frame_stops_playback_first(self):
        fake = _FakeNavDialog(steps=[{"energy": 1.0}, {"energy": 2.0}], is_playing=True)
        TrajectoryResultDialog.go_to_last_frame(fake)
        self.assertEqual(fake.toggle_play_calls, 1)
        fake.slider.setValue.assert_called_once_with(1)

    def test_go_to_first_frame_no_playback_toggle_when_stopped(self):
        fake = _FakeNavDialog(steps=[{"energy": 1.0}], is_playing=False)
        TrajectoryResultDialog.go_to_first_frame(fake)
        self.assertEqual(fake.toggle_play_calls, 0)

    def test_go_to_last_frame_no_playback_toggle_when_stopped(self):
        fake = _FakeNavDialog(steps=[{"energy": 1.0}], is_playing=False)
        TrajectoryResultDialog.go_to_last_frame(fake)
        self.assertEqual(fake.toggle_play_calls, 0)


if __name__ == "__main__":
    unittest.main()
