"""
tests/test_mo_compare.py
Coverage for the multi-orbital comparison window (mo_compare.MOCompareDialog)
and the MODialog plumbing it depends on: the silent cube-generation batch, the
launcher/teardown handshake, and the context-menu label.

The two modules are loaded through separate isolated harness loads, so their
``Qt`` stubs are distinct objects and a role constant taken from one does not
match the other. Tests that cross the boundary rebind ``mo_compare.Qt`` to the
MO dialog's copy, which is what a real single Qt binding gives you.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

M = gui_harness.load_isolated("mo_analysis")
CMP = gui_harness.load_isolated("mo_compare")


def _restricted():
    return {
        str(i): {"id": i, "energy": e, "occ": occ, "spin": "restricted"}
        for i, (e, occ) in enumerate([(-1.0, 2.0), (-0.5, 2.0), (0.2, 0.0), (0.6, 0.0)])
    }


class _FakeVisualizer:
    """Records what the compare dialog asked the 3D view to draw."""

    calls = []
    loadable = True

    def __init__(self, mw):
        self.mw = mw

    def load_file(self, path):
        self.path = path
        return self.loadable

    def show_iso(self, isovalue, **kw):
        _FakeVisualizer.calls.append({"iso": isovalue, "path": self.path, **kw})


class _CompareCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.out = os.path.join(self.tmp, "job.out")

        # Both modules recompute settings.json from their own directory, so
        # without this the suite writes into the package source tree.
        saved = M.__file__
        M.__file__ = os.path.join(self.tmp, "mo_analysis.py")
        self.addCleanup(lambda: setattr(M, "__file__", saved))
        saved_cmp = CMP.__file__
        CMP.__file__ = os.path.join(self.tmp, "mo_compare.py")
        self.addCleanup(lambda: setattr(CMP, "__file__", saved_cmp))

        self.host = MagicMock()
        self.host.context = MagicMock()
        self.host.parser.filename = self.out
        self.host.file_path = self.out

        self.dlg = M.MODialog(self.host, _restricted(), result_dir=self.tmp)
        self.dlg.parent_dlg = self.host
        self.dlg.settings_file = os.path.join(self.tmp, "settings.json")

        # One Qt enum, as a real binding would have.
        p = patch.object(CMP, "Qt", M.Qt)
        p.start()
        self.addCleanup(p.stop)

        _FakeVisualizer.calls = []
        _FakeVisualizer.loadable = True
        p2 = patch.object(CMP, "CubeVisualizer", _FakeVisualizer)
        p2.start()
        self.addCleanup(p2.stop)

    def _make_compare(self):
        return CMP.MOCompareDialog(self.dlg)

    def _write_cube(self, display_id):
        path = self.dlg.get_cube_path(display_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("cube\n")
        return path

    def _row(self, display_id):
        tree = self.dlg.tree
        for i in range(tree.topLevelItemCount()):
            if tree.topLevelItem(i).text(0) == display_id:
                return tree.topLevelItem(i)
        raise AssertionError(f"no row {display_id}")

    def _select_rows(self, *display_ids):
        """Highlight rows in the MO table, as a click would."""
        for i in range(self.dlg.tree.topLevelItemCount()):
            self.dlg.tree.topLevelItem(i).setSelected(False)
        for display_id in display_ids:
            self._row(display_id).setSelected(True)

    def _blank_compare(self):
        """A comparison window with every slot switched off, so a test can
        enable exactly the slots it is about."""
        cmp_dlg = self._make_compare()
        for slot in cmp_dlg.slots:
            slot.check_on.setChecked(False)
        return cmp_dlg

    def _enable(self, cmp_dlg, index, display_id):
        slot = cmp_dlg.slots[index]
        slot.check_on.setChecked(True)
        for i in range(slot.combo_mo.count()):
            if slot.combo_mo.itemData(i)[1] == display_id:
                slot.combo_mo.setCurrentIndex(i)
                break
        return slot

    def _render(self, cmp_dlg):
        """Drop the automatic redraws, then render once deliberately.

        Enabling a slot or nudging a spin box now redraws on its own, so a
        test that counts draws has to start from a clean slate.
        """
        _FakeVisualizer.calls = []
        cmp_dlg.render_all()
        return _FakeVisualizer.calls

    def _shown(self, cmp_dlg):
        """(slot index, display id) for every switched-on slot."""
        return [(s.index, s.selection()[1]) for s in cmp_dlg.slots if s.is_on()]


# ---------------------------------------------------------------------------
# Context menu wording
# ---------------------------------------------------------------------------


class TestContextMenuLabel(_CompareCase):
    def _labels(self):
        captured = []

        class _Menu:
            def __init__(self, *a, **k):
                pass

            def addAction(self, text):
                captured.append(text)
                act = MagicMock()
                act.setEnabled = lambda v: None
                return act

            def addSeparator(self):
                return None

            def exec(self, *a, **k):
                return None

        self._row("1").setSelected(True)
        with patch.object(M, "QMenu", _Menu):
            self.dlg.show_tree_context_menu(MagicMock())
        return captured

    def _choose(self, index):
        """Right-click and pick the action at *index*."""

        class _Menu:
            def __init__(self, *a, **k):
                self.actions = []

            def addAction(self, text):
                act = MagicMock()
                act.text = text
                act.enabled = True
                act.setEnabled = lambda v, a=act: setattr(a, "enabled", v)
                self.actions.append(act)
                return act

            def addSeparator(self):
                return None

            def exec(self, *a, **k):
                self.picked = self.actions[index]
                return self.picked

        made = []

        class _Recorded(_Menu):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                made.append(self)

        with patch.object(M, "QMenu", _Recorded):
            with patch.object(self.dlg, "show_compare_dialog") as opened:
                with patch.object(self.dlg, "_generate_single_mo"):
                    self.dlg.show_tree_context_menu(MagicMock())
        return made[0], opened

    def test_the_menu_offers_compare(self):
        self._row("1").setSelected(True)
        self.assertEqual(self._labels()[3], "Compare Selected (1)")

    def test_choosing_compare_opens_the_comparison_window(self):
        self._row("1").setSelected(True)
        _menu, opened = self._choose(3)
        opened.assert_called_once()

    def test_compare_is_disabled_beyond_four_orbitals(self):
        for mo in ("0", "1", "2", "3"):
            self._row(mo).setSelected(True)
        menu, _opened = self._choose(1)
        self.assertTrue(menu.actions[3].enabled)
        self.dlg.tree.selectedItems = lambda: [self._row(m) for m in "0123"] + [
            self._row("0")
        ]
        menu, _opened = self._choose(1)
        self.assertFalse(menu.actions[3].enabled)

    def test_choosing_visualise_does_not_open_the_comparison(self):
        self._row("1").setSelected(True)
        _menu, opened = self._choose(1)
        opened.assert_not_called()

    def _write_stamped_cube(self, display_id, stamp):
        path = self.dlg.get_cube_path(display_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"GAMESS Analyzer Cube File: MO {display_id}\n{stamp}\n 1 0 0 0\n")
        return path

    def test_the_menu_leads_with_the_cached_cube_settings(self):
        self._write_stamped_cube(
            "1",
            "Generated by MoleditPy GAMESS Result Analyzer v9.9.9 "
            "| grid=40 | margin=4.00",
        )
        head = self._labels()[0]
        self.assertIn("40 pts", head)
        self.assertIn("4.00 Å", head)
        self.assertIn("9.9.9", head)

    def test_an_ungenerated_orbital_says_so(self):
        self.assertEqual(self._labels()[0], "Not generated yet")

    def test_a_cube_without_stamped_settings_says_unknown(self):
        """Never describe an old cube with the current spin-box values."""
        self._write_stamped_cube("1", "Generated by something else")
        head = self._labels()[0]
        self.assertIn("unknown grid", head)
        self.assertIn("unknown margin", head)

    def test_a_partially_stamped_cube_reports_only_what_is_missing(self):
        self._write_stamped_cube(
            "1", "Generated by MoleditPy GAMESS Result Analyzer v3.0.0 | grid=60"
        )
        head = self._labels()[0]
        self.assertIn("60 pts", head)
        self.assertIn("unknown margin", head)

    def test_regenerate_entry_no_longer_says_overwrite(self):
        self.assertNotIn("overwrite", " ".join(self._labels()).lower())

    def test_regenerate_entry_still_names_the_action_and_count(self):
        self.assertEqual(self._labels()[2], "Regenerate Cube (1)")

    def test_the_overwrite_meaning_survives_in_the_tooltip(self):
        """The wording moved, the behaviour did not."""
        self.assertIn(
            "replacing it",
            open(
                os.path.join(
                    os.path.dirname(__file__),
                    "..",
                    "gamess_result_analyzer",
                    "mo_analysis.py",
                ),
                encoding="utf-8",
            ).read(),
        )


# ---------------------------------------------------------------------------
# Silent batch generation on the MO dialog
# ---------------------------------------------------------------------------


class TestSilentGeneration(_CompareCase):
    def setUp(self):
        super().setUp()
        self.host.parser.data = {
            "mo_coeffs": {"1": {"coeffs": [{"coeff": 0.1}, {"coeff": 0.2}]}},
            "atoms": ["H", "H"],
            "coords": [(0.0, 0.0, 0.0), (0.0, 0.0, 0.74)],
        }
        engine = MagicMock()
        engine.n_basis = 2
        p = patch.object(self.dlg, "get_engine", return_value=engine)
        p.start()
        self.addCleanup(p.stop)

        self.shown = []
        p2 = patch.object(self.dlg, "show_cube", self.shown.append)
        p2.start()
        self.addCleanup(p2.stop)

    def test_a_silent_batch_writes_the_cube_without_redrawing(self):
        self._write_cube("1")
        self.dlg.generate_cubes(["1"])
        self.assertEqual(self.shown, [])

    def test_a_normal_batch_still_redraws(self):
        path = self._write_cube("1")
        self.dlg.generation_queue = ["1"]
        self.dlg.process_generation_queue()
        self.assertEqual(self.shown, [path])

    def test_the_callback_runs_once_the_batch_drains(self):
        self._write_cube("1")
        seen = []
        self.dlg.generate_cubes(["1"], on_done=lambda: seen.append(True))
        self.assertEqual(seen, [True])

    def test_an_empty_key_list_still_calls_back(self):
        seen = []
        self.dlg.generate_cubes([], on_done=lambda: seen.append(True))
        self.assertEqual(seen, [True])

    def test_none_keys_are_dropped(self):
        seen = []
        self.dlg.generate_cubes([None, None], on_done=lambda: seen.append(True))
        self.assertEqual(seen, [True])
        self.assertEqual(self.dlg.generation_queue, [])

    def test_silence_is_lifted_when_the_batch_ends(self):
        """A stuck flag would make the next plain Visualize draw nothing."""
        self._write_cube("1")
        self.dlg.generate_cubes(["1"])
        self.assertFalse(self.dlg.generation_silent)

    def test_the_callback_does_not_fire_twice(self):
        self._write_cube("1")
        seen = []
        self.dlg.generate_cubes(["1"], on_done=lambda: seen.append(True))
        self.dlg.process_generation_queue()
        self.assertEqual(seen, [True])

    def test_a_silent_batch_does_not_hijack_the_main_view(self):
        """last_cube_path drives update_vis_only; a silent batch stealing it
        made the next colour tweak redraw somebody else's orbital."""
        self.dlg.last_cube_path = "/somewhere/else.cube"
        self._write_cube("1")
        self.dlg.generate_cubes(["1"])
        self.assertEqual(self.dlg.last_cube_path, "/somewhere/else.cube")

    def test_a_normal_batch_does_take_over_the_main_view(self):
        path = self._write_cube("1")
        self.dlg.generation_queue = ["1"]
        self.dlg.process_generation_queue()
        self.assertEqual(self.dlg.last_cube_path, path)

    def test_forcing_is_honoured_by_a_silent_batch(self):
        self._write_cube("1")
        with patch.object(M, "CalcWorker") as worker:
            self.dlg.generate_cubes(["1"], force=True)
            worker.assert_called_once()

    def test_display_cube_forwards_when_not_silent(self):
        self.dlg.generation_silent = False
        self.dlg._display_cube("x.cube")
        self.assertEqual(self.shown, ["x.cube"])


# ---------------------------------------------------------------------------
# Launcher / teardown handshake
# ---------------------------------------------------------------------------


class TestCompareLauncher(_CompareCase):
    def test_the_button_opens_the_comparison_window(self):
        with patch.object(M, "MOCompareDialog") as cls:
            self.dlg.show_compare_dialog()
            cls.assert_called_once_with(self.dlg)

    def test_a_second_click_raises_the_existing_window(self):
        with patch.object(M, "MOCompareDialog") as cls:
            self.dlg.show_compare_dialog()
            self.dlg.show_compare_dialog()
            self.assertEqual(cls.call_count, 1)
        self.dlg.compare_dlg.raise_.assert_called_once()

    def test_reopening_refills_the_slots_from_the_new_selection(self):
        """Otherwise the raised window still shows the previous orbitals."""
        with patch.object(M, "MOCompareDialog"):
            self.dlg.show_compare_dialog()
            self.dlg.show_compare_dialog()
        self.dlg.compare_dlg.apply_selection.assert_called_once_with()

    def test_reopening_really_refills_a_live_window(self):
        self._select_rows("2")
        cmp_dlg = self._make_compare()
        self.dlg.compare_dlg = cmp_dlg
        self._select_rows("0")
        self.dlg.show_compare_dialog()
        self.assertEqual(self._shown(cmp_dlg), [(0, "0")])

    def test_a_destroyed_window_is_rebuilt_rather_than_raised(self):
        stale = MagicMock()
        stale.raise_.side_effect = RuntimeError("wrapped C/C++ object deleted")
        self.dlg.compare_dlg = stale
        with patch.object(M, "MOCompareDialog") as cls:
            self.dlg.show_compare_dialog()
            cls.assert_called_once()

    def test_closing_the_window_clears_the_reference(self):
        self.dlg.compare_dlg = MagicMock()
        self.dlg.on_compare_closed()
        self.assertIsNone(self.dlg.compare_dlg)

    def test_the_missing_module_is_reported_not_ignored(self):
        with patch.object(M, "MOCompareDialog", None):
            with patch.object(M, "QMessageBox") as mb:
                self.dlg.show_compare_dialog()
                mb.warning.assert_called_once()

    def test_closing_the_mo_dialog_closes_the_comparison(self):
        child = MagicMock()
        self.dlg.compare_dlg = child
        self.dlg.closeEvent(MagicMock())
        child.close.assert_called_once()
        self.assertIsNone(self.dlg.compare_dlg)


# ---------------------------------------------------------------------------
# Dialog construction
# ---------------------------------------------------------------------------


class TestCompareConstruction(_CompareCase):
    def test_there_are_four_slots(self):
        self.assertEqual(len(self._make_compare().slots), CMP.SLOT_COUNT)

    def test_every_slot_lists_every_orbital(self):
        cmp_dlg = self._make_compare()
        for slot in cmp_dlg.slots:
            self.assertEqual(slot.combo_mo.count(), 4)

    def test_orbital_entries_are_labelled_with_their_frontier_tag(self):
        labels = [lbl for lbl, _k, _d, _t in self._make_compare().collect_orbitals()]
        self.assertTrue(any("HOMO" in lbl for lbl in labels))

    def test_orbital_entries_carry_the_frontier_tag(self):
        tags = {d: t for _l, _k, d, t in self._make_compare().collect_orbitals()}
        self.assertEqual(tags["1"], "HOMO")
        self.assertEqual(tags["2"], "LUMO")

    def test_orbital_entries_carry_the_lookup_key_and_display_id(self):
        cmp_dlg = self._make_compare()
        key, display_id = cmp_dlg.slots[0].combo_mo.itemData(0)
        # The top row is the highest-energy orbital, as in the MO table.
        self.assertEqual(display_id, "3")
        self.assertEqual(key, self._row("3").data(0, M.Qt.ItemDataRole.UserRole))

    def test_rows_without_a_lookup_key_are_skipped(self):
        self._row("1").setData(0, M.Qt.ItemDataRole.UserRole, None)
        self.assertEqual(len(self._make_compare().collect_orbitals()), 3)

    def test_no_orbitals_without_a_table(self):
        self.dlg.tree = None
        self.assertEqual(self._make_compare().collect_orbitals(), [])

    def test_each_slot_gets_its_own_actor_namespace(self):
        prefixes = [s.prefix for s in self._make_compare().slots]
        self.assertEqual(len(set(prefixes)), CMP.SLOT_COUNT)


# ---------------------------------------------------------------------------
# Which orbitals the slots open on
# ---------------------------------------------------------------------------


class TestDefaultOrbitals(_CompareCase):
    """With nothing selected in the table, the slots open on the frontier."""

    def test_the_slots_are_filled_with_the_frontier_orbitals(self):
        # HOMO=1, LUMO=2, HOMO-1=0, LUMO+1=3 for this fixture.
        self.assertEqual(
            self._shown(self._make_compare()),
            [(0, "1"), (1, "2"), (2, "0"), (3, "3")],
        )

    def test_all_four_start_switched_on(self):
        self.assertTrue(all(s.is_on() for s in self._make_compare().slots))

    def test_the_default_order_is_homo_lumo_homo_minus_one_lumo_plus_one(self):
        self.assertEqual(CMP.DEFAULT_TAGS, ["HOMO", "LUMO", "HOMO-1", "LUMO+1"])

    def test_an_unassigned_slot_still_shows_a_frontier_orbital(self):
        """With one row selected in the table only slot 1 is assigned; the
        rest used to sit on row 0, the highest-numbered virtual orbital."""
        self._select_rows("1")
        cmp_dlg = self._make_compare()
        picked = [s.selection()[1] for s in cmp_dlg.slots]
        self.assertEqual(picked, ["1", "2", "0", "3"])

    def test_those_unassigned_slots_stay_switched_off(self):
        self._select_rows("1")
        cmp_dlg = self._make_compare()
        self.assertEqual(
            [s.is_on() for s in cmp_dlg.slots], [True, False, False, False]
        )

    def test_no_slot_opens_on_the_top_row_by_accident(self):
        """Row 0 is the highest virtual orbital -- never a useful default."""
        self._select_rows("1")
        cmp_dlg = self._make_compare()
        top_row = cmp_dlg.orbitals[0][2]
        self.assertEqual(top_row, "3")
        self.assertNotEqual(cmp_dlg.slots[1].selection()[1], top_row)

    def test_absent_frontier_tags_leave_their_slots_off(self):
        """A two-orbital job has no LUMO+1 or HOMO-1 to show."""
        small = {
            str(i): {"id": i, "energy": e, "occ": occ, "spin": "restricted"}
            for i, (e, occ) in enumerate([(-1.0, 2.0), (0.2, 0.0)])
        }
        self.dlg.mos = small
        self.dlg.normalize_and_populate()
        cmp_dlg = self._make_compare()
        self.assertEqual(self._shown(cmp_dlg), [(0, "0"), (1, "1")])

    def test_the_same_tag_in_two_spin_channels_is_taken_once(self):
        mos = {}
        for i, (e, occ) in enumerate([(-1.0, 1.0), (0.3, 0.0)]):
            mos[f"{i}_alpha"] = {"id": i, "energy": e, "occ": occ, "spin": "alpha"}
            mos[f"{i}_beta"] = {"id": i, "energy": e, "occ": occ, "spin": "beta"}
        self.dlg.mos = mos
        self.dlg.normalize_and_populate()
        filled = [d for _i, d in self._shown(self._make_compare())]
        self.assertEqual(len(filled), len(set(filled)))


class TestPreselectedOrbitals(_CompareCase):
    """Rows highlighted in the MO table fill the slots in order."""

    def test_a_single_selected_orbital_fills_the_first_slot(self):
        self._select_rows("2")
        self.assertEqual(self._shown(self._make_compare()), [(0, "2")])

    def test_several_selected_orbitals_fill_consecutive_slots(self):
        self._select_rows("3", "1")
        self.assertEqual(self._shown(self._make_compare()), [(0, "3"), (1, "1")])

    def test_unfilled_slots_are_switched_off(self):
        self._select_rows("2")
        slots = self._make_compare().slots
        self.assertFalse(any(s.is_on() for s in slots[1:]))

    def test_a_selection_beats_the_frontier_defaults(self):
        self._select_rows("0")
        self.assertEqual(self._shown(self._make_compare()), [(0, "0")])

    def test_more_than_four_selected_orbitals_are_truncated(self):
        mos = {
            str(i): {
                "id": i,
                "energy": -1.0 + 0.2 * i,
                "occ": 2.0,
                "spin": "restricted",
            }
            for i in range(6)
        }
        self.dlg.mos = mos
        self.dlg.normalize_and_populate()
        self._select_rows("0", "1", "2", "3", "4", "5")
        self.assertEqual(len(self._shown(self._make_compare())), CMP.SLOT_COUNT)

    def test_explicit_keys_win_over_everything(self):
        cmp_dlg = self._make_compare()
        cmp_dlg.apply_selection(["0"])
        self.assertEqual(self._shown(cmp_dlg), [(0, "0")])

    def test_reapplying_replaces_the_previous_fill(self):
        cmp_dlg = self._make_compare()
        cmp_dlg.apply_selection(["2", "3"])
        cmp_dlg.apply_selection(["1"])
        self.assertEqual(self._shown(cmp_dlg), [(0, "1")])

    def test_an_unknown_key_does_not_fill_a_slot(self):
        cmp_dlg = self._make_compare()
        cmp_dlg.apply_selection(["nosuchmo"])
        self.assertEqual(self._shown(cmp_dlg), [])

    def test_a_table_that_cannot_report_its_selection_falls_back(self):
        self.dlg.tree.selectedItems = MagicMock(side_effect=RuntimeError("gone"))
        self.assertEqual(self._make_compare().preselected_keys(), [])


class TestNoOrbitals(_CompareCase):
    """An output whose MO table is empty must still open a usable window."""

    def setUp(self):
        super().setUp()
        self.dlg.tree.clear()

    def test_the_window_still_builds(self):
        self.assertEqual(len(self._make_compare().slots), CMP.SLOT_COUNT)

    def test_a_slot_with_nothing_to_show_reports_no_selection(self):
        self.assertEqual(self._make_compare().slots[0].selection(), (None, None))

    def test_update_generates_nothing(self):
        cmp_dlg = self._make_compare()
        with patch.object(self.dlg, "generate_cubes") as gen:
            cmp_dlg.update_view()
            gen.assert_not_called()

    def test_render_draws_nothing(self):
        self._make_compare().render_all()
        self.assertEqual(_FakeVisualizer.calls, [])

    def test_a_table_row_that_vanished_is_skipped(self):
        self.dlg.tree.topLevelItem = lambda i: None
        self.dlg.tree.topLevelItemCount = lambda: 2
        self.assertEqual(self._make_compare().collect_orbitals(), [])


class TestFirstSlotMirrorsTheMODialog(_CompareCase):
    def test_the_positive_lobe_colour_matches(self):
        self.dlg.set_btn_color(self.dlg.btn_color_p, "#123456")
        self.assertEqual(self._make_compare().slots[0].color("p"), "#123456")

    def test_the_negative_lobe_colour_matches(self):
        self.dlg.set_btn_color(self.dlg.btn_color_n, "#654321")
        self.assertEqual(self._make_compare().slots[0].color("n"), "#654321")

    def test_the_other_slots_get_distinct_colours(self):
        cmp_dlg = self._make_compare()
        pos = [s.color("p") for s in cmp_dlg.slots]
        self.assertEqual(len(set(pos)), CMP.SLOT_COUNT)

    def test_the_isovalue_matches(self):
        self.dlg.spin_iso.setValue(0.077)
        self.assertAlmostEqual(self._make_compare().slots[0].spin_iso.value(), 0.077)

    def test_the_opacity_matches(self):
        self.dlg.spin_opacity.setValue(0.3)
        self.assertAlmostEqual(self._make_compare().slots[0].spin_opacity.value(), 0.3)

    def test_the_style_matches(self):
        self.dlg.combo_style.setCurrentText("Wireframe")
        self.assertEqual(
            self._make_compare().slots[0].combo_style.currentText(), "Wireframe"
        )

    def test_the_smooth_shading_flag_matches(self):
        self.dlg.check_smooth.setChecked(False)
        self.assertFalse(self._make_compare().slots[0].check_smooth.isChecked())

    def test_an_unreadable_parent_colour_falls_back_to_the_default(self):
        self.dlg.get_color_hex = MagicMock(side_effect=AttributeError("gone"))
        self.assertEqual(
            self._make_compare().slots[0].color("p"), CMP.DEFAULT_COLORS[0][0]
        )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class TestRendering(_CompareCase):
    def test_an_enabled_slot_with_a_cube_is_drawn(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        self.assertEqual(len(self._render(cmp_dlg)), 1)

    def test_four_orbitals_are_drawn_at_once(self):
        cmp_dlg = self._blank_compare()
        for i, mo in enumerate(["0", "1", "2", "3"]):
            self._write_cube(mo)
            self._enable(cmp_dlg, i, mo)
        cmp_dlg.render_all()
        self.assertEqual(len(_FakeVisualizer.calls), 4)

    def test_each_orbital_gets_its_own_actor_names(self):
        cmp_dlg = self._blank_compare()
        for i, mo in enumerate(["0", "1", "2", "3"]):
            self._write_cube(mo)
            self._enable(cmp_dlg, i, mo)
        cmp_dlg.render_all()
        names = [c["name_prefix"] for c in _FakeVisualizer.calls]
        self.assertEqual(len(set(names)), 4)

    def test_each_slot_renders_with_its_own_colours(self):
        cmp_dlg = self._blank_compare()
        for i, mo in enumerate(["0", "1"]):
            self._write_cube(mo)
            self._enable(cmp_dlg, i, mo)
        cmp_dlg.slots[1].set_color("p", "#00ff00")
        cmp_dlg.render_all()
        self.assertEqual(_FakeVisualizer.calls[1]["color_p"], "#00ff00")

    def test_per_slot_isovalue_and_opacity_reach_the_renderer(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        slot = self._enable(cmp_dlg, 0, "1")
        slot.spin_iso.setValue(0.055)
        slot.spin_opacity.setValue(0.8)
        call = self._render(cmp_dlg)[0]
        self.assertAlmostEqual(call["iso"], 0.055)
        self.assertAlmostEqual(call["opacity"], 0.8)

    def test_the_style_is_lower_cased_for_pyvista(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        slot = self._enable(cmp_dlg, 0, "1")
        slot.combo_style.setCurrentText("Wireframe")
        self.assertEqual(self._render(cmp_dlg)[0]["style"], "wireframe")

    def test_a_disabled_slot_is_not_drawn(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1").check_on.setChecked(False)
        self.assertEqual(self._render(cmp_dlg), [])

    def test_a_disabled_slot_has_its_actors_removed(self):
        cmp_dlg = self._blank_compare()
        cmp_dlg.slots[1].check_on.setChecked(False)
        cmp_dlg.render_all()
        removed = [c[0][0] for c in self.dlg.mw.plotter.remove_actor.call_args_list]
        self.assertIn("mo_cmp1_p", removed)

    def test_the_single_orbital_actors_are_cleared_first(self):
        """Otherwise the MO dialog's own lobes double-draw over slot 1."""
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        cmp_dlg.render_all()
        removed = [c[0][0] for c in self.dlg.mw.plotter.remove_actor.call_args_list]
        self.assertIn("mo_iso_p", removed)
        self.assertIn("mo_iso_n", removed)

    def test_a_missing_cube_is_reported_not_drawn(self):
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        cmp_dlg.render_all()
        self.assertEqual(_FakeVisualizer.calls, [])
        self.assertIn("missing", cmp_dlg.lbl_status.text())

    def test_an_unreadable_cube_is_skipped(self):
        self._write_cube("1")
        _FakeVisualizer.loadable = False
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        cmp_dlg.render_all()
        self.assertEqual(_FakeVisualizer.calls, [])

    def test_the_status_line_counts_what_was_drawn(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        cmp_dlg.render_all()
        self.assertIn("1 orbital", cmp_dlg.lbl_status.text())

    def test_a_missing_visualiser_is_reported(self):
        cmp_dlg = self._blank_compare()
        with patch.object(CMP, "CubeVisualizer", None):
            with patch.object(CMP, "QMessageBox") as mb:
                cmp_dlg.render_all()
                mb.warning.assert_called_once()

    def test_rendering_without_a_main_window_is_a_no_op(self):
        cmp_dlg = self._blank_compare()
        cmp_dlg.mw = None
        cmp_dlg.render_all()
        self.assertEqual(_FakeVisualizer.calls, [])


# ---------------------------------------------------------------------------
# Update / generation flow
# ---------------------------------------------------------------------------


class TestUpdateView(_CompareCase):
    def test_cached_orbitals_are_drawn_without_recomputing(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        _FakeVisualizer.calls = []
        with patch.object(self.dlg, "generate_cubes") as gen:
            cmp_dlg.update_view()
            gen.assert_not_called()
        self.assertEqual(len(_FakeVisualizer.calls), 1)

    def test_a_missing_cube_is_requested_from_the_mo_dialog(self):
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        with patch.object(self.dlg, "generate_cubes") as gen:
            cmp_dlg.update_view()
            self.assertEqual(gen.call_args[0][0], ["1"])

    def test_the_render_runs_once_generation_finishes(self):
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        with patch.object(self.dlg, "generate_cubes") as gen:
            cmp_dlg.update_view()
        self.assertEqual(gen.call_args[1]["on_done"], cmp_dlg.render_all)

    def test_only_missing_cubes_are_generated(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        self._enable(cmp_dlg, 1, "2")
        with patch.object(self.dlg, "generate_cubes") as gen:
            cmp_dlg.update_view()
            self.assertEqual(gen.call_args[0][0], ["2"])

    def test_the_same_orbital_in_two_slots_is_generated_once(self):
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        self._enable(cmp_dlg, 1, "1")
        with patch.object(self.dlg, "generate_cubes") as gen:
            cmp_dlg.update_view()
            self.assertEqual(gen.call_args[0][0], ["1"])

    def test_disabled_slots_do_not_trigger_generation(self):
        cmp_dlg = self._blank_compare()
        with patch.object(self.dlg, "generate_cubes") as gen:
            cmp_dlg.slots[0].check_on.setChecked(False)
            cmp_dlg.update_view()
            gen.assert_not_called()

    def test_a_generation_failure_is_reported_to_the_user(self):
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        with patch.object(self.dlg, "generate_cubes", side_effect=RuntimeError("boom")):
            with patch.object(CMP, "QMessageBox") as mb:
                cmp_dlg.update_view()
                mb.warning.assert_called_once()


# ---------------------------------------------------------------------------
# Colour picking, clearing and teardown
# ---------------------------------------------------------------------------


class TestSlotColorPicking(_CompareCase):
    def _pick(self, cmp_dlg, slot, which, hex_c, valid=True):
        chosen = MagicMock()
        chosen.isValid.return_value = valid
        chosen.name.return_value = hex_c
        dialog = MagicMock()
        dialog.getColor.return_value = chosen
        with gui_harness.qt_available():
            with patch.object(CMP, "QColorDialog", dialog):
                cmp_dlg.pick_slot_color(slot, which)

    def test_a_chosen_colour_is_stored_on_the_slot(self):
        cmp_dlg = self._blank_compare()
        self._pick(cmp_dlg, cmp_dlg.slots[2], "p", "#abcdef")
        self.assertEqual(cmp_dlg.slots[2].color("p"), "#abcdef")

    def test_choosing_a_colour_redraws(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        self._pick(cmp_dlg, cmp_dlg.slots[0], "n", "#abcdef")
        self.assertTrue(_FakeVisualizer.calls)

    def test_cancelling_leaves_the_colour_alone(self):
        cmp_dlg = self._blank_compare()
        before = cmp_dlg.slots[3].color("p")
        self._pick(cmp_dlg, cmp_dlg.slots[3], "p", "#abcdef", valid=False)
        self.assertEqual(cmp_dlg.slots[3].color("p"), before)

    def test_a_colour_only_affects_its_own_slot(self):
        cmp_dlg = self._blank_compare()
        other = cmp_dlg.slots[1].color("p")
        self._pick(cmp_dlg, cmp_dlg.slots[2], "p", "#abcdef")
        self.assertEqual(cmp_dlg.slots[1].color("p"), other)

    def test_a_slot_with_no_stylesheet_reports_a_default(self):
        cmp_dlg = self._blank_compare()
        cmp_dlg.slots[0].btn_p.setStyleSheet("")
        self.assertEqual(cmp_dlg.slots[0].color("p"), "#ff0000")


class TestRefreshButton(_CompareCase):
    def test_it_redraws_the_orbitals(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        _FakeVisualizer.calls = []
        cmp_dlg.refresh_view()
        self.assertEqual(len(_FakeVisualizer.calls), 1)

    def test_it_works_even_while_redraws_are_suspended(self):
        """It is an explicit request, not a side effect of a setting."""
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        _FakeVisualizer.calls = []
        cmp_dlg._suspend += 1
        try:
            cmp_dlg.refresh_view()
        finally:
            cmp_dlg._suspend -= 1
        self.assertEqual(len(_FakeVisualizer.calls), 1)

    def test_it_leaves_the_suspend_count_as_it_found_it(self):
        cmp_dlg = self._blank_compare()
        cmp_dlg._suspend += 2
        cmp_dlg.refresh_view()
        self.assertEqual(cmp_dlg._suspend, 2)
        cmp_dlg._suspend -= 2

    def test_it_is_harmless_with_nothing_shown(self):
        cmp_dlg = self._blank_compare()
        _FakeVisualizer.calls = []
        cmp_dlg.refresh_view()
        self.assertEqual(_FakeVisualizer.calls, [])

    def test_the_button_is_wired_to_it(self):
        cmp_dlg = self._blank_compare()
        self._write_cube("1")
        self._enable(cmp_dlg, 0, "1")
        _FakeVisualizer.calls = []
        cmp_dlg.btn_refresh.clicked.emit()
        self.assertEqual(len(_FakeVisualizer.calls), 1)


class TestSyncIsovalue(_CompareCase):
    def test_every_slot_takes_orbital_ones_isovalue(self):
        cmp_dlg = self._blank_compare()
        cmp_dlg.slots[0].spin_iso.setValue(0.066)
        cmp_dlg.sync_isovalue()
        self.assertTrue(
            all(abs(s.spin_iso.value() - 0.066) < 1e-9 for s in cmp_dlg.slots)
        )

    def test_orbital_one_is_left_alone(self):
        cmp_dlg = self._blank_compare()
        cmp_dlg.slots[0].spin_iso.setValue(0.066)
        cmp_dlg.sync_isovalue()
        self.assertAlmostEqual(cmp_dlg.slots[0].spin_iso.value(), 0.066)

    def test_the_other_settings_stay_independent(self):
        """Only the contour level is shared; the colours must stay distinct."""
        cmp_dlg = self._blank_compare()
        cmp_dlg.slots[1].set_color("p", "#00ff00")
        cmp_dlg.slots[1].spin_opacity.setValue(0.9)
        cmp_dlg.slots[1].combo_style.setCurrentText("Points")
        cmp_dlg.sync_isovalue()
        self.assertEqual(cmp_dlg.slots[1].color("p"), "#00ff00")
        self.assertAlmostEqual(cmp_dlg.slots[1].spin_opacity.value(), 0.9)
        self.assertEqual(cmp_dlg.slots[1].combo_style.currentText(), "Points")

    def test_syncing_redraws_at_the_new_level(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 1, "1")
        cmp_dlg.slots[0].spin_iso.setValue(0.066)
        cmp_dlg.sync_isovalue()
        self.assertAlmostEqual(_FakeVisualizer.calls[-1]["iso"], 0.066)

    def test_syncing_with_no_slots_is_harmless(self):
        cmp_dlg = self._blank_compare()
        cmp_dlg.slots = []
        cmp_dlg.sync_isovalue()
        self.assertEqual(_FakeVisualizer.calls, [])

    def test_the_synced_isovalue_is_saved(self):
        cmp_dlg = self._blank_compare()
        cmp_dlg.slots[0].spin_iso.setValue(0.066)
        cmp_dlg.sync_isovalue()
        cmp_dlg.closeEvent(MagicMock())
        self.assertAlmostEqual(self._blank_compare().slots[2].spin_iso.value(), 0.066)


class TestLiveRedraw(_CompareCase):
    """Cheap settings re-contour a grid already in memory, so they apply at
    once; only a missing cube costs anything, and that is what the button is
    for."""

    def _armed(self, cmp_dlg):
        _FakeVisualizer.calls = []
        return _FakeVisualizer.calls

    def test_changing_the_isovalue_redraws_without_the_button(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        calls = self._armed(cmp_dlg)
        cmp_dlg.slots[0].spin_iso.valueChanged.emit(0.05)
        self.assertEqual(len(calls), 1)

    def test_changing_the_opacity_redraws(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        calls = self._armed(cmp_dlg)
        cmp_dlg.slots[0].spin_opacity.valueChanged.emit(0.9)
        self.assertEqual(len(calls), 1)

    def test_changing_the_style_redraws(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        calls = self._armed(cmp_dlg)
        cmp_dlg.slots[0].combo_style.currentTextChanged.emit("Points")
        self.assertEqual(len(calls), 1)

    def test_toggling_smooth_shading_redraws(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        calls = self._armed(cmp_dlg)
        cmp_dlg.slots[0].check_smooth.toggled.emit(False)
        self.assertEqual(len(calls), 1)

    def test_ticking_show_draws_that_orbital(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        calls = self._armed(cmp_dlg)
        cmp_dlg.slots[0].check_on.toggled.emit(True)
        self.assertEqual(len(calls), 1)

    def test_unticking_show_removes_that_orbital(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        slot = self._enable(cmp_dlg, 0, "1")
        calls = self._armed(cmp_dlg)
        slot.check_on.setChecked(False)
        slot.check_on.toggled.emit(False)
        self.assertEqual(calls, [])

    def test_switching_orbital_redraws(self):
        self._write_cube("1")
        self._write_cube("2")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        calls = self._armed(cmp_dlg)
        self._enable(cmp_dlg, 0, "2")
        cmp_dlg.slots[0].combo_mo.currentIndexChanged.emit(0)
        self.assertTrue(calls[-1]["path"].endswith("_MO_2.cube"))

    def test_a_half_built_dialog_never_renders(self):
        """Signals are wired last; setup writes every widget itself."""
        cmp_dlg = self._blank_compare()
        cmp_dlg._ready = False
        calls = self._armed(cmp_dlg)
        cmp_dlg.on_live_change()
        self.assertEqual(calls, [])

    def test_a_suspended_batch_does_not_render(self):
        """Real Qt setters DO emit, unlike these stubs, so the guard is what
        stops apply_selection and sync_isovalue redrawing per widget."""
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        calls = self._armed(cmp_dlg)
        cmp_dlg._suspend += 1
        try:
            cmp_dlg.on_live_change()
            cmp_dlg.render_all()
        finally:
            cmp_dlg._suspend -= 1
        self.assertEqual(calls, [])

    def test_the_guard_is_released_afterwards(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        cmp_dlg.sync_isovalue()
        self.assertEqual(cmp_dlg._suspend, 0)
        calls = self._armed(cmp_dlg)
        cmp_dlg.on_live_change()
        self.assertEqual(len(calls), 1)

    def test_syncing_iso_redraws_exactly_once(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        calls = self._armed(cmp_dlg)
        cmp_dlg.sync_isovalue()
        self.assertEqual(len(calls), 1)

    def test_apply_selection_leaves_the_guard_balanced_even_on_failure(self):
        cmp_dlg = self._blank_compare()
        cmp_dlg.slots[0].combo_mo.setCurrentIndex = MagicMock(
            side_effect=RuntimeError("boom")
        )
        with self.assertRaises(RuntimeError):
            cmp_dlg.apply_selection(["1"])
        self.assertEqual(cmp_dlg._suspend, 0)


class TestUpdateButtonState(_CompareCase):
    """The button is the only thing the user must press, so its enabled state
    has to track "something shown still needs computing" exactly."""

    def test_it_is_off_when_every_shown_orbital_is_cached(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        cmp_dlg.render_all()
        self.assertFalse(cmp_dlg.btn_update.isEnabled())

    def test_it_says_so_when_there_is_nothing_to_do(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        cmp_dlg.render_all()
        self.assertIn("Ready", cmp_dlg.btn_update.text())

    def test_it_is_on_when_a_shown_orbital_has_no_cube(self):
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        cmp_dlg.render_all()
        self.assertTrue(cmp_dlg.btn_update.isEnabled())

    def test_it_counts_what_is_missing(self):
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        self._enable(cmp_dlg, 1, "2")
        cmp_dlg.render_all()
        self.assertIn("2", cmp_dlg.btn_update.text())

    def test_hiding_the_only_missing_orbital_turns_it_off(self):
        cmp_dlg = self._blank_compare()
        slot = self._enable(cmp_dlg, 0, "1")
        self.assertTrue(cmp_dlg.btn_update.isEnabled())
        slot.check_on.setChecked(False)
        cmp_dlg.render_all()
        self.assertFalse(cmp_dlg.btn_update.isEnabled())

    def test_switching_to_an_uncached_orbital_turns_it_on(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        cmp_dlg.render_all()
        self.assertFalse(cmp_dlg.btn_update.isEnabled())
        self._enable(cmp_dlg, 0, "2")
        cmp_dlg.render_all()
        self.assertTrue(cmp_dlg.btn_update.isEnabled())

    def test_generating_turns_it_off_again(self):
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        self.assertTrue(cmp_dlg.btn_update.isEnabled())

        def fake_generate(keys, on_done=None, force=False):
            for k in keys:
                self._write_cube(k)
            if on_done:
                on_done()

        with patch.object(self.dlg, "generate_cubes", fake_generate):
            cmp_dlg.update_view()
        self.assertFalse(cmp_dlg.btn_update.isEnabled())

    def test_a_failed_generation_leaves_it_on(self):
        """The work still needs doing, so the user must still be able to ask."""
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")

        def fake_generate(keys, on_done=None, force=False):
            if on_done:
                on_done()  # nothing was written

        with patch.object(self.dlg, "generate_cubes", fake_generate):
            cmp_dlg.update_view()
        self.assertTrue(cmp_dlg.btn_update.isEnabled())

    def test_it_is_disabled_while_the_batch_runs(self):
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        seen = {}

        def fake_generate(keys, on_done=None, force=False):
            seen["enabled_during"] = cmp_dlg.btn_update.isEnabled()

        with patch.object(self.dlg, "generate_cubes", fake_generate):
            cmp_dlg.update_view()
        self.assertFalse(seen["enabled_during"])

    def test_an_unavailable_generator_leaves_it_pressable(self):
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        with patch.object(self.dlg, "generate_cubes", side_effect=RuntimeError("boom")):
            with patch.object(CMP, "QMessageBox"):
                cmp_dlg.update_view()
        self.assertTrue(cmp_dlg.btn_update.isEnabled())

    def test_an_empty_table_leaves_nothing_to_press(self):
        self.dlg.tree.clear()
        cmp_dlg = self._make_compare()
        self.assertFalse(cmp_dlg.btn_update.isEnabled())

    def test_clearing_all_turns_it_off(self):
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        self.assertTrue(cmp_dlg.btn_update.isEnabled())
        cmp_dlg.clear_all()
        self.assertFalse(cmp_dlg.btn_update.isEnabled())

    def test_a_slot_that_is_off_never_counts_as_missing(self):
        cmp_dlg = self._blank_compare()
        self.assertEqual(cmp_dlg.missing_keys(), [])

    def test_the_same_missing_orbital_twice_counts_once(self):
        cmp_dlg = self._blank_compare()
        self._enable(cmp_dlg, 0, "1")
        self._enable(cmp_dlg, 1, "1")
        self.assertEqual(cmp_dlg.missing_keys(), ["1"])


class TestOpenBehaviour(_CompareCase):
    def test_cached_orbitals_are_drawn_as_soon_as_the_window_opens(self):
        for mo in ("1", "2", "3", "0"):
            self._write_cube(mo)
        self._make_compare()
        self.assertEqual(len(_FakeVisualizer.calls), 4)

    def test_opening_on_the_defaults_generates_nothing(self):
        """Four cubes is minutes of work nobody asked for."""
        with patch.object(self.dlg, "generate_cubes") as gen:
            self._make_compare()
            gen.assert_not_called()

    def test_opening_on_the_defaults_offers_the_button_instead(self):
        cmp_dlg = self._make_compare()
        self.assertTrue(cmp_dlg.btn_update.isEnabled())

    def test_a_preselection_is_generated_and_drawn_on_open(self):
        self._select_rows("2")
        with patch.object(self.dlg, "generate_cubes") as gen:
            self._make_compare()
            self.assertEqual(gen.call_args[0][0], ["2"])

    def test_a_preselection_already_cached_is_not_regenerated(self):
        self._write_cube("2")
        self._select_rows("2")
        with patch.object(self.dlg, "generate_cubes") as gen:
            cmp_dlg = self._make_compare()
            gen.assert_not_called()
        self.assertEqual(len(_FakeVisualizer.calls), 1)
        self.assertFalse(cmp_dlg.btn_update.isEnabled())

    def test_reopening_from_a_new_preselection_generates_it(self):
        cmp_dlg = self._make_compare()
        self.dlg.compare_dlg = cmp_dlg
        self._select_rows("0")
        with patch.object(self.dlg, "generate_cubes") as gen:
            self.dlg.show_compare_dialog()
            self.assertEqual(gen.call_args[0][0], ["0"])


class TestContrastText(unittest.TestCase):
    def test_dark_backgrounds_get_white_text(self):
        self.assertEqual(CMP.contrast_text("#000080"), "white")

    def test_light_backgrounds_get_black_text(self):
        self.assertEqual(CMP.contrast_text("#ffff00"), "black")

    def test_a_malformed_colour_does_not_raise(self):
        self.assertEqual(CMP.contrast_text("nonsense"), "black")

    def test_a_non_hex_triplet_does_not_raise(self):
        self.assertEqual(CMP.contrast_text("#zzzzzz"), "black")


class TestClearAndClose(_CompareCase):
    def test_clear_all_switches_every_slot_off(self):
        cmp_dlg = self._blank_compare()
        cmp_dlg.clear_all()
        self.assertFalse(any(s.is_on() for s in cmp_dlg.slots))

    def test_clear_all_removes_every_actor(self):
        cmp_dlg = self._blank_compare()
        cmp_dlg.clear_all()
        removed = [c[0][0] for c in self.dlg.mw.plotter.remove_actor.call_args_list]
        for i in range(CMP.SLOT_COUNT):
            self.assertIn(f"mo_cmp{i}_p", removed)
            self.assertIn(f"mo_cmp{i}_n", removed)

    def test_closing_removes_every_actor(self):
        cmp_dlg = self._blank_compare()
        cmp_dlg.closeEvent(MagicMock())
        removed = [c[0][0] for c in self.dlg.mw.plotter.remove_actor.call_args_list]
        self.assertIn("mo_cmp3_n", removed)

    def test_closing_tells_the_mo_dialog_to_forget_it(self):
        self.dlg.compare_dlg = object()
        cmp_dlg = self._blank_compare()
        cmp_dlg.closeEvent(MagicMock())
        self.assertIsNone(self.dlg.compare_dlg)

    def test_closing_accepts_the_event_rather_than_recursing(self):
        cmp_dlg = self._blank_compare()
        event = MagicMock()
        cmp_dlg.closeEvent(event)
        event.accept.assert_called_once()

    def test_escape_routes_through_the_cleanup(self):
        cmp_dlg = self._blank_compare()
        with patch.object(cmp_dlg, "close") as close:
            cmp_dlg.reject()
            close.assert_called_once()


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestSettingsPersistence(_CompareCase):
    def _settings(self):
        import json

        with open(os.path.join(self.tmp, "settings.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_closing_writes_the_slot_settings(self):
        cmp_dlg = self._blank_compare()
        cmp_dlg.slots[1].set_color("p", "#010203")
        cmp_dlg.closeEvent(MagicMock())
        self.assertEqual(
            self._settings()["mo_compare"]["slots"][1]["color_p"], "#010203"
        )

    def test_every_slot_is_written(self):
        self._blank_compare().closeEvent(MagicMock())
        self.assertEqual(len(self._settings()["mo_compare"]["slots"]), CMP.SLOT_COUNT)

    def test_rendering_saves_too(self):
        self._write_cube("1")
        cmp_dlg = self._blank_compare()
        cmp_dlg.slots[0].spin_iso.setValue(0.031)
        cmp_dlg.render_all()
        self.assertAlmostEqual(self._settings()["mo_compare"]["slots"][0]["iso"], 0.031)

    def test_saved_colours_come_back_on_reopen(self):
        first = self._blank_compare()
        first.slots[2].set_color("n", "#0a0b0c")
        first.closeEvent(MagicMock())
        self.assertEqual(self._blank_compare().slots[2].color("n"), "#0a0b0c")

    def test_saved_isovalue_opacity_and_style_come_back(self):
        first = self._blank_compare()
        first.slots[1].spin_iso.setValue(0.044)
        first.slots[1].spin_opacity.setValue(0.9)
        first.slots[1].combo_style.setCurrentText("Points")
        first.slots[1].check_smooth.setChecked(False)
        first.closeEvent(MagicMock())

        slot = self._blank_compare().slots[1]
        self.assertAlmostEqual(slot.spin_iso.value(), 0.044)
        self.assertAlmostEqual(slot.spin_opacity.value(), 0.9)
        self.assertEqual(slot.combo_style.currentText(), "Points")
        self.assertFalse(slot.check_smooth.isChecked())

    def test_which_slots_are_on_is_not_persisted(self):
        """That follows the loaded file's orbitals, not last session's."""
        first = self._blank_compare()
        first.closeEvent(MagicMock())
        self.assertNotIn("enabled", self._settings()["mo_compare"]["slots"][0])
        self.assertTrue(all(s.is_on() for s in self._make_compare().slots))

    def test_a_saved_slot_one_colour_wins_over_the_mo_dialog_default(self):
        first = self._blank_compare()
        first.slots[0].set_color("p", "#111111")
        first.closeEvent(MagicMock())
        self.dlg.set_btn_color(self.dlg.btn_color_p, "#999999")
        self.assertEqual(self._blank_compare().slots[0].color("p"), "#111111")

    def test_the_mo_dialog_settings_are_not_clobbered(self):
        self.dlg.presets["Mine"] = {"iso": 0.05}
        self.dlg.settings_file = os.path.join(self.tmp, "settings.json")
        self.dlg.save_settings()
        self._blank_compare().closeEvent(MagicMock())
        self.assertIn("Mine", self._settings()["mo_settings"]["presets"])

    def test_nothing_saved_yet_leaves_the_defaults_alone(self):
        cmp_dlg = self._blank_compare()
        self.assertEqual(cmp_dlg.slots[1].color("p"), CMP.DEFAULT_COLORS[1][0])

    def test_a_corrupt_settings_file_is_survivable(self):
        with open(os.path.join(self.tmp, "settings.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(len(self._blank_compare().slots), CMP.SLOT_COUNT)

    def test_a_settings_file_without_a_compare_section_is_survivable(self):
        with open(os.path.join(self.tmp, "settings.json"), "w", encoding="utf-8") as fh:
            fh.write('{"mo_settings": {}}')
        self.assertEqual(len(self._blank_compare().slots), CMP.SLOT_COUNT)

    def test_malformed_slot_entries_are_ignored(self):
        with open(os.path.join(self.tmp, "settings.json"), "w", encoding="utf-8") as fh:
            fh.write('{"mo_compare": {"slots": ["nonsense", {"iso": "big"}]}}')
        cmp_dlg = self._blank_compare()
        self.assertAlmostEqual(cmp_dlg.slots[1].spin_iso.value(), 0.02)

    def test_save_preserves_several_other_dialogs_sections(self):
        import json

        others = {
            "mo_settings": {"iso": 0.02},
            "nmr_settings": {"ref": "TMS"},
            "thermal_settings": {"show_details": True},
        }
        with open(os.path.join(self.tmp, "settings.json"), "w", encoding="utf-8") as fh:
            json.dump(others, fh)

        self._blank_compare().closeEvent(MagicMock())

        on_disk = self._settings()
        for key, val in others.items():
            self.assertEqual(on_disk[key], val)
        self.assertIn("mo_compare", on_disk)

    def test_round_trips_through_the_shared_helpers(self):
        S = gui_harness.load_isolated("settings")
        cmp_dlg = self._blank_compare()
        cmp_dlg.slots[0].spin_iso.setValue(0.077)
        cmp_dlg.save_settings()

        section = S.load_section(os.path.join(self.tmp, "settings.json"), "mo_compare")
        self.assertAlmostEqual(section["slots"][0]["iso"], 0.077)

    def test_an_unwritable_settings_path_does_not_break_closing(self):
        cmp_dlg = self._blank_compare()
        CMP.__file__ = os.path.join(self.tmp, "job.out", "mo_compare.py")
        with open(self.out, "w", encoding="utf-8") as fh:
            fh.write("x")
        event = MagicMock()
        cmp_dlg.closeEvent(event)
        event.accept.assert_called_once()


if __name__ == "__main__":
    unittest.main()
