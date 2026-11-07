"""
tests/test_gui_interaction.py
Covers the main dialog's interactive surface: the click-vs-drag event filter on
the 3D plotter, atom picking and the selection rules it drives, drag-and-drop
of results and directories, the directory picker widget, and the file menu
actions (reload, open output, open directory).
"""

import contextlib
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

G = gui_harness.load_isolated("gui")


class _Point:
    def __init__(self, x, y):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y

    def toPoint(self):
        return self


def _mouse(kind, x=0, y=0, button="L"):
    ev = MagicMock()
    ev.type.return_value = kind
    ev.button.return_value = button
    ev.position.return_value = _Point(x, y)
    return ev


# ---------------------------------------------------------------------------
# _ClickFilter: a click is a press and release within 5 px
# ---------------------------------------------------------------------------


class TestClickFilter(unittest.TestCase):
    def setUp(self):
        self.clicked = []
        self.pressed = []
        self.filter = G._ClickFilter(
            lambda x, y, obj: self.clicked.append((x, y)),
            press_callback=lambda x, y, obj: self.pressed.append((x, y)),
        )
        self.press_type = G.QEvent.Type.MouseButtonPress
        self.release_type = G.QEvent.Type.MouseButtonRelease
        self.left = G.Qt.MouseButton.LeftButton

    def test_a_press_reports_its_position(self):
        self.filter.eventFilter(None, _mouse(self.press_type, 10, 20, self.left))
        self.assertEqual(self.pressed, [(10, 20)])

    def test_press_and_release_in_place_is_a_click(self):
        obj = object()
        self.filter.eventFilter(obj, _mouse(self.press_type, 10, 20, self.left))
        self.filter.eventFilter(obj, _mouse(self.release_type, 10, 20, self.left))
        self.assertEqual(self.clicked, [(10, 20)])

    def test_a_small_wobble_still_counts_as_a_click(self):
        obj = object()
        self.filter.eventFilter(obj, _mouse(self.press_type, 10, 20, self.left))
        self.filter.eventFilter(obj, _mouse(self.release_type, 13, 23, self.left))
        self.assertEqual(self.clicked, [(13, 23)])

    def test_a_drag_is_not_a_click(self):
        # dragging rotates the camera; it must not select an atom
        obj = object()
        self.filter.eventFilter(obj, _mouse(self.press_type, 10, 20, self.left))
        self.filter.eventFilter(obj, _mouse(self.release_type, 60, 90, self.left))
        self.assertEqual(self.clicked, [])

    def test_a_release_without_a_press_is_ignored(self):
        self.filter.eventFilter(None, _mouse(self.release_type, 10, 20, self.left))
        self.assertEqual(self.clicked, [])

    def test_a_right_click_is_ignored(self):
        obj = object()
        self.filter.eventFilter(obj, _mouse(self.press_type, 10, 20, "R"))
        self.filter.eventFilter(obj, _mouse(self.release_type, 10, 20, "R"))
        self.assertEqual(self.clicked, [])
        self.assertEqual(self.pressed, [])

    def test_events_are_never_consumed(self):
        # returning True would break camera interaction
        result = self.filter.eventFilter(None, _mouse(self.press_type, 1, 1, self.left))
        self.assertFalse(result)

    def test_the_press_is_forgotten_after_a_release(self):
        obj = object()
        self.filter.eventFilter(obj, _mouse(self.press_type, 10, 20, self.left))
        self.filter.eventFilter(obj, _mouse(self.release_type, 10, 20, self.left))
        self.filter.eventFilter(obj, _mouse(self.release_type, 10, 20, self.left))
        self.assertEqual(len(self.clicked), 1)


# ---------------------------------------------------------------------------
# Dialog fixture
# ---------------------------------------------------------------------------


class _GuiCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        self.path = os.path.join(self.tmp, "job.out")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("GAMESS output\n")

        self.plotter = MagicMock()
        self.v3d = MagicMock()
        self.v3d.plotter = self.plotter
        self.e3d = MagicMock()
        self.e3d.selected_atoms_3d = set()

        self.mw = MagicMock()
        self.mw.view_3d_manager = self.v3d
        self.mw.edit_3d_manager = self.e3d

        self.context = MagicMock()
        self.parser = MagicMock()
        self.parser.filename = self.path
        self.parser.data = {}

        self.dlg = G.GAMESSResultAnalyzerDialog(
            self.mw, self.parser, self.path, context=self.context
        )
        self.dlg.mw = self.mw


# ---------------------------------------------------------------------------
# Picking lifecycle and selection rules
# ---------------------------------------------------------------------------


class TestPickingLifecycle(_GuiCase):
    def setUp(self):
        super().setUp()
        # Construction already enables picking; start from a clean slate.
        self.plotter.reset_mock()

    def test_enabling_installs_the_event_filter(self):
        self.dlg._enable_plotter_picking()
        self.plotter.installEventFilter.assert_called_once()
        self.assertIsNotNone(self.dlg._click_filter)

    def test_disabling_removes_the_event_filter(self):
        self.dlg._enable_plotter_picking()
        installed = self.dlg._click_filter
        self.dlg._disable_plotter_picking()
        self.plotter.removeEventFilter.assert_called_once_with(installed)
        self.assertIsNone(self.dlg._click_filter)

    def test_enabling_without_a_viewer_is_a_noop(self):
        self.mw.view_3d_manager = None
        self.dlg._enable_plotter_picking()
        self.plotter.installEventFilter.assert_not_called()

    def test_disabling_when_never_enabled_is_a_noop(self):
        self.dlg._click_filter = None
        self.dlg._disable_plotter_picking()
        self.plotter.removeEventFilter.assert_not_called()


class TestPlotterClick(_GuiCase):
    def _click(self, atom=None, shift=False):
        self.dlg._pending_click_atom = atom
        mods = MagicMock()
        mods.__and__ = lambda self, other: 1 if shift else 0
        with patch.object(G.QApplication, "keyboardModifiers", return_value=mods):
            self.dlg._on_plotter_click(0, 0, MagicMock())

    def test_clicking_an_atom_selects_it(self):
        self._click(atom=2)
        self.assertEqual(self.e3d.selected_atoms_3d, {2})

    def test_clicking_a_second_atom_replaces_the_selection(self):
        self.e3d.selected_atoms_3d = {1}
        self._click(atom=2)
        self.assertEqual(self.e3d.selected_atoms_3d, {2})

    def test_clicking_the_only_selected_atom_deselects_it(self):
        self.e3d.selected_atoms_3d = {2}
        self._click(atom=2)
        self.assertEqual(self.e3d.selected_atoms_3d, set())

    def test_shift_click_adds_to_the_selection(self):
        self.e3d.selected_atoms_3d = {1}
        self._click(atom=2, shift=True)
        self.assertEqual(self.e3d.selected_atoms_3d, {1, 2})

    def test_shift_clicking_a_selected_atom_removes_it(self):
        self.e3d.selected_atoms_3d = {1, 2}
        self._click(atom=2, shift=True)
        self.assertEqual(self.e3d.selected_atoms_3d, {1})

    def test_clicking_empty_space_clears_the_selection(self):
        self.e3d.selected_atoms_3d = {1, 2}
        self._click(atom=None)
        self.e3d.selected_atoms_3d.clear.assert_called_once() if hasattr(
            self.e3d.selected_atoms_3d, "clear_calls"
        ) else self.assertEqual(self.e3d.selected_atoms_3d, set())

    def test_the_viewer_is_refreshed_after_a_click(self):
        self._click(atom=2)
        self.e3d.update_selection_visuals.assert_called_once()

    def test_the_pending_atom_is_consumed(self):
        self._click(atom=2)
        self.assertIsNone(self.dlg._pending_click_atom)

    def test_a_click_without_an_editor_is_ignored(self):
        self.mw.edit_3d_manager = None
        self.dlg._pending_click_atom = 2
        self.dlg._on_plotter_click(0, 0, MagicMock())  # must not raise


class TestAtomPicking(_GuiCase):
    def test_picking_without_a_viewer_returns_nothing(self):
        self.mw.view_3d_manager = None
        self.assertIsNone(self.dlg._pick_atom_at(10, 10, MagicMock()))

    def test_picking_without_atoms_returns_nothing(self):
        self.v3d.atom_positions_3d = []
        self.assertIsNone(self.dlg._pick_atom_at(10, 10, MagicMock()))

    def test_picking_without_an_atom_actor_returns_nothing(self):
        self.v3d.atom_actor = None
        self.assertIsNone(self.dlg._pick_atom_at(10, 10, MagicMock()))

    def test_a_press_records_the_atom_under_the_cursor(self):
        with patch.object(self.dlg, "_pick_atom_at", return_value=3):
            self.dlg._on_plotter_press(10, 10, MagicMock())
        self.assertEqual(self.dlg._pending_click_atom, 3)

    def test_a_press_on_empty_space_records_nothing(self):
        with patch.object(self.dlg, "_pick_atom_at", return_value=None):
            self.dlg._on_plotter_press(10, 10, MagicMock())
        self.assertIsNone(self.dlg._pending_click_atom)

    def test_a_failing_pick_is_tolerated(self):
        with patch.object(self.dlg, "_pick_atom_at", side_effect=RuntimeError("vtk")):
            self.dlg._on_plotter_press(10, 10, MagicMock())
        self.assertIsNone(self.dlg._pending_click_atom)


# ---------------------------------------------------------------------------
# Drag and drop
# ---------------------------------------------------------------------------


def _drop_event(paths):
    urls = []
    for p in paths:
        url = MagicMock()
        url.toLocalFile.return_value = p
        urls.append(url)
    mime = MagicMock()
    mime.hasUrls.return_value = bool(urls)
    mime.urls.return_value = urls
    ev = MagicMock()
    ev.mimeData.return_value = mime
    return ev


@contextlib.contextmanager
def _deferred():
    """Hold what the drop queues for later; call the result to run it."""
    queued = []

    def run():
        for call in queued:
            call.args[-1]()

    with patch.object(G.QTimer, "singleShot") as single_shot:
        queued = single_shot.call_args_list
        yield run


class TestDragAndDrop(_GuiCase):
    def test_an_output_file_is_accepted(self):
        ev = _drop_event([os.path.join(self.tmp, "other.out")])
        self.dlg.dragEnterEvent(ev)
        ev.setDropAction.assert_called_once_with(G.Qt.DropAction.CopyAction)
        ev.accept.assert_called_once()

    def test_a_directory_is_accepted(self):
        ev = _drop_event([self.tmp])
        self.dlg.dragEnterEvent(ev)
        ev.setDropAction.assert_called_once_with(G.Qt.DropAction.CopyAction)
        ev.accept.assert_called_once()

    def test_an_unrelated_file_is_refused(self):
        ev = _drop_event([os.path.join(self.tmp, "notes.txt")])
        self.dlg.dragEnterEvent(ev)
        ev.ignore.assert_called_once()

    def test_a_drag_without_files_is_refused(self):
        ev = _drop_event([])
        self.dlg.dragEnterEvent(ev)
        ev.ignore.assert_called_once()

    def test_dropping_an_output_file_loads_it(self):
        target = os.path.join(self.tmp, "dropped.out")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("x")
        with patch.object(self.dlg, "load_file") as load, _deferred() as run:
            self.dlg.dropEvent(_drop_event([target]))
            load.assert_not_called()
            run()
        load.assert_called_once_with(target)

    def test_dropping_a_directory_opens_the_picker(self):
        with patch.object(self.dlg, "_open_directory_path") as open_dir, _deferred() as run:
            self.dlg.dropEvent(_drop_event([self.tmp]))
            open_dir.assert_not_called()
            run()
        open_dir.assert_called_once_with(self.tmp)

    def test_dropping_an_unrelated_file_does_nothing(self):
        with patch.object(self.dlg, "load_file") as load:
            self.dlg.dropEvent(_drop_event([os.path.join(self.tmp, "notes.txt")]))
        load.assert_not_called()

    def test_only_the_first_usable_item_is_taken(self):
        first = os.path.join(self.tmp, "a.out")
        second = os.path.join(self.tmp, "b.out")
        for p in (first, second):
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("x")
        with patch.object(self.dlg, "load_file") as load, _deferred() as run:
            self.dlg.dropEvent(_drop_event([first, second]))
            run()
        load.assert_called_once_with(first)

    def test_a_drop_is_answered_as_a_copy_before_anything_opens(self):
        target = os.path.join(self.tmp, "copy.out")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("x")
        ev = _drop_event([target])
        with patch.object(self.dlg, "load_file") as load, _deferred() as run:
            self.dlg.dropEvent(ev)
            ev.setDropAction.assert_called_once_with(G.Qt.DropAction.CopyAction)
            ev.accept.assert_called_once()
            ev.acceptProposedAction.assert_not_called()
            load.assert_not_called()
            run()
        load.assert_called_once_with(target)

    def test_a_source_that_can_only_move_is_refused(self):
        target = os.path.join(self.tmp, "moved.out")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("x")
        ev = _drop_event([target])
        ev.possibleActions.return_value.__and__.return_value = 0
        with patch.object(self.dlg, "load_file") as load, _deferred() as run:
            self.dlg.dropEvent(ev)
            run()
        ev.ignore.assert_called_once()
        ev.accept.assert_not_called()
        load.assert_not_called()


# ---------------------------------------------------------------------------
# Directory picker widget
# ---------------------------------------------------------------------------


class TestDirectoryPicker(_GuiCase):
    def _picker(self, names=("a.out", "b.out")):
        return G._DirectoryFilePicker(self.dlg, self.tmp, list(names))

    def test_every_file_is_listed(self):
        picker = self._picker()
        self.assertEqual(picker._list.count(), 2)

    def test_the_first_entry_is_preselected(self):
        picker = self._picker()
        self.assertEqual(picker._list.currentRow(), 0)

    def test_each_entry_carries_its_full_path(self):
        picker = self._picker()
        item = picker._list.item(1)
        self.assertEqual(
            item.data(G.Qt.ItemDataRole.UserRole), os.path.join(self.tmp, "b.out")
        )

    def test_an_empty_directory_preselects_nothing(self):
        picker = self._picker(names=())
        self.assertEqual(picker._list.count(), 0)
        self.assertEqual(picker._list.currentRow(), -1)

    def test_nothing_is_chosen_until_the_user_acts(self):
        self.assertIsNone(self._picker().selected_path)

    def test_double_clicking_an_entry_chooses_it(self):
        picker = self._picker()
        item = MagicMock()
        item.data.return_value = os.path.join(self.tmp, "b.out")
        with patch.object(picker, "accept"):
            picker._accept_item(item)
        self.assertEqual(picker.selected_path, os.path.join(self.tmp, "b.out"))

    def test_confirming_takes_the_highlighted_entry(self):
        picker = self._picker()
        picker._list.setCurrentRow(1)
        with patch.object(picker, "accept") as accept:
            picker._accept_selection()
        accept.assert_called_once()
        self.assertEqual(picker.selected_path, os.path.join(self.tmp, "b.out"))

    def test_confirming_with_nothing_highlighted_chooses_nothing(self):
        picker = self._picker()
        picker._list.setCurrentRow(-1)
        with patch.object(picker, "accept") as accept:
            picker._accept_selection()
        accept.assert_not_called()
        self.assertIsNone(picker.selected_path)


# ---------------------------------------------------------------------------
# File menu actions
# ---------------------------------------------------------------------------


class TestFileActions(_GuiCase):
    def _modifiers(self, shift=False):
        """open_file branches to the directory picker on Shift+click."""
        mods = MagicMock()
        mods.__and__ = lambda self, other: 1 if shift else 0
        return patch.object(G.QApplication, "keyboardModifiers", return_value=mods)

    def test_opening_a_file_loads_the_chosen_path(self):
        chosen = os.path.join(self.tmp, "picked.out")
        with self._modifiers():
            with patch.object(
                G.QFileDialog, "getOpenFileName", return_value=(chosen, "")
            ):
                with patch.object(self.dlg, "load_file") as load:
                    self.dlg.open_file()
        load.assert_called_once_with(chosen)

    def test_cancelling_the_open_dialog_loads_nothing(self):
        with self._modifiers():
            with patch.object(G.QFileDialog, "getOpenFileName", return_value=("", "")):
                with patch.object(self.dlg, "load_file") as load:
                    self.dlg.open_file()
        load.assert_not_called()

    def test_shift_opening_goes_to_the_directory_picker(self):
        with self._modifiers(shift=True):
            with patch.object(self.dlg, "open_directory") as open_dir:
                with patch.object(self.dlg, "load_file") as load:
                    self.dlg.open_file()
        open_dir.assert_called_once()
        load.assert_not_called()

    def test_reloading_re_reads_the_current_file(self):
        with patch.object(self.dlg, "load_file") as load:
            self.dlg.reload_file()
        load.assert_called_once_with(self.path)

    def test_reloading_without_a_file_does_nothing(self):
        self.dlg.file_path = ""
        with patch.object(self.dlg, "load_file") as load:
            self.dlg.reload_file()
        load.assert_not_called()

    def test_choosing_a_directory_opens_the_picker(self):
        with patch.object(G.QFileDialog, "getExistingDirectory", return_value=self.tmp):
            with patch.object(self.dlg, "_open_directory_path") as open_dir:
                self.dlg.open_directory()
        open_dir.assert_called_once_with(self.tmp)

    def test_cancelling_the_directory_chooser_does_nothing(self):
        with patch.object(G.QFileDialog, "getExistingDirectory", return_value=""):
            with patch.object(self.dlg, "_open_directory_path") as open_dir:
                self.dlg.open_directory()
        open_dir.assert_not_called()

    def test_revealing_the_output_opens_it_externally(self):
        with patch.object(G, "QDesktopServices") as services:
            self.dlg.open_output_file()
        services.openUrl.assert_called_once()

    def test_revealing_a_missing_output_warns(self):
        self.dlg.file_path = os.path.join(self.tmp, "gone.out")
        with patch.object(G.QMessageBox, "warning") as warn:
            self.dlg.open_output_file()
        warn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
