"""
tests/test_mo_selection.py
Covers MODialog's selection handling: auto-loading an already-generated cube
when a row is chosen, the coefficient check that gates the Visualize button
(and the "add these GAMESS keywords" hint shown when they are missing), and the
batch generation queue.

The worker that actually evaluates the basis set is stubbed out — these tests
are about the queueing and gating around it.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

M = gui_harness.load_isolated("mo_analysis")


def _mos():
    return {
        str(i): {"id": i, "energy": e, "occ": occ, "spin": "restricted"}
        for i, (e, occ) in enumerate([(-1.0, 2.0), (-0.5, 2.0), (0.2, 0.0)])
    }


class _MOCase(unittest.TestCase):
    def setUp(self):
        # Queue semantics are covered by the native Qt subprocess regressions.
        def connect_double(signal, callback, owner=None, signature=(), cleanup=()):
            signal.connect(callback)
            return callback
        callbacks = patch.object(M, "connect_queued", connect_double)
        callbacks.start()
        self.addCleanup(callbacks.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        saved = M.__file__
        M.__file__ = os.path.join(self.tmp, "mo_analysis.py")
        self.addCleanup(lambda: setattr(M, "__file__", saved))

        self.out = os.path.join(self.tmp, "job.out")
        self.host = MagicMock()
        self.host.parser.filename = self.out
        self.host.parser.data = {"mo_coeffs": {"1": {"coeffs": [0.1, 0.2]}}}
        self.host.file_path = self.out

        self.dlg = M.MODialog(self.host, _mos(), result_dir=self.tmp)
        self.dlg.parent_dlg = self.host
        self.dlg.mw = MagicMock()

    def _row(self, mo_id):
        """The tree row whose MO column reads `mo_id`."""
        tree = self.dlg.tree
        return next(
            tree.topLevelItem(i)
            for i in range(tree.topLevelItemCount())
            if tree.topLevelItem(i).text(0) == str(mo_id)
        )

    def _select(self, *mo_ids):
        rows = [self._row(i) for i in mo_ids]
        self.dlg.tree.selectedItems = lambda: rows
        return rows

    def _cube_for(self, mo_id):
        cube_dir = os.path.join(self.tmp, "job_cubes")
        os.makedirs(cube_dir, exist_ok=True)
        path = os.path.join(cube_dir, f"job_MO_{mo_id}.cube")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("cube")
        return path


# ---------------------------------------------------------------------------
# Choosing a row
# ---------------------------------------------------------------------------


class TestRowSelected(_MOCase):
    def test_an_already_generated_cube_is_shown(self):
        path = self._cube_for(1)
        with patch.object(self.dlg, "show_cube") as show:
            self.dlg.on_item_changed(self._row(1), None)
        show.assert_called_once_with(path)

    def test_a_row_without_a_cube_shows_nothing(self):
        with patch.object(self.dlg, "show_cube") as show:
            self.dlg.on_item_changed(self._row(1), None)
        show.assert_not_called()

    def test_clearing_the_row_selection_is_ignored(self):
        with patch.object(self.dlg, "show_cube") as show:
            self.dlg.on_item_changed(None, None)
        show.assert_not_called()


class TestVisualiseGating(_MOCase):
    def test_an_orbital_with_coefficients_can_be_visualised(self):
        self._select(1)
        self.dlg.on_selection_changed()
        self.assertTrue(self.dlg.btn_vis.isEnabled())

    def test_an_orbital_without_coefficients_cannot(self):
        self._select(2)
        self.dlg.on_selection_changed()
        self.assertFalse(self.dlg.btn_vis.isEnabled())

    def test_a_missing_orbital_explains_itself(self):
        self._select(2)
        self.dlg.on_selection_changed()
        self.assertIn("No coefficients", self.dlg.btn_vis.toolTip())

    def test_missing_coefficients_surface_the_required_keywords(self):
        self._select(2)
        self.dlg.on_selection_changed()
        self.assertTrue(self.dlg.lbl_warning.isVisible())
        self.assertIn("NPRINT=7", self.dlg.lbl_warning.text())
        self.assertTrue(self.dlg.btn_copy_input.isVisible())

    def test_the_hint_is_hidden_once_coefficients_are_present(self):
        self._select(2)
        self.dlg.on_selection_changed()
        self._select(1)
        self.dlg.on_selection_changed()
        self.assertFalse(self.dlg.lbl_warning.isVisible())
        self.assertEqual(self.dlg.btn_vis.toolTip(), "")

    def test_no_selection_disables_visualisation(self):
        self.dlg.tree.selectedItems = lambda: []
        self.dlg.on_selection_changed()
        self.assertFalse(self.dlg.btn_vis.isEnabled())

    def test_no_selection_does_not_show_the_hint(self):
        self.dlg.tree.selectedItems = lambda: []
        self.dlg.on_selection_changed()
        self.assertFalse(self.dlg.lbl_warning.isVisible())

    def test_a_result_without_coefficients_at_all_is_handled(self):
        self.host.parser.data = {}
        self._select(1)
        self.dlg.on_selection_changed()
        self.assertFalse(self.dlg.btn_vis.isEnabled())


# ---------------------------------------------------------------------------
# Batch generation queue
# ---------------------------------------------------------------------------


class TestGenerationQueue(_MOCase):
    def test_visualising_queues_the_selected_orbitals(self):
        self._select(0, 1, 2)
        with patch.object(self.dlg, "_generate_single_mo"):
            self.dlg.visualize_selected_mos()
        # the first is popped and handed to the worker straight away
        self.assertEqual(self.dlg.generation_queue, ["1", "2"])

    def test_the_first_orbital_starts_generating_immediately(self):
        self._select(0, 1)
        with patch.object(self.dlg, "_generate_single_mo") as gen:
            self.dlg.visualize_selected_mos()
        gen.assert_called_once_with("0")

    def test_visualising_nothing_queues_nothing(self):
        self.dlg.tree.selectedItems = lambda: []
        with patch.object(self.dlg, "_generate_single_mo") as gen:
            self.dlg.visualize_selected_mos()
        gen.assert_not_called()
        self.assertEqual(self.dlg.generation_queue, [])

    def test_the_queue_is_replaced_not_appended(self):
        self.dlg.generation_queue = ["stale"]
        self._select(1)
        with patch.object(self.dlg, "_generate_single_mo"):
            self.dlg.visualize_selected_mos()
        self.assertEqual(self.dlg.generation_queue, [])

    def test_the_queue_is_drained_one_at_a_time(self):
        self.dlg.generation_queue = ["0", "1", "2"]
        with patch.object(self.dlg, "_generate_single_mo") as gen:
            self.dlg.process_generation_queue()
        gen.assert_called_once_with("0")
        self.assertEqual(self.dlg.generation_queue, ["1", "2"])

    def test_draining_an_empty_queue_closes_the_progress_dialog(self):
        progress = MagicMock()
        self.dlg.progress_dialog = progress
        self.dlg.generation_queue = []
        self.dlg.process_generation_queue()
        progress.close.assert_called_once()
        self.assertIsNone(self.dlg.progress_dialog)

    def test_draining_an_empty_queue_starts_no_work(self):
        self.dlg.generation_queue = []
        self.dlg.progress_dialog = None
        with patch.object(self.dlg, "_generate_single_mo") as gen:
            self.dlg.process_generation_queue()
        gen.assert_not_called()


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Regenerate (overwrite) — context menu on the orbital table
# ---------------------------------------------------------------------------


class _RegenCase(_MOCase):
    """Drives _generate_single_mo far enough to reach the cache check."""

    def setUp(self):
        super().setUp()
        self.host.parser.data = {
            "mo_coeffs": {"1": {"coeffs": [{"coeff": 0.1}, {"coeff": 0.2}]}},
            "atoms": ["H", "H"],
            "coords": [(0.0, 0.0, 0.0), (0.0, 0.0, 0.74)],
        }
        engine = MagicMock()
        engine.n_basis = 2
        patcher = patch.object(self.dlg, "get_engine", return_value=engine)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.shown = []
        patcher2 = patch.object(self.dlg, "show_cube", self.shown.append)
        patcher2.start()
        self.addCleanup(patcher2.stop)

        self.worker_cls = patch.object(M, "CalcWorker")
        self.worker = self.worker_cls.start()
        self.addCleanup(self.worker_cls.stop)
        retained = patch.object(M, "_RUNNING_WORKERS", set())
        retained.start()
        self.addCleanup(retained.stop)

    def _write_cached_cube(self):
        path = self.dlg.get_cube_path("1")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("stale cube\n")
        return path


class TestGenerationShutdown(_RegenCase):
    def test_closed_dialog_cannot_restart_the_generation_queue(self):
        self.dlg.closeEvent(MagicMock())
        with patch.object(self.dlg, "_generate_single_mo") as generate:
            self.dlg.process_generation_queue()
        generate.assert_not_called()
        self.assertIsNone(self.dlg.progress_dialog)
        self.assertEqual(self.dlg.generation_queue, [])

    def test_close_cancels_the_worker_and_discards_the_batch(self):
        self.dlg.progress_dialog = None
        self.dlg._generate_single_mo("1")
        worker = self.worker.return_value
        self.dlg.generation_queue = ["2"]
        done = MagicMock()
        self.dlg.generation_done_cb = done
        progress = self.dlg.progress_dialog
        with patch.object(progress, "close") as close_progress:
            self.dlg.closeEvent(MagicMock())
        worker.cancel.assert_called_once()
        self.assertEqual(self.dlg.generation_queue, [])
        self.assertIsNone(self.dlg.generation_done_cb)
        close_progress.assert_called_once()
        self.assertIn(worker, M._RUNNING_WORKERS)

    def test_late_results_cannot_redraw_or_advance_the_closed_dialog(self):
        self.dlg._generate_single_mo("1")
        worker = self.worker.return_value
        result = worker.finished_sig.connect.call_args.args[0]
        self.dlg.closeEvent(MagicMock())
        with patch.object(self.dlg, "process_generation_queue") as advance:
            result(True, "old.cube")
        self.assertEqual(self.shown, [])
        advance.assert_not_called()
        self.assertIn(worker, M._RUNNING_WORKERS)
        worker.finished.connect.call_args.args[0]()
        self.assertNotIn(worker, M._RUNNING_WORKERS)


class TestRegenerateOverwrites(_RegenCase):
    def test_an_existing_cube_is_reused_without_recomputing(self):
        path = self._write_cached_cube()
        self.dlg._generate_single_mo("1")
        self.assertEqual(self.shown, [path])
        self.worker.assert_not_called()

    def test_forcing_recomputes_even_though_the_cube_exists(self):
        self._write_cached_cube()
        self.dlg.generation_force = True
        self.dlg._generate_single_mo("1")
        # The cached file must NOT be displayed; a worker must run instead.
        self.assertEqual(self.shown, [])
        self.worker.assert_called_once()

    def test_the_worker_writes_over_the_existing_path(self):
        path = self._write_cached_cube()
        self.dlg.generation_force = True
        self.dlg._generate_single_mo("1")
        self.assertEqual(self.worker.call_args[0][-1], path)

    def test_regenerate_selected_sets_the_force_flag(self):
        self._select(1)
        with patch.object(self.dlg, "_generate_single_mo"):
            self.dlg.regenerate_selected_mos()
        self.assertTrue(self.dlg.generation_force)

    def test_plain_visualise_leaves_the_force_flag_off(self):
        self._select(1)
        with patch.object(self.dlg, "_generate_single_mo"):
            self.dlg.visualize_selected_mos()
        self.assertFalse(self.dlg.generation_force)

    def test_force_is_cleared_once_the_batch_drains(self):
        """Otherwise the next plain Visualize would silently recompute too."""
        self.dlg.generation_force = True
        self.dlg.generation_queue = []
        self.dlg.progress_dialog = None
        self.dlg.process_generation_queue()
        self.assertFalse(self.dlg.generation_force)

    def test_a_stale_force_flag_does_not_survive_into_a_new_batch(self):
        self.dlg.generation_force = True
        self._select(1)
        with patch.object(self.dlg, "_generate_single_mo"):
            self.dlg.visualize_selected_mos()
        self.assertFalse(self.dlg.generation_force)


class TestTreeContextMenu(_RegenCase):
    def _menu(self):
        """Capture the QMenu built for a right-click."""
        created = []

        class _Menu:
            def __init__(self, *a, **k):
                self.actions = []
                created.append(self)

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
                return self.chosen

        return created, _Menu

    def test_no_menu_without_a_selection(self):
        self.dlg.tree.selectedItems = lambda: []
        created, menu_cls = self._menu()
        with patch.object(M, "QMenu", menu_cls):
            self.dlg.show_tree_context_menu(MagicMock())
        self.assertEqual(created, [])

    def test_menu_offers_visualise_regenerate_and_compare(self):
        self._select(1)
        created, menu_cls = self._menu()
        menu_cls.chosen = None
        with patch.object(M, "QMenu", menu_cls):
            self.dlg.show_tree_context_menu(MagicMock())
        labels = [a.text for a in created[0].actions]
        # The cached-cube description sits at the top, above the actions.
        self.assertEqual(len(labels), 4)
        self.assertIn("Visualize", labels[1])
        self.assertIn("Regenerate", labels[2])
        self.assertIn("Compare", labels[3])

    def test_regenerate_is_disabled_when_nothing_is_cached(self):
        self._select(1)
        created, menu_cls = self._menu()
        menu_cls.chosen = None
        with patch.object(M, "QMenu", menu_cls):
            self.dlg.show_tree_context_menu(MagicMock())
        self.assertFalse(created[0].actions[2].enabled)

    def test_regenerate_is_enabled_once_a_cube_exists(self):
        self._write_cached_cube()
        self._select(1)
        created, menu_cls = self._menu()
        menu_cls.chosen = None
        with patch.object(M, "QMenu", menu_cls):
            self.dlg.show_tree_context_menu(MagicMock())
        self.assertTrue(created[0].actions[2].enabled)

    def test_choosing_regenerate_forces_a_rebuild(self):
        self._write_cached_cube()
        self._select(1)
        created, menu_cls = self._menu()

        class _MenuChoosingRegen(menu_cls):
            def exec(self, *a, **k):
                return self.actions[2]

        with patch.object(M, "QMenu", _MenuChoosingRegen):
            with patch.object(self.dlg, "_generate_single_mo"):
                self.dlg.show_tree_context_menu(MagicMock())
        self.assertTrue(self.dlg.generation_force)

    def test_choosing_visualise_does_not_force(self):
        self._write_cached_cube()
        self._select(1)
        created, menu_cls = self._menu()

        class _MenuChoosingVis(menu_cls):
            def exec(self, *a, **k):
                return self.actions[1]

        with patch.object(M, "QMenu", _MenuChoosingVis):
            with patch.object(self.dlg, "_generate_single_mo"):
                self.dlg.show_tree_context_menu(MagicMock())
        self.assertFalse(self.dlg.generation_force)

    def test_dismissing_the_menu_starts_no_work(self):
        self._select(1)
        created, menu_cls = self._menu()
        menu_cls.chosen = None
        with patch.object(M, "QMenu", menu_cls):
            with patch.object(self.dlg, "_generate_single_mo") as gen:
                self.dlg.show_tree_context_menu(MagicMock())
        gen.assert_not_called()


# ---------------------------------------------------------------------------
# One batch at a time, and Cancel
# ---------------------------------------------------------------------------


class _Running:
    """A worker stand-in that reports it is still computing."""

    def __init__(self):
        self.cancelled = False

    def isRunning(self):
        return True

    def cancel(self):
        self.cancelled = True


class TestGenerationBusyGuard(_RegenCase):
    def test_visualise_is_refused_while_a_worker_runs(self):
        self.dlg.worker = _Running()
        self._select(1)
        with patch.object(M, "notify") as note, patch.object(
            self.dlg, "_generate_single_mo"
        ) as gen:
            self.dlg.visualize_selected_mos()
        gen.assert_not_called()
        self.assertIn("already in progress", note.call_args[0][1])

    def test_generate_cubes_is_refused_but_still_calls_back(self):
        self.dlg.worker = _Running()
        self.dlg.generation_queue = ["kept"]
        seen = []
        with patch.object(M, "notify"):
            self.dlg.generate_cubes(["1"], on_done=lambda: seen.append(True))
        self.assertEqual(seen, [True])
        self.assertEqual(self.dlg.generation_queue, ["kept"])

    def test_a_stub_worker_does_not_count_as_running(self):
        self.dlg.worker = MagicMock()
        self.assertFalse(self.dlg._generation_busy())

    def test_a_deleted_worker_does_not_count_as_running(self):
        class _Gone:
            def isRunning(self):
                raise RuntimeError("wrapped C/C++ object has been deleted")

        self.dlg.worker = _Gone()
        self.assertFalse(self.dlg._generation_busy())


class TestCancelGeneration(_RegenCase):
    def test_cancel_drops_the_queue_and_stops_the_worker(self):
        worker = _Running()
        self.dlg.worker = worker
        self.dlg.generation_queue = ["2", "3"]
        self.dlg.cancel_generation()
        self.assertEqual(self.dlg.generation_queue, [])
        self.assertTrue(worker.cancelled)

    def test_the_progress_cancel_button_is_wired(self):
        self.dlg.progress_dialog = None
        self.dlg.generation_force = True
        with patch.object(M, "QProgressDialog") as progress:
            self.dlg._generate_single_mo("1")
        progress.return_value.canceled.connect.assert_called_once_with(
            self.dlg.cancel_generation
        )

    def _finish(self, success, message):
        self.dlg.progress_dialog = None
        self.dlg.generation_force = True
        with patch.object(M, "QProgressDialog"):
            self.dlg._generate_single_mo("1")
        on_finished = self.worker.return_value.finished_sig.connect.call_args[0][0]
        with patch.object(M.QMessageBox, "warning") as warn, patch.object(
            self.dlg, "process_generation_queue"
        ) as nxt:
            on_finished(success, message)
        return warn, nxt

    def test_a_cancelled_worker_is_not_reported_as_a_failure(self):
        warn, nxt = self._finish(False, M.CANCELLED)
        warn.assert_not_called()
        nxt.assert_called_once_with()

    def test_a_real_failure_is_still_reported(self):
        warn, _ = self._finish(False, "disk full")
        self.assertIn("disk full", warn.call_args[0][2])
