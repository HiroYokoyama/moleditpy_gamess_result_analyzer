"""
tests/test_small_dialogs.py
Coverage for the smaller analysis dialogs under the headless Qt harness:

  * CustomReferenceDialog  — building NMR reference standards
  * DipoleDialog           — dipole arrow placement in the 3D view
  * SCFTraceDialog         — SCF convergence traces and their CSV export

Each dialog derives ``settings_file`` from its own module directory, so the
module ``__file__`` is redirected into a temp dir — otherwise the suite writes
a settings.json into the package source tree.
"""

import os
import sys
import csv
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

REF = gui_harness.load_isolated("nmr_custom_ref_dialog")
DIP = gui_harness.load_isolated("dipole_analysis")
SCF = gui_harness.load_isolated("scf_analysis")


class _TempModuleDir(unittest.TestCase):
    """Points a loaded module's __file__ at a temp dir for the test's lifetime."""

    MODULE = None

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        if self.MODULE is not None:
            saved = self.MODULE.__file__
            self.MODULE.__file__ = os.path.join(self.tmp, "mod.py")
            self.addCleanup(lambda: setattr(self.MODULE, "__file__", saved))


# ---------------------------------------------------------------------------
# CustomReferenceDialog
# ---------------------------------------------------------------------------


class TestCustomReference(_TempModuleDir):
    MODULE = REF

    def setUp(self):
        super().setUp()
        self.dlg = REF.CustomReferenceDialog(MagicMock(), existing_nuclei=["1H", "13C"])

    def test_it_starts_with_one_nucleus_row(self):
        self.assertEqual(len(self.dlg.nucleus_widgets), 1)

    def test_adding_a_row_extends_the_form(self):
        self.dlg.add_nucleus_row()
        self.assertEqual(len(self.dlg.nucleus_widgets), 2)

    def test_removing_a_row_shrinks_the_form(self):
        self.dlg.add_nucleus_row()
        row_widget = self.dlg.nucleus_widgets[1][3]
        self.dlg.remove_nucleus_row(row_widget)
        self.assertEqual(len(self.dlg.nucleus_widgets), 1)

    def test_the_last_row_cannot_be_removed(self):
        row_widget = self.dlg.nucleus_widgets[0][3]
        with patch.object(REF.QMessageBox, "warning") as warn:
            self.dlg.remove_nucleus_row(row_widget)
        warn.assert_called_once()
        self.assertEqual(len(self.dlg.nucleus_widgets), 1)

    def test_removing_an_unknown_row_leaves_the_form_alone(self):
        self.dlg.add_nucleus_row()
        self.dlg.remove_nucleus_row(MagicMock())
        self.assertEqual(len(self.dlg.nucleus_widgets), 2)

    def test_the_configured_reference_is_returned(self):
        self.dlg.edit_name.setText("My Standard")
        combo, delta, sigma, _ = self.dlg.nucleus_widgets[0]
        combo.setCurrentText("1H")
        delta.setValue(7.26)
        sigma.setValue(24.5)

        name, data = self.dlg.get_reference_data()
        self.assertEqual(name, "My Standard")
        self.assertEqual(data, {"1H": {"delta_ref": 7.26, "sigma_ref": 24.5}})

    def test_several_nuclei_are_returned_together(self):
        self.dlg.edit_name.setText("Multi")
        self.dlg.add_nucleus_row()
        for row, (nucleus, d, s) in zip(
            self.dlg.nucleus_widgets, [("1H", 7.26, 24.5), ("13C", 77.16, 105.2)]
        ):
            row[0].setCurrentText(nucleus)
            row[1].setValue(d)
            row[2].setValue(s)

        _, data = self.dlg.get_reference_data()
        self.assertEqual(set(data), {"1H", "13C"})

    def test_the_reference_name_is_trimmed(self):
        self.dlg.edit_name.setText("  Padded  ")
        name, _ = self.dlg.get_reference_data()
        self.assertEqual(name, "Padded")

    def test_an_unnamed_reference_is_rejected(self):
        self.dlg.edit_name.setText("   ")
        with patch.object(REF.QMessageBox, "warning") as warn:
            with patch.object(self.dlg, "accept") as accept:
                self.dlg.accept_reference()
        warn.assert_called_once()
        accept.assert_not_called()

    def test_a_duplicated_nucleus_is_rejected(self):
        self.dlg.edit_name.setText("Dup")
        self.dlg.add_nucleus_row()
        for row in self.dlg.nucleus_widgets:
            row[0].setCurrentText("1H")

        with patch.object(REF.QMessageBox, "warning") as warn:
            with patch.object(self.dlg, "accept") as accept:
                self.dlg.accept_reference()
        warn.assert_called_once()
        accept.assert_not_called()

    def test_a_valid_reference_is_accepted(self):
        self.dlg.edit_name.setText("Good")
        self.dlg.nucleus_widgets[0][0].setCurrentText("1H")
        with patch.object(self.dlg, "accept") as accept:
            self.dlg.accept_reference()
        accept.assert_called_once()


# ---------------------------------------------------------------------------
# DipoleDialog
# ---------------------------------------------------------------------------


class TestDipole(_TempModuleDir):
    MODULE = DIP

    def setUp(self):
        super().setUp()
        self.plotter = MagicMock()
        self.host = MagicMock()
        self.host.mw.plotter = self.plotter
        self.host.parser.data = {"coords": [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]]}
        self.dipole = {"vector": [1.0, 0.0, 0.0], "magnitude": 1.0}
        self.dlg = DIP.DipoleDialog(self.host, self.dipole)

    def test_the_arrow_is_hidden_by_default(self):
        self.assertFalse(self.dlg.chk_show.isChecked())
        self.assertIsNone(self.dlg.arrow_actor)

    def test_enabling_the_arrow_adds_it_to_the_scene(self):
        self.dlg.chk_show.setChecked(True)
        self.dlg.update_view()
        self.plotter.add_mesh.assert_called_once()
        self.assertIsNotNone(self.dlg.arrow_actor)

    def test_disabling_the_arrow_removes_it(self):
        self.dlg.chk_show.setChecked(True)
        self.dlg.update_view()
        actor = self.dlg.arrow_actor

        self.dlg.chk_show.setChecked(False)
        self.dlg.update_view()
        self.plotter.remove_actor.assert_called_once_with(actor)
        self.assertIsNone(self.dlg.arrow_actor)

    def test_redrawing_replaces_the_previous_arrow(self):
        self.dlg.chk_show.setChecked(True)
        self.dlg.update_view()
        self.dlg.update_view()
        self.plotter.remove_actor.assert_called_once()
        self.assertEqual(self.plotter.add_mesh.call_count, 2)

    def test_a_vanishing_dipole_draws_nothing(self):
        dlg = DIP.DipoleDialog(self.host, {"vector": [0.0, 0.0, 0.0], "magnitude": 0.0})
        dlg.chk_show.setChecked(True)
        dlg.update_view()
        self.plotter.add_mesh.assert_not_called()

    def test_a_missing_geometry_is_tolerated(self):
        self.host.parser.data = {"coords": []}
        self.dlg.chk_show.setChecked(True)
        self.dlg.update_view()
        self.plotter.add_mesh.assert_called_once()

    def test_changing_the_resolution_redraws(self):
        self.dlg.chk_show.setChecked(True)
        with patch.object(self.dlg, "update_view") as upd:
            self.dlg.on_res_changed(32)
        self.assertEqual(self.dlg.arrow_res, 32)
        upd.assert_called_once()

    def test_picking_a_colour_redraws(self):
        colour = MagicMock()
        colour.isValid.return_value = True
        colour.name.return_value = "#00ff00"
        with patch.object(DIP.QColorDialog, "getColor", return_value=colour):
            with patch.object(self.dlg, "update_view") as upd:
                self.dlg.pick_color()
        self.assertEqual(self.dlg.arrow_color, "#00ff00")
        upd.assert_called_once()

    def test_cancelling_the_colour_picker_changes_nothing(self):
        colour = MagicMock()
        colour.isValid.return_value = False
        before = self.dlg.arrow_color
        with patch.object(DIP.QColorDialog, "getColor", return_value=colour):
            with patch.object(self.dlg, "update_view") as upd:
                self.dlg.pick_color()
        self.assertEqual(self.dlg.arrow_color, before)
        upd.assert_not_called()

    def test_escape_routes_through_close(self):
        with patch.object(self.dlg, "close") as closer:
            self.dlg.reject()
        closer.assert_called_once()

    def test_settings_round_trip(self):
        self.dlg.arrow_color = "#123456"
        self.dlg.spin_res.setValue(16)
        self.dlg.save_settings()

        fresh = DIP.DipoleDialog(self.host, self.dipole)
        fresh.load_settings()
        self.assertEqual(fresh.arrow_color, "#123456")


# ---------------------------------------------------------------------------
# SCFTraceDialog
# ---------------------------------------------------------------------------


def _traces():
    return [
        {
            "step": "Geometry 1",
            "iterations": [
                {"iter": 1, "energy": -76.10},
                {"iter": 2, "energy": -76.20},
                {"iter": 3, "energy": -76.25},
            ],
        },
        {
            "step": "Geometry 2",
            "iterations": [
                {"iter": 1, "energy": -76.26},
                {"iter": 2, "energy": -76.27},
            ],
        },
    ]


class TestSCFTrace(_TempModuleDir):
    MODULE = SCF

    def setUp(self):
        super().setUp()
        self.host = MagicMock()
        self.host.file_path = os.path.join(self.tmp, "job.out")
        # spin_s2 matches parser output: {"actual", "ideal", "contamination"}
        self.dlg = SCF.SCFTraceDialog(
            self.host,
            _traces(),
            dispersion=-0.01,
            spin_s2={"actual": 0.7538, "ideal": 0.75, "contamination": 0.0038},
        )
        self.dlg.parent = lambda: self.host

    def test_every_block_is_offered_plus_an_all_option(self):
        self.assertEqual(self.dlg.combo_steps.count(), 3)

    def test_the_all_blocks_option_comes_first(self):
        self.assertEqual(self.dlg.combo_steps.itemText(0), "All Blocks")
        self.assertEqual(self.dlg.combo_steps.itemData(0), -1)

    def test_blocks_are_labelled_by_their_step(self):
        self.assertIn("Geometry 1", self.dlg.combo_steps.itemText(1))

    def test_plotting_all_blocks_does_not_raise(self):
        self.dlg.combo_steps.setCurrentIndex(0)
        self.dlg.update_plot()

    def test_plotting_a_single_block_does_not_raise(self):
        self.dlg.combo_steps.setCurrentIndex(1)
        self.dlg.update_plot()

    def test_an_empty_trace_list_is_tolerated(self):
        dlg = SCF.SCFTraceDialog(self.host, [])
        dlg.update_plot()
        self.assertEqual(dlg.combo_steps.count(), 1)

    def test_exporting_all_blocks_writes_every_iteration(self):
        path = os.path.join(self.tmp, "scf.csv")
        self.dlg.combo_steps.setCurrentIndex(0)
        with patch.object(SCF.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_csv()
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(len(rows), 6)  # header + 3 + 2 iterations

    def test_the_combined_export_numbers_iterations_cumulatively(self):
        path = os.path.join(self.tmp, "scf.csv")
        self.dlg.combo_steps.setCurrentIndex(0)
        with patch.object(SCF.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_csv()
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.reader(fh))[1:]
        self.assertEqual([r[0] for r in rows], ["1", "2", "3", "4", "5"])

    def test_the_combined_export_records_the_owning_block(self):
        path = os.path.join(self.tmp, "scf.csv")
        self.dlg.combo_steps.setCurrentIndex(0)
        with patch.object(SCF.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_csv()
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.reader(fh))[1:]
        self.assertEqual(rows[-1][1], "Geometry 2")

    def test_exporting_one_block_writes_only_its_iterations(self):
        path = os.path.join(self.tmp, "scf.csv")
        self.dlg.combo_steps.setCurrentIndex(2)  # Geometry 2
        with patch.object(SCF.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_csv()
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(len(rows), 3)  # header + 2 iterations

    def test_a_cancelled_export_writes_nothing(self):
        with patch.object(SCF.QFileDialog, "getSaveFileName", return_value=("", "")):
            self.dlg.export_csv()
        self.assertFalse([f for f in os.listdir(self.tmp) if f.endswith(".csv")])

    def test_escape_routes_through_close(self):
        with patch.object(self.dlg, "close") as closer:
            self.dlg.reject()
        closer.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class TestCustomReferenceValidation(_TempModuleDir):
    MODULE = REF

    def _dialog(self, parent=None):
        return REF.CustomReferenceDialog(parent or MagicMock(), existing_nuclei=["1H"])

    def test_the_reserved_names_are_refused(self):
        for name in ("Custom", "No Reference"):
            dlg = self._dialog()
            dlg.edit_name.setText(name)
            with patch.object(REF.QMessageBox, "warning") as warn, patch.object(
                dlg, "accept"
            ) as acc:
                dlg.accept_reference()
            acc.assert_not_called()
            self.assertIn("reserved", warn.call_args[0][2])

    def test_symbol_and_isotope_count_as_the_same_nucleus(self):
        nmr = MagicMock()
        nmr.get_nucleus_key = lambda s: {"H": "1H"}.get(s, s)
        dlg = self._dialog()
        dlg.parent = lambda: nmr
        dlg.edit_name.setText("Mine")
        dlg.add_nucleus_row()
        dlg.nucleus_widgets[0][0].setCurrentText("H")
        dlg.nucleus_widgets[1][0].setCurrentText("1H")
        with patch.object(REF.QMessageBox, "warning") as warn, patch.object(
            dlg, "accept"
        ) as acc:
            dlg.accept_reference()
        acc.assert_not_called()
        self.assertIn("multiple times", warn.call_args[0][2])


class TestSCFExportFailure(TestSCFTrace):
    def test_a_failed_export_is_reported_to_the_user(self):
        blocker = os.path.join(self.tmp, "a_file")
        with open(blocker, "w", encoding="utf-8") as fh:
            fh.write("x")
        bad = os.path.join(blocker, "scf.csv")  # a path under a file
        with patch.object(
            SCF.QFileDialog, "getSaveFileName", return_value=(bad, "")
        ), patch.object(SCF.QMessageBox, "critical") as crit:
            self.dlg.export_csv()
        crit.assert_called_once()
