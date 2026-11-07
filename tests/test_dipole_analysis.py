"""
tests/test_dipole_analysis.py
Unit tests for DipoleDialog's pure logic: the update_view arrow builder
(center-of-mass, reverse toggle, zero-magnitude guard, actor clearing) and the
settings load/save round-trip.

PySide6 is stubbed so the module imports headlessly; numpy/pyvista are real.
The dialog's Qt constructor is bypassed (object.__new__) with lightweight
fakes for the checkboxes/spin boxes and a recording plotter. pyvista.Arrow is
patched to capture the direction/scale it is handed.
"""

import os
import sys
import json
import types
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402


def _assert_close(testcase, actual, expected, tol=1e-6):
    """Element-wise closeness without numpy.testing (another test module can
    corrupt the shared numpy, which would make np.testing.* raise)."""
    actual = [float(x) for x in actual]
    expected = [float(x) for x in expected]
    testcase.assertEqual(len(actual), len(expected))
    for a, e in zip(actual, expected):
        testcase.assertAlmostEqual(a, e, delta=tol)


# Load dipole_analysis in isolation (own copy of the Qt/pyvista stubs, restored
# afterwards so the shared stubs other test modules rely on stay untouched).
D = gui_harness.load_isolated("dipole_analysis")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeCheck:
    def __init__(self, checked):
        self._checked = checked

    def isChecked(self):
        return self._checked

    def setChecked(self, v):
        self._checked = bool(v)


class _FakeSpin:
    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value

    def setValue(self, v):
        self._value = v


class _RecordingPlotter:
    def __init__(self):
        self.added = []
        self.removed = []
        self.rendered = 0

    def add_mesh(self, mesh, **k):
        actor = types.SimpleNamespace(mesh=mesh, kwargs=k)
        self.added.append(actor)
        return actor

    def remove_actor(self, actor):
        self.removed.append(actor)

    def render(self):
        self.rendered += 1


def _make_host(coords):
    plotter = _RecordingPlotter()
    return types.SimpleNamespace(
        mw=types.SimpleNamespace(plotter=plotter),
        parser=types.SimpleNamespace(data={"coords": coords}),
    )


def _bare_dialog(dipole_data, coords, show=True, reverse=False, scale=2.0, res=20):
    inst = D.DipoleDialog.__new__(D.DipoleDialog)
    inst.dipole_data = dipole_data
    inst.parent_dlg = _make_host(coords)
    inst.arrow_actor = None
    inst.arrow_color = "cyan"
    inst.arrow_res = res
    inst.chk_show = _FakeCheck(show)
    inst.chk_reverse = _FakeCheck(reverse)
    inst.spin_scale = _FakeSpin(scale)
    inst.spin_res = _FakeSpin(res)
    inst.spin_alpha = _FakeSpin(1.0)
    return inst


# ---------------------------------------------------------------------------
# update_view
# ---------------------------------------------------------------------------


class TestUpdateView(unittest.TestCase):
    def setUp(self):
        # Another test module may have pinned a minimal pyvista stub (without
        # Arrow) into sys.modules before dipole_analysis was imported; guard.
        self._had_arrow = hasattr(D.pv, "Arrow")
        self._saved_arrow = getattr(D.pv, "Arrow", None)
        self.arrow_calls = []

        def _fake_arrow(*a, **k):
            self.arrow_calls.append(k)
            return object()

        D.pv.Arrow = _fake_arrow

    def tearDown(self):
        if self._had_arrow:
            D.pv.Arrow = self._saved_arrow
        else:
            try:
                del D.pv.Arrow
            except Exception:
                pass

    def test_arrow_placed_at_center_of_mass(self):
        dlg = _bare_dialog(
            {"vector": [1.0, 0.0, 0.0]}, coords=[[0.0, 0, 0], [2.0, 0, 0]]
        )
        dlg.update_view()
        self.assertEqual(len(self.arrow_calls), 1)
        _assert_close(self, self.arrow_calls[0]["start"], [1.0, 0.0, 0.0])

    def test_reverse_flips_direction(self):
        fwd = _bare_dialog(
            {"vector": [0.0, 0.0, 3.0]}, coords=[[0, 0, 0]], reverse=False
        )
        fwd.update_view()
        rev = _bare_dialog(
            {"vector": [0.0, 0.0, 3.0]}, coords=[[0, 0, 0]], reverse=True
        )
        rev.update_view()
        _assert_close(self, self.arrow_calls[0]["direction"], [0, 0, 1])
        _assert_close(self, self.arrow_calls[1]["direction"], [0, 0, -1])

    def test_scale_sets_arrow_length(self):
        dlg = _bare_dialog({"vector": [0.0, 0.0, 4.0]}, coords=[[0, 0, 0]], scale=2.0)
        dlg.update_view()
        # length = magnitude(4) * scale(2) = 8
        self.assertAlmostEqual(self.arrow_calls[0]["scale"], 8.0)

    def test_zero_magnitude_draws_no_arrow(self):
        dlg = _bare_dialog({"vector": [0.0, 0.0, 0.0]}, coords=[[0, 0, 0]])
        dlg.update_view()
        self.assertEqual(len(self.arrow_calls), 0)
        self.assertIsNone(dlg.arrow_actor)

    def test_no_coords_uses_origin(self):
        dlg = _bare_dialog({"vector": [1.0, 0.0, 0.0]}, coords=[])
        dlg.update_view()
        _assert_close(self, self.arrow_calls[0]["start"], [0.0, 0.0, 0.0])

    def test_show_adds_actor_to_plotter(self):
        dlg = _bare_dialog({"vector": [1.0, 0.0, 0.0]}, coords=[[0, 0, 0]], show=True)
        dlg.update_view()
        self.assertEqual(len(dlg.parent_dlg.mw.plotter.added), 1)
        self.assertIsNotNone(dlg.arrow_actor)

    def test_unchecked_clears_existing_actor_and_returns(self):
        dlg = _bare_dialog({"vector": [1.0, 0.0, 0.0]}, coords=[[0, 0, 0]], show=True)
        dlg.update_view()  # draws
        prev = dlg.arrow_actor
        dlg.chk_show.setChecked(False)
        dlg.update_view()  # should remove and not re-add
        self.assertIn(prev, dlg.parent_dlg.mw.plotter.removed)
        self.assertIsNone(dlg.arrow_actor)

    def test_redraw_removes_previous_actor_first(self):
        dlg = _bare_dialog({"vector": [1.0, 0.0, 0.0]}, coords=[[0, 0, 0]], show=True)
        dlg.update_view()
        first = dlg.arrow_actor
        dlg.update_view()
        self.assertIn(first, dlg.parent_dlg.mw.plotter.removed)


# ---------------------------------------------------------------------------
# settings load / save round-trip
# ---------------------------------------------------------------------------


class TestSettings(unittest.TestCase):
    def test_save_then_load_round_trips(self):
        with tempfile.TemporaryDirectory() as d:
            settings = os.path.join(d, "settings.json")

            dlg = _bare_dialog(
                {"vector": [1, 0, 0]},
                coords=[[0, 0, 0]],
                show=True,
                reverse=False,
                res=42,
            )
            dlg.arrow_color = "#ff0000"
            dlg.settings_file = settings
            dlg.save_settings()

            with open(settings, encoding="utf-8") as f:
                on_disk = json.load(f)["dipole_settings"]
            self.assertEqual(on_disk["res"], 42)
            self.assertEqual(on_disk["color"], "#ff0000")
            self.assertTrue(on_disk["show"])
            self.assertFalse(on_disk["reverse"])

            other = _bare_dialog(
                {"vector": [1, 0, 0]},
                coords=[[0, 0, 0]],
                show=False,
                reverse=True,
                res=20,
            )
            other.btn_color = MagicMock()
            other.settings_file = settings
            other.load_settings()
            self.assertEqual(other.spin_res.value(), 42)
            self.assertEqual(other.arrow_color, "#ff0000")
            self.assertTrue(other.chk_show.isChecked())
            self.assertFalse(other.chk_reverse.isChecked())

    def test_save_preserves_unrelated_sections(self):
        with tempfile.TemporaryDirectory() as d:
            settings = os.path.join(d, "settings.json")
            with open(settings, "w", encoding="utf-8") as f:
                json.dump({"thermal_settings": {"show_details": True}}, f)

            dlg = _bare_dialog({"vector": [1, 0, 0]}, coords=[[0, 0, 0]])
            dlg.settings_file = settings
            dlg.save_settings()

            with open(settings, encoding="utf-8") as f:
                on_disk = json.load(f)
        self.assertTrue(on_disk["thermal_settings"]["show_details"])
        self.assertIn("dipole_settings", on_disk)

    def test_load_missing_file_is_noop(self):
        dlg = _bare_dialog({"vector": [1, 0, 0]}, coords=[[0, 0, 0]])
        dlg.btn_color = MagicMock()
        dlg.settings_file = os.path.join(
            tempfile.gettempdir(), "dipole_does_not_exist_xyz.json"
        )
        dlg.load_settings()  # must not raise

    def test_save_preserves_several_other_dialogs_sections(self):
        with tempfile.TemporaryDirectory() as d:
            settings = os.path.join(d, "settings.json")
            others = {
                "mo_settings": {"iso": 0.02},
                "nmr_settings": {"ref": "TMS"},
                "thermal_settings": {"show_details": True},
            }
            with open(settings, "w", encoding="utf-8") as f:
                json.dump(others, f)

            dlg = _bare_dialog({"vector": [1, 0, 0]}, coords=[[0, 0, 0]])
            dlg.settings_file = settings
            dlg.save_settings()

            with open(settings, encoding="utf-8") as f:
                on_disk = json.load(f)
        for key, val in others.items():
            self.assertEqual(on_disk[key], val)
        self.assertIn("dipole_settings", on_disk)

    def test_round_trips_through_the_shared_helpers(self):
        S = gui_harness.load_isolated("settings")
        with tempfile.TemporaryDirectory() as d:
            settings = os.path.join(d, "settings.json")
            dlg = _bare_dialog(
                {"vector": [1, 0, 0]}, coords=[[0, 0, 0]], show=True, res=17
            )
            dlg.arrow_color = "#123456"
            dlg.settings_file = settings
            dlg.save_settings()

            section = S.load_section(settings, "dipole_settings")
            self.assertEqual(section["res"], 17)
            self.assertEqual(section["color"], "#123456")

    def test_non_numeric_res_does_not_crash_load(self):
        # int(settings["res"]) raises ValueError; the narrowed handler must
        # swallow it instead of crashing the dialog.
        with tempfile.TemporaryDirectory() as d:
            settings = os.path.join(d, "settings.json")
            with open(settings, "w", encoding="utf-8") as f:
                json.dump({"dipole_settings": {"res": "not_a_number"}}, f)

            dlg = _bare_dialog({"vector": [1, 0, 0]}, coords=[[0, 0, 0]])
            dlg.btn_color = MagicMock()
            dlg.settings_file = settings
            dlg.load_settings()  # must not raise
            self.assertEqual(dlg.spin_res.value(), 20)


if __name__ == "__main__":
    unittest.main()
