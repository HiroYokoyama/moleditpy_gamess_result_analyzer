"""
tests/test_thermal_and_traj_modes.py
Fills two remaining gaps under the headless Qt harness:

  * ThermalTableDialog end-to-end — construction, the detail toggle, settings
    persistence and the CSV/clipboard exports. (tests/test_thermal_analysis.py
    drives the row builder against fakes; this drives the real dialog.)
  * TrajectoryResultDialog view-mode switching — scan points vs. all steps and
    relative vs. absolute energies, including how they gate the log scale.

Both dialogs derive settings_file from their module directory, so the module
__file__ is redirected into a temp dir — otherwise the suite writes a
settings.json into the package source tree.
"""

import os
import sys
import csv
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

T = gui_harness.load_isolated("thermal_analysis")
TR = gui_harness.load_isolated("traj_analysis")


def _thermal_data(**overrides):
    data = {
        "temperature": 298.15,
        "pressure": 1.0,
        "electronic_energy": -76.400000,
        "zpe": 0.021000,
        "thermal_energy": -76.375000,
        "enthalpy": -76.374000,
        "final_gibbs": -76.398000,
        "entropy_total": 0.021500,
    }
    data.update(overrides)
    return data


class _ThermalCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        saved = T.__file__
        T.__file__ = os.path.join(self.tmp, "thermal_analysis.py")
        self.addCleanup(lambda: setattr(T, "__file__", saved))

        self.context = MagicMock()
        self.host = MagicMock()
        self.host.context = self.context
        self.host.file_path = os.path.join(self.tmp, "job.out")

        self.dlg = T.ThermalTableDialog(self.host, _thermal_data())
        self.dlg.parent = lambda: self.host

    def _rows(self):
        table = self.dlg.table
        return [
            (table.item(r, 0).text(), table.item(r, 1).text())
            for r in range(table.rowCount())
            if table.item(r, 0) is not None
        ]

    def _properties(self):
        return [p for p, _ in self._rows()]


class TestThermalDialog(_ThermalCase):
    def test_the_table_is_populated(self):
        self.assertGreater(self.dlg.table.rowCount(), 0)

    def test_the_gibbs_energy_is_listed(self):
        self.assertTrue(any("Gibbs" in p for p in self._properties()))

    def test_details_are_hidden_by_default(self):
        self.assertFalse(self.dlg.chk_details.isChecked())

    def test_enabling_details_shows_more_rows(self):
        brief = self.dlg.table.rowCount()
        self.dlg.chk_details.setChecked(True)
        self.dlg.update_table()
        self.assertGreater(self.dlg.table.rowCount(), brief)

    def test_missing_values_do_not_break_the_table(self):
        dlg = T.ThermalTableDialog(self.host, {})
        dlg.update_table()  # must not raise

    def test_the_detail_setting_survives_a_round_trip(self):
        self.dlg.chk_details.setChecked(True)
        self.dlg.save_settings()

        fresh = T.ThermalTableDialog(self.host, _thermal_data())
        fresh.load_settings()
        self.assertTrue(fresh.chk_details.isChecked())

    def test_saving_preserves_unrelated_sections(self):
        with open(self.dlg.settings_file, "w", encoding="utf-8") as fh:
            json.dump({"mo_settings": {"last_preset": "Default"}}, fh)
        self.dlg.save_settings()
        saved = json.load(open(self.dlg.settings_file, encoding="utf-8"))
        self.assertEqual(saved["mo_settings"], {"last_preset": "Default"})

    def test_corrupt_settings_are_ignored(self):
        with open(self.dlg.settings_file, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.dlg.load_settings()  # must not raise

    def test_closing_persists_the_setting(self):
        self.dlg.chk_details.setChecked(True)
        self.dlg.closeEvent(MagicMock())
        saved = json.load(open(self.dlg.settings_file, encoding="utf-8"))
        self.assertTrue(saved["thermal_settings"]["show_details"])

    def test_escape_routes_through_close(self):
        with patch.object(self.dlg, "close") as closer:
            self.dlg.reject()
        closer.assert_called_once()

    def test_the_csv_mirrors_the_table(self):
        path = os.path.join(self.tmp, "thermo.csv")
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_csv()
        with open(path, encoding="utf-8") as fh:
            rows = [r for r in csv.reader(fh) if r]
        self.assertEqual(len(rows), self.dlg.table.rowCount() + 1)

    def test_a_cancelled_export_writes_nothing(self):
        with patch.object(T.QFileDialog, "getSaveFileName", return_value=("", "")):
            self.dlg.export_csv()
        self.assertFalse([f for f in os.listdir(self.tmp) if f.endswith(".csv")])

    def test_copying_puts_the_table_on_the_clipboard(self):
        clipboard = MagicMock()
        with patch.object(T.QApplication, "clipboard", return_value=clipboard):
            self.dlg.copy_table()
        clipboard.setText.assert_called_once()
        self.assertIn("Gibbs", clipboard.setText.call_args.args[0])


# ---------------------------------------------------------------------------
# Trajectory view modes
# ---------------------------------------------------------------------------


def _step(energy, x=0.0, stype="opt_cycle", scan_id=None, **extra):
    s = {
        "energy": energy,
        "atoms": ["O", "H"],
        "coords": [[0.0, 0.0, 0.0], [x, 0.0, 0.0]],
        "type": stype,
    }
    if scan_id is not None:
        s["scan_step_id"] = scan_id
    s.update(extra)
    return s


def _scan_steps():
    return [
        _step(-100.0, 1.0, "opt_cycle", scan_id=1),
        _step(-100.5, 1.15, "opt_final", scan_id=1),
        _step(-100.2, 1.3, "opt_cycle", scan_id=2),
        _step(-100.3, 1.35, "opt_final", scan_id=2),
    ]


def _neb_steps():
    return [
        _step(-100.0, 1.0, "neb_image", dist=0.0),
        _step(-99.5, 1.2, "neb_image", dist=0.5),
    ]


class _TrajModeCase(unittest.TestCase):
    def _make(self, steps):
        dlg = TR.TrajectoryResultDialog(MagicMock(), steps, context=MagicMock())
        # on_traj_mode_changed consults sender(); the real signal is not wired
        # under the harness, so stand in for the newly-checked button.
        checked = MagicMock()
        checked.isChecked.return_value = True
        dlg.sender = lambda: checked
        return dlg


class TestTrajModes(_TrajModeCase):
    def test_switching_to_all_steps_shows_every_cycle(self):
        dlg = self._make(_scan_steps())
        dlg.radio_scan.setChecked(False)
        dlg.on_traj_mode_changed()
        self.assertEqual(len(dlg.steps), 4)

    def test_switching_back_to_scan_points_collapses_again(self):
        dlg = self._make(_scan_steps())
        dlg.radio_scan.setChecked(False)
        dlg.on_traj_mode_changed()
        dlg.radio_scan.setChecked(True)
        dlg.on_traj_mode_changed()
        self.assertEqual(len(dlg.steps), 2)

    def test_scan_points_are_shown_relative(self):
        dlg = self._make(_scan_steps())
        dlg.radio_scan.setChecked(True)
        dlg.on_traj_mode_changed()
        self.assertTrue(dlg.show_relative)

    def test_all_steps_of_an_optimisation_are_shown_absolute(self):
        dlg = self._make(_scan_steps())
        dlg.radio_scan.setChecked(False)
        dlg.on_traj_mode_changed()
        self.assertFalse(dlg.show_relative)


    def test_an_unchecked_sender_is_ignored(self):
        dlg = self._make(_scan_steps())
        unchecked = MagicMock()
        unchecked.isChecked.return_value = False
        dlg.sender = lambda: unchecked
        before = len(dlg.steps)
        dlg.radio_scan.setChecked(False)
        dlg.on_traj_mode_changed()
        self.assertEqual(len(dlg.steps), before)

    def test_switching_mode_stops_playback(self):
        dlg = self._make(_scan_steps())
        dlg.timer = MagicMock()
        dlg.is_playing = True
        dlg.radio_scan.setChecked(False)
        dlg.on_traj_mode_changed()
        self.assertFalse(dlg.is_playing)

    def test_absolute_energies_disable_the_log_scale(self):
        dlg = self._make(_scan_steps())
        dlg.radio_rel.setChecked(False)
        dlg.on_toggle_mode()
        self.assertFalse(dlg.use_log_scale)
        self.assertFalse(dlg.chk_log.isEnabled())

    def test_relative_energies_allow_the_log_scale(self):
        dlg = self._make(_scan_steps())
        dlg.radio_rel.setChecked(True)
        dlg.chk_log.setChecked(True)
        dlg.on_toggle_mode()
        self.assertTrue(dlg.chk_log.isEnabled())
        self.assertTrue(dlg.use_log_scale)

    def test_switching_to_absolute_rescales_the_displayed_energies(self):
        dlg = self._make(_scan_steps())
        dlg.radio_rel.setChecked(False)
        dlg.on_toggle_mode()
        self.assertLess(min(dlg.display_energies), 0)

    def test_switching_to_relative_zeroes_the_minimum(self):
        dlg = self._make(_scan_steps())
        dlg.radio_rel.setChecked(True)
        dlg.on_toggle_mode()
        self.assertAlmostEqual(min(dlg.display_energies), 0.0)


if __name__ == "__main__":
    unittest.main()
