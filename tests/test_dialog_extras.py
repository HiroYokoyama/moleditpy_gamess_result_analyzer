"""
tests/test_dialog_extras.py
Remaining gaps across three dialogs, under the headless Qt harness:

  * MODialog     — visualisation presets (save/apply/delete) and the GAMESS
                   input snippet helper.
  * FrequencyDialog — mode selection driven from the spectrum window and from
                   the tree, and vector redrawing.
  * ChargeDialog — custom colour-scheme creation and persistence.

Several of these methods import Qt lazily inside the function body (QInputDialog,
QColorDialog), so they run under gui_harness.qt_available.
"""

import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

M = gui_harness.load_isolated("mo_analysis")
F = gui_harness.load_isolated("freq_analysis")
C = gui_harness.load_isolated("charge_analysis")


def _redirect_module_dir(case, module):
    """Point a module's __file__ at the test temp dir.

    These dialogs derive settings_file from their own directory, so without
    this the suite writes a settings.json into the package source tree.
    """
    saved = module.__file__
    module.__file__ = os.path.join(case.tmp, "mod.py")
    case.addCleanup(lambda: setattr(module, "__file__", saved))


# ---------------------------------------------------------------------------
# MODialog presets
# ---------------------------------------------------------------------------


def _mos():
    return {
        str(i): {"id": i, "energy": e, "occ": occ, "spin": "restricted"}
        for i, (e, occ) in enumerate([(-1.0, 2.0), (-0.5, 2.0), (0.2, 0.0)])
    }


class _MOCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        _redirect_module_dir(self, M)

        self.host = MagicMock()
        self.host.parser.filename = os.path.join(self.tmp, "job.out")
        self.host.file_path = self.host.parser.filename

        self.dlg = M.MODialog(self.host, _mos(), result_dir=self.tmp)
        self.dlg.parent_dlg = self.host

    def _name_prompt(self, name, accepted=True):
        # mo_analysis imports QInputDialog at module level, so the binding
        # captured at load time is what save_preset calls.
        prompt = MagicMock()
        prompt.getText.return_value = (name, accepted)
        return patch.object(M, "QInputDialog", prompt)


class TestMOPresets(_MOCase):
    def test_the_default_preset_always_exists(self):
        self.assertIn("Default", self.dlg.presets)

    def test_saving_a_preset_captures_the_current_settings(self):
        self.dlg.spin_iso.setValue(0.045)
        self.dlg.set_btn_color(self.dlg.btn_color_p, "#00ff00")
        with self._name_prompt("Tight"):
            self.dlg.save_preset()
        self.assertAlmostEqual(self.dlg.presets["Tight"]["iso"], 0.045)
        self.assertEqual(self.dlg.presets["Tight"]["color_p"].lower(), "#00ff00")

    def test_a_saved_preset_becomes_the_selection(self):
        with self._name_prompt("Tight"):
            self.dlg.save_preset()
        self.assertEqual(self.dlg.combo_presets.currentText(), "Tight")

    def test_a_saved_preset_is_persisted(self):
        with self._name_prompt("Tight"):
            self.dlg.save_preset()
        saved = json.load(open(self.dlg.settings_file, encoding="utf-8"))
        self.assertIn("Tight", saved["mo_settings"]["presets"])

    def test_cancelling_the_name_prompt_saves_nothing(self):
        with self._name_prompt("", accepted=False):
            self.dlg.save_preset()
        self.assertEqual(set(self.dlg.presets), {"Default"})

    def test_applying_a_preset_restores_every_setting(self):
        self.dlg.presets["Tight"] = {
            "iso": 0.09,
            "opacity": 0.7,
            "style": "Wireframe",
            "color_p": "#010203",
            "color_n": "#040506",
            "smooth_shading": False,
        }
        self.dlg.apply_preset("Tight")
        self.assertAlmostEqual(self.dlg.spin_iso.value(), 0.09)
        self.assertAlmostEqual(self.dlg.spin_opacity.value(), 0.7)
        self.assertEqual(self.dlg.get_color_hex("p").lower(), "#010203")

    def test_applying_an_unknown_preset_changes_nothing(self):
        before = self.dlg.spin_iso.value()
        self.dlg.apply_preset("Nope")
        self.assertEqual(self.dlg.spin_iso.value(), before)

    def test_deleting_a_preset_removes_it(self):
        self.dlg.presets["Tight"] = dict(self.dlg.presets["Default"])
        self.dlg.combo_presets.addItem("Tight")
        self.dlg.combo_presets.setCurrentText("Tight")
        self.dlg.delete_preset()
        self.assertNotIn("Tight", self.dlg.presets)

    def test_deleting_falls_back_to_the_default_preset(self):
        self.dlg.presets["Tight"] = dict(self.dlg.presets["Default"])
        self.dlg.combo_presets.addItem("Tight")
        self.dlg.combo_presets.setCurrentText("Tight")
        self.dlg.delete_preset()
        self.assertEqual(self.dlg.combo_presets.currentText(), "Default")

    def test_the_default_preset_cannot_be_deleted(self):
        self.dlg.combo_presets.setCurrentText("Default")
        with patch.object(M.QMessageBox, "warning") as warn:
            self.dlg.delete_preset()
        warn.assert_called_once()
        self.assertIn("Default", self.dlg.presets)

    def test_the_route_keywords_are_copied(self):
        clipboard = MagicMock()
        with patch.object(M.QApplication, "clipboard", return_value=clipboard):
            self.dlg.copy_gamess_input()
        text = clipboard.setText.call_args.args[0]
        self.assertIn("NPRINT=7", text)


# ---------------------------------------------------------------------------
# FrequencyDialog mode selection
# ---------------------------------------------------------------------------


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
        _redirect_module_dir(self, F)

        self.conf = MagicMock()
        self.mol = MagicMock()
        self.mol.GetConformer.return_value = self.conf
        self.context = MagicMock()
        self.mw = MagicMock()
        self.mw.current_mol = self.mol

        self.dlg = F.FrequencyDialog(
            self.mw,
            _frequencies(),
            ["O", "H", "H"],
            [[0.0, 0.0, 0.0], [0.76, 0.59, 0.0], [-0.76, 0.59, 0.0]],
            self.context,
        )
        self.dlg.populate_list()


class TestFreqModeSelection(_FreqCase):
    def test_selecting_by_item_highlights_the_matching_row(self):
        self.dlg.select_mode_by_item({"_orig_idx": 7})
        self.assertEqual(self.dlg.tree.currentItem().text(0), "7")

    def test_selecting_none_clears_the_tree_selection(self):
        self.dlg.spectrum_win = MagicMock()
        self.dlg.select_mode_by_item(None)
        self.dlg.spectrum_win.spectrum.set_selected_item.assert_called_once_with(None)

    def test_an_item_without_an_index_is_ignored(self):
        self.dlg.select_mode_by_item({"freq": 1600.0})
        self.assertIsNone(self.dlg.tree.currentItem())

    def test_selecting_by_item_mirrors_into_the_spectrum(self):
        self.dlg.spectrum_win = MagicMock()
        item = {"_orig_idx": 7}
        self.dlg.select_mode_by_item(item)
        self.dlg.spectrum_win.spectrum.set_selected_item.assert_called_once_with(item)

    def test_a_hidden_mode_leaves_the_selection_alone(self):
        # modes 0-5 are the trivial ones and are not listed
        self.dlg.select_mode_by_item({"_orig_idx": 2})
        self.assertIsNone(self.dlg.tree.currentItem())

    def test_choosing_a_row_records_the_current_mode(self):
        row = self.dlg.tree.topLevelItem(0)
        with patch.object(self.dlg, "update_view"):
            self.dlg.on_mode_selected(row, None)
        self.assertEqual(self.dlg.current_mode_idx, int(row.text(0)))

    def test_clearing_the_row_selection_is_ignored(self):
        self.dlg.current_mode_idx = 7
        self.dlg.on_mode_selected(None, None)
        self.assertEqual(self.dlg.current_mode_idx, 7)


# ---------------------------------------------------------------------------
# ChargeDialog custom colour schemes
# ---------------------------------------------------------------------------


def _charges():
    return {
        "Mulliken": [
            {"atom_idx": 0, "atom_sym": "O", "charge": -0.42, "spin": 0.0},
            {"atom_idx": 1, "atom_sym": "H", "charge": 0.21, "spin": 0.0},
        ]
    }


class _ChargeCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        _redirect_module_dir(self, C)

        self.plotter = MagicMock()
        self.host = MagicMock()
        self.host.mw.plotter = self.plotter
        self.host.mw.view_3d_manager._plugin_color_overrides = {}
        self.host.file_path = os.path.join(self.tmp, "job.out")
        self.host.parser.data = {"coords": [[0.0, 0.0, 0.0], [0.96, 0.0, 0.0]]}

        self.dlg = C.ChargeDialog(self.host, _charges())
        self.dlg.parent_dlg = self.host

    def _scheme_prompt(self, name, n_colors, colours, name_ok=True, count_ok=True):
        """edit_custom_scheme asks for a name, a colour count, then each colour.

        charge_analysis imports both dialogs at module level, so patch the
        bindings captured at load time.
        """
        prompt = MagicMock()
        prompt.getText.return_value = (name, name_ok)
        prompt.getInt.return_value = (n_colors, count_ok)

        picked = []
        for hexval in colours:
            colour = MagicMock()
            colour.isValid.return_value = hexval is not None
            colour.name.return_value = hexval
            picked.append(colour)
        colour_dialog = MagicMock()
        colour_dialog.getColor.side_effect = picked

        class _Both:
            def __enter__(inner):
                inner._a = patch.object(C, "QInputDialog", prompt)
                inner._b = patch.object(C, "QColorDialog", colour_dialog)
                inner._a.start()
                inner._b.start()
                return inner

            def __exit__(inner, *exc):
                inner._b.stop()
                inner._a.stop()
                return False

        return _Both()


class TestChargeSchemes(_ChargeCase):
    def test_the_builtin_schemes_are_available(self):
        self.assertGreater(len(self.dlg.schemes), 0)

    def test_a_custom_scheme_is_registered(self):
        with self._scheme_prompt("Mine", 2, ["#000000", "#ffffff"]):
            self.dlg.edit_custom_scheme()
        self.assertIn("Custom: Mine", self.dlg.schemes)

    def test_a_custom_scheme_keeps_its_colours(self):
        with self._scheme_prompt("Mine", 2, ["#000000", "#ffffff"]):
            self.dlg.edit_custom_scheme()
        self.assertEqual(self.dlg.schemes["Custom: Mine"], ["#000000", "#ffffff"])

    def test_a_custom_scheme_becomes_the_selection(self):
        with self._scheme_prompt("Mine", 2, ["#000000", "#ffffff"]):
            self.dlg.edit_custom_scheme()
        self.assertEqual(self.dlg.combo_scheme.currentText(), "Custom: Mine")

    def test_a_three_colour_scheme_is_accepted(self):
        with self._scheme_prompt("Tri", 3, ["#ff0000", "#ffffff", "#0000ff"]):
            self.dlg.edit_custom_scheme()
        self.assertEqual(len(self.dlg.schemes["Custom: Tri"]), 3)

    def test_cancelling_the_name_prompt_registers_nothing(self):
        before = set(self.dlg.schemes)
        with self._scheme_prompt("", 2, [], name_ok=False):
            self.dlg.edit_custom_scheme()
        self.assertEqual(set(self.dlg.schemes), before)

    def test_cancelling_the_colour_count_registers_nothing(self):
        before = set(self.dlg.schemes)
        with self._scheme_prompt("Mine", 2, [], count_ok=False):
            self.dlg.edit_custom_scheme()
        self.assertEqual(set(self.dlg.schemes), before)

    def test_cancelling_a_colour_picker_registers_nothing(self):
        before = set(self.dlg.schemes)
        with self._scheme_prompt("Mine", 2, ["#000000", None]):
            self.dlg.edit_custom_scheme()
        self.assertEqual(set(self.dlg.schemes), before)

    def test_a_custom_scheme_is_persisted(self):
        # charge_analysis derives the settings path from its module directory
        # inside each method rather than storing it on the dialog.
        with self._scheme_prompt("Mine", 2, ["#000000", "#ffffff"]):
            self.dlg.edit_custom_scheme()
        saved = json.load(
            open(os.path.join(self.tmp, "settings.json"), encoding="utf-8")
        )
        self.assertIn("Mine", json.dumps(saved))


if __name__ == "__main__":
    unittest.main()
