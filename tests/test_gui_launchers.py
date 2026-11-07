"""
tests/test_gui_launchers.py
Covers how GAMESSResultAnalyzerDialog opens each analysis window: the data guard
that refuses to open one without results, the single-instance rule that closes
a previous window first, and the teardown that closes every tracked dialog.

Each launcher is driven with its sub-dialog class patched out, so this checks
the wiring rather than re-testing the dialogs themselves.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

G = gui_harness.load_isolated("gui")


# (launcher method, patched dialog class, attribute, parser data that enables it)
LAUNCHERS = [
    ("show_mo_analyzer", "MODialog", "mo_dlg", {"mo_coeffs": {"0": {"energy": -1.0}}}),
    (
        "show_freq",
        "FrequencyDialog",
        "freq_dlg",
        {"frequencies": [{"freq": 1600.0}], "atoms": ["O"], "coords": [[0, 0, 0]]},
    ),
    (
        "show_thermal",
        "ThermalTableDialog",
        "thermal_dlg",
        {"thermal": {"gibbs": -76.4}},
    ),
    ("show_tddft", "TDDFTDialog", "tddft_dlg", {"tddft": [{"state": 1}]}),
    ("show_dipole", "DipoleDialog", "dipole_dlg", {"dipoles": {"magnitude": 1.8}}),
    ("show_charges", "ChargeDialog", "charges_dlg", {"charges": {"Mulliken": []}}),
    ("show_nmr", "NMRDialog", "nmr_dlg", {"nmr_shielding": [{"atom_idx": 0}]}),
    (
        "show_scf_trace",
        "SCFTraceDialog",
        "scf_dlg",
        {"scf_traces": [{"iterations": []}]},
    ),
]


class _LauncherCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        self.path = os.path.join(self.tmp, "job.out")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("GAMESS output\n")

        self.parser = MagicMock()
        self.parser.filename = self.path
        self.parser.data = {}

        self.dlg = G.GAMESSResultAnalyzerDialog(
            MagicMock(), self.parser, self.path, context=MagicMock()
        )
        # Opening a window redraws the 3D structure; that path is covered
        # separately and needs a live viewer.
        patcher = patch.object(self.dlg, "load_structure_3d")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _clear_tracked(self):
        for _, _, attr, _ in LAUNCHERS:
            setattr(self.dlg, attr, None)


class TestLaunchers(_LauncherCase):
    def test_reopening_nmr_respects_a_failed_save(self):
        self.parser.data = {"nmr_shielding": [{"atom_idx": 0}]}
        previous = MagicMock()
        previous.close.return_value = False
        self.dlg.nmr_dlg = previous
        with patch.object(G, "NMRDialog") as dialog:
            self.dlg.show_nmr()
        dialog.assert_not_called()
        self.assertIs(self.dlg.nmr_dlg, previous)

    def test_each_launcher_opens_its_window(self):
        for method, cls, attr, data in LAUNCHERS:
            with self.subTest(method):
                self.parser.data = dict(data)
                self._clear_tracked()
                with patch.object(G, cls) as sub:
                    getattr(self.dlg, method)()
                sub.assert_called_once()
                sub.return_value.show.assert_called_once()
                self.assertIs(getattr(self.dlg, attr), sub.return_value)

    def test_each_launcher_refuses_without_data(self):
        for method, cls, _, _ in LAUNCHERS:
            with self.subTest(method):
                self.parser.data = {}
                self._clear_tracked()
                with patch.object(G, cls) as sub:
                    with patch.object(G.QMessageBox, "warning") as warn:
                        getattr(self.dlg, method)()
                sub.assert_not_called()
                warn.assert_called_once()

    def test_each_launcher_replaces_the_previous_window(self):
        for method, cls, attr, data in LAUNCHERS:
            with self.subTest(method):
                self.parser.data = dict(data)
                previous = MagicMock()
                setattr(self.dlg, attr, previous)
                with patch.object(G, cls):
                    getattr(self.dlg, method)()
                previous.close.assert_called_once()

    def test_orbital_energies_alone_still_open_the_orbital_window(self):
        self.parser.data = {"orbital_energies": [-1.0, -0.5]}
        self.dlg.mo_dlg = None
        with patch.object(G, "MODialog") as sub:
            self.dlg.show_mo_analyzer()
        sub.assert_called_once()

    def test_opening_a_window_redraws_the_structure(self):
        self.parser.data = {"thermal": {"gibbs": -76.4}}
        self.dlg.thermal_dlg = None
        with patch.object(G, "ThermalTableDialog"):
            self.dlg.show_thermal()
        self.dlg.load_structure_3d.assert_called_once()


class TestTeardown(_LauncherCase):
    ATTRS = [
        "mo_dlg",
        "freq_dlg",
        "traj_dlg",
        "forces_dlg",
        "conv_graph_dlg",
        "thermal_dlg",
        "tddft_dlg",
        "dipole_dlg",
        "charges_dlg",
        "nmr_dlg",
        "scf_dlg",
        "props_dlg",
        "bond_dlg",
        "energy_dlg",
    ]

    def test_every_tracked_window_is_closed(self):
        opened = {attr: MagicMock() for attr in self.ATTRS}
        for attr, dlg in opened.items():
            setattr(self.dlg, attr, dlg)
        self.dlg.close_all_sub_dialogs()
        for attr, dlg in opened.items():
            with self.subTest(attr):
                dlg.close.assert_called_once()

    def test_every_reference_is_dropped(self):
        for attr in self.ATTRS:
            setattr(self.dlg, attr, MagicMock())
        self.dlg.close_all_sub_dialogs()
        for attr in self.ATTRS:
            with self.subTest(attr):
                self.assertIsNone(getattr(self.dlg, attr))

    def test_windows_that_are_not_open_are_skipped(self):
        for attr in self.ATTRS:
            setattr(self.dlg, attr, None)
        self.dlg.close_all_sub_dialogs()  # must not raise

    def test_a_window_that_refuses_to_close_is_tolerated(self):
        stubborn = MagicMock()
        stubborn.close.side_effect = RuntimeError("already destroyed")
        self.dlg.mo_dlg = stubborn
        self.dlg.close_all_sub_dialogs()
        self.assertIsNone(self.dlg.mo_dlg)

    def test_closing_the_analyzer_closes_its_windows(self):
        self.dlg.mo_dlg = MagicMock()
        with patch.object(self.dlg, "_disable_plotter_picking"):
            self.dlg.closeEvent(MagicMock())
        self.assertIsNone(self.dlg.mo_dlg)

    def test_closing_releases_the_3d_picking_hook(self):
        with patch.object(self.dlg, "_disable_plotter_picking") as unhook:
            self.dlg.closeEvent(MagicMock())
        unhook.assert_called_once()

    def test_escape_routes_through_close(self):
        with patch.object(self.dlg, "close") as closer:
            self.dlg.reject()
        closer.assert_called_once()


if __name__ == "__main__":
    unittest.main()
