"""Bond Analysis panel: Mayer bond orders from the Mulliken analysis.

Selecting a row highlights that bond in the 3D editor.
"""

import logging

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QLabel,
    QPushButton,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence


class _CopyableTable(QTableWidget):
    """Read-only table whose selection copies (Ctrl+C) as multi-line TSV text.

    Optionally distributes column widths by per-column weights (so e.g. the
    Hybridization column can be wider than the rest).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._col_weights = None

    def set_column_weights(self, weights):
        """Store the per-column width weights and re-apply them immediately."""
        self._col_weights = list(weights)
        self._apply_weights()

    def _apply_weights(self):
        """Resize each column proportionally to its stored weight."""
        if not self._col_weights:
            return
        total = sum(self._col_weights)
        if total <= 0:
            return
        width = self.viewport().width()
        for c, w in enumerate(self._col_weights):
            if c < self.columnCount():
                self.setColumnWidth(c, max(48, int(width * w / total)))

    def resizeEvent(self, event):
        """Re-apply the stored column weights when the table is resized."""
        super().resizeEvent(event)
        self._apply_weights()

    def keyPressEvent(self, event):
        """Copy the current selection as TSV on Ctrl+C, otherwise defer to Qt."""
        if event.matches(QKeySequence.StandardKey.Copy):
            self._copy_selection()
            return
        super().keyPressEvent(event)

    def _copy_selection(self):
        """Put the selected cells on the clipboard as tab-separated rows."""
        items = self.selectedItems()
        if not items:
            return
        rows = {}
        for it in items:
            rows.setdefault(it.row(), {})[it.column()] = it.text()
        lines = [
            "\t".join(cols[c] for c in sorted(cols)) for _, cols in sorted(rows.items())
        ]
        QApplication.clipboard().setText("\n".join(lines))


_TABLE_STYLE = """
    QTableWidget { gridline-color: #e6e6e6; background: #ffffff; }
    QTableWidget::item { padding: 7px 16px; }
    QTableWidget::item:selected { background: #cfe5ff; color: #000; }
    QHeaderView::section {
        background-color: #f3f3f3;
        padding: 7px 16px;
        border: none;
        border-bottom: 1px solid #cccccc;
        font-weight: bold;
    }
"""


def _make_table(headers, rows, weights=None):
    table = _CopyableTable(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setStyleSheet(_TABLE_STYLE)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            item = QTableWidgetItem(str(value))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(r, c, item)
    table.verticalHeader().setDefaultSectionSize(32)
    header = table.horizontalHeader()
    header.setHighlightSections(False)
    if weights:
        # Width distributed by weight (e.g. a wider Hybridization column).
        for c in range(len(headers)):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        table.set_column_weights(weights)
    else:
        for c in range(len(headers)):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
    return table


try:
    from rdkit.Chem import GetPeriodicTable

    _PERIODIC_TABLE = GetPeriodicTable()
except ImportError:
    _PERIODIC_TABLE = None

# Highlight halo radius as a fraction of the van der Waals radius.
_VDW_FRACTION = 0.40


def _vdw(sym):
    """van der Waals radius (Angstrom) for an element symbol, via RDKit."""
    if _PERIODIC_TABLE is not None:
        try:
            return _PERIODIC_TABLE.GetRvdw(sym)
        except (RuntimeError, AttributeError, ValueError):
            logging.debug(
                "vdW radius lookup failed for '%s'; using default", sym, exc_info=True
            )
    return 1.70


class BondAnalysisDialog(QDialog):
    """Dialog showing Mayer bond orders."""

    def __init__(self, parent, data):
        super().__init__(parent)
        self.parent_dlg = parent  # GAMESSResultAnalyzerDialog (has .mw, .parser)
        self.setWindowTitle("Bond Analysis")
        self.resize(900, 640)

        self._actors = []
        self._mbo = data.get("mayer_bond_orders", [])

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        # Switching tabs clears any active 3D highlight.
        tabs.currentChanged.connect(lambda _i: self._clear_highlight())

        if self._mbo:
            rows = [
                [
                    f"{b['atom_idx1']} {b['atom_sym1']}",
                    f"{b['atom_idx2']} {b['atom_sym2']}",
                    f"{b['order']:.4f}",
                ]
                for b in self._mbo
            ]
            t = _make_table(["Atom 1", "Atom 2", "Bond Order"], rows)
            t.itemSelectionChanged.connect(lambda tbl=t: self._on_mayer_selected(tbl))
            tabs.addTab(t, f"Mayer Bond Orders ({len(self._mbo)})")

        if tabs.count() == 0:
            layout.addWidget(QLabel("No bond-analysis data found."))
        else:
            hint = QLabel(
                "Select a row to highlight it in 3D · Ctrl+C to copy."
            )
            hint.setStyleSheet("color:#777; font-size:9pt;")
            layout.addWidget(hint)
            layout.addWidget(tabs)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    # ----- 3D highlighting -------------------------------------------------

    def _coords(self):
        parser = getattr(self.parent_dlg, "parser", None)
        if parser is None:
            return []
        return parser.data.get("coords", [])

    def _atoms(self):
        parser = getattr(self.parent_dlg, "parser", None)
        if parser is None:
            return []
        return parser.data.get("atoms", [])

    def _halo_radius(self, idx, atoms):
        sym = atoms[idx] if 0 <= idx < len(atoms) else "C"
        return _VDW_FRACTION * _vdw(sym)

    def _plotter(self):
        mw = getattr(self.parent_dlg, "mw", None)
        if mw is None or not hasattr(mw, "plotter"):
            return None
        return mw.plotter

    def _clear_highlight(self):
        plotter = self._plotter()
        if plotter is None:
            self._actors = []
            return
        for actor in self._actors:
            try:
                plotter.remove_actor(actor)
            except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                logging.debug("Could not remove a bond-highlight actor: %s", e)
        self._actors = []
        try:
            plotter.render()
        except (RuntimeError, AttributeError, KeyError, ValueError) as e:
            logging.debug(
                "Could not render the plotter after clearing bond highlights: %s", e
            )

    def _highlight_atoms(self, indices):
        self._clear_highlight()
        plotter = self._plotter()
        coords = self._coords()
        atoms = self._atoms()
        if plotter is None or not coords:
            return
        try:
            import pyvista as pv

            for idx in indices:
                if 0 <= idx < len(coords):
                    sphere = pv.Sphere(
                        radius=self._halo_radius(idx, atoms), center=coords[idx]
                    )
                    self._actors.append(
                        plotter.add_mesh(sphere, color="yellow", opacity=0.4)
                    )
            plotter.render()
        except (ImportError, RuntimeError, AttributeError, IndexError) as e:
            logging.warning(
                "Bond analysis: could not highlight atoms %s in the 3D view: %s",
                indices,
                e,
            )

    def _highlight_bond(self, i, j):
        self._clear_highlight()
        plotter = self._plotter()
        coords = self._coords()
        atoms = self._atoms()
        if plotter is None or not coords:
            return
        if not (0 <= i < len(coords) and 0 <= j < len(coords)):
            return
        try:
            import pyvista as pv

            p1, p2 = coords[i], coords[j]
            tube = pv.Line(p1, p2).tube(radius=0.12)
            self._actors.append(plotter.add_mesh(tube, color="yellow", opacity=0.7))
            for idx, p in ((i, p1), (j, p2)):
                sphere = pv.Sphere(radius=self._halo_radius(idx, atoms), center=p)
                self._actors.append(
                    plotter.add_mesh(sphere, color="orange", opacity=0.4)
                )
            plotter.render()
        except (ImportError, RuntimeError, AttributeError, IndexError) as e:
            logging.warning(
                "Bond analysis: could not highlight the bond %d-%d in the 3D view: %s",
                i,
                j,
                e,
            )

    @staticmethod
    def _single_selected_row(table):
        """Return the row index iff exactly one row is selected, else None.

        Multi-row selections (used for copy) clear the 3D highlight rather than
        showing an ambiguous single one.
        """
        rows = table.selectionModel().selectedRows()
        return rows[0].row() if len(rows) == 1 else None

    def _on_mayer_selected(self, table):
        row = self._single_selected_row(table)
        if row is None:
            self._clear_highlight()
            return
        if 0 <= row < len(self._mbo):
            b = self._mbo[row]
            self._highlight_bond(b["atom_idx1"], b["atom_idx2"])

    def reject(self):
        """Route Esc through close() so closeEvent cleanup always runs."""
        # Esc must run closeEvent cleanup (QDialog.reject only hides)
        self.close()

    def closeEvent(self, event):
        """Clear any active 3D highlight before the dialog closes."""
        self._clear_highlight()
        # accept() not super().closeEvent(): QDialog.closeEvent calls reject(),
        # which is routed back through close() and would recurse.
        event.accept()
