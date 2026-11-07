"""
tests/test_utils.py
Unit tests for gamess_result_analyzer/utils.py (pure Python, no stubs required).
"""

import json
import os
import shutil
import stat
import sys
import importlib.util
import unittest
from unittest.mock import patch

_SRC = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "gamess_result_analyzer", "utils.py")
)


def _load_utils():
    spec = importlib.util.spec_from_file_location("gamess_utils_mod", _SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gamess_utils_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


_utils = _load_utils()
get_default_export_path = _utils.get_default_export_path
normalize_atom_symbol = _utils.normalize_atom_symbol
determine_bonds_without_dummies = _utils.determine_bonds_without_dummies
list_gamess_output_files = _utils.list_gamess_output_files
clear_atom_color_overrides = _utils.clear_atom_color_overrides
sync_main_window_file = _utils.sync_main_window_file
save_json_atomic = _utils.save_json_atomic
notify = _utils.notify

# ---------------------------------------------------------------------------
# RDKit availability — used by skipUnless decorators throughout this module
# ---------------------------------------------------------------------------
try:
    from rdkit import Chem
    from rdkit.Geometry import Point3D

    _RDKIT_AVAILABLE = True
except ImportError:
    Chem = None
    Point3D = None
    _RDKIT_AVAILABLE = False


class TestGetDefaultExportPath(unittest.TestCase):
    def test_csv_extension(self):
        result = get_default_export_path(
            "/some/dir/job.out", suffix="_scf_trace", extension=".csv"
        )
        self.assertEqual(result, os.path.join("/some/dir", "job_scf_trace.csv"))

    def test_default_suffix(self):
        result = get_default_export_path("/data/mol.out", extension=".csv")
        self.assertEqual(result, os.path.join("/data", "mol_analyzed.csv"))

    def test_empty_base_path_returns_empty_string(self):
        self.assertEqual(get_default_export_path(""), "")
        self.assertEqual(get_default_export_path(None), "")

    def test_no_directory(self):
        result = get_default_export_path("job.out", suffix="_result", extension=".txt")
        self.assertEqual(result, "job_result.txt")

    def test_no_extension_arg(self):
        result = get_default_export_path("/dir/calc.out", suffix="_data")
        self.assertEqual(result, os.path.join("/dir", "calc_data"))

    def test_preserves_directory(self):
        base = os.path.join("a", "b", "c", "mol.out")
        result = get_default_export_path(base, suffix="_x", extension=".png")
        expected_dir = os.path.join("a", "b", "c")
        self.assertIn(expected_dir, result)
        self.assertTrue(result.endswith("mol_x.png"))


class TestNormalizeAtomSymbol(unittest.TestCase):
    """Tests for normalize_atom_symbol — dummy-label cases only (no RDKit required)."""

    def _norm(self, raw):
        return normalize_atom_symbol(raw)

    def test_known_dummy_da(self):
        self.assertEqual(self._norm("DA"), "*")

    def test_known_dummy_bq(self):
        self.assertEqual(self._norm("BQ"), "*")

    def test_known_dummy_du(self):
        self.assertEqual(self._norm("DU"), "*")

    def test_known_dummy_asterisk(self):
        self.assertEqual(self._norm("*"), "*")

    def test_colon_suffix_dummy(self):
        # "X:1" strips to "X" which is a known dummy label
        self.assertEqual(self._norm("X:1"), "*")

    def test_whitespace_stripped(self):
        self.assertEqual(self._norm("  DA  "), "*")


@unittest.skipUnless(_RDKIT_AVAILABLE, "RDKit not installed")
class TestNormalizeAtomSymbolRealElements(unittest.TestCase):
    """Real-element cases — require RDKit periodic table."""

    def _norm(self, raw):
        return normalize_atom_symbol(raw)

    def test_colon_suffix_real(self):
        # "C:2" strips to "C" → Carbon
        self.assertEqual(self._norm("C:2"), "C")

    def test_real_element_carbon(self):
        self.assertEqual(self._norm("C"), "C")

    def test_real_element_iron(self):
        self.assertEqual(self._norm("Fe"), "Fe")

    def test_real_element_lowercase(self):
        self.assertEqual(self._norm("fe"), "Fe")


@unittest.skipUnless(_RDKIT_AVAILABLE, "RDKit not installed")
class TestDetermineBondsWithoutDummies(unittest.TestCase):
    """Tests for determine_bonds_without_dummies()."""

    def _make_mol(self, symbols, coords_angstrom):
        """Build an RWMol with a conformer from lists of symbols and (x,y,z) tuples."""
        mol = Chem.RWMol()
        conf = Chem.Conformer(len(symbols))
        for i, (sym, (x, y, z)) in enumerate(zip(symbols, coords_angstrom)):
            mol.AddAtom(Chem.Atom(sym))
            conf.SetAtomPosition(i, Point3D(x, y, z))
        mol.AddConformer(conf)
        return mol

    def test_pure_real_atoms_get_bonds(self):
        """H2O: both O-H bonds should be found."""
        # Simple water geometry (approximate)
        symbols = ["O", "H", "H"]
        coords = [(0.0, 0.0, 0.0), (0.96, 0.0, 0.0), (-0.24, 0.93, 0.0)]
        mol = self._make_mol(symbols, coords)
        determine_bonds_without_dummies(mol, charge=0, bond_orders=False)
        bond_pairs = {(b.GetBeginAtomIdx(), b.GetEndAtomIdx()) for b in mol.GetBonds()}
        # O(0)-H(1) and O(0)-H(2) must exist
        self.assertTrue(
            (0, 1) in bond_pairs or (1, 0) in bond_pairs,
            f"O-H bond missing; got {bond_pairs}",
        )
        self.assertTrue(
            (0, 2) in bond_pairs or (2, 0) in bond_pairs,
            f"O-H bond missing; got {bond_pairs}",
        )

    def test_dummy_atom_has_no_bonds(self):
        """Molecule with a dummy atom: no bond should touch the dummy index."""
        # H2O (indices 0,1,2) + dummy at index 3 far away
        symbols = ["O", "H", "H", "*"]
        coords = [
            (0.0, 0.0, 0.0),
            (0.96, 0.0, 0.0),
            (-0.24, 0.93, 0.0),
            (99.0, 99.0, 99.0),  # far dummy
        ]
        mol = self._make_mol(symbols, coords)
        determine_bonds_without_dummies(mol, charge=0, bond_orders=False)
        dummy_idx = 3
        for bond in mol.GetBonds():
            self.assertNotIn(
                dummy_idx,
                (bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()),
                "Dummy atom must not participate in any bond",
            )

    def test_all_dummy_atoms_no_crash(self):
        """All-dummy molecule must not raise."""
        symbols = ["*", "*"]
        coords = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]
        mol = self._make_mol(symbols, coords)
        try:
            determine_bonds_without_dummies(mol, charge=0, bond_orders=False)
        except Exception as exc:
            self.fail(f"determine_bonds_without_dummies raised unexpectedly: {exc}")
        self.assertEqual(mol.GetNumBonds(), 0)

    def test_non_fatal_on_bad_charge(self):
        """Passing an impossible charge must not crash (exception is swallowed)."""
        symbols = ["O", "H", "H"]
        coords = [(0.0, 0.0, 0.0), (0.96, 0.0, 0.0), (-0.24, 0.93, 0.0)]
        mol = self._make_mol(symbols, coords)
        try:
            determine_bonds_without_dummies(mol, charge=999, bond_orders=True)
        except Exception as exc:
            self.fail(f"Should be non-fatal, but raised: {exc}")
        # Even with bad charge/bond orders, connectivity should fall back successfully
        self.assertEqual(mol.GetNumBonds(), 2)


import tempfile  # noqa: E402


class TestListGAMESSOutputFiles(unittest.TestCase):
    """Tests for list_gamess_output_files() — pure Python, no RDKit or Qt needed."""

    def _make_dir(self, filenames):
        """Create a temp directory containing files with the given names."""
        d = tempfile.mkdtemp()
        for name in filenames:
            open(os.path.join(d, name), "w").close()
        return d

    def test_returns_only_out_files(self):
        d = self._make_dir(["job.out", "job.inp", "job.xyz", "result.out"])
        result = list_gamess_output_files(d)
        self.assertEqual(result, ["job.out", "result.out"])

    def test_case_insensitive(self):
        d = self._make_dir(["A.OUT", "b.Out", "c.out"])
        result = list_gamess_output_files(d)
        self.assertEqual(len(result), 3)
        self.assertIn("A.OUT", result)

    def test_sorted_alphabetically(self):
        d = self._make_dir(["z.out", "a.out", "m.out"])
        result = list_gamess_output_files(d)
        self.assertEqual(result, ["a.out", "m.out", "z.out"])

    def test_empty_directory_returns_empty_list(self):
        d = self._make_dir([])
        self.assertEqual(list_gamess_output_files(d), [])

    def test_no_out_files_returns_empty_list(self):
        d = self._make_dir(["job.inp", "job.xyz", "README.txt"])
        self.assertEqual(list_gamess_output_files(d), [])

    def test_nonexistent_directory_returns_empty_list(self):
        result = list_gamess_output_files("/nonexistent/path/that/does/not/exist")
        self.assertEqual(result, [])


class _FakeV3D:
    def __init__(self, overrides=None):
        self._plugin_color_overrides = dict(overrides or {})


class _FakeMW:
    def __init__(self, v3d):
        self.view_3d_manager = v3d


class _FakeInit:
    def __init__(self):
        self.current_file_path = None


class _FakeState:
    def __init__(self):
        self.title_updates = 0

    def update_window_title(self):
        self.title_updates += 1


class _FakeHost:
    def __init__(self):
        self.init_manager = _FakeInit()
        self.state_manager = _FakeState()


class _FakeContext:
    def __init__(self):
        self.refreshes = 0

    def refresh_ui(self):
        self.refreshes += 1


class TestSyncMainWindowFile(unittest.TestCase):
    """The main window must name the loaded GAMESS file, as it does its own."""

    def setUp(self):
        self.mw = _FakeHost()

    def test_the_path_becomes_the_current_file(self):
        sync_main_window_file(self.mw, "/tmp/job.out")
        self.assertEqual(self.mw.init_manager.current_file_path, "/tmp/job.out")

    def test_the_title_is_rebuilt(self):
        sync_main_window_file(self.mw, "/tmp/job.out")
        self.assertEqual(self.mw.state_manager.title_updates, 1)

    def test_only_the_title_is_touched_when_the_host_can_do_it(self):
        ctx = _FakeContext()
        sync_main_window_file(self.mw, "/tmp/job.out", ctx)
        self.assertEqual((self.mw.state_manager.title_updates, ctx.refreshes), (1, 0))

    def test_a_host_without_a_title_call_falls_back_to_the_context(self):
        class _Bare:
            pass

        mw = _FakeHost()
        mw.state_manager = _Bare()
        ctx = _FakeContext()
        sync_main_window_file(mw, "/tmp/job.out", ctx)
        self.assertEqual(ctx.refreshes, 1)

    def test_a_failing_refresh_does_not_break_the_load(self):
        class _Broken:
            def update_window_title(self):
                raise RuntimeError("wrapped C/C++ object deleted")

        self.mw.state_manager = _Broken()
        sync_main_window_file(self.mw, "/tmp/job.out")
        self.assertEqual(self.mw.init_manager.current_file_path, "/tmp/job.out")

    def test_no_main_window_is_a_noop(self):
        sync_main_window_file(None, "/tmp/job.out")

    def test_a_host_without_managers_is_a_noop(self):
        class _Bare:
            pass

        sync_main_window_file(_Bare(), "/tmp/job.out")


class TestClearAtomColorOverrides(unittest.TestCase):
    """Tests for clear_atom_color_overrides() — used on every 'new file loaded'
    entry point so charge-coloring from a previous result never bleeds onto a
    differently-indexed molecule."""

    def test_clears_existing_overrides(self):
        v3d = _FakeV3D({0: "#ff0000", 1: "#0000ff"})
        mw = _FakeMW(v3d)
        clear_atom_color_overrides(mw)
        self.assertEqual(v3d._plugin_color_overrides, {})

    def test_leaves_empty_dict_empty(self):
        v3d = _FakeV3D()
        mw = _FakeMW(v3d)
        clear_atom_color_overrides(mw)
        self.assertEqual(v3d._plugin_color_overrides, {})

    def test_none_main_window_is_noop(self):
        try:
            clear_atom_color_overrides(None)
        except Exception as exc:
            self.fail(f"Should be non-fatal with mw=None, raised: {exc}")

    def test_missing_view_3d_manager_is_noop(self):
        class _MWNoView3D:
            pass

        try:
            clear_atom_color_overrides(_MWNoView3D())
        except Exception as exc:
            self.fail(f"Should be non-fatal without view_3d_manager, raised: {exc}")

    def test_missing_overrides_attr_is_noop(self):
        class _V3DNoOverrides:
            pass

        mw = _FakeMW(_V3DNoOverrides())
        try:
            clear_atom_color_overrides(mw)
        except Exception as exc:
            self.fail(
                f"Should be non-fatal without _plugin_color_overrides, raised: {exc}"
            )


class TestSaveJsonAtomic(unittest.TestCase):
    """Windows hands back WinError 5 from os.replace for two very different
    reasons: a transient handle held by a scanner/indexer, and a genuinely
    read-only destination. The first recovers on its own, the second never
    does — a save that gives up on the first PermissionError loses the
    user's settings either way (observed against a plugin dir unpacked from
    a zip that carried the read-only attribute)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "settings.json")

    def test_writes_and_reads_back(self):
        save_json_atomic(self.path, {"a": 1})
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"a": 1})
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_transient_permission_error_is_retried(self):
        save_json_atomic(self.path, {"gen": 1})
        real_replace = os.replace
        calls = []

        def flaky(src, dst):
            calls.append(src)
            if len(calls) < 3:
                raise PermissionError(5, "Access is denied")
            return real_replace(src, dst)

        with (
            patch.object(_utils.os, "replace", flaky),
            patch.object(_utils.time, "sleep", lambda _s: None),
        ):
            save_json_atomic(self.path, {"gen": 2})

        self.assertEqual(len(calls), 3)
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"gen": 2})

    def test_read_only_destination_is_cleared_and_written(self):
        save_json_atomic(self.path, {"gen": 1})
        os.chmod(self.path, stat.S_IREAD)
        self.addCleanup(os.chmod, self.path, stat.S_IWRITE | stat.S_IREAD)

        attempts = []
        real_replace = os.replace

        def readonly_aware(src, dst):
            attempts.append(dst)
            # Emulate Windows: replacing onto a read-only file is denied.
            if not os.access(dst, os.W_OK):
                raise PermissionError(5, "Access is denied")
            return real_replace(src, dst)

        with (
            patch.object(_utils.os, "replace", readonly_aware),
            patch.object(_utils.time, "sleep", lambda _s: None),
        ):
            save_json_atomic(self.path, {"gen": 2})

        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"gen": 2})

    def test_permanent_failure_raises_and_leaves_original_intact(self):
        save_json_atomic(self.path, {"keep": True})

        def always_denied(src, dst):
            raise PermissionError(5, "Access is denied")

        with (
            patch.object(_utils.os, "replace", always_denied),
            patch.object(_utils.time, "sleep", lambda _s: None),
        ):
            with self.assertRaises(PermissionError):
                save_json_atomic(self.path, {"keep": False})

        # The point of the temp-file dance: the previous settings survive.
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"keep": True})

    def test_failed_save_does_not_leave_a_temp_file(self):
        def always_denied(src, dst):
            raise PermissionError(5, "Access is denied")

        with (
            patch.object(_utils.os, "replace", always_denied),
            patch.object(_utils.time, "sleep", lambda _s: None),
        ):
            with self.assertRaises(PermissionError):
                save_json_atomic(self.path, {"a": 1})

        self.assertFalse(
            os.path.exists(self.path + ".tmp"),
            "a failing save must not accumulate .tmp files beside the real one",
        )

    def test_unserializable_payload_cleans_up_and_raises(self):
        with self.assertRaises(TypeError):
            save_json_atomic(self.path, {"bad": object()})
        self.assertFalse(os.path.exists(self.path + ".tmp"))


class _Ctx:
    def __init__(self, boom=None):
        self.msgs = []
        self.boom = boom

    def show_status_message(self, message, timeout):
        if self.boom:
            raise self.boom
        self.msgs.append((message, timeout))


class _Node:
    """Stand-in for a dialog; attributes are attached per test."""


class TestNotify(unittest.TestCase):
    """notify() replaces four hand-written spellings of the same host lookup."""

    def test_uses_a_context_on_the_object_itself(self):
        ctx = _Ctx()
        owner = _Node()
        owner.context = ctx
        self.assertTrue(notify(owner, "hello", 1234))
        self.assertEqual(ctx.msgs, [("hello", 1234)])

    def test_default_timeout_is_three_seconds(self):
        ctx = _Ctx()
        owner = _Node()
        owner.context = ctx
        notify(owner, "hi")
        self.assertEqual(ctx.msgs, [("hi", 3000)])

    def test_walks_parent_dlg_freq_dialog_and_parent_callable(self):
        for hop, build in (
            ("parent_dlg", lambda child, top: setattr(child, "parent_dlg", top)),
            ("freq_dialog", lambda child, top: setattr(child, "freq_dialog", top)),
            ("parent()", lambda child, top: setattr(child, "parent", lambda: top)),
        ):
            with self.subTest(hop=hop):
                ctx = _Ctx()
                top = _Node()
                top.context = ctx
                child = _Node()
                build(child, top)
                self.assertTrue(notify(child, "via " + hop))
                self.assertEqual(ctx.msgs, [("via " + hop, 3000)])

    def test_logs_instead_of_raising_when_there_is_no_host(self):
        with self.assertLogs(level="INFO") as cm:
            self.assertFalse(notify(None, "no host"))
        self.assertTrue(any("no host" in line for line in cm.output))

    def test_a_context_without_the_method_is_not_used(self):
        owner = _Node()
        owner.context = object()
        with self.assertLogs(level="INFO"):
            self.assertFalse(notify(owner, "unusable context"))

    def test_a_host_that_raises_is_logged_not_propagated(self):
        owner = _Node()
        owner.context = _Ctx(boom=RuntimeError("wrapped C/C++ object deleted"))
        with self.assertLogs(level="WARNING") as cm:
            self.assertFalse(notify(owner, "dead window"))
        self.assertTrue(any("rejected by host" in line for line in cm.output))

    def test_a_parent_cycle_terminates(self):
        loop = _Node()
        loop.parent_dlg = loop
        with self.assertLogs(level="INFO"):
            self.assertFalse(notify(loop, "cycle"))

    def test_a_chain_longer_than_the_hop_limit_gives_up_quietly(self):
        ctx = _Ctx()
        top = _Node()
        top.context = ctx
        node = top
        for _ in range(8):
            child = _Node()
            child.parent_dlg = node
            node = child
        with self.assertLogs(level="INFO"):
            self.assertFalse(notify(node, "too deep"))
        self.assertEqual(ctx.msgs, [])


if __name__ == "__main__":
    unittest.main()
