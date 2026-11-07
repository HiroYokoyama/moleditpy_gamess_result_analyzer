"""
tests/test_spectrum_widget.py
Direct coverage for SpectrumWidget: construction defaults, the display
setters, peak picking (nearest-point tolerance, double-click reset) and the
zoom/pan callback that syncs the axis ranges back to the host dialog.

The GAMESS broadening and CSV export paths are exercised through
tests/test_tddft_analysis.py, which drives a real widget end-to-end.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

S = gui_harness.load_isolated("spectrum_widget")


def _uv_data():
    return [
        {"energy_nm": 400.0, "osc": 0.05},
        {"energy_nm": 310.0, "osc": 0.20},
        {"energy_nm": 250.0, "osc": 0.00},  # dark state
    ]


def _ir_data():
    return [
        {"freq": 1600.0, "ir": 55.0},
        {"freq": 3200.0, "ir": 5.0},
    ]


def _widget(data=None, **kw):
    kw.setdefault("x_key", "energy_nm")
    kw.setdefault("y_key", "osc")
    kw.setdefault("x_unit", "Wavelength (nm)")
    return S.SpectrumWidget(_uv_data() if data is None else data, **kw)


def _click(x=None, button=1, dblclick=False, inaxes=True, widget=None):
    ev = MagicMock()
    ev.xdata = x
    ev.button = button
    ev.dblclick = dblclick
    ev.inaxes = widget.canvas.axes if (inaxes and widget) else None
    return ev


class TestConstruction(unittest.TestCase):
    def test_the_data_is_retained(self):
        self.assertEqual(len(_widget().data_list), 3)

    def test_a_wavelength_axis_is_not_inverted(self):
        self.assertFalse(_widget().invert_x)

    def test_a_frequency_axis_is_inverted_by_convention(self):
        w = _widget(_ir_data(), x_key="freq", y_key="ir", x_unit="Frequency (cm-1)")
        self.assertTrue(w.invert_x)

    def test_a_frequency_axis_gets_the_standard_window(self):
        w = _widget(_ir_data(), x_key="freq", y_key="ir", x_unit="Frequency (cm-1)")
        self.assertEqual(w.x_range, (400, 4000))

    def test_a_wavelength_axis_starts_auto_ranged(self):
        self.assertIsNone(_widget().x_range)

    def test_sticks_and_curve_are_shown_by_default(self):
        w = _widget()
        self.assertTrue(w.show_sticks)
        self.assertTrue(w.show_gamess)

    def test_broadening_defaults_to_the_display_axis(self):
        self.assertFalse(_widget().broaden_in_energy)

    def test_normalisation_defaults_to_peak_height(self):
        self.assertEqual(_widget().normalization_mode, "height")

    def test_an_empty_dataset_still_builds(self):
        self.assertEqual(_widget([]).data_list, [])


class TestSetters(unittest.TestCase):
    def setUp(self):
        self.w = _widget()
        # Every setter redraws; the drawing itself is covered elsewhere.
        patcher = patch.object(self.w, "plot_spectrum")
        self.plot = patcher.start()
        self.addCleanup(patcher.stop)

    def test_replacing_the_data_redraws(self):
        self.w.set_data(_ir_data())
        self.assertEqual(len(self.w.data_list), 2)
        self.plot.assert_called_once()

    def test_selecting_an_item_redraws(self):
        item = self.w.data_list[0]
        self.w.set_selected_item(item)
        self.assertIs(self.w.selected_item, item)
        self.plot.assert_called_once()

    def test_setting_an_x_range(self):
        self.w.set_x_range(200.0, 500.0)
        self.assertEqual(self.w.x_range, (200.0, 500.0))

    def test_clearing_the_x_range(self):
        self.w.set_x_range(200.0, 500.0)
        self.w.set_auto_x_range()
        self.assertIsNone(self.w.x_range)

    def test_setting_a_y_range(self):
        self.w.set_y_range(0.0, 1.0)
        self.assertEqual(self.w.y_range, (0.0, 1.0))

    def test_clearing_the_y_range(self):
        self.w.set_y_range(0.0, 1.0)
        self.w.set_auto_range()
        self.assertIsNone(self.w.y_range)

    def test_setting_the_broadening_width(self):
        self.w.set_sigma(1234.0)
        self.assertEqual(self.w.sigma, 1234.0)

    def test_toggling_the_sticks(self):
        self.w.set_sticks(False)
        self.assertFalse(self.w.show_sticks)
        self.w.set_sticks(True)
        self.assertTrue(self.w.show_sticks)

    def test_toggling_the_curve(self):
        self.w.set_gamess(False)
        self.assertFalse(self.w.show_gamess)

    def test_toggling_the_markers(self):
        self.w.set_markers(False)
        self.assertFalse(self.w.show_markers)

    def test_setting_the_intensity_scaling(self):
        self.w.set_scaling(2.5)
        self.assertEqual(self.w.scaling_factor, 2.5)

    def test_enabling_the_second_axis(self):
        self.w.set_dual_axis(True)
        self.assertTrue(self.w.use_dual_axis)

    def test_update_redraws(self):
        self.w.update()
        self.plot.assert_called_once()


class TestPeakPicking(unittest.TestCase):
    def setUp(self):
        self.w = _widget()
        self.w.canvas.axes.set_xlim(200, 500)
        patcher = patch.object(self.w, "plot_spectrum")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.emitted = []
        self.w.clicked = MagicMock()
        self.w.clicked.emit = self.emitted.append

    def test_clicking_a_peak_selects_it(self):
        self.w.on_click(_click(400.0, widget=self.w))
        self.assertEqual(self.w.selected_item["energy_nm"], 400.0)

    def test_selecting_a_peak_notifies_the_host(self):
        self.w.on_click(_click(400.0, widget=self.w))
        self.assertEqual(self.emitted[-1]["energy_nm"], 400.0)

    def test_the_nearest_peak_wins(self):
        self.w.on_click(_click(305.0, widget=self.w))
        self.assertEqual(self.w.selected_item["energy_nm"], 310.0)

    def test_a_dark_state_can_still_be_selected(self):
        self.w.on_click(_click(250.0, widget=self.w))
        self.assertEqual(self.w.selected_item["energy_nm"], 250.0)

    def test_clicking_empty_space_clears_the_selection(self):
        self.w.selected_item = self.w.data_list[0]
        self.w.on_click(_click(480.0, widget=self.w))
        self.assertIsNone(self.w.selected_item)
        self.assertIsNone(self.emitted[-1])

    def test_a_click_outside_the_axes_is_ignored(self):
        self.w.on_click(_click(400.0, inaxes=False, widget=self.w))
        self.assertEqual(self.emitted, [])

    def test_a_right_click_is_ignored(self):
        self.w.on_click(_click(400.0, button=3, widget=self.w))
        self.assertEqual(self.emitted, [])

    def test_a_click_without_coordinates_is_ignored(self):
        self.w.on_click(_click(None, widget=self.w))
        self.assertEqual(self.emitted, [])

    def test_clicking_an_empty_plot_is_ignored(self):
        w = _widget([])
        w.clicked = MagicMock()
        w.on_click(_click(400.0, widget=w))
        w.clicked.emit.assert_not_called()

    def test_double_click_clears_the_selection(self):
        self.w.selected_item = self.w.data_list[0]
        self.w.on_click(_click(400.0, dblclick=True, widget=self.w))
        self.assertIsNone(self.w.selected_item)
        self.assertIsNone(self.emitted[-1])

    def test_double_click_restores_auto_ranging(self):
        self.w.set_x_range(300, 350)
        self.w.on_click(_click(400.0, dblclick=True, widget=self.w))
        self.assertIsNone(self.w.x_range)
        self.assertIsNone(self.w.y_range)

    def test_double_click_restores_the_standard_ir_window(self):
        w = _widget(_ir_data(), x_key="freq", y_key="ir", x_unit="Frequency (cm-1)")
        with patch.object(w, "plot_spectrum"):
            w.clicked = MagicMock()
            w.canvas.axes.set_xlim(1000, 2000)
            w.on_click(_click(1500.0, dblclick=True, widget=w))
        self.assertEqual(w.x_range, (400, 4000))


class TestAxesCallback(unittest.TestCase):
    def setUp(self):
        self.w = _widget()
        self.w._initial_plot_done = True
        self.w._is_plotting = False
        self.w.range_changed = MagicMock()

    def test_zooming_records_the_new_range(self):
        self.w.canvas.axes.set_xlim(300, 350)
        self.w.canvas.axes.set_ylim(0, 1)
        self.w._on_axes_changed(self.w.canvas.axes)
        self.assertEqual(self.w.x_range, (300, 350))
        self.assertEqual(self.w.y_range, (0, 1))

    def test_zooming_notifies_the_host_as_a_manual_range(self):
        self.w.canvas.axes.set_xlim(300, 350)
        self.w._on_axes_changed(self.w.canvas.axes)
        self.assertTrue(self.w.range_changed.emit.call_args.args[-1])

    def test_the_callback_is_inert_while_plotting(self):
        self.w._is_plotting = True
        self.w._on_axes_changed(self.w.canvas.axes)
        self.w.range_changed.emit.assert_not_called()

    def test_the_callback_is_inert_before_the_first_plot(self):
        self.w._initial_plot_done = False
        self.w._on_axes_changed(self.w.canvas.axes)
        self.w.range_changed.emit.assert_not_called()

    @staticmethod
    def _listener_count(widget):
        registry = widget.canvas.axes.callbacks.callbacks
        return sum(
            len(registry.get(sig, {})) for sig in ("xlim_changed", "ylim_changed")
        )

    def test_replotting_leaves_exactly_one_listener_per_signal(self):
        """Axes.clear() installs a fresh registry, so connections must not pile up."""
        for _ in range(4):
            self.w.plot_spectrum()
        self.assertEqual(self._listener_count(self.w), 2)

    def test_an_empty_replot_keeps_the_axes_wired(self):
        """The "No Data" path returns early -- it must still leave listeners.

        clear() drops the registry at the top of plot_spectrum, so returning
        before reconnecting left the axes with nothing listening: the toolbar's
        zoom stopped emitting range_changed and the host's range boxes silently
        stopped tracking the view until the next non-empty plot.
        """
        self.w.plot_spectrum()
        self.w.data_list = []
        self.w.plot_spectrum()
        self.assertEqual(self._listener_count(self.w), 2)

        self.w._is_plotting = False
        self.w._initial_plot_done = True
        self.w.range_changed = MagicMock()
        self.w.canvas.axes.set_xlim(300, 350)
        self.w.range_changed.emit.assert_called()


class TestStickExport(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.w = _widget()

    def test_sticks_are_written_in_ascending_x_order(self):
        path = os.path.join(self.tmp, "sticks.csv")
        self.assertTrue(self.w.save_sticks_csv(path))
        with open(path, encoding="utf-8") as fh:
            xs = [float(line.split(",")[0]) for line in fh.read().splitlines()[1:]]
        self.assertEqual(xs, sorted(xs))

    def test_zero_intensity_peaks_are_omitted(self):
        path = os.path.join(self.tmp, "sticks.csv")
        self.w.save_sticks_csv(path)
        with open(path, encoding="utf-8") as fh:
            rows = fh.read().splitlines()[1:]
        self.assertEqual(len(rows), 2)  # the dark state is dropped

    def test_a_dataset_with_no_intensity_exports_nothing(self):
        w = _widget([{"energy_nm": 400.0, "osc": 0.0}])
        self.assertFalse(w.save_sticks_csv(os.path.join(self.tmp, "x.csv")))

    def test_an_unwritable_path_reports_failure(self):
        blocker = os.path.join(self.tmp, "blocker")
        with open(blocker, "w", encoding="utf-8") as fh:
            fh.write("x")
        self.assertFalse(self.w.save_sticks_csv(os.path.join(blocker, "x.csv")))


class TestCurveCsvKeepsNegativeIntensity(unittest.TestCase):
    """A CD spectrum has negative bands; the export must not flatten them.

    The denormal cleanup compared the signed value against a positive
    threshold, so every negative point in the exported curve became 0.0 while
    the on-screen plot stayed correct.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # Two bands of opposite sign — a minimal bisignate CD couplet.
        self.w = _widget(
            [
                {"energy_nm": 350.0, "rot": 40.0},
                {"energy_nm": 300.0, "rot": -40.0},
            ],
            y_key="rot",
        )
        self.w.sigma = 10.0

    def _export(self):
        path = os.path.join(self.tmp, "cd.csv")
        self.assertTrue(self.w.save_csv(path))
        with open(path, encoding="utf-8") as fh:
            rows = fh.read().splitlines()[1:]
        return [float(r.split(",")[1]) for r in rows]

    def test_negative_lobe_survives_the_export(self):
        self.assertLess(min(self._export()), -1.0)

    def test_positive_lobe_survives_the_export(self):
        self.assertGreater(max(self._export()), 1.0)

    def test_an_all_positive_curve_is_still_cleaned(self):
        w = _widget([{"energy_nm": 350.0, "osc": 1.0}])
        w.sigma = 10.0
        w.x_range = (250.0, 450.0)  # reach out to wings below the threshold
        path = os.path.join(self.tmp, "abs.csv")
        self.assertTrue(w.save_csv(path))
        with open(path, encoding="utf-8") as fh:
            ys = [float(r.split(",")[1]) for r in fh.read().splitlines()[1:]]
        self.assertGreaterEqual(min(ys), 0.0)
        self.assertIn(0.0, ys)  # far wings still collapse to exact zero


if __name__ == "__main__":
    unittest.main()


class TestStickCsvMatchesPlot(unittest.TestCase):
    def test_raw_stick_key_and_units_are_exported(self):
        import csv
        for key, unit, value in [("osc_len", "Oscillator strength", 0.1), ("rot", "Rotatory strength", -2.0)]:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                widget = _widget([{ "energy_nm": 400, "processed_y": 23150000.0, key: value}], y_key="processed_y")
                widget.y_key_sticks = key
                widget.y_unit_sticks = unit
                widget.y_unit = "Curve units"
                path = os.path.join(tmp, "sticks.csv")
                self.assertTrue(widget.save_sticks_csv(path))
                with open(path, encoding="utf-8") as file:
                    rows = list(csv.reader(file))
                self.assertEqual(rows[0], ["Wavelength (nm)", unit])
                self.assertEqual(float(rows[1][1]), value)

    def test_fallback_uses_curve_key_and_units(self):
        import csv
        with tempfile.TemporaryDirectory() as tmp:
            widget = _widget()
            widget.y_key_sticks = None
            widget.y_unit = "Oscillator strength"
            path = os.path.join(tmp, "sticks.csv")
            self.assertTrue(widget.save_sticks_csv(path))
            with open(path, encoding="utf-8") as file:
                rows = list(csv.reader(file))
            self.assertEqual(rows[0][1], "Oscillator strength")
            self.assertEqual([float(row[1]) for row in rows[1:]], [0.2, 0.05])
