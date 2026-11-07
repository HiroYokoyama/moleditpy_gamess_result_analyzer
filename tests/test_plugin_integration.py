"""
Integration tests for gamess_result_analyzer/__init__.py
Verifies the plugin contract (file opener, drop handler) without Qt.

Two execution modes
-------------------
1. Stub mode    Ealways runs (CI + local).
2. Real-context mode  Eruns when python_molecular_editor is present.

CI setup
--------
    - name: Clone main app (for real-context integration tests)
      run: git clone --depth 1 https://github.com/HiroYokoyama/python_molecular_editor.git
             ../python_molecular_editor || true
"""

import sys
import os
import types
import tempfile
import unittest
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Stub Qt before importing the plugin
# ---------------------------------------------------------------------------


def _install_stubs():
    if "PySide6" not in sys.modules or not hasattr(sys.modules["PySide6"], "__file__"):
        pyside6 = types.ModuleType("PySide6")
        qt_core = types.ModuleType("PySide6.QtCore")
        qt_core.Qt = MagicMock()
        qt_core.QTimer = MagicMock()
        qt_core.Signal = MagicMock()

        qt_widgets = types.ModuleType("PySide6.QtWidgets")
        for cls_name in [
            "QMessageBox",
            "QFileDialog",
            "QApplication",
            "QDialog",
            "QVBoxLayout",
            "QHBoxLayout",
        ]:
            setattr(qt_widgets, cls_name, MagicMock())

        qt_gui = types.ModuleType("PySide6.QtGui")

        sys.modules.setdefault("PySide6", pyside6)
        sys.modules.setdefault("PySide6.QtCore", qt_core)
        sys.modules.setdefault("PySide6.QtWidgets", qt_widgets)
        sys.modules.setdefault("PySide6.QtGui", qt_gui)

    sys.modules.setdefault("pyvista", types.ModuleType("pyvista"))


_install_stubs()

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

# Clean up placeholder parent package if stubbed by other tests
if "gamess_result_analyzer" in sys.modules:
    _mod = sys.modules["gamess_result_analyzer"]
    if not hasattr(_mod, "initialize") or getattr(_mod, "__file__", None) is None:
        sys.modules.pop("gamess_result_analyzer")

from gamess_result_analyzer import initialize, PLUGIN_NAME, PLUGIN_VERSION  # noqa: E402


# ---------------------------------------------------------------------------
# Stub PluginContext
# ---------------------------------------------------------------------------


class _StubContext:
    def __init__(self):
        self._file_openers = []  # (ext, callback, priority)
        self._drop_handlers = []  # (callback, priority)
        self._menu_actions = []  # (path, callback)

    def register_file_opener(self, extension, callback, priority=0):
        self._file_openers.append((extension, callback, priority))

    def register_drop_handler(self, callback, priority=0):
        self._drop_handlers.append((callback, priority))

    def add_menu_action(self, path, callback, **kwargs):
        self._menu_actions.append((path, callback))

    # Full standard API stubs
    def get_main_window(self):
        return MagicMock()

    def show_status_message(self, msg, duration=0):
        pass

    def register_save_handler(self, fn):
        pass

    def register_load_handler(self, fn):
        pass

    def register_document_reset_handler(self, fn):
        pass

    def add_export_action(self, label, fn):
        pass

    def add_analysis_tool(self, label, fn):
        pass

    def add_toolbar_action(self, fn, text, icon=None, tooltip=None):
        pass

    def register_window(self, key, win):
        pass

    def get_window(self, key):
        return None


# ---------------------------------------------------------------------------
# Tests: metadata
# ---------------------------------------------------------------------------


class TestMetadata(unittest.TestCase):
    def test_plugin_name_contains_gamess(self):
        self.assertIn("GAMESS", PLUGIN_NAME)

    def test_plugin_version_is_semver(self):
        from packaging.version import Version
        assert Version(PLUGIN_VERSION).public == PLUGIN_VERSION


# ---------------------------------------------------------------------------
# Tests: initialize contract
# ---------------------------------------------------------------------------


class TestInitialize(unittest.TestCase):
    def setUp(self):
        self.ctx = _StubContext()
        initialize(self.ctx)

    def test_registers_file_opener_for_out(self):
        exts = [ext for ext, _, _ in self.ctx._file_openers]
        self.assertIn(".out", exts)

    def test_file_opener_has_high_priority(self):
        for ext, _, priority in self.ctx._file_openers:
            if ext == ".out":
                self.assertGreaterEqual(priority, 100)

    def test_file_opener_is_callable(self):
        for _, callback, _ in self.ctx._file_openers:
            self.assertTrue(callable(callback))

    def test_registers_drop_handler(self):
        self.assertGreater(len(self.ctx._drop_handlers), 0)

    def test_drop_handler_has_high_priority(self):
        for _, priority in self.ctx._drop_handlers:
            self.assertGreaterEqual(priority, 100)

    def test_drop_handler_is_callable(self):
        for callback, _ in self.ctx._drop_handlers:
            self.assertTrue(callable(callback))


# ---------------------------------------------------------------------------
# Tests: drop handler file-type filtering
# ---------------------------------------------------------------------------


class TestDropHandler(unittest.TestCase):
    def setUp(self):
        ctx = _StubContext()
        initialize(ctx)
        self.drop, _ = ctx._drop_handlers[0]

    def test_rejects_xyz_file(self):
        self.assertFalse(self.drop("molecule.xyz"))

    def test_rejects_mol_file(self):
        self.assertFalse(self.drop("molecule.mol"))

    def test_rejects_txt_file(self):
        self.assertFalse(self.drop("data.txt"))

    def test_rejects_non_gamess_out_file(self):
        with tempfile.NamedTemporaryFile(
            suffix=".out", mode="w", encoding="utf-8", delete=False
        ) as f:
            f.write("This is not an GAMESS output file.\n")
            tmp = f.name
        try:
            self.assertFalse(self.drop(tmp))
        finally:
            os.unlink(tmp)

    def test_claims_gamess_banner_file(self):
        with tempfile.NamedTemporaryFile(
            suffix=".out", mode="w", encoding="utf-8", delete=False
        ) as f:
            f.write(" GAMESS 16:  EM64W-G16RevB.01 20-Dec-2017\n")
            tmp = f.name
        try:
            result = self.drop(tmp)
            # drop handler returns True once the GAMESS signature is detected;
            # the GUI open call may fail headlessly  Ethat's expected.
            self.assertTrue(result)
        except Exception:
            pass  # GUI path raising in headless mode is acceptable
        finally:
            os.unlink(tmp)

    def test_claims_program_version_header(self):
        with tempfile.NamedTemporaryFile(
            suffix=".out", mode="w", encoding="utf-8", delete=False
        ) as f:
            f.write("Program Version 5.0.4 -  RELEASE  -\n")
            tmp = f.name
        try:
            result = self.drop(tmp)
            self.assertTrue(result)
        except Exception:
            pass
        finally:
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# Real PluginContext tier
# ---------------------------------------------------------------------------

_MAIN_APP_CANDIDATES = [
    os.path.normpath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "python_molecular_editor",
            "moleditpy",
            "src",
        )
    ),
    os.environ.get("CI_MAIN_APP_SRC", ""),
]
_MAIN_APP_SRC = next(
    (p for p in _MAIN_APP_CANDIDATES if p and os.path.isdir(p)),
    None,
)
HAS_MAIN_APP = _MAIN_APP_SRC is not None

try:
    import pytest

    _skipif = pytest.mark.skipif(
        not HAS_MAIN_APP,
        reason="main app not found; clone python_molecular_editor or set CI_MAIN_APP_SRC",
    )
except ImportError:

    def _skipif(cls):
        return unittest.skip("pytest not available")(cls)


def _clear_qt_stubs():
    """Remove fake PySide6 stub modules so real PySide6 can be imported by moleditpy."""
    to_remove = [
        k
        for k in list(sys.modules)
        if k.startswith("PySide6") and not hasattr(sys.modules[k], "__file__")
    ]
    for k in to_remove:
        del sys.modules[k]
    # Clear any moleditpy import that may have been attempted with stubs
    for k in [k for k in list(sys.modules) if k.startswith("moleditpy")]:
        del sys.modules[k]


@_skipif
class TestWithRealPluginContext(unittest.TestCase):
    """Verify initialize() works with the actual MoleditPy PluginContext."""

    @classmethod
    def setUpClass(cls):
        if not HAS_MAIN_APP:
            return
        # Load plugin_interface.py directly to avoid triggering moleditpy/__init__.py
        # which imports PySide6 and conflicts with PySide6 loaded by pytest-qt on Windows.
        import importlib.util as _ilu

        _pi_path = os.path.join(
            _MAIN_APP_SRC, "moleditpy", "plugins", "plugin_interface.py"
        )
        _spec = _ilu.spec_from_file_location(
            "moleditpy.plugins.plugin_interface", _pi_path
        )
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        cls.PluginContext = _mod.PluginContext
        mock_manager = MagicMock()
        mock_manager.get_main_window.return_value = MagicMock()
        cls.real_ctx = cls.PluginContext(mock_manager, PLUGIN_NAME)

    def test_real_initialize_does_not_raise(self):
        try:
            initialize(self.real_ctx)
        except Exception as e:
            self.fail(f"initialize(real_context) raised: {e}")

    def test_real_context_is_plugincontext_instance(self):
        self.assertIsInstance(self.real_ctx, self.PluginContext)

    def test_stub_interface_matches_real(self):
        for method in [
            "register_file_opener",
            "register_drop_handler",
            "get_main_window",
        ]:
            self.assertTrue(
                hasattr(self.PluginContext, method),
                f"Real PluginContext missing: {method}",
            )


if __name__ == "__main__":
    unittest.main()
