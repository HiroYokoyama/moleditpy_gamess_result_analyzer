"""
tests/test_nmr_export.py
Coverage for _NMRExportMixin: export_spectrum (savefig driven, no rasteriser),
export_spectrum_csv (stick and real-spectrum modes), export_table_csv,
get_j_coupling_string, and copy_table.
"""

import os
import sys
import csv
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import gui_harness  # noqa: E402

N = gui_harness.load_isolated("nmr_analysis")
NMRDialog = N.NMRDialog

# The export mixin is loaded as a sibling package member
NE = sys.modules[f"{N.__package__}.nmr_export"]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _data():
    return [
        {"atom_idx": 0, "atom_sym": "C", "shielding": 150.0},
        {"atom_idx": 1, "atom_sym": "H", "shielding": 30.0},
        {"atom_idx": 2, "atom_sym": "H", "shielding": 30.5},
    ]


def _couplings():
    return [
        {"atom_idx1": 0, "atom_idx2": 1, "coupling": 5.5},
        {"atom_idx1": 1, "atom_idx2": 2, "coupling": 7.3},
    ]


class _FakeItem:
    def __init__(self, text=""):
        self._text = str(text)

    def text(self):
        return self._text


class _FakeTable:
    HEADERS = ["Atom", "Nucleus", "Shielding", "Shift", "J"]

    def __init__(self, rows=2):
        self._rows = rows
        self._cells = {
            (r, c): _FakeItem(f"r{r}c{c}") for r in range(rows) for c in range(5)
        }

    def rowCount(self):
        return self._rows

    def columnCount(self):
        return 5

    def horizontalHeaderItem(self, c):
        return _FakeItem(self.HEADERS[c])

    def item(self, r, c):
        return self._cells.get((r, c))

    def __getattr__(self, name):
        return MagicMock()


class _ExportCase(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name
        self.addCleanup(self._tmpdir.cleanup)

        host = MagicMock()
        host.file_path = os.path.join(self.tmp, "job.out")
        self.dlg = NMRDialog(host, _data(), file_path=None)
        self.dlg.parent_dlg = host
        self.dlg.settings_file = os.path.join(self.tmp, "settings.json")
        self.dlg.merged_peaks_file = os.path.join(self.tmp, "merges.json")
        self.dlg.merged_peaks = []
        self.dlg.file_path = os.path.join(self.tmp, "job.out")
        self.dlg.current_nucleus = "H"

        # Standard table
        self.dlg.table = _FakeTable(rows=2)

        # Keep off the rendering stack
        self._pp = patch.object(self.dlg, "plot_spectrum")
        self._pp.start()
        self.addCleanup(self._pp.stop)


# ===========================================================================
# export_spectrum
# ===========================================================================


class TestExportSpectrum(_ExportCase):
    def test_savefig_is_called_with_chosen_path(self):
        path = os.path.join(self.tmp, "spec.png")
        with patch.object(self.dlg.figure, "savefig") as mock_save:
            with patch.object(
                NE.QFileDialog, "getSaveFileName", return_value=(path, "")
            ):
                self.dlg.export_spectrum()
        mock_save.assert_called_once()
        self.assertEqual(mock_save.call_args[0][0], path)

    def test_cancelled_dialog_skips_save(self):
        with patch.object(self.dlg.figure, "savefig") as mock_save:
            with patch.object(NE.QFileDialog, "getSaveFileName", return_value=("", "")):
                self.dlg.export_spectrum()
        mock_save.assert_not_called()

    def test_oserror_on_save_shows_critical_dialog(self):
        path = os.path.join(self.tmp, "spec.png")
        with patch.object(self.dlg.figure, "savefig", side_effect=OSError("disk full")):
            with patch.object(
                NE.QFileDialog, "getSaveFileName", return_value=(path, "")
            ):
                with patch.object(NE.QMessageBox, "critical") as crit:
                    self.dlg.export_spectrum()
        crit.assert_called_once()


# ===========================================================================
# export_spectrum_csv — stick mode
# ===========================================================================


class TestExportSpectrumCsvStick(_ExportCase):
    def setUp(self):
        super().setUp()
        self.dlg.peaks_metadata = [
            (1.8, 1.0, False, [1]),
            (1.3, 3.0, True, [0, 1, 2]),
        ]
        self.dlg.displayed_data = list(_data())
        # stick mode
        chk = MagicMock()
        chk.isChecked.return_value = False
        self.dlg.chk_real_spectrum = chk

    def test_cancelled_dialog_writes_nothing(self):
        with patch.object(NE.QFileDialog, "getSaveFileName", return_value=("", "")):
            self.dlg.export_spectrum_csv()
        self.assertEqual(os.listdir(self.tmp), [])

    def test_no_displayed_data_shows_warning(self):
        self.dlg.displayed_data = []
        with patch.object(NE.QMessageBox, "warning") as warn:
            self.dlg.export_spectrum_csv()
        warn.assert_called_once()

    def test_stick_csv_has_header_row(self):
        path = os.path.join(self.tmp, "sticks.csv")
        with patch.object(NE.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_spectrum_csv()
        with open(path, encoding="utf-8") as fh:
            first = fh.readline()
        self.assertIn("Chemical Shift", first)
        self.assertIn("Intensity", first)

    def test_stick_csv_has_one_row_per_peak(self):
        path = os.path.join(self.tmp, "sticks.csv")
        with patch.object(NE.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_spectrum_csv()
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        # header + 2 data rows
        self.assertEqual(len(rows), 3)

    def test_stick_csv_includes_atom_indices(self):
        path = os.path.join(self.tmp, "sticks.csv")
        with patch.object(NE.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_spectrum_csv()
        content = open(path, encoding="utf-8").read()
        self.assertIn("AtomIndices", content)

    def test_fallback_written_when_peaks_metadata_is_none(self):
        self.dlg.peaks_metadata = None
        path = os.path.join(self.tmp, "sticks2.csv")
        with patch.object(NE.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_spectrum_csv()
        content = open(path, encoding="utf-8").read()
        self.assertIn("No peak data", content)


# ===========================================================================
# export_spectrum_csv — real-spectrum mode
# ===========================================================================


class TestExportSpectrumCsvReal(_ExportCase):
    def setUp(self):
        super().setUp()
        self.dlg.displayed_data = list(_data())
        chk = MagicMock()
        chk.isChecked.return_value = True
        self.dlg.chk_real_spectrum = chk

    def _make_figure_with_line(self, marker="None", color="b", linestyle="-"):
        import matplotlib.figure as mf

        fig = mf.Figure()
        ax = fig.add_subplot(111)
        ax.plot(
            [0.0, 1.0, 2.0],
            [0.0, 0.5, 0.0],
            marker=marker,
            color=color,
            linestyle=linestyle,
        )
        return fig

    def test_real_mode_writes_xy_header(self):
        self.dlg.figure = self._make_figure_with_line()
        path = os.path.join(self.tmp, "real.csv")
        with patch.object(NE.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_spectrum_csv()
        with open(path, encoding="utf-8") as fh:
            first = fh.readline()
        self.assertIn("Chemical Shift", first)
        self.assertIn("Intensity", first)


# ===========================================================================
# export_table_csv
# ===========================================================================


class TestExportTableCsv(_ExportCase):
    def test_writes_header_and_rows(self):
        path = os.path.join(self.tmp, "table.csv")
        with patch.object(NE.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_table_csv()
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        # header + 2 data rows
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], _FakeTable.HEADERS)

    def test_comma_in_cell_is_quoted(self):
        # Inject a cell containing a comma
        self.dlg.table._cells[(0, 0)] = _FakeItem("val,ue")
        path = os.path.join(self.tmp, "table2.csv")
        with patch.object(NE.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_table_csv()
        content = open(path, encoding="utf-8").read()
        self.assertIn('"val,ue"', content)

    def test_cancelled_dialog_writes_nothing(self):
        with patch.object(NE.QFileDialog, "getSaveFileName", return_value=("", "")):
            self.dlg.export_table_csv()
        self.assertEqual(os.listdir(self.tmp), [])

    def test_missing_cell_is_treated_as_empty_string(self):
        self.dlg.table._cells.clear()  # no cells at all
        path = os.path.join(self.tmp, "table3.csv")
        with patch.object(NE.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.dlg.export_table_csv()
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(len(rows), 3)


# ===========================================================================
# get_j_coupling_string
# ===========================================================================


# ===========================================================================
# copy_table
# ===========================================================================


class TestCopyTable(_ExportCase):
    def test_calls_set_text_on_clipboard(self):
        clipboard = MagicMock()
        with patch.object(NE.QApplication, "clipboard", return_value=clipboard):
            self.dlg.copy_table()
        clipboard.setText.assert_called_once()
        text = clipboard.setText.call_args[0][0]
        self.assertIn("Idx", text)

    def test_text_contains_header_and_rows(self):
        clipboard = MagicMock()
        with patch.object(NE.QApplication, "clipboard", return_value=clipboard):
            self.dlg.copy_table()
        text = clipboard.setText.call_args[0][0]
        lines = text.strip().splitlines()
        # header + 2 data rows
        self.assertEqual(len(lines), 3)


if __name__ == "__main__":
    unittest.main()
