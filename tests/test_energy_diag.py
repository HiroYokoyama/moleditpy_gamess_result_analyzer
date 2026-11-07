"""
tests/test_energy_diag.py
Coverage for energy_diag: the pure ``calculate_arrow_shifts`` clustering helper
and the EnergyDiagramDialog (construction, painting, zoom/pan, hit testing and
cube lookup).

The dialog is driven under the headless Qt harness. ``paintEvent`` is exercised
directly with real geometry (width/height stubs) so the ~200-line drawing path
runs and populates ``hit_zones``; a real QRect stand-in is injected so the
hit-testing arithmetic in ``mousePressEvent`` operates on genuine numbers.
"""

import os
import sys
import types
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

E = gui_harness.load_isolated("energy_diag")


# ---------------------------------------------------------------------------
# Real geometry stand-ins (the shared Qt stubs return MagicMock for these)
# ---------------------------------------------------------------------------


class _Point:
    def __init__(self, x, y):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y

    def toPoint(self):
        return self


class _Rect:
    """QRect(x, y, w, h) stand-in with the accessors paintEvent/hit-testing use."""

    def __init__(self, x, y, w, h):
        self._x, self._y, self._w, self._h = x, y, w, h

    def left(self):
        return self._x

    def right(self):
        return self._x + self._w

    def top(self):
        return self._y

    def bottom(self):
        return self._y + self._h

    def center(self):
        return _Point(self._x + self._w // 2, self._y + self._h // 2)

    def contains(self, p):
        return (
            self.left() <= p.x() <= self.right()
            and self.top() <= p.y() <= self.bottom()
        )


def _make_dialog(mo_data, parent=None, result_dir=None, unit="eV", w=500, h=600):
    dlg = E.EnergyDiagramDialog(mo_data, parent=parent, result_dir=result_dir)
    dlg.width = lambda: w
    dlg.height = lambda: h
    # Real widget stand-ins: the drawing code reads currentText(), and
    # save_image() toggles setVisible() on both of these.
    dlg.unit_combo = MagicMock()
    dlg.unit_combo.currentText.return_value = unit
    dlg.status_label = MagicMock()
    # The widget stub returns a (truthy) MagicMock for any unset attribute, so
    # pin the drag flag to a real False to make "did not start dragging" testable.
    dlg.dragging = False
    return dlg


def _rhf_data():
    return {
        "type": "RHF",
        "energies": [-1.0, -0.8, -0.5, 0.2, 0.6],
        "occupations": [2, 2, 2, 0, 0],
    }


def _uhf_data():
    return {
        "type": "UHF",
        "energies": [[-1.0, -0.5, 0.3], [-0.9, -0.4, 0.4]],
        "occupations": [[1, 1, 0], [1, 0, 0]],
    }


# ---------------------------------------------------------------------------
# calculate_arrow_shifts — pure clustering logic
# ---------------------------------------------------------------------------


class TestCalculateArrowShifts(unittest.TestCase):
    """items are (orig_index, value); val_to_y maps value -> pixel y."""

    @staticmethod
    def _identity(v):
        return v

    def test_empty_items_returns_empty_dict(self):
        self.assertEqual(E.calculate_arrow_shifts([], self._identity), {})

    def test_single_item_gets_zero_shift(self):
        shifts = E.calculate_arrow_shifts([(7, 100)], self._identity)
        self.assertEqual(shifts, {7: 0})

    def test_well_separated_items_all_zero(self):
        items = [(0, 0), (1, 100), (2, 200)]
        shifts = E.calculate_arrow_shifts(items, self._identity)
        self.assertEqual(shifts, {0: 0, 1: 0, 2: 0})

    def test_two_overlapping_items_split_symmetrically(self):
        # gap of 5 < threshold 15 -> one cluster of 2
        items = [(0, 0), (1, 5)]
        shifts = E.calculate_arrow_shifts(items, self._identity)
        self.assertEqual(shifts, {0: 10, 1: -10})

    def test_three_overlapping_items_centre_stays_put(self):
        items = [(0, 0), (1, 5), (2, 10)]
        shifts = E.calculate_arrow_shifts(items, self._identity)
        self.assertEqual(shifts, {0: 20, 1: 0, 2: -20})

    def test_four_overlapping_items_are_evenly_spread(self):
        items = [(0, 0), (1, 2), (2, 4), (3, 6)]
        shifts = E.calculate_arrow_shifts(items, self._identity)
        self.assertEqual(shifts, {0: 30, 1: 10, 2: -10, 3: -30})

    def test_shifts_within_a_cluster_sum_to_zero(self):
        items = [(i, i * 3) for i in range(5)]
        shifts = E.calculate_arrow_shifts(items, self._identity)
        self.assertEqual(sum(shifts.values()), 0)

    def test_threshold_is_exclusive_at_the_boundary(self):
        # gap exactly == threshold is NOT overlapping -> two singleton clusters
        items = [(0, 0), (1, 15)]
        shifts = E.calculate_arrow_shifts(items, self._identity, threshold=15)
        self.assertEqual(shifts, {0: 0, 1: 0})

        # one less than threshold -> clustered
        items = [(0, 0), (1, 14)]
        shifts = E.calculate_arrow_shifts(items, self._identity, threshold=15)
        self.assertEqual(shifts, {0: 10, 1: -10})

    def test_custom_distance_controls_spread(self):
        items = [(0, 0), (1, 5)]
        shifts = E.calculate_arrow_shifts(items, self._identity, distance=40)
        self.assertEqual(shifts, {0: 20, 1: -20})

    def test_odd_distance_is_truncated_toward_zero(self):
        # start_shift = 7.5 -> int() truncates toward zero on both signs
        items = [(0, 0), (1, 1)]
        shifts = E.calculate_arrow_shifts(items, self._identity, distance=15)
        self.assertEqual(shifts, {0: 7, 1: -7})

    def test_separate_clusters_are_shifted_independently(self):
        # two pairs, far apart from each other
        items = [(0, 0), (1, 5), (2, 500), (3, 505)]
        shifts = E.calculate_arrow_shifts(items, self._identity)
        self.assertEqual(shifts, {0: 10, 1: -10, 2: 10, 3: -10})

    def test_original_indices_are_used_as_keys(self):
        # keys come from items[i][0], not the position in the list
        items = [(42, 0), (99, 5)]
        shifts = E.calculate_arrow_shifts(items, self._identity)
        self.assertEqual(set(shifts), {42, 99})

    def test_val_to_y_is_applied_to_values(self):
        # val_to_y scales values up so a previously-tight pair separates
        items = [(0, 0), (1, 5)]
        shifts = E.calculate_arrow_shifts(items, lambda v: v * 10)
        self.assertEqual(shifts, {0: 0, 1: 0})

    def test_every_item_receives_a_shift(self):
        items = [(i, i) for i in range(12)]
        shifts = E.calculate_arrow_shifts(items, self._identity)
        self.assertEqual(len(shifts), 12)


# ---------------------------------------------------------------------------
# Construction / data normalisation
# ---------------------------------------------------------------------------


class TestDialogConstruction(unittest.TestCase):
    def test_rhf_splits_homo_and_lumo(self):
        dlg = _make_dialog(_rhf_data())
        self.assertEqual(dlg.homo_energy, -0.5)
        self.assertEqual(dlg.lumo_energy, 0.2)
        self.assertEqual(dlg.energies_b, [])
        self.assertEqual(dlg.occ_b, [])

    def test_rhf_view_spans_three_times_the_gap(self):
        dlg = _make_dialog(_rhf_data())
        gap = dlg.lumo_energy - dlg.homo_energy  # 0.7
        centre = (dlg.homo_energy + dlg.lumo_energy) / 2
        self.assertAlmostEqual(dlg.current_max - dlg.current_min, gap * 3)
        self.assertAlmostEqual((dlg.current_min + dlg.current_max) / 2, centre)

    def test_uhf_separates_alpha_and_beta(self):
        dlg = _make_dialog(_uhf_data())
        self.assertTrue(dlg.is_uhf)
        self.assertEqual(dlg.energies_a, [-1.0, -0.5, 0.3])
        self.assertEqual(dlg.energies_b, [-0.9, -0.4, 0.4])
        # HOMO is the highest occupied across both spins
        self.assertEqual(dlg.homo_energy, -0.5)
        # LUMO is the lowest virtual across both spins
        self.assertEqual(dlg.lumo_energy, -0.4)

    def test_uhf_full_range_covers_both_spins(self):
        dlg = _make_dialog(_uhf_data())
        self.assertEqual(dlg.full_min, -1.0)
        self.assertEqual(dlg.full_max, 0.4)

    def test_uhf_without_nested_lists_falls_back_to_alpha_only(self):
        data = {"type": "UHF", "energies": [-1.0, 0.5], "occupations": [1, 0]}
        dlg = _make_dialog(data)
        self.assertEqual(dlg.energies_a, [-1.0, 0.5])
        self.assertEqual(dlg.energies_b, [])

    def test_nested_occupations_are_flattened(self):
        data = {
            "type": "RHF",
            "energies": [-1.0, 0.5],
            "occupations": [[2], [0]],
        }
        dlg = _make_dialog(data)
        self.assertEqual(dlg.occ_a, [2, 0])
        self.assertEqual(dlg.homo_energy, -1.0)

    def test_empty_occupation_entries_flatten_to_zero(self):
        data = {"type": "RHF", "energies": [-1.0, 0.5], "occupations": [[], []]}
        dlg = _make_dialog(data)
        self.assertEqual(dlg.occ_a, [0, 0])

    def test_empty_energies_use_safe_defaults(self):
        data = {"type": "RHF", "energies": [], "occupations": []}
        dlg = _make_dialog(data)
        self.assertEqual(dlg.full_min, -1.0)
        self.assertEqual(dlg.full_max, 1.0)
        self.assertEqual(dlg.homo_energy, -0.5)
        self.assertEqual(dlg.lumo_energy, 0.5)

    def test_all_occupied_falls_back_for_missing_lumo(self):
        data = {"type": "RHF", "energies": [-1.0, -0.5], "occupations": [2, 2]}
        dlg = _make_dialog(data)
        self.assertEqual(dlg.homo_energy, -0.5)
        # no virtual orbitals -> LUMO falls back to the top of the range
        self.assertEqual(dlg.lumo_energy, dlg.full_max)

    def test_all_virtual_falls_back_for_missing_homo(self):
        data = {"type": "RHF", "energies": [0.5, 1.0], "occupations": [0, 0]}
        dlg = _make_dialog(data)
        self.assertEqual(dlg.homo_energy, dlg.full_min)
        self.assertEqual(dlg.lumo_energy, 0.5)

    def test_near_degenerate_gap_gets_minimum_span(self):
        # gap ~0 -> falls back, and the span floor of 0.2 applies
        data = {
            "type": "RHF",
            "energies": [-0.5, -0.5 + 1e-6],
            "occupations": [2, 0],
        }
        dlg = _make_dialog(data)
        self.assertAlmostEqual(dlg.current_max - dlg.current_min, 0.2)

    def test_large_gap_is_not_clamped(self):
        data = {"type": "RHF", "energies": [-5.0, 5.0], "occupations": [2, 0]}
        dlg = _make_dialog(data)
        self.assertAlmostEqual(dlg.current_max - dlg.current_min, 30.0)

    def test_result_dir_is_retained(self):
        dlg = _make_dialog(_rhf_data(), result_dir="/some/dir")
        self.assertEqual(dlg.result_dir, "/some/dir")


# ---------------------------------------------------------------------------
# paintEvent — the drawing path also builds the clickable hit zones
# ---------------------------------------------------------------------------


class TestPaintEvent(unittest.TestCase):
    def setUp(self):
        self._saved_rect = E.QRect
        E.QRect = _Rect

    def tearDown(self):
        E.QRect = self._saved_rect

    def test_rhf_paint_creates_a_hit_zone_per_orbital(self):
        dlg = _make_dialog(_rhf_data())
        dlg.current_min, dlg.current_max = -1.5, 1.0  # show every level
        dlg.paintEvent(object())
        self.assertEqual(len(dlg.hit_zones), 5)

    def test_hit_zones_are_rebuilt_not_appended(self):
        dlg = _make_dialog(_rhf_data())
        dlg.current_min, dlg.current_max = -1.5, 1.0
        dlg.paintEvent(object())
        first = len(dlg.hit_zones)
        dlg.paintEvent(object())
        self.assertEqual(len(dlg.hit_zones), first)

    def test_levels_outside_the_view_are_not_clickable(self):
        dlg = _make_dialog(_rhf_data())
        # window around the frontier orbitals only
        dlg.current_min, dlg.current_max = -0.6, 0.3
        dlg.paintEvent(object())
        self.assertLess(len(dlg.hit_zones), 5)

    def test_uhf_paint_labels_both_spins(self):
        dlg = _make_dialog(_uhf_data())
        dlg.current_min, dlg.current_max = -1.5, 1.0
        dlg.paintEvent(object())
        suffixes = {hz[3] for hz in dlg.hit_zones}
        self.assertEqual(suffixes, {"_A", "_B"})

    def test_hartree_unit_paints_without_error(self):
        dlg = _make_dialog(_rhf_data(), unit="Hartree")
        dlg.current_min, dlg.current_max = -1.5, 1.0
        dlg.paintEvent(object())
        self.assertEqual(len(dlg.hit_zones), 5)

    def test_empty_diagram_paints_with_no_hit_zones(self):
        dlg = _make_dialog({"type": "RHF", "energies": [], "occupations": []})
        dlg.paintEvent(object())
        self.assertEqual(dlg.hit_zones, [])

    def test_degenerate_levels_still_each_get_a_zone(self):
        data = {
            "type": "RHF",
            "energies": [-0.5, -0.5, -0.5, 0.5],
            "occupations": [2, 2, 2, 0],
        }
        dlg = _make_dialog(data)
        dlg.current_min, dlg.current_max = -1.0, 1.0
        dlg.paintEvent(object())
        self.assertEqual(len(dlg.hit_zones), 4)

    def test_tiny_window_does_not_raise(self):
        dlg = _make_dialog(_rhf_data(), w=60, h=60)
        dlg.paintEvent(object())  # must not raise


# ---------------------------------------------------------------------------
# Zoom / pan
# ---------------------------------------------------------------------------


def _wheel_event(angle=0, pixel=0, ctrl=False):
    mods = MagicMock()
    # `event.modifiers() & ControlModifier` must be truthy/falsy as requested
    mods.__and__ = lambda self, other: 1 if ctrl else 0
    return types.SimpleNamespace(
        modifiers=lambda: mods,
        angleDelta=lambda: types.SimpleNamespace(y=lambda: angle),
        pixelDelta=lambda: types.SimpleNamespace(
            y=lambda: pixel, isNull=lambda: pixel == 0
        ),
    )


class TestZoomAndPan(unittest.TestCase):
    def test_ctrl_wheel_up_zooms_in(self):
        dlg = _make_dialog(_rhf_data())
        before = dlg.current_max - dlg.current_min
        dlg.wheelEvent(_wheel_event(angle=120, ctrl=True))
        self.assertLess(dlg.current_max - dlg.current_min, before)

    def test_ctrl_wheel_down_zooms_out(self):
        dlg = _make_dialog(_rhf_data())
        before = dlg.current_max - dlg.current_min
        dlg.wheelEvent(_wheel_event(angle=-120, ctrl=True))
        self.assertGreater(dlg.current_max - dlg.current_min, before)

    def test_zoom_preserves_the_centre(self):
        dlg = _make_dialog(_rhf_data())
        centre = (dlg.current_min + dlg.current_max) / 2
        dlg.wheelEvent(_wheel_event(angle=120, ctrl=True))
        self.assertAlmostEqual((dlg.current_min + dlg.current_max) / 2, centre)

    def test_zoom_in_is_clamped_to_a_minimum_span(self):
        dlg = _make_dialog(_rhf_data())
        for _ in range(80):
            dlg.wheelEvent(_wheel_event(angle=120, ctrl=True))
        self.assertGreaterEqual(dlg.current_max - dlg.current_min, 0.1 - 1e-9)

    def test_zoom_out_is_clamped_to_a_maximum_span(self):
        dlg = _make_dialog(_rhf_data())
        for _ in range(200):
            dlg.wheelEvent(_wheel_event(angle=-120, ctrl=True))
        self.assertLessEqual(dlg.current_max - dlg.current_min, 2000.0 + 1e-6)

    def test_ctrl_wheel_with_no_delta_is_a_noop(self):
        dlg = _make_dialog(_rhf_data())
        before = (dlg.current_min, dlg.current_max)
        dlg.wheelEvent(_wheel_event(angle=0, ctrl=True))
        self.assertEqual((dlg.current_min, dlg.current_max), before)

    def test_plain_wheel_pans_without_changing_span(self):
        dlg = _make_dialog(_rhf_data())
        span = dlg.current_max - dlg.current_min
        dlg.wheelEvent(_wheel_event(angle=120))
        self.assertAlmostEqual(dlg.current_max - dlg.current_min, span)

    def test_wheel_up_pans_toward_higher_energy(self):
        dlg = _make_dialog(_rhf_data())
        before = dlg.current_min
        dlg.wheelEvent(_wheel_event(angle=120))
        self.assertGreater(dlg.current_min, before)

    def test_wheel_down_pans_toward_lower_energy(self):
        dlg = _make_dialog(_rhf_data())
        before = dlg.current_min
        dlg.wheelEvent(_wheel_event(angle=-120))
        self.assertLess(dlg.current_min, before)

    def test_trackpad_pixel_delta_pans(self):
        dlg = _make_dialog(_rhf_data())
        before = dlg.current_min
        dlg.wheelEvent(_wheel_event(pixel=50))
        self.assertGreater(dlg.current_min, before)

    def test_pixel_delta_takes_precedence_over_angle_delta(self):
        dlg_pixel = _make_dialog(_rhf_data())
        dlg_both = _make_dialog(_rhf_data())
        dlg_pixel.wheelEvent(_wheel_event(pixel=50))
        dlg_both.wheelEvent(_wheel_event(pixel=50, angle=120))
        self.assertAlmostEqual(dlg_pixel.current_min, dlg_both.current_min)

    def test_double_click_resets_to_three_times_the_gap(self):
        dlg = _make_dialog(_rhf_data())
        dlg.current_min, dlg.current_max = -50.0, 50.0
        dlg.mouseDoubleClickEvent(object())
        gap = abs(dlg.lumo_energy - dlg.homo_energy)
        self.assertAlmostEqual(dlg.current_max - dlg.current_min, gap * 3)

    def test_double_click_recentres_on_the_gap(self):
        dlg = _make_dialog(_rhf_data())
        dlg.current_min, dlg.current_max = 10.0, 20.0
        dlg.mouseDoubleClickEvent(object())
        centre = (dlg.homo_energy + dlg.lumo_energy) / 2
        self.assertAlmostEqual((dlg.current_min + dlg.current_max) / 2, centre)

    def test_double_click_without_frontier_orbitals_shows_full_range(self):
        dlg = _make_dialog(_rhf_data())
        dlg.homo_energy = None
        dlg.mouseDoubleClickEvent(object())
        # full range plus a 5% margin either side
        margin = 0.05 * (dlg.full_max - dlg.full_min)
        self.assertAlmostEqual(dlg.current_min, dlg.full_min - margin)
        self.assertAlmostEqual(dlg.current_max, dlg.full_max + margin)


# ---------------------------------------------------------------------------
# Mouse press: hit testing vs. drag
# ---------------------------------------------------------------------------


def _press_event(x, y, left=True):
    btn = "L" if left else "R"
    return types.SimpleNamespace(
        button=lambda: btn,
        position=lambda: _Point(x, y),
    )


class TestMousePress(unittest.TestCase):
    def setUp(self):
        self._saved_rect = E.QRect
        E.QRect = _Rect
        self._saved_left = E.Qt.MouseButton.LeftButton
        E.Qt.MouseButton.LeftButton = "L"

    def tearDown(self):
        E.QRect = self._saved_rect
        E.Qt.MouseButton.LeftButton = self._saved_left

    def _painted(self):
        dlg = _make_dialog(_rhf_data(), result_dir=None)
        dlg.current_min, dlg.current_max = -1.5, 1.0
        dlg.paintEvent(object())
        return dlg

    def test_click_on_a_level_triggers_cube_load(self):
        dlg = self._painted()
        rect, index, label, spin = dlg.hit_zones[0]
        centre = rect.center()
        with patch.object(dlg, "try_load_cube") as loader:
            dlg.mousePressEvent(_press_event(centre.x(), centre.y()))
        loader.assert_called_once_with(index, label, spin)

    def test_click_on_a_level_does_not_start_a_drag(self):
        dlg = self._painted()
        centre = dlg.hit_zones[0][0].center()
        with patch.object(dlg, "try_load_cube"):
            dlg.mousePressEvent(_press_event(centre.x(), centre.y()))
        self.assertFalse(getattr(dlg, "dragging", False))

    def test_click_on_empty_space_starts_a_drag(self):
        dlg = self._painted()
        with patch.object(dlg, "try_load_cube") as loader:
            dlg.mousePressEvent(_press_event(5, 5))
        loader.assert_not_called()
        self.assertTrue(dlg.dragging)
        self.assertEqual(dlg.last_mouse_y, 5)

    def test_right_click_is_ignored(self):
        dlg = self._painted()
        with patch.object(dlg, "try_load_cube") as loader:
            dlg.mousePressEvent(_press_event(5, 5, left=False))
        loader.assert_not_called()
        self.assertFalse(getattr(dlg, "dragging", False))

    def test_release_ends_the_drag(self):
        dlg = self._painted()
        dlg.dragging = True
        dlg.mouseReleaseEvent(types.SimpleNamespace(button=lambda: "L"))
        self.assertFalse(dlg.dragging)


# ---------------------------------------------------------------------------
# try_load_cube — filename pattern search
# ---------------------------------------------------------------------------


class TestTryLoadCube(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _touch(self, name, subdir=None):
        base = self.dir if subdir is None else os.path.join(self.dir, subdir)
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("cube")
        return path

    def _dialog(self, parent):
        dlg = _make_dialog(_rhf_data(), parent=parent, result_dir=self.dir)
        dlg.parent = lambda: parent
        return dlg

    def _host(self):
        return types.SimpleNamespace(
            load_file_by_path=MagicMock(),
            generate_specific_orbital=MagicMock(),
        )

    def test_without_result_dir_it_does_nothing(self):
        host = self._host()
        dlg = _make_dialog(_rhf_data(), parent=host, result_dir=None)
        dlg.parent = lambda: host
        dlg.try_load_cube(5, "HOMO")
        host.load_file_by_path.assert_not_called()

    def test_finds_restricted_cube_by_mo_index(self):
        path = self._touch("job_MO_5.cube")
        host = self._host()
        self._dialog(host).try_load_cube(5, "HOMO")
        host.load_file_by_path.assert_called_once_with(path)

    def test_finds_alpha_cube_via_spin_suffix(self):
        path = self._touch("job_MO_5_a.cube")
        host = self._host()
        self._dialog(host).try_load_cube(5, "HOMO", "_A")
        host.load_file_by_path.assert_called_once_with(path)

    def test_finds_beta_cube_via_spin_suffix(self):
        path = self._touch("job_MO_5_b.cube")
        host = self._host()
        self._dialog(host).try_load_cube(5, "HOMO", "_B")
        host.load_file_by_path.assert_called_once_with(path)

    def test_alpha_lookup_ignores_the_beta_file(self):
        self._touch("job_MO_5_b.cube")
        host = self._host()
        with patch.object(E.QMessageBox, "question", return_value="no"):
            self._dialog(host).try_load_cube(5, "HOMO", "_A")
        host.load_file_by_path.assert_not_called()

    def test_falls_back_to_legacy_padded_names(self):
        path = self._touch("005_HOMO.cube")
        host = self._host()
        self._dialog(host).try_load_cube(5, "HOMO")
        host.load_file_by_path.assert_called_once_with(path)

    def test_searches_a_cubes_subfolder(self):
        path = self._touch("job_MO_5.cube", subdir="job_cubes")
        host = self._host()
        self._dialog(host).try_load_cube(5, "HOMO")
        host.load_file_by_path.assert_called_once_with(path)

    def test_missing_cube_prompts_and_generates_on_yes(self):
        host = self._host()
        dlg = self._dialog(host)
        yes = E.QMessageBox.StandardButton.Yes
        with patch.object(E.QMessageBox, "question", return_value=yes):
            dlg.try_load_cube(9, "LUMO+3", "_A")
        host.generate_specific_orbital.assert_called_once_with(9, "LUMO+3", "_A")

    def test_missing_cube_does_not_generate_on_no(self):
        host = self._host()
        dlg = self._dialog(host)
        with patch.object(E.QMessageBox, "question", return_value="declined"):
            dlg.try_load_cube(9, "LUMO+3")
        host.generate_specific_orbital.assert_not_called()

    def test_unreadable_result_dir_is_tolerated(self):
        host = self._host()
        dlg = self._dialog(host)
        with patch.object(E.os, "listdir", side_effect=OSError("boom")):
            with patch.object(E.QMessageBox, "question", return_value="no"):
                dlg.try_load_cube(5, "HOMO")  # must not raise
        host.load_file_by_path.assert_not_called()


# ---------------------------------------------------------------------------
# Misc dialog plumbing
# ---------------------------------------------------------------------------


class TestDialogMisc(unittest.TestCase):
    def test_update_unit_repaints(self):
        dlg = _make_dialog(_rhf_data())
        with patch.object(dlg, "update") as upd:
            dlg.update_unit("Hartree")
        upd.assert_called_once()

    def test_save_image_writes_the_chosen_file(self):
        dlg = _make_dialog(_rhf_data())
        pix = MagicMock()
        dlg.grab = lambda: pix
        with patch.object(
            E.QFileDialog, "getSaveFileName", return_value=("/tmp/d.png", "")
        ):
            dlg.save_image()
        pix.save.assert_called_once_with("/tmp/d.png")

    def test_save_image_cancelled_writes_nothing(self):
        dlg = _make_dialog(_rhf_data())
        pix = MagicMock()
        dlg.grab = lambda: pix
        with patch.object(E.QFileDialog, "getSaveFileName", return_value=("", "")):
            dlg.save_image()
        pix.save.assert_not_called()

    def test_save_image_hides_then_restores_the_controls(self):
        dlg = _make_dialog(_rhf_data())
        dlg.grab = lambda: MagicMock()
        combo = MagicMock()
        dlg.unit_combo = combo
        with patch.object(
            E.QFileDialog, "getSaveFileName", return_value=("/tmp/d.png", "")
        ):
            dlg.save_image()
        # hidden for the grab, then made visible again
        combo.setVisible.assert_any_call(False)
        self.assertEqual(combo.setVisible.call_args_list[-1][0], (True,))


if __name__ == "__main__":
    unittest.main()


class TestCubeLookupStaysWithTheLoadedFile(TestTryLoadCube):
    def _host_for(self, out_name):
        host = self._host()
        parser = types.SimpleNamespace(filename=os.path.join(self.dir, out_name))
        host.parent_dlg = types.SimpleNamespace(parser=parser)
        return host

    def test_another_jobs_cube_folder_is_not_searched(self):
        self._touch("other_MO_5.cube", subdir="other_cubes")
        host = self._host_for("job.out")
        with patch.object(E.QMessageBox, "question", return_value="no"):
            self._dialog(host).try_load_cube(5, "HOMO")
        host.load_file_by_path.assert_not_called()

    def test_the_loaded_files_own_cube_folder_is_searched(self):
        path = self._touch("job_MO_5.cube", subdir="job_cubes")
        host = self._host_for("job.out")
        self._dialog(host).try_load_cube(5, "HOMO")
        host.load_file_by_path.assert_called_once_with(path)


class TestDoubleClickReset(unittest.TestCase):
    def test_a_degenerate_gap_keeps_a_visible_window(self):
        dlg = _make_dialog(_rhf_data())
        dlg.homo_energy = dlg.lumo_energy = -0.3
        dlg.update = MagicMock()
        dlg.mouseDoubleClickEvent(None)
        self.assertGreaterEqual(dlg.current_max - dlg.current_min, 0.2)


class TestSharedDirectoryCubeProvenance(TestCubeLookupStaysWithTheLoadedFile):
    def test_foreign_cube_in_result_directory_is_not_loaded(self):
        self._touch("other_MO_5.cube")
        self._touch("005_HOMO.cube")
        host = self._host_for("current.out")
        with patch.object(E.QMessageBox, "question", return_value=E.QMessageBox.StandardButton.Yes):
            self._dialog(host).try_load_cube(5, "HOMO")
        host.load_file_by_path.assert_not_called()
        host.generate_specific_orbital.assert_called_once_with(5, "HOMO", "")

    def test_exact_current_cube_wins_over_foreign_file(self):
        self._touch("other_MO_5.cube")
        path = self._touch("current_MO_5.cube")
        host = self._host_for("current.out")
        self._dialog(host).try_load_cube(5, "HOMO")
        host.load_file_by_path.assert_called_once_with(path)

    def test_restricted_request_does_not_load_spin_cube(self):
        self._touch("current_MO_5_b.cube")
        host = self._host_for("current.out")
        with patch.object(E.QMessageBox, "question", return_value="no"):
            self._dialog(host).try_load_cube(5, "HOMO")
        host.load_file_by_path.assert_not_called()

    def test_legacy_cube_inside_current_folder_is_supported(self):
        path = self._touch("005_HOMO.cube", subdir="current_cubes")
        host = self._host_for("current.out")
        self._dialog(host).try_load_cube(5, "HOMO")
        host.load_file_by_path.assert_called_once_with(path)
