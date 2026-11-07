"""
tests/test_nmr_split.py
Asserts the nmr_analysis.py -> mixin-module decomposition holds: every
previously public NMRDialog method still resolves, each mixin owns exactly
its assigned methods, and no method name is duplicated across mixins.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

N = gui_harness.load_isolated("nmr_analysis")
NMRDialog = N.NMRDialog

_EXPECTED = {
    "_NMRMergeMixin": (
        N._NMRMergeMixin,
        [
            "_merged_peaks_path",
            "merge_selected_peaks",
            "unmerge_selected_peaks",
            "_mark_merges_dirty",
            "save_merges_clicked",
            "save_merged_peaks",
            "load_merged_peaks",
        ],
    ),
    "_NMRPlotMixin": (
        N._NMRPlotMixin,
        [
            "plot_spectrum",
            "_get_current_peaks",
            "highlight_selected_peaks",
            "reset_zoom",
            "on_peak_click",
            "clear_peak_selection",
            "_remap_selection_to_new_peaks",
            "_calculate_peak_selection_from_atoms",
            "select_peaks_by_atom_indices",
            "_check_external_selection",
            "update_selected_labels",
            "add_atom_label",
            "toggle_all_labels",
            "clear_atom_labels",
            "_shift_labels_enabled",
            "on_label_shifts_toggled",
            "highlight_atom_in_3d",
            "draw_custom_nmr_highlights_3d",
        ],
    ),
    "_NMRExportMixin": (
        N._NMRExportMixin,
        [
            "export_spectrum",
            "export_spectrum_csv",
            "export_table_csv",
            "copy_table",
        ],
    ),
}

# Methods that stayed on NMRDialog itself.
_KEPT_ON_DIALOG = [
    "__init__",
    "get_nucleus_key",
    "load_settings",
    "save_settings",
    "setup_ui",
    "update_reference_combo",
    "on_ref_change",
    "on_ref_value_change",
    "add_custom_reference",
    "delete_custom_reference",
    "on_spectrum_settings_change",
    "on_nucleus_changed",
    "update_x_range_defaults",
    "apply_filter",
    "recalc",
    "reset_selection",
    "reject",
    "closeEvent",
    "keyPressEvent",
]


class TestEveryMethodStillResolves(unittest.TestCase):
    def test_all_prior_public_names_resolve_on_nmr_dialog(self):
        for _clsname, (_cls, names) in _EXPECTED.items():
            for name in names:
                with self.subTest(name=name):
                    self.assertTrue(
                        hasattr(NMRDialog, name),
                        f"NMRDialog lost attribute {name!r}",
                    )

    def test_kept_names_still_resolve_on_nmr_dialog(self):
        for name in _KEPT_ON_DIALOG:
            with self.subTest(name=name):
                self.assertTrue(hasattr(NMRDialog, name))

    def test_class_level_constants_survive(self):
        self.assertIn("1H", NMRDialog.ISOTOPE_MAP.values())
        self.assertIn("H", NMRDialog.GAMMA)
        self.assertTrue(hasattr(N, "DEFAULT_REFERENCE_STANDARDS"))


class TestMixinsOwnExactlyTheirMethods(unittest.TestCase):
    def test_each_mixin_defines_exactly_its_methods(self):
        for clsname, (cls, names) in _EXPECTED.items():
            with self.subTest(cls=clsname):
                own_methods = {
                    k
                    for k, v in vars(cls).items()
                    if not k.startswith("__") and callable(getattr(cls, k))
                }
                self.assertEqual(own_methods, set(names))

    def test_no_method_defined_in_more_than_one_mixin(self):
        seen = {}
        for clsname, (_cls, names) in _EXPECTED.items():
            for name in names:
                self.assertNotIn(
                    name,
                    seen,
                    f"{name!r} defined in both {seen.get(name)!r} and {clsname!r}",
                )
                seen[name] = clsname

    def test_kept_names_are_not_redefined_in_a_mixin(self):
        mixin_names = {n for _cls, names in _EXPECTED.values() for n in names}
        for name in _KEPT_ON_DIALOG:
            self.assertNotIn(name, mixin_names)


class TestMroOrder(unittest.TestCase):
    def test_nmr_dialog_inherits_in_the_documented_order(self):
        mro_names = [c.__name__ for c in NMRDialog.__mro__]
        expected_order = [
            "NMRDialog",
            "_NMRMergeMixin",
            "_NMRPlotMixin",
            "_NMRExportMixin",
        ]
        for name in expected_order:
            self.assertIn(name, mro_names)
        indices = [mro_names.index(n) for n in expected_order]
        self.assertEqual(indices, sorted(indices))


if __name__ == "__main__":
    unittest.main()
