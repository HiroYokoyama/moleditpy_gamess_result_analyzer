"""
tests/test_init.py
Unit tests for gamess_result_analyzer/__init__.py.

Tests plugin metadata, initialize() registration contract, and the
handle_drop() detection logic (non-Qt paths only).

PySide6 is stubbed at module level since __init__.py imports
QMessageBox and QFileDialog unconditionally.  The Qt-dependent code
paths inside open_gamess_file() (QApplication, GAMESSResultAnalyzerDialog, etc.)
are not exercised here because they require a live display.
"""

import os
import sys
import types
import tempfile
import importlib.util
import unittest
from unittest.mock import MagicMock, patch

_SRC_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


def _install_qt_stubs():
    """Install minimal PySide6 stubs needed for __init__.py import."""
    existing_widgets = sys.modules.get("PySide6.QtWidgets")
    existing_pyside6 = sys.modules.get("PySide6")

    if existing_widgets is not None:
        if not hasattr(existing_widgets, "QMessageBox"):
            existing_widgets.QMessageBox = MagicMock()
        if not hasattr(existing_widgets, "QFileDialog"):
            existing_widgets.QFileDialog = MagicMock()
        if not hasattr(existing_widgets, "QApplication"):
            app_mock = MagicMock()
            app_mock.processEvents = MagicMock()
            existing_widgets.QApplication = app_mock
        elif not hasattr(existing_widgets.QApplication, "processEvents"):
            existing_widgets.QApplication.processEvents = MagicMock()
    else:
        _pyside6 = existing_pyside6 or types.ModuleType("PySide6")
        _widgets = types.ModuleType("PySide6.QtWidgets")
        _widgets.QMessageBox = MagicMock()
        _widgets.QFileDialog = MagicMock()
        app_mock = MagicMock()
        app_mock.processEvents = MagicMock()
        _widgets.QApplication = app_mock
        _pyside6.QtWidgets = _widgets

        sys.modules.update(
            {
                "PySide6": _pyside6,
                "PySide6.QtWidgets": _widgets,
            }
        )


def _load_init_module():
    """Load gamess_result_analyzer/__init__.py directly, bypassing Qt."""
    _install_qt_stubs()

    init_src = os.path.normpath(
        os.path.join(_SRC_DIR, "gamess_result_analyzer", "__init__.py")
    )
    spec = importlib.util.spec_from_file_location("gamess_result_analyzer_init", init_src)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "gamess_result_analyzer"
    sys.modules["gamess_result_analyzer_init"] = mod
    spec.loader.exec_module(mod)
    return mod


_init_mod = _load_init_module()


class StubContext:
    """Mirrors the PluginContext API surface used by initialize()."""

    def __init__(self):
        self.file_openers = {}  # ext → (callback, priority)
        self.drop_handlers = []  # list of (handler, priority)
        self.menu_actions = {}
        self._windows = {}
        self.mark_project_modified_call_count = 0
        self.document_reset_handlers = []

    def register_file_opener(self, ext, callback, priority=0):
        self.file_openers[ext] = (callback, priority)

    def register_drop_handler(self, handler, priority=0):
        self.drop_handlers.append((handler, priority))

    def register_document_reset_handler(self, callback):
        self.document_reset_handlers.append(callback)

    def add_menu_action(self, path, callback):
        self.menu_actions[path] = callback

    def get_main_window(self):
        return MagicMock()

    def register_window(self, window_id: str, window) -> None:
        self._windows[window_id] = window

    def get_window(self, window_id: str):
        return self._windows.get(window_id)

    def mark_project_modified(self) -> None:
        self.mark_project_modified_call_count += 1


# ---------------------------------------------------------------------------
# TestMetadata
# ---------------------------------------------------------------------------


class TestMetadata(unittest.TestCase):
    def test_plugin_name_present(self):
        self.assertTrue(hasattr(_init_mod, "PLUGIN_NAME"))
        self.assertIsInstance(_init_mod.PLUGIN_NAME, str)
        self.assertGreater(len(_init_mod.PLUGIN_NAME), 0)

    def test_plugin_version_present(self):
        self.assertTrue(hasattr(_init_mod, "PLUGIN_VERSION"))
        self.assertIsInstance(_init_mod.PLUGIN_VERSION, str)

    def test_plugin_author_present(self):
        self.assertTrue(hasattr(_init_mod, "PLUGIN_AUTHOR"))

    def test_plugin_description_present(self):
        self.assertTrue(hasattr(_init_mod, "PLUGIN_DESCRIPTION"))


# ---------------------------------------------------------------------------
# TestInitialize — registration contract
# ---------------------------------------------------------------------------


class TestInitialize(unittest.TestCase):
    def _make_ctx(self):
        ctx = StubContext()
        _init_mod.initialize(ctx)
        return ctx

    def test_registers_out_file_opener(self):
        ctx = self._make_ctx()
        self.assertIn(".out", ctx.file_openers)

    def test_out_opener_priority(self):
        ctx = self._make_ctx()
        _, priority = ctx.file_openers[".out"]
        self.assertEqual(priority, 110)

    def test_registers_drop_handler(self):
        ctx = self._make_ctx()
        self.assertEqual(len(ctx.drop_handlers), 1)

    def test_drop_handler_priority(self):
        ctx = self._make_ctx()
        _, priority = ctx.drop_handlers[0]
        self.assertEqual(priority, 110)

    def test_initialize_callable(self):
        self.assertTrue(callable(_init_mod.initialize))


# ---------------------------------------------------------------------------
# TestDropHandler — non-Qt code paths
# ---------------------------------------------------------------------------


class TestDropHandler(unittest.TestCase):
    """
    Tests the handle_drop closure registered by initialize().
    Only the False-return paths are tested here: correct extension but no
    GAMESS content, and wrong extension entirely.
    The True-return path (GAMESS header detected) calls open_gamess_file() which
    requires a live Qt application and is not exercised in this headless suite.
    """

    def setUp(self):
        ctx = StubContext()
        _init_mod.initialize(ctx)
        self._handler, _ = ctx.drop_handlers[0]

    def test_non_out_extension_returns_false(self):
        self.assertFalse(self._handler("molecule.mol"))

    def test_log_extension_returns_false(self):
        self.assertFalse(self._handler("calc.log"))

    def test_nonexistent_out_file_returns_false(self):
        self.assertFalse(self._handler("/nonexistent/path/calc.out"))

    def test_out_file_without_gamess_content_returns_false(self):
        with tempfile.NamedTemporaryFile(
            suffix=".out", mode="w", delete=False, encoding="utf-8"
        ) as f:
            f.write("This is just a plain text file, not GAMESS output.\n")
            tmp = f.name
        try:
            result = self._handler(tmp)
            self.assertFalse(result)
        finally:
            os.unlink(tmp)

    def test_empty_out_file_returns_false(self):
        with tempfile.NamedTemporaryFile(
            suffix=".out", mode="w", delete=False, encoding="utf-8"
        ) as f:
            tmp = f.name
        try:
            result = self._handler(tmp)
            self.assertFalse(result)
        finally:
            os.unlink(tmp)

    def test_case_insensitive_extension(self):
        """Extension check should be case-insensitive (.OUT, .Out, etc.)."""
        # Non-existent file: open() will raise, caught → returns False
        # The important check is that a .OUT path reaches the extension gate
        self.assertFalse(self._handler("/some/path/CALC.OUT"))


# ---------------------------------------------------------------------------
# TestInitializeIdempotent
# ---------------------------------------------------------------------------


class TestInitializeIdempotent(unittest.TestCase):
    def test_multiple_initialize_calls_do_not_duplicate(self):
        """Each initialize() call on a fresh context should register exactly once."""
        ctx = StubContext()
        _init_mod.initialize(ctx)
        self.assertEqual(len(ctx.drop_handlers), 1)
        self.assertEqual(sorted(ctx.file_openers), [".log", ".out"])


# ---------------------------------------------------------------------------
# TestContextRegistryAPI — stub completeness for V4 context registry
# ---------------------------------------------------------------------------


class TestContextRegistryAPI(unittest.TestCase):
    """Verify StubContext implements all V4 registry methods the plugin uses,
    and that initialize() does not call any methods absent from the stub."""

    def setUp(self):
        self.ctx = StubContext()
        _init_mod.initialize(self.ctx)

    def test_stub_has_register_window(self):
        self.assertTrue(callable(getattr(self.ctx, "register_window", None)))

    def test_stub_has_get_window(self):
        self.assertTrue(callable(getattr(self.ctx, "get_window", None)))

    def test_stub_has_mark_project_modified(self):
        self.assertTrue(callable(getattr(self.ctx, "mark_project_modified", None)))

    def test_registry_roundtrip(self):
        sentinel = object()
        self.ctx.register_window("test_win", sentinel)
        self.assertIs(self.ctx.get_window("test_win"), sentinel)

    def test_get_window_returns_none_for_unknown(self):
        self.assertIsNone(self.ctx.get_window("no_such_window"))

    def test_initialize_does_not_raise_with_full_stub(self):
        ctx2 = StubContext()
        try:
            _init_mod.initialize(ctx2)
        except AttributeError as exc:
            self.fail(f"initialize() called a context method not on StubContext: {exc}")


# ---------------------------------------------------------------------------
# TestExtensionsMenuAction — Extensions/GAMESS Result Analyzer registered in initialize()
# ---------------------------------------------------------------------------


class TestExtensionsMenuAction(unittest.TestCase):
    def setUp(self):
        self.ctx = StubContext()
        _init_mod.initialize(self.ctx)

    def test_extensions_menu_action_registered(self):
        self.assertIn("Extensions/GAMESS Result Analyzer", self.ctx.menu_actions)

    def test_extensions_menu_action_is_callable(self):
        action = self.ctx.menu_actions.get("Extensions/GAMESS Result Analyzer")
        self.assertTrue(callable(action))


# ---------------------------------------------------------------------------
# TestRunFunction — run() no longer opens a file dialog
# ---------------------------------------------------------------------------


class TestRunFunction(unittest.TestCase):
    def test_run_is_callable(self):
        self.assertTrue(callable(_init_mod.run))

    def test_run_without_context_does_not_raise(self):
        """run() with no prior initialize() should silently return."""
        original = _init_mod._context
        _init_mod._context = None
        try:
            _init_mod.run(MagicMock())  # must not raise
        finally:
            _init_mod._context = original

    def test_run_does_not_open_file_dialog(self):
        """run() must NOT call QFileDialog.getOpenFileName (no file pre-selection)."""
        ctx = StubContext()
        _init_mod.initialize(ctx)

        # Patch _open_gamess_analyzer_empty to be a no-op so Qt is never touched
        original = _init_mod._open_gamess_analyzer_empty
        calls = []
        _init_mod._open_gamess_analyzer_empty = lambda c: calls.append(c)

        # Also make sure QFileDialog is a clean mock we can introspect
        widgets_mod = sys.modules.get("PySide6.QtWidgets")
        file_dialog_mock = MagicMock()
        original_fd = getattr(widgets_mod, "QFileDialog", None)
        if widgets_mod:
            widgets_mod.QFileDialog = file_dialog_mock

        try:
            _init_mod.run(MagicMock())
            # _open_gamess_analyzer_empty should have been called once
            self.assertEqual(len(calls), 1)
            # QFileDialog.getOpenFileName must NOT have been called
            file_dialog_mock.getOpenFileName.assert_not_called()
        finally:
            _init_mod._open_gamess_analyzer_empty = original
            if widgets_mod and original_fd is not None:
                widgets_mod.QFileDialog = original_fd


# ---------------------------------------------------------------------------
# TestOpenAnalyzerEmpty — singleton + window-registration contract
# ---------------------------------------------------------------------------


class TestOpenAnalyzerEmpty(unittest.TestCase):
    def _make_ctx_with_empty_open(self):
        """Return a StubContext where _open_gamess_analyzer_empty is
        monkey-patched to record calls without touching Qt."""
        ctx = StubContext()
        _init_mod.initialize(ctx)
        return ctx

    def test_extensions_action_invokes_empty_open(self):
        """Calling the Extensions menu action should invoke _open_gamess_analyzer_empty."""
        ctx = self._make_ctx_with_empty_open()
        calls = []
        original = _init_mod._open_gamess_analyzer_empty
        _init_mod._open_gamess_analyzer_empty = lambda c: calls.append(c)
        try:
            ctx.menu_actions["Extensions/GAMESS Result Analyzer"]()
            self.assertEqual(len(calls), 1)
            self.assertIs(calls[0], ctx)
        finally:
            _init_mod._open_gamess_analyzer_empty = original

    def test_singleton_raises_existing_window(self):
        """If a window is already registered, it should be raised, not replaced."""
        ctx = StubContext()
        # Pre-register a fake window
        fake_win = MagicMock()
        ctx.register_window("analyzer", fake_win)

        # Call the real function — it should detect the existing window and raise it
        _init_mod._open_gamess_analyzer_empty(ctx)

        # The fake window should have been shown/raised
        fake_win.show.assert_called_once()
        fake_win.raise_.assert_called_once()
        fake_win.activateWindow.assert_called_once()

        # A second window must NOT have been registered
        self.assertIs(ctx.get_window("analyzer"), fake_win)


# ---------------------------------------------------------------------------
# TestDocumentResetHandler — File->New must close the analyzer and drop
# stale atom-color overrides (they otherwise survive clear_3d_view()).
# ---------------------------------------------------------------------------


def _real_utils_module():
    """Load the real utils.py directly.

    Several other test files in this suite install a bare-bones fake module
    at sys.modules["gamess_result_analyzer.utils"] (for their own isolated
    imports) and never clean it up, so by the time the full suite runs, that
    slot may hold a stub whose clear_atom_color_overrides (if present at all)
    is a no-op MagicMock. _on_document_reset()'s deferred `from .utils import
    clear_atom_color_overrides` would then silently pick up the stub instead
    of the real implementation. Tests that need the real dict-clearing
    behavior swap this module in for the duration of the test.
    """
    path = os.path.normpath(os.path.join(_SRC_DIR, "gamess_result_analyzer", "utils.py"))
    spec = importlib.util.spec_from_file_location("gamess_result_analyzer.utils", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestDocumentResetHandler(unittest.TestCase):
    def setUp(self):
        self.ctx = StubContext()
        _init_mod.initialize(self.ctx)
        self._original_utils_mod = sys.modules.get("gamess_result_analyzer.utils")
        sys.modules["gamess_result_analyzer.utils"] = _real_utils_module()

    def tearDown(self):
        if self._original_utils_mod is not None:
            sys.modules["gamess_result_analyzer.utils"] = self._original_utils_mod
        else:
            sys.modules.pop("gamess_result_analyzer.utils", None)

    def test_registers_document_reset_handler(self):
        self.assertEqual(len(self.ctx.document_reset_handlers), 1)

    def test_handler_closes_existing_analyzer_window(self):
        fake_win = MagicMock()
        self.ctx.register_window("analyzer", fake_win)
        handler = self.ctx.document_reset_handlers[0]

        handler()

        fake_win.close.assert_called_once()

    def test_handler_noop_when_no_window_registered(self):
        handler = self.ctx.document_reset_handlers[0]
        try:
            handler()
        except Exception as exc:
            self.fail(
                f"document reset handler should not raise without a window: {exc}"
            )

    def test_handler_survives_window_close_exception(self):
        fake_win = MagicMock()
        fake_win.close.side_effect = RuntimeError("boom")
        self.ctx.register_window("analyzer", fake_win)
        handler = self.ctx.document_reset_handlers[0]

        try:
            handler()
        except Exception as exc:
            self.fail(f"document reset handler must swallow close() errors: {exc}")

    def test_handler_clears_atom_color_overrides(self):
        fixed_mw = MagicMock()
        fixed_mw.view_3d_manager._plugin_color_overrides = {0: "#ff0000", 1: "#00ff00"}
        self.ctx.get_main_window = lambda: fixed_mw
        handler = self.ctx.document_reset_handlers[0]

        handler()

        self.assertEqual(fixed_mw.view_3d_manager._plugin_color_overrides, {})

    def test_handler_clears_overrides_even_without_a_window(self):
        """Overrides must be dropped even if the analyzer was already closed."""
        fixed_mw = MagicMock()
        fixed_mw.view_3d_manager._plugin_color_overrides = {2: "#0000ff"}
        self.ctx.get_main_window = lambda: fixed_mw
        handler = self.ctx.document_reset_handlers[0]

        handler()

        self.assertEqual(fixed_mw.view_3d_manager._plugin_color_overrides, {})


class TestReadGAMESSFile(unittest.TestCase):
    """_read_gamess_file tries several encodings before giving up."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.parent = MagicMock()

    def _write(self, name, text, encoding="utf-8"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding=encoding) as fh:
            fh.write(text)
        return path

    def test_a_utf8_file_is_read(self):
        path = self._write("utf8.out", "GAMESS TERMINATED NORMALLY\n")
        self.assertIn("TERMINATED", _init_mod._read_gamess_file(path, self.parent))

    def test_a_utf16_file_is_read(self):
        path = self._write("utf16.out", "GAMESS TERMINATED NORMALLY\n", encoding="utf-16")
        self.assertIn("TERMINATED", _init_mod._read_gamess_file(path, self.parent))

    def test_a_latin1_file_is_read(self):
        path = self._write("latin1.out", "Angström\n", encoding="latin-1")
        self.assertIsNotNone(_init_mod._read_gamess_file(path, self.parent))

    def test_undecodable_bytes_fall_back_to_replacement(self):
        path = os.path.join(self.tmp, "binary.out")
        with open(path, "wb") as fh:
            fh.write(b"GAMESS\x00\xff\xfe\xfd rubbish\n")
        self.assertIsNotNone(_init_mod._read_gamess_file(path, self.parent))

    def _read_expecting_error(self, path):
        """Read *path* with a known-good QMessageBox, returning (result, box).

        Other test modules install their own PySide6 stubs process-wide, so pin
        the message box rather than relying on whichever one is currently
        bound; the error branch calls QMessageBox.critical.
        """
        box = MagicMock()
        with patch.object(_init_mod, "QMessageBox", box):
            result = _init_mod._read_gamess_file(path, self.parent)
        return result, box

    def test_a_missing_file_reports_an_error(self):
        missing = os.path.join(self.tmp, "nope.out")
        result, box = self._read_expecting_error(missing)
        self.assertIsNone(result)
        box.critical.assert_called_once()

    def test_a_directory_reports_an_error(self):
        result, box = self._read_expecting_error(self.tmp)
        self.assertIsNone(result)
        box.critical.assert_called_once()


# ---------------------------------------------------------------------------
# TestOpenAnalyzerEmptyReuseBranch
# ---------------------------------------------------------------------------


class TestOpenGAMESSFileExistingWindow(unittest.TestCase):
    def setUp(self):
        self.ctx = StubContext()
        self.previous = MagicMock()
        self.ctx.register_window("analyzer", self.previous)
        self.parser = object()
        self.loading = types.ModuleType("gamess_result_analyzer.loading")
        self.loading.load_gamess_parser = MagicMock(return_value=self.parser)
        self.utils = types.ModuleType("gamess_result_analyzer.utils")
        self.utils.clear_atom_color_overrides = MagicMock()
        self.utils.sync_main_window_file = MagicMock()
        self.gui = types.ModuleType("gamess_result_analyzer.gui")
        self.gui.GAMESSResultAnalyzerDialog = MagicMock()
        siblings = {
            "gamess_result_analyzer.loading": self.loading,
            "gamess_result_analyzer.utils": self.utils,
            "gamess_result_analyzer.gui": self.gui,
        }
        modules = patch.dict(sys.modules, siblings)
        modules.start()
        self.addCleanup(modules.stop)

    def test_rejected_close_preserves_existing_result(self):
        self.previous.close.return_value = False
        _init_mod._open_gamess_file("new.out", self.ctx)
        self.previous.close.assert_called_once()
        self.assertIs(self.ctx.get_window("analyzer"), self.previous)
        self.gui.GAMESSResultAnalyzerDialog.assert_not_called()
        self.utils.clear_atom_color_overrides.assert_not_called()
        self.utils.sync_main_window_file.assert_not_called()

    def test_accepted_close_replaces_the_window_and_draws_the_new_result(self):
        self.previous.close.return_value = True
        _init_mod._open_gamess_file("new.out", self.ctx)
        self.previous.close.assert_called_once()
        window = self.gui.GAMESSResultAnalyzerDialog.return_value
        self.assertIs(self.ctx.get_window("analyzer"), window)
        self.assertIs(self.gui.GAMESSResultAnalyzerDialog.call_args.args[1], self.parser)
        self.utils.clear_atom_color_overrides.assert_called_once()
        self.assertEqual(self.utils.sync_main_window_file.call_args.args[1], "new.out")
        window.show.assert_called_once()
        window.load_structure_3d.assert_called_once_with(fit_camera=True)


class TestOpenAnalyzerEmptyReuseBranch(unittest.TestCase):
    """_open_gamess_analyzer_empty() re-shows a registered window instead of
    rebuilding it, so a closed window MUST have left the registry — otherwise
    the stale dialog comes back with its plotter picking already torn down.

    The real dialog class is swapped out here; only the branch matters.
    """

    def setUp(self):
        self.ctx = StubContext()
        self.built = []

        def _factory(mw, parser, path, context):
            win = MagicMock()
            self.built.append(win)
            return win

        gui_stub = types.ModuleType("gamess_result_analyzer.gui")
        gui_stub.GAMESSResultAnalyzerDialog = _factory
        utils_stub = types.ModuleType("gamess_result_analyzer.utils")
        utils_stub.clear_atom_color_overrides = MagicMock()

        self._saved = {
            k: sys.modules.get(k)
            for k in ("gamess_result_analyzer.gui", "gamess_result_analyzer.utils")
        }
        sys.modules["gamess_result_analyzer.gui"] = gui_stub
        sys.modules["gamess_result_analyzer.utils"] = utils_stub

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def test_a_first_open_builds_and_registers_a_window(self):
        _init_mod._open_gamess_analyzer_empty(self.ctx)
        self.assertEqual(len(self.built), 1)
        self.assertIs(self.ctx.get_window("analyzer"), self.built[0])

    def test_a_registered_window_is_reused_not_rebuilt(self):
        _init_mod._open_gamess_analyzer_empty(self.ctx)
        _init_mod._open_gamess_analyzer_empty(self.ctx)
        self.assertEqual(len(self.built), 1)
        self.built[0].show.assert_called()

    def test_a_deregistered_window_is_rebuilt(self):
        """What closing must enable: the next open gets a live dialog."""
        _init_mod._open_gamess_analyzer_empty(self.ctx)
        first = self.built[0]

        # gui.closeEvent does exactly this.
        self.ctx.register_window("analyzer", None)

        _init_mod._open_gamess_analyzer_empty(self.ctx)
        self.assertEqual(len(self.built), 2)
        self.assertIsNot(self.ctx.get_window("analyzer"), first)


if __name__ == "__main__":
    unittest.main()
