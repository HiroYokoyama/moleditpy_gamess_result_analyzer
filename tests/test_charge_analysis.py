"""
tests/test_charge_analysis.py
Instantiation + method coverage for charge_analysis (GradientBar, ChargeDialog).

Drives the whole dialog under the headless Qt harness: construction (which runs
update_table), scheme/type switching, apply/reset coloring, 3D labels, CSV
export and close cleanup, against a fake GAMESSResultAnalyzerDialog host.
"""

import os
import sys
import types
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

_C = gui_harness.load_isolated("charge_analysis")
GradientBar = _C.GradientBar
ChargeDialog = _C.ChargeDialog


def _make_host(coords, n_atoms):
    v3d = types.SimpleNamespace(_plugin_color_overrides={})
    plotter = MagicMock()
    plotter.add_point_labels.return_value = object()
    plotter.add_mesh.return_value = object()
    mw = types.SimpleNamespace(
        plotter=plotter,
        view_3d_manager=v3d,
        current_mol=object(),
    )
    context = MagicMock()
    return types.SimpleNamespace(
        mw=mw,
        context=context,
        file_path=os.path.join(tempfile.gettempdir(), "job.out"),
        parser=types.SimpleNamespace(data={"coords": coords}),
    )


def _charges():
    return {
        "Mulliken": [
            {"atom_idx": 0, "atom_sym": "O", "charge": -0.42, "spin": 0.0},
            {"atom_idx": 1, "atom_sym": "H", "charge": 0.21, "spin": 0.0},
            {"atom_idx": 2, "atom_sym": "H", "charge": 0.21, "spin": 0.0},
        ],
        "Mayer": [
            {
                "atom_idx": 0,
                "atom_sym": "O",
                "charge": -0.4,
                "valency": 2.0,
                "bonded_valency": 1.9,
                "free_valency": 0.1,
            },
            {
                "atom_idx": 1,
                "atom_sym": "H",
                "charge": 0.2,
                "valency": 1.0,
                "bonded_valency": 1.0,
                "free_valency": 0.0,
            },
            {
                "atom_idx": 2,
                "atom_sym": "H",
                "charge": 0.2,
                "valency": 1.0,
                "bonded_valency": 1.0,
                "free_valency": 0.0,
            },
        ],
        "NBO": [
            {
                "atom_idx": 0,
                "atom_sym": "O",
                "charge": -0.5,
                "core": 2.0,
                "valence": 6.4,
                "rydberg": 0.1,
                "total": 8.5,
            },
            {
                "atom_idx": 1,
                "atom_sym": "H",
                "charge": 0.25,
                "core": 0.0,
                "valence": 0.74,
                "rydberg": 0.01,
                "total": 0.75,
            },
            {
                "atom_idx": 2,
                "atom_sym": "H",
                "charge": 0.25,
                "core": 0.0,
                "valence": 0.74,
                "rydberg": 0.01,
                "total": 0.75,
            },
        ],
    }


COORDS = [[0.0, 0.0, 0.0], [0.76, 0.59, 0.0], [-0.76, 0.59, 0.0]]


class TestGradientBar(unittest.TestCase):
    def test_construct_and_set_colors(self):
        bar = GradientBar(None, ["red", "white", "blue"])
        bar.set_colors(["blue", "red"])
        # paintEvent / get_gradient touch MagicMock painters; just ensure no raise
        bar.get_gradient()
        bar.paintEvent(MagicMock())


class TestChargeDialog(unittest.TestCase):
    def setUp(self):
        # charge_analysis derives its settings path from the module directory
        # on every save, so an unredirected run writes a settings.json into the
        # package source tree. Point the module at a temp dir instead.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        saved = _C.__file__
        _C.__file__ = os.path.join(self._tmp.name, "charge_analysis.py")
        self.addCleanup(lambda: setattr(_C, "__file__", saved))

    def _dialog(self, charges=None, coords=None, n=3):
        host = _make_host(coords if coords is not None else COORDS, n)
        dlg = ChargeDialog(host, charges if charges is not None else _charges())
        return dlg, host

    def test_construct_runs_update_table(self):
        dlg, _ = self._dialog()
        self.assertEqual(dlg.current_type, "Mulliken")

    def test_empty_charges(self):
        dlg, _ = self._dialog(charges={})
        self.assertEqual(dlg.current_type, "")

    def test_type_switch_rebuilds_table(self):
        dlg, _ = self._dialog()
        dlg.on_type_change("Mayer")
        self.assertEqual(dlg.current_type, "Mayer")
        dlg.on_type_change("NBO")
        self.assertEqual(dlg.current_type, "NBO")

    def test_scheme_change_updates_gradient_and_saves(self):
        dlg, _ = self._dialog()
        with tempfile.TemporaryDirectory() as d:
            dlg.settings_file = os.path.join(d, "settings.json")  # unused by save
            dlg.on_scheme_change("Blue(-) - White - Red(+)")
        self.assertEqual(dlg.current_scheme, "Blue(-) - White - Red(+)")

    def test_apply_colors_writes_overrides(self):
        dlg, host = self._dialog()
        dlg.apply_colors()
        overrides = host.mw.view_3d_manager._plugin_color_overrides
        self.assertEqual(set(overrides.keys()), {0, 1, 2})
        # each override is a hex color string
        self.assertTrue(all(v.startswith("#") for v in overrides.values()))
        host.context.draw_molecule_3d.assert_called()

    def test_apply_colors_two_color_scheme(self):
        dlg, host = self._dialog()
        dlg.on_scheme_change("Red(-) - Blue(+)")
        dlg.apply_colors()
        self.assertEqual(len(host.mw.view_3d_manager._plugin_color_overrides), 3)

    def test_apply_colors_all_zero_charges(self):
        charges = {"Z": [{"atom_idx": 0, "atom_sym": "C", "charge": 0.0}]}
        dlg, host = self._dialog(charges=charges, coords=[[0, 0, 0]], n=1)
        dlg.apply_colors()  # max_c==0 branch
        self.assertIn(0, host.mw.view_3d_manager._plugin_color_overrides)

    def test_asymmetric_charges_share_zero_centered_color_domain(self):
        charges = {"Mulliken": [
            {"atom_idx": i, "atom_sym": "C", "charge": q}
            for i, q in enumerate([-0.8, 0.2, 0.0])
        ]}
        dlg, host = self._dialog(charges=charges)
        dlg.current_scheme = "Regression"
        dlg.schemes["Regression"] = ["red", "white", "blue"]
        dlg.apply_colors()
        overrides = host.mw.view_3d_manager._plugin_color_overrides
        self.assertEqual(overrides[2], "#ffffff")
        self.assertEqual(overrides[0], "#ff0000")
        self.assertEqual(overrides[1], "#bfbfff")
        self.assertEqual(host.mw.plotter.add_mesh.call_args.kwargs["clim"], [-0.8, 0.8])

    def test_reset_colors_clears_overrides(self):
        dlg, host = self._dialog()
        dlg.apply_colors()
        dlg.reset_colors()
        self.assertEqual(host.mw.view_3d_manager._plugin_color_overrides, {})
        host.context.show_status_message.assert_called()

    def test_reset_colors_notifies_host_status_bar(self):
        dlg, host = self._dialog()
        dlg.apply_colors()
        dlg.reset_colors()
        host.context.show_status_message.assert_called_with(
            "Colors reset to CPK default.", 5000
        )

    def test_toggle_labels_on_then_off(self):
        dlg, host = self._dialog()
        dlg.chk_show_labels = _Check(True)
        dlg.toggle_labels()
        self.assertTrue(host.mw.plotter.add_point_labels.called)
        dlg.chk_show_labels = _Check(False)
        dlg.toggle_labels()

    def test_toggle_labels_coord_mismatch_warns(self):
        dlg, host = self._dialog(coords=[[0, 0, 0]])  # 1 coord vs 3 atoms
        dlg.chk_show_labels = _Check(True)
        dlg.toggle_labels()  # hits the "no coordinates" warning branch

    def test_apply_colors_with_labels_enabled(self):
        dlg, host = self._dialog()
        dlg.chk_show_labels = _Check(True)
        dlg.apply_colors()
        host.mw.plotter.add_point_labels.assert_called()

    def test_export_csv_writes_file(self):
        dlg, host = self._dialog()
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "charges.csv")
            saved = _patch_savedialog(out)
            try:
                dlg.export_csv()
            finally:
                saved()
            self.assertTrue(os.path.exists(out))
            with open(out, encoding="utf-8") as f:
                head = f.readline()
        self.assertIn("Charge", head)

    def test_export_csv_notifies_host_status_bar(self):
        dlg, host = self._dialog()
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "charges.csv")
            saved = _patch_savedialog(out)
            try:
                dlg.export_csv()
            finally:
                saved()
        host.context.show_status_message.assert_called_with(
            f"Data exported to {out}", 5000
        )

    def test_export_csv_without_host_does_not_raise(self):
        dlg, host = self._dialog()
        host.context = None
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "charges.csv")
            saved = _patch_savedialog(out)
            try:
                dlg.export_csv()  # must not raise even with no host reachable
            finally:
                saved()
            self.assertTrue(os.path.exists(out))

    def test_export_csv_cancelled(self):
        dlg, _ = self._dialog()
        saved = _patch_savedialog("")
        try:
            dlg.export_csv()
        finally:
            saved()

    def test_export_mayer_headers(self):
        dlg, _ = self._dialog()
        dlg.on_type_change("Mayer")
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "mayer.csv")
            saved = _patch_savedialog(out)
            try:
                dlg.export_csv()
            finally:
                saved()
            with open(out, encoding="utf-8") as f:
                head = f.readline()
        self.assertIn("Valency", head)

    def test_close_event_saves_and_cleans(self):
        dlg, host = self._dialog()
        dlg.apply_colors()
        dlg.closeEvent(MagicMock())
        # scalar bar + render invoked during cleanup
        host.mw.plotter.render.assert_called()

    def test_reject_calls_close(self):
        dlg, _ = self._dialog()
        dlg.close = MagicMock()
        dlg.reject()
        dlg.close.assert_called_once()

    # -- columns are unioned over all rows, not taken from row 0 -------------
    # GAMESS omits trailing columns for some atoms; keying off the first row
    # dropped the property from both the table and the export entirely.

    @staticmethod
    def _ragged():
        return {
            "Hirshfeld": [
                {"atom_idx": 0, "atom_sym": "C", "charge": -0.1},
                {"atom_idx": 1, "atom_sym": "H", "charge": 0.05, "spin": 0.42},
                {"atom_idx": 2, "atom_sym": "H", "charge": 0.05},
            ]
        }

    def test_a_column_missing_from_the_first_row_still_gets_a_header(self):
        dlg, _ = self._dialog(charges=self._ragged())
        headers = [
            dlg.table.horizontalHeaderItem(c).text()
            for c in range(dlg.table.columnCount())
        ]
        self.assertIn("Spin", headers)

    def test_the_late_column_carries_its_value(self):
        dlg, _ = self._dialog(charges=self._ragged())
        headers = [
            dlg.table.horizontalHeaderItem(c).text()
            for c in range(dlg.table.columnCount())
        ]
        col = headers.index("Spin")
        self.assertEqual(dlg.table.item(1, col).text(), "0.4200")
        self.assertEqual(dlg.table.item(0, col).text(), "")

    def test_the_export_header_also_unions_the_columns(self):
        dlg, _ = self._dialog(charges=self._ragged())
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "ragged.csv")
            saved = _patch_savedialog(out)
            try:
                dlg.export_csv()
            finally:
                saved()
            with open(out, encoding="utf-8") as f:
                head = f.readline()
        self.assertIn("spin", head.lower())


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _Check:
    def __init__(self, v):
        self._v = v

    def isChecked(self):
        return self._v

    def setChecked(self, v):
        self._v = bool(v)


def _patch_savedialog(return_path):
    saved = _C.QFileDialog
    _C.QFileDialog = MagicMock()
    _C.QFileDialog.getSaveFileName.return_value = (return_path, "CSV Files (*.csv)")

    def restore():
        _C.QFileDialog = saved

    return restore


# ---------------------------------------------------------------------------
# settings.py helper round-trip (Task A conversion)
# ---------------------------------------------------------------------------


class TestChargeSettingsHelpers(unittest.TestCase):
    def setUp(self):
        import json

        self._json = json
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        saved = _C.__file__
        _C.__file__ = os.path.join(self._tmp.name, "charge_analysis.py")
        self.addCleanup(lambda: setattr(_C, "__file__", saved))
        self.path = os.path.join(self._tmp.name, "settings.json")

    def test_save_preserves_several_other_dialogs_sections(self):
        others = {
            "mo_settings": {"iso": 0.02},
            "nmr_settings": {"ref": "TMS"},
            "thermal_settings": {"show_details": True},
        }
        with open(self.path, "w", encoding="utf-8") as fh:
            self._json.dump(others, fh)

        host = _make_host(COORDS, 3)
        dlg = ChargeDialog(host, _charges())
        dlg.on_scheme_change(
            "Blue(-) - White - Red(+)"
        )  # saves via save_custom_schemes

        with open(self.path, encoding="utf-8") as fh:
            on_disk = self._json.load(fh)
        for key, val in others.items():
            self.assertEqual(on_disk[key], val)
        self.assertIn("charge_settings", on_disk)

    def test_round_trips_through_the_shared_helpers(self):
        S = gui_harness.load_isolated("settings")
        host = _make_host(COORDS, 3)
        dlg = ChargeDialog(host, _charges())
        dlg.save_settings()

        section = S.load_section(self.path, "charge_settings")
        self.assertEqual(section["last_charge_scheme"], dlg.current_scheme)

        fresh = ChargeDialog(_make_host(COORDS, 3), _charges())
        self.assertEqual(fresh.current_scheme, dlg.current_scheme)

    def test_malformed_custom_schemes_do_not_crash_construction(self):
        # A non-dict entry makes scheme_data.get(...) raise AttributeError;
        # the narrowed handler in __init__ must swallow it, not crash.
        with open(self.path, "w", encoding="utf-8") as fh:
            self._json.dump(
                {"charge_settings": {"custom_color_schemes": ["not_a_dict"]}}, fh
            )
        dlg = ChargeDialog(_make_host(COORDS, 3), _charges())
        self.assertEqual(dlg.current_scheme, "Red(-) - White - Blue(+)")


if __name__ == "__main__":
    unittest.main()


class TestMissingSavedScheme(unittest.TestCase):
    def test_a_vanished_saved_scheme_falls_back_instead_of_crashing(self):
        import json

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        saved = _C.__file__
        _C.__file__ = os.path.join(tmp.name, "charge_analysis.py")
        self.addCleanup(lambda: setattr(_C, "__file__", saved))
        with open(os.path.join(tmp.name, "settings.json"), "w", encoding="utf-8") as fh:
            json.dump({"charge_settings": {"last_charge_scheme": "Custom: Gone"}}, fh)

        dlg = ChargeDialog(_make_host([[0, 0, 0]] * 3, 3), _charges())
        self.assertEqual(dlg.current_scheme, "Red(-) - White - Blue(+)")
