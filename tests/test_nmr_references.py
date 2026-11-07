"""
tests/test_nmr_references.py
Covers NMRDialog's reference-standard management: adding a custom reference
from the builder dialog (including the isotope-key normalisation that stores
"H" under "1H"), and deleting one — which must refuse to remove a built-in
standard and must ask before removing a custom one.
"""

import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

N = gui_harness.load_isolated("nmr_analysis")


def _data():
    return [
        {"atom_idx": 0, "atom_sym": "C", "shielding": 150.0},
        {"atom_idx": 1, "atom_sym": "H", "shielding": 30.0},
    ]


class _RefCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        self.context = MagicMock()
        self.host = MagicMock()
        self.host.context = self.context
        self.host.file_path = os.path.join(self.tmp, "job.out")

        self.dlg = N.NMRDialog(self.host, _data())
        self.dlg.parent_dlg = self.host
        self.dlg.settings_file = os.path.join(self.tmp, "settings.json")
        self.dlg.merged_peaks_file = os.path.join(self.tmp, "merges.json")
        self.dlg.current_nucleus = "H"

        patcher = patch.object(self.dlg, "plot_spectrum")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _builder(self, name, nucleus_data, accepted=True):
        """Stand in for CustomReferenceDialog."""
        dialog = MagicMock()
        dialog.exec.return_value = (
            N.QDialog.DialogCode.Accepted if accepted else "rejected"
        )
        dialog.get_reference_data.return_value = (name, nucleus_data)
        return patch.object(N, "CustomReferenceDialog", return_value=dialog)


class TestAddCustomReference(_RefCase):
    def test_a_custom_reference_is_stored(self):
        with self._builder("MyRef", {"H": {"delta_ref": 1.5, "sigma_ref": 30.0}}):
            self.dlg.add_custom_reference()
        self.assertIn("MyRef", self.dlg.reference_standards["1H"])

    def test_the_stored_values_are_kept(self):
        with self._builder("MyRef", {"H": {"delta_ref": 1.5, "sigma_ref": 30.0}}):
            self.dlg.add_custom_reference()
        self.assertEqual(
            self.dlg.reference_standards["1H"]["MyRef"],
            {"delta_ref": 1.5, "sigma_ref": 30.0},
        )

    def test_a_bare_element_is_normalised_to_its_isotope_key(self):
        with self._builder("MyRef", {"C": {"delta_ref": 0.0, "sigma_ref": 182.4}}):
            self.dlg.add_custom_reference()
        self.assertIn("MyRef", self.dlg.reference_standards["13C"])

    def test_a_reference_spanning_several_nuclei_is_stored_under_each(self):
        with self._builder(
            "Multi",
            {
                "H": {"delta_ref": 1.5, "sigma_ref": 30.0},
                "C": {"delta_ref": 0.0, "sigma_ref": 182.4},
            },
        ):
            self.dlg.add_custom_reference()
        self.assertIn("Multi", self.dlg.reference_standards["1H"])
        self.assertIn("Multi", self.dlg.reference_standards["13C"])

    def test_a_nucleus_with_no_standards_yet_gets_a_new_entry(self):
        self.dlg.reference_standards.pop("19F", None)
        with self._builder("FRef", {"F": {"delta_ref": 0.0, "sigma_ref": 100.0}}):
            self.dlg.add_custom_reference()
        self.assertIn("FRef", self.dlg.reference_standards["19F"])

    def test_a_custom_reference_is_persisted(self):
        with self._builder("MyRef", {"H": {"delta_ref": 1.5, "sigma_ref": 30.0}}):
            self.dlg.add_custom_reference()
        saved = json.load(open(self.dlg.settings_file, encoding="utf-8"))
        self.assertIn("MyRef", json.dumps(saved))

    def test_cancelling_the_builder_stores_nothing(self):
        before = json.dumps(self.dlg.reference_standards, sort_keys=True)
        with self._builder("MyRef", {"H": {}}, accepted=False):
            self.dlg.add_custom_reference()
        self.assertEqual(
            json.dumps(self.dlg.reference_standards, sort_keys=True), before
        )


class TestDeleteCustomReference(_RefCase):
    def _delete(self, confirm=True):
        yes = N.QMessageBox.StandardButton.Yes
        answer = yes if confirm else "declined"
        with patch.object(N.QMessageBox, "question", return_value=answer):
            with patch.object(N.QMessageBox, "warning") as warn:
                self.dlg.delete_custom_reference()
        return warn

    def _add_custom(self, name="MyRef"):
        self.dlg.reference_standards.setdefault("1H", {})[name] = {
            "delta_ref": 1.5,
            "sigma_ref": 30.0,
        }
        self.dlg.update_reference_combo()
        self.dlg.combo_ref.setCurrentText(name)

    def test_a_custom_reference_is_removed_once_confirmed(self):
        self._add_custom()
        self._delete(confirm=True)
        self.assertNotIn("MyRef", self.dlg.reference_standards["1H"])

    def test_declining_the_confirmation_keeps_it(self):
        self._add_custom()
        self._delete(confirm=False)
        self.assertIn("MyRef", self.dlg.reference_standards["1H"])

    def test_removal_is_persisted(self):
        self._add_custom()
        self._delete(confirm=True)
        saved = json.load(open(self.dlg.settings_file, encoding="utf-8"))
        self.assertNotIn("MyRef", json.dumps(saved))

    def test_removal_is_reported(self):
        self._add_custom()
        self._delete(confirm=True)
        self.context.show_status_message.assert_called()

    def test_a_builtin_standard_cannot_be_deleted(self):
        self.dlg.update_reference_combo()
        self.dlg.combo_ref.setCurrentText("TMS")
        warn = self._delete()
        warn.assert_called_once()
        self.assertIn("TMS", self.dlg.reference_standards["1H"])

    def test_the_no_reference_entry_cannot_be_deleted(self):
        self.dlg.update_reference_combo()
        self.dlg.combo_ref.setCurrentText("No Reference")
        warn = self._delete()
        warn.assert_called_once()

    def test_deleting_without_a_nucleus_selected_does_nothing(self):
        self.dlg.current_nucleus = ""
        with patch.object(N.QMessageBox, "question") as ask:
            self.dlg.delete_custom_reference()
        ask.assert_not_called()

    def test_deleting_with_no_reference_chosen_does_nothing(self):
        self.dlg.combo_ref.clear()
        with patch.object(N.QMessageBox, "question") as ask:
            self.dlg.delete_custom_reference()
        ask.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class TestDeletedReferenceRecalculates(_RefCase):
    def test_replacement_reference_updates_displayed_shifts_and_plot(self):
        self.dlg.reference_standards["1H"]["MyRef"] = {"sigma_ref": 40.0, "delta_ref": 0.0}
        self.dlg.update_reference_combo()
        self.dlg.combo_ref.setCurrentText("MyRef")
        self.dlg.displayed_data = [self.dlg.data[1]]
        self.dlg.on_ref_change()
        self.dlg.recalc()
        self.assertEqual(self.dlg.table.item(0, 3).text(), "10.00")
        self.dlg.plot_spectrum.reset_mock()
        with patch.object(N.QMessageBox, "question", return_value=N.QMessageBox.StandardButton.Yes):
            self.dlg.delete_custom_reference()
        self.assertEqual(self.dlg.combo_ref.currentText(), "TMS")
        self.assertEqual(self.dlg.table.item(0, 3).text(), "1.80")
        self.dlg.plot_spectrum.assert_called_once()
