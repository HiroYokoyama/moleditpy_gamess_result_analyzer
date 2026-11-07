"""
tests/test_freq_window.py
Remaining FrequencyDialog / FreqSpectrumWindow behaviour: syncing the axis
spin boxes when the user zooms the plot, the vector colour picker, teardown
(stopping playback and removing the arrows), and the settings rule that
preserves the spectrum window's own settings when it was never opened.
"""

import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

F = gui_harness.load_isolated("freq_analysis")

COORDS = [[0.0, 0.0, 0.0], [0.76, 0.59, 0.0], [-0.76, 0.59, 0.0]]


def _frequencies():
    trivial = [
        {"freq": v, "ir": 0.0, "raman": 0.0, "vector": [[0.0, 0.0, 0.0]] * 3}
        for v in (0.01, 0.02, 0.03, 0.04, 0.05, 0.06)
    ]
    real = [
        {"freq": 1600.0, "ir": 55.0, "raman": 3.0, "vector": [[0.0, 0.1, 0.0]] * 3},
        {"freq": 3200.0, "ir": 5.0, "raman": 9.0, "vector": [[0.0, 0.0, 0.1]] * 3},
    ]
    return trivial + real


class _FreqCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        saved = F.__file__
        F.__file__ = os.path.join(self.tmp, "freq_analysis.py")
        self.addCleanup(lambda: setattr(F, "__file__", saved))

        self.plotter = MagicMock()
        self.conf = MagicMock()
        self.mol = MagicMock()
        self.mol.GetConformer.return_value = self.conf
        self.mw = MagicMock()
        self.mw.plotter = self.plotter
        self.mw.current_mol = self.mol
        self.mw.init_manager.current_file_path = os.path.join(self.tmp, "job.out")

        self.dlg = F.FrequencyDialog(
            self.mw, _frequencies(), ["O", "H", "H"], COORDS, MagicMock()
        )
        self.dlg.settings_file = os.path.join(self.tmp, "settings.json")


# ---------------------------------------------------------------------------
# Zoom -> spin box sync
# ---------------------------------------------------------------------------


class TestRangeSync(_FreqCase):
    def setUp(self):
        super().setUp()
        self.win = F.FreqSpectrumWindow(self.dlg, _frequencies())

    def test_a_zoom_updates_the_axis_spin_boxes(self):
        self.win.on_spectrum_range_changed(500.0, 3000.0, 0.0, 60.0, True)
        self.assertAlmostEqual(self.win.spin_x_min.value(), 500.0)
        self.assertAlmostEqual(self.win.spin_x_max.value(), 3000.0)
        self.assertAlmostEqual(self.win.spin_y_min.value(), 0.0)
        self.assertAlmostEqual(self.win.spin_y_max.value(), 60.0)

    def test_a_manual_zoom_turns_off_auto_ranging(self):
        self.win.chk_auto_x.setChecked(True)
        self.win.chk_auto_y.setChecked(True)
        self.win.on_spectrum_range_changed(500.0, 3000.0, 0.0, 60.0, True)
        self.assertFalse(self.win.chk_auto_x.isChecked())
        self.assertFalse(self.win.chk_auto_y.isChecked())

    def test_a_manual_zoom_enables_the_spin_boxes(self):
        self.win.spin_x_min.setEnabled = MagicMock()
        self.win.on_spectrum_range_changed(500.0, 3000.0, 0.0, 60.0, True)
        self.win.spin_x_min.setEnabled.assert_called_with(True)

    def test_an_automatic_rescale_leaves_auto_ranging_on(self):
        self.win.chk_auto_x.setChecked(True)
        self.win.chk_auto_y.setChecked(True)
        self.win.on_spectrum_range_changed(500.0, 3000.0, 0.0, 60.0, False)
        self.assertTrue(self.win.chk_auto_x.isChecked())
        self.assertTrue(self.win.chk_auto_y.isChecked())

    def test_an_automatic_rescale_still_updates_the_spin_boxes(self):
        self.win.on_spectrum_range_changed(400.0, 4000.0, 0.0, 100.0, False)
        self.assertAlmostEqual(self.win.spin_x_max.value(), 4000.0)

    def test_saving_the_spectrum_png_uses_the_result_name(self):
        path = os.path.join(self.tmp, "spec.png")
        with patch.object(F.QFileDialog, "getSaveFileName", return_value=(path, "")):
            with patch.object(self.win.spectrum, "save_png") as saver:
                self.win.save_png()
        saver.assert_called_once_with(path)

    def test_cancelling_the_png_export_writes_nothing(self):
        with patch.object(F.QFileDialog, "getSaveFileName", return_value=("", "")):
            with patch.object(self.win.spectrum, "save_png") as saver:
                self.win.save_png()
        saver.assert_not_called()


# ---------------------------------------------------------------------------
# Vector appearance
# ---------------------------------------------------------------------------


class TestVectorAppearance(_FreqCase):
    def test_picking_a_colour_redraws_the_arrows(self):
        colour = MagicMock()
        colour.isValid.return_value = True
        colour.name.return_value = "#00ff00"
        with patch.object(F.QColorDialog, "getColor", return_value=colour):
            with patch.object(self.dlg, "update_view") as redraw:
                self.dlg.pick_color()
        self.assertEqual(self.dlg.vector_color, "#00ff00")
        redraw.assert_called_once()

    def test_cancelling_the_colour_picker_changes_nothing(self):
        colour = MagicMock()
        colour.isValid.return_value = False
        before = self.dlg.vector_color
        with patch.object(F.QColorDialog, "getColor", return_value=colour):
            with patch.object(self.dlg, "update_view") as redraw:
                self.dlg.pick_color()
        self.assertEqual(self.dlg.vector_color, before)
        redraw.assert_not_called()

    def test_changing_the_resolution_redraws(self):
        with patch.object(self.dlg, "update_view") as redraw:
            self.dlg.on_res_changed(24)
        self.assertEqual(self.dlg.vector_res, 24)
        redraw.assert_called_once()


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------


class TestTeardown(_FreqCase):
    def test_closing_stops_playback(self):
        self.dlg.timer = MagicMock()
        self.dlg.is_playing = True
        self.dlg.chk_manual_displ.setChecked(False)
        self.dlg.closeEvent(MagicMock())
        self.assertFalse(self.dlg.is_playing)

    def test_closing_in_manual_mode_also_stops_the_timer(self):
        self.dlg.timer = MagicMock()
        self.dlg.is_playing = True
        self.dlg.animation_step = 5
        self.dlg.chk_manual_displ.setChecked(True)
        self.dlg.closeEvent(MagicMock())
        self.assertFalse(self.dlg.is_playing)
        self.assertEqual(self.dlg.animation_step, 0)
        self.dlg.timer.stop.assert_called_once()

    def test_closing_removes_the_arrows(self):
        actor = object()
        self.dlg.vector_actor = actor
        self.dlg.timer = MagicMock()
        self.dlg.closeEvent(MagicMock())
        self.plotter.remove_actor.assert_any_call(actor)

    def test_closing_also_closes_the_spectrum_window(self):
        window = MagicMock()
        self.dlg.spectrum_win = window
        self.dlg.timer = MagicMock()
        self.dlg.closeEvent(MagicMock())
        window.close.assert_called_once()

    def test_closing_persists_the_settings(self):
        self.dlg.timer = MagicMock()
        self.dlg.spin_sf_a.setValue(0.97)
        self.dlg.closeEvent(MagicMock())
        saved = json.load(open(self.dlg.settings_file, encoding="utf-8"))
        self.assertAlmostEqual(saved["freq_settings"]["sf_a"], 0.97)

    def test_a_stale_arrow_actor_is_tolerated(self):
        self.dlg.vector_actor = object()
        self.dlg.timer = MagicMock()
        self.plotter.remove_actor.side_effect = ValueError("gone")
        self.dlg.closeEvent(MagicMock())  # must not raise


class TestSpectrumSettingsPreserved(_FreqCase):
    def test_an_open_spectrum_window_contributes_its_settings(self):
        self.dlg.open_spectrum()
        self.dlg.spectrum_win.spin_sigma.setValue(42.0)
        self.dlg.save_settings()
        saved = json.load(open(self.dlg.settings_file, encoding="utf-8"))
        self.assertAlmostEqual(saved["freq_settings"]["spec_sigma"], 42.0)

    def test_settings_survive_a_save_with_the_window_closed(self):
        # Save once with the window open, then again without it: the spectrum
        # settings must not be dropped just because it is not showing.
        self.dlg.open_spectrum()
        self.dlg.spectrum_win.spin_sigma.setValue(42.0)
        self.dlg.save_settings()

        self.dlg.spectrum_win = None
        self.dlg.save_settings()
        saved = json.load(open(self.dlg.settings_file, encoding="utf-8"))
        self.assertAlmostEqual(saved["freq_settings"]["spec_sigma"], 42.0)


if __name__ == "__main__":
    unittest.main()
