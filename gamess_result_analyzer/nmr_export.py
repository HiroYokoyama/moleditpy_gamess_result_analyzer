"""NMR dialog mixin: exporting the shielding/coupling tables and spectrum plot."""

import os
import csv
from PySide6.QtWidgets import QApplication, QMessageBox, QFileDialog
from .utils import get_default_export_path, notify


class _NMRExportMixin:
    def export_spectrum(self):
        """Export spectrum to file"""
        current_nucleus = self.current_nucleus
        default_path = get_default_export_path(
            self.file_path, suffix=f"_nmr_{current_nucleus}_spectrum", extension=".png"
        )

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export Spectrum",
            default_path,
            "PNG Image (*.png);;PDF (*.pdf);;SVG (*.svg)",
        )

        if filename:
            try:
                self.figure.savefig(filename, dpi=300, bbox_inches="tight")
                notify(
                    self, f"Spectrum exported to: {os.path.basename(filename)}", 5000
                )
            # ValueError: savefig rejects an unsupported extension typed in
            # the file dialog, which previously escaped the slot.
            except (OSError, ValueError) as e:
                QMessageBox.critical(self, "Error", f"Export failed:\n{e}")

    def export_spectrum_csv(self):
        """Export spectrum data to CSV (Stick or Real)"""
        if not self.displayed_data:
            QMessageBox.warning(self, "No Data", "No spectrum data to export.")
            return

        default_path = get_default_export_path(
            self.file_path,
            suffix=f"_nmr_{self.current_nucleus}_sticks",
            extension=".csv",
        )
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Spectrum CSV", default_path, "CSV Files (*.csv)"
        )
        if not filename:
            return

        try:
            with open(filename, "w", encoding="utf-8") as f:
                # Stick Spectrum Export
                # Export Peak List: Shift, Intensity
                f.write("Chemical Shift (ppm),Intensity,AtomIndices\n")

                # We can use peaks_metadata if available, or reconstruct
                if (
                    getattr(self, "peaks_metadata", None) is not None
                    and self.peaks_metadata
                ):
                    for (
                        shift,
                        intensity,
                        _is_merged,
                        atom_indices,
                    ) in self.peaks_metadata:
                        indices_str = ";".join(str(i) for i in atom_indices)
                        f.write(f"{shift:.6f},{intensity:.4f},{indices_str}\n")
                else:
                    # Fallback
                    f.write("# No peak data available.\n")

            notify(
                self, f"Spectrum data exported to: {os.path.basename(filename)}", 5000
            )

        # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
        except Exception as e:  # pylint: disable=broad-exception-caught
            QMessageBox.critical(self, "Error", f"Export failed:\n{e}")

    def export_table_csv(self):
        """Export table data to CSV"""
        current_nucleus = self.current_nucleus
        default_path = get_default_export_path(
            self.file_path, suffix=f"_nmr_{current_nucleus}_table", extension=".csv"
        )

        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Table CSV", default_path, "CSV Files (*.csv)"
        )
        if not filename:
            return

        try:
            with open(filename, "w", newline="", encoding="utf-8") as f:
                # Headers
                headers = []
                for c in range(self.table.columnCount()):
                    headers.append(self.table.horizontalHeaderItem(c).text())
                writer = csv.writer(f)
                writer.writerow(headers)

                # Rows
                for r in range(self.table.rowCount()):
                    cols = []
                    for c in range(self.table.columnCount()):
                        it = self.table.item(r, c)
                        text = it.text() if it else ""
                        cols.append(text)
                    writer.writerow(cols)

            notify(self, f"Table data exported to: {os.path.basename(filename)}", 5000)
        # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
        except Exception as e:  # pylint: disable=broad-exception-caught
            QMessageBox.critical(self, "Error", f"Export failed:\n{e}")

    def copy_table(self):
        """Copy table data to clipboard"""
        text = "Idx\tNucleus\tShielding\tShift\n"
        for r in range(self.table.rowCount()):
            cols = []
            for c in range(self.table.columnCount()):
                it = self.table.item(r, c)
                cols.append(it.text() if it else "")
            text += "\t".join(cols) + "\n"
        QApplication.clipboard().setText(text)
        notify(self, "Table data copied to clipboard!", 5000)
