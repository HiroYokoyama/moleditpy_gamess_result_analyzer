"""
tests/test_mo_dialog.py
Coverage for MODialog under the headless Qt harness: orbital list construction
(ordering, HOMO/LUMO labelling per spin channel, energy unit derivation,
occupancy colouring), cube path derivation, colour presets and settings, and
the CSV export.

Complements tests/test_cube_and_mo.py and tests/test_mo_engine.py, which cover
the cube parsing and isosurface engine; this module drives the dialog itself.
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

M = gui_harness.load_isolated("mo_analysis")

EV_PER_HARTREE = 27.2114


def _restricted():
    """Four restricted orbitals: two occupied, two virtual.

    Shaped like parser.parse_mo_coeffs output, which carries an explicit
    numeric "id" alongside the dictionary key.
    """
    return {
        str(i): {"id": i, "energy": e, "occ": occ, "spin": "restricted"}
        for i, (e, occ) in enumerate([(-1.0, 2.0), (-0.5, 2.0), (0.2, 0.0), (0.6, 0.0)])
    }


def _unrestricted():
    """Alpha and beta channels with different occupancies (a doublet)."""
    mos = {}
    for i, (e, occ) in enumerate([(-1.0, 1.0), (-0.4, 1.0), (0.3, 0.0)]):
        mos[f"{i}_alpha"] = {"id": i, "energy": e, "occ": occ, "spin": "alpha"}
    for i, (e, occ) in enumerate([(-0.9, 1.0), (-0.3, 0.0), (0.4, 0.0)]):
        mos[f"{i}_beta"] = {"id": i, "energy": e, "occ": occ, "spin": "beta"}
    return mos


class _MOCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.out = os.path.join(self.tmp, "job.out")

        # load_settings() recomputes settings_file from the module's own
        # directory, so redirecting the attribute after construction is not
        # enough — point the module itself at the temp dir, or the suite writes
        # a settings.json into the package source tree.
        self._saved_file = M.__file__
        M.__file__ = os.path.join(self.tmp, "mo_analysis.py")
        self.addCleanup(lambda: setattr(M, "__file__", self._saved_file))

        self.context = MagicMock()
        self.host = MagicMock()
        self.host.context = self.context
        self.host.parser.filename = self.out
        self.host.file_path = self.out

        self.dlg = self._make(_restricted())

    def tearDown(self):
        self._tmp.cleanup()

    def _make(self, mos):
        dlg = M.MODialog(self.host, mos, result_dir=self.tmp)
        dlg.parent_dlg = self.host
        dlg.settings_file = os.path.join(self.tmp, "settings.json")
        return dlg

    def _rows(self):
        """Tree rows as (label_id, homo_lumo, occ, e_ev, e_eh) tuples."""
        tree = self.dlg.tree
        return [
            tuple(tree.topLevelItem(i).text(c) for c in range(5))
            for i in range(tree.topLevelItemCount())
        ]

    def _labels(self):
        return [r[1] for r in self._rows()]

    def _ids(self):
        return [r[0] for r in self._rows()]


# ---------------------------------------------------------------------------
# Orbital list construction
# ---------------------------------------------------------------------------


class TestOrbitalList(_MOCase):
    def test_every_orbital_is_listed(self):
        self.assertEqual(self.dlg.tree.topLevelItemCount(), 4)

    def test_highest_energy_orbital_is_listed_first(self):
        self.assertEqual(self._ids()[0], "3")

    def test_orbitals_are_listed_in_descending_order(self):
        self.assertEqual(self._ids(), ["3", "2", "1", "0"])

    def test_the_frontier_orbitals_are_labelled(self):
        labels = self._labels()
        self.assertIn("HOMO", labels)
        self.assertIn("LUMO", labels)

    def test_homo_is_the_highest_occupied(self):
        rows = self._rows()
        homo = next(r for r in rows if r[1] == "HOMO")
        self.assertEqual(homo[0], "1")

    def test_lumo_is_the_lowest_virtual(self):
        rows = self._rows()
        lumo = next(r for r in rows if r[1] == "LUMO")
        self.assertEqual(lumo[0], "2")

    def test_deeper_orbitals_are_numbered_below_homo(self):
        self.assertIn("HOMO-1", self._labels())

    def test_higher_orbitals_are_numbered_above_lumo(self):
        self.assertIn("LUMO+1", self._labels())

    def test_homo_is_the_highest_occupied_index_not_the_occupied_count(self):
        """Fractional-occupancy output (FOD, natural orbitals) leaves gaps in
        the occupied manifold; counting occupied orbitals and subtracting one
        labelled a mid-manifold orbital HOMO."""
        mos = {
            str(i): {"id": i, "energy": e, "occ": occ, "spin": "restricted"}
            for i, (e, occ) in enumerate(
                [(-1.0, 2.0), (-0.5, 0.0), (0.2, 1.8), (0.6, 0.0)]
            )
        }
        dlg = self._make(mos)
        rows = [
            tuple(dlg.tree.topLevelItem(i).text(c) for c in range(5))
            for i in range(dlg.tree.topLevelItemCount())
        ]
        homo = next(r for r in rows if r[1] == "HOMO")
        self.assertEqual(homo[0], "2")

    def test_occupancy_is_shown_to_two_decimals(self):
        rows = self._rows()
        homo = next(r for r in rows if r[1] == "HOMO")
        self.assertEqual(homo[2], "2.00")

    def test_energy_in_electronvolts_is_derived_from_hartree(self):
        rows = self._rows()
        homo = next(r for r in rows if r[1] == "HOMO")
        self.assertAlmostEqual(float(homo[3]), -0.5 * EV_PER_HARTREE, places=3)

    def test_hartree_energy_is_shown_at_full_precision(self):
        rows = self._rows()
        homo = next(r for r in rows if r[1] == "HOMO")
        self.assertEqual(homo[4], "-0.50000")

    def test_hartree_is_derived_when_only_electronvolts_are_given(self):
        mos = {"0": {"energy_ev": -13.6057, "occ": 2.0, "spin": "restricted"}}
        dlg = self._make(mos)
        self.assertAlmostEqual(float(dlg.tree.topLevelItem(0).text(4)), -0.5, places=4)

    def test_a_missing_energy_falls_back_to_zero(self):
        mos = {"0": {"occ": 2.0, "spin": "restricted"}}
        dlg = self._make(mos)
        self.assertEqual(dlg.tree.topLevelItem(0).text(4), "0.00000")

    def test_the_occupation_alias_is_understood(self):
        mos = {"0": {"energy": -1.0, "occupation": 2.0, "spin": "restricted"}}
        dlg = self._make(mos)
        self.assertEqual(dlg.tree.topLevelItem(0).text(2), "2.00")

    def test_the_selection_starts_on_the_homo(self):
        current = self.dlg.tree.currentItem()
        self.assertIsNotNone(current)
        self.assertIn("HOMO", current.text(1))

    def test_an_empty_orbital_set_is_tolerated(self):
        dlg = self._make({})
        self.assertEqual(dlg.tree.topLevelItemCount(), 0)

    def test_a_list_of_orbitals_is_accepted(self):
        mos = [
            {"energy": -1.0, "occ": 2.0, "spin": "restricted"},
            {"energy": 0.5, "occ": 0.0, "spin": "restricted"},
        ]
        dlg = self._make(mos)
        self.assertEqual(dlg.tree.topLevelItemCount(), 2)

    def test_rebuilding_the_list_does_not_duplicate_rows(self):
        self.dlg.normalize_and_populate()
        self.assertEqual(self.dlg.tree.topLevelItemCount(), 4)


class TestUnrestrictedList(_MOCase):
    def setUp(self):
        super().setUp()
        self.dlg = self._make(_unrestricted())

    def test_both_spin_channels_are_listed(self):
        self.assertEqual(self.dlg.tree.topLevelItemCount(), 6)

    def test_alpha_orbitals_are_tagged(self):
        self.assertTrue(any(i.endswith("(a)") for i in self._ids()))

    def test_beta_orbitals_are_tagged(self):
        self.assertTrue(any(i.endswith("(b)") for i in self._ids()))

    def test_each_spin_channel_gets_its_own_frontier_labels(self):
        rows = self._rows()
        homos = {r[0] for r in rows if r[1] == "HOMO"}
        # alpha HOMO is orbital 1, beta HOMO is orbital 0
        self.assertEqual(homos, {"1 (a)", "0 (b)"})

    def test_each_spin_channel_gets_its_own_lumo(self):
        rows = self._rows()
        lumos = {r[0] for r in rows if r[1] == "LUMO"}
        self.assertEqual(lumos, {"2 (a)", "1 (b)"})


# ---------------------------------------------------------------------------
# Cube path derivation
# ---------------------------------------------------------------------------


class TestCubePath(_MOCase):
    def test_cubes_live_beside_the_output_in_their_own_folder(self):
        path = self.dlg.get_cube_path("5")
        self.assertEqual(os.path.dirname(path), os.path.join(self.tmp, "job_cubes"))

    def test_the_filename_carries_the_output_base_and_orbital(self):
        self.assertTrue(self.dlg.get_cube_path("5").endswith("job_MO_5.cube"))

    def test_spin_tags_are_sanitised_into_the_filename(self):
        self.assertTrue(self.dlg.get_cube_path("1 (a)").endswith("job_MO_1_a.cube"))

    def test_beta_spin_tags_are_sanitised(self):
        self.assertTrue(self.dlg.get_cube_path("2 (b)").endswith("job_MO_2_b.cube"))

    def test_repeated_separators_are_collapsed(self):
        self.assertNotIn("__", os.path.basename(self.dlg.get_cube_path("1 : (a)")))

    def test_no_path_without_a_parsed_file(self):
        self.dlg.parent_dlg = MagicMock()
        self.dlg.parent_dlg.parser = None
        self.assertIsNone(self.dlg.get_cube_path("1"))

    def test_the_cube_folder_is_not_created_eagerly(self):
        self.dlg.get_cube_path("5")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "job_cubes")))

    def test_an_existing_cube_is_highlighted_in_the_list(self):
        cube_dir = os.path.join(self.tmp, "job_cubes")
        os.makedirs(cube_dir, exist_ok=True)
        with open(os.path.join(cube_dir, "job_MO_1.cube"), "w", encoding="utf-8") as fh:
            fh.write("cube")
        dlg = self._make(_restricted())
        row = next(
            dlg.tree.topLevelItem(i)
            for i in range(dlg.tree.topLevelItemCount())
            if dlg.tree.topLevelItem(i).text(0) == "1"
        )
        self.assertEqual(len(row.backgrounds), 5)

    def test_orbitals_without_a_cube_are_not_highlighted(self):
        row = next(
            self.dlg.tree.topLevelItem(i)
            for i in range(self.dlg.tree.topLevelItemCount())
            if self.dlg.tree.topLevelItem(i).text(0) == "1"
        )
        self.assertEqual(row.backgrounds, {})


# ---------------------------------------------------------------------------
# Colour handling
# ---------------------------------------------------------------------------


class TestColours(_MOCase):
    def test_the_positive_lobe_colour_round_trips(self):
        self.dlg.set_btn_color(self.dlg.btn_color_p, "#00ff00")
        self.assertEqual(self.dlg.get_color_hex("p").lower(), "#00ff00")

    def test_the_negative_lobe_colour_round_trips(self):
        self.dlg.set_btn_color(self.dlg.btn_color_n, "#ff00ff")
        self.assertEqual(self.dlg.get_color_hex("n").lower(), "#ff00ff")

    def test_a_dark_swatch_gets_light_text(self):
        self.dlg.set_btn_color(self.dlg.btn_color_p, "#000000")
        self.assertIn("color: white", self.dlg.btn_color_p.styleSheet())

    def test_a_light_swatch_gets_dark_text(self):
        self.dlg.set_btn_color(self.dlg.btn_color_p, "#ffffff")
        self.assertIn("color: black", self.dlg.btn_color_p.styleSheet())


# ---------------------------------------------------------------------------
# Settings and presets
# ---------------------------------------------------------------------------


class TestSettings(_MOCase):
    def test_saving_writes_a_settings_file(self):
        self.dlg.save_settings()
        self.assertTrue(os.path.exists(self.dlg.settings_file))

    def test_saving_preserves_unrelated_sections(self):
        with open(self.dlg.settings_file, "w", encoding="utf-8") as fh:
            json.dump({"nmr_settings": {"ref": 31.7}}, fh)
        self.dlg.save_settings()
        saved = json.load(open(self.dlg.settings_file, encoding="utf-8"))
        self.assertEqual(saved["nmr_settings"], {"ref": 31.7})

    def test_a_custom_preset_survives_a_round_trip(self):
        # Display state is persisted through named presets, not as loose
        # values: load_settings re-applies the last preset on startup.
        self.dlg.presets["Tight"] = {
            "iso": 0.075,
            "opacity": 0.8,
            "style": "Surface",
            "color_p": "#ff0000",
            "color_n": "#0000ff",
            "smooth_shading": True,
        }
        self.dlg.combo_presets.addItem("Tight")
        self.dlg.combo_presets.setCurrentText("Tight")
        self.dlg.save_settings()

        fresh = self._make(_restricted())
        fresh.load_settings()
        self.assertIn("Tight", fresh.presets)
        self.assertAlmostEqual(fresh.spin_iso.value(), 0.075)

    def test_the_builtin_default_preset_is_not_persisted(self):
        self.dlg.save_settings()
        saved = json.load(open(self.dlg.settings_file, encoding="utf-8"))
        self.assertNotIn("Default", saved["mo_settings"]["presets"])

    def test_applying_a_preset_sets_the_isosurface_level(self):
        self.dlg.presets["Loose"] = dict(self.dlg.presets["Default"], iso=0.005)
        self.dlg.apply_preset("Loose")
        self.assertAlmostEqual(self.dlg.spin_iso.value(), 0.005)

    def test_corrupt_settings_are_ignored(self):
        with open(self.dlg.settings_file, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.dlg.load_settings()  # must not raise

    def test_a_missing_settings_file_is_ignored(self):
        self.dlg.settings_file = os.path.join(self.tmp, "nope.json")
        self.dlg.load_settings()  # must not raise

    def test_save_preserves_several_other_dialogs_sections(self):
        others = {
            "dipole_settings": {"res": 20},
            "nmr_settings": {"ref": "TMS"},
            "thermal_settings": {"show_details": True},
        }
        with open(self.dlg.settings_file, "w", encoding="utf-8") as fh:
            json.dump(others, fh)

        self.dlg.save_settings()

        with open(self.dlg.settings_file, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        for key, val in others.items():
            self.assertEqual(on_disk[key], val)
        self.assertIn("mo_settings", on_disk)

    def test_round_trips_through_the_shared_helpers(self):
        S = gui_harness.load_isolated("settings")
        self.dlg.save_settings()

        section = S.load_section(self.dlg.settings_file, "mo_settings")
        self.assertEqual(section["last_preset"], self.dlg.combo_presets.currentText())

    def test_escape_routes_through_close(self):
        with patch.object(self.dlg, "close") as closer:
            self.dlg.reject()
        closer.assert_called_once()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class TestExport(_MOCase):
    def test_csv_has_a_row_per_orbital(self):
        path = os.path.join(self.tmp, "mos.csv")
        with patch.object(M.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_csv()
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(len(rows), 5)  # header + 4 orbitals

    def test_csv_records_the_orbital_energies(self):
        path = os.path.join(self.tmp, "mos.csv")
        with patch.object(M.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_csv()
        self.assertIn("-0.50000", open(path, encoding="utf-8").read())

    def test_a_cancelled_export_writes_nothing(self):
        with patch.object(M.QFileDialog, "getSaveFileName", return_value=("", "")):
            self.dlg.export_csv()
        self.assertFalse([f for f in os.listdir(self.tmp) if f.endswith(".csv")])


if __name__ == "__main__":
    unittest.main()
