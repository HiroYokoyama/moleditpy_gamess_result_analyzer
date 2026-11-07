"""SCF Trace dialog: convergence plot per iteration, dispersion and spin-contamination."""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QComboBox,
    QGroupBox,
    QFileDialog,
    QMessageBox,
)
from matplotlib.backends.backend_qtagg import (
    FigureCanvasQTAgg as FigureCanvas,
    NavigationToolbar2QT as NavigationToolbar,
)
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
import csv
from .utils import get_default_export_path, notify
import logging


class SCFTraceDialog(QDialog):
    """Dialog plotting SCF energy convergence, with dispersion and spin-contamination info."""

    def __init__(self, parent, scf_traces, dispersion=None, spin_s2=None):
        super().__init__(parent)
        self.setWindowTitle("SCF Energy Trace")
        self.resize(700, 500)
        self.scf_traces = scf_traces
        self.dispersion = dispersion
        self.spin_s2 = spin_s2

        layout = QVBoxLayout(self)

        # Controls Group
        ctrl_group = QGroupBox("Selection")
        ctrl_layout = QHBoxLayout(ctrl_group)

        ctrl_layout.addWidget(QLabel("SCF Block:"))
        self.combo_steps = QComboBox()

        # Add "All Blocks" option at the beginning
        self.combo_steps.addItem("All Blocks", -1)

        for idx, trace in enumerate(self.scf_traces):
            label = f"{idx + 1}: {trace.get('step', 'Step')}"
            self.combo_steps.addItem(label, idx)

        self.combo_steps.currentIndexChanged.connect(self.update_plot)
        ctrl_layout.addWidget(self.combo_steps)

        btn_final = QPushButton("Final Step")
        btn_final.clicked.connect(
            lambda: self.combo_steps.setCurrentIndex(self.combo_steps.count() - 1)
        )
        ctrl_layout.addWidget(btn_final)

        # Set "All Blocks" as default
        self.combo_steps.setCurrentIndex(0)
        ctrl_layout.addStretch()

        btn_export = QPushButton("Export CSV...")
        btn_export.clicked.connect(self.export_csv)
        ctrl_layout.addWidget(btn_export)

        layout.addWidget(ctrl_group)

        # Dispersion correction (DFT-D3/D4), if present
        if self.dispersion is not None:
            lbl_disp = QLabel(f"Dispersion correction: {self.dispersion:.6f} Eh")
            lbl_disp.setStyleSheet("color:#444; font-size:9pt; padding:2px;")
            lbl_disp.setToolTip(
                "London dispersion correction (DFT-D3/D4), included in the final energy."
            )
            layout.addWidget(lbl_disp)

        # Spin contamination summary (open-shell / UHF only) — lives here
        # alongside SCF convergence rather than Atomic Charges, since <S**2>
        # is a wavefunction-quality diagnostic, not a per-atom property.
        if self.spin_s2 and self.spin_s2.get("actual") is not None:
            actual = self.spin_s2["actual"]
            ideal = self.spin_s2.get("ideal")
            cont = self.spin_s2.get("contamination")
            parts = [f"⟨S²⟩ = {actual:.4f}"]
            if ideal is not None:
                parts.append(f"ideal {ideal:.4f}")
            if cont is not None:
                parts.append(f"contamination {cont:+.4f}")
            lbl_s2 = QLabel("   •   ".join(parts))
            lbl_s2.setStyleSheet("color:#444; font-size:9pt; padding:2px;")
            lbl_s2.setToolTip(
                "UHF/UKS spin expectation value <S**2> vs the ideal S(S+1)."
            )
            layout.addWidget(lbl_s2)

        # Plotting Area
        self.figure = Figure(figsize=(5, 4), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)

        # Add Toolbar
        self.toolbar = NavigationToolbar(self.canvas, self)
        layout.addWidget(self.toolbar)

        layout.addWidget(self.canvas, stretch=1)

        # Close Button
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)  # accept() skips closeEvent (Qt >= 6.3)
        layout.addWidget(btn_close)

        self.update_plot()

    def reject(self):
        """Route Esc through close() so closeEvent cleanup always runs."""
        # Esc must run closeEvent cleanup (QDialog.reject only hides)
        self.close()

    def closeEvent(self, event):
        """Close the matplotlib figure to release its resources."""
        try:
            plt.close(self.figure)
        except (AttributeError, ValueError, NotImplementedError) as e:
            logging.debug(
                "SCF: could not close the matplotlib figure on dialog close: %s", e
            )
        # accept() not super().closeEvent(): QDialog.closeEvent calls reject(),
        # which is routed back through close() and would recurse.
        event.accept()

    def update_plot(self):
        """Redraw the SCF energy plot for the selected block (or all blocks)."""
        idx = self.combo_steps.currentData()
        if idx is None:
            return

        self.ax.clear()

        if idx == -1:
            # Concatenate all blocks
            all_iters = []
            all_energies = []
            cumulative_idx = 1
            separators = []

            for trace in self.scf_traces:
                iterations = trace.get("iterations", [])
                if iterations:
                    separators.append(cumulative_idx)
                    for d in iterations:
                        all_iters.append(cumulative_idx)
                        all_energies.append(d["energy"])
                        cumulative_idx += 1

            if all_energies:
                self.ax.plot(
                    all_iters,
                    all_energies,
                    "o-",
                    color="blue",
                    linewidth=1.0,
                    markersize=2,
                )

                # Add vertical separators and labels
                for i, sep in enumerate(separators):
                    if sep > 1:
                        self.ax.axvline(
                            x=sep - 0.5, color="red", linestyle="--", alpha=0.3
                        )

                    # Add text label for the block
                    label = self.scf_traces[i].get("step", f"Step {i + 1}")
                    # Position strictly inside plot at the top
                    self.ax.text(
                        sep + 0.3,
                        0.95,
                        label,
                        transform=self.ax.get_xaxis_transform(),
                        rotation=90,
                        fontsize=7,
                        verticalalignment="top",
                        alpha=0.8,
                        color="darkgray",
                        clip_on=True,
                    )

                self.ax.set_title("Total SCF Convergence Trace (All Blocks)", pad=15)
                self.ax.set_xlabel("Cumulative Iteration")
                self.ax.set_ylabel("Energy (Eh)")
                self.ax.grid(True, linestyle="--", alpha=0.7)
                self.ax.ticklabel_format(useOffset=False, style="plain")
            else:
                self.ax.text(0.5, 0.5, "No data available", ha="center", va="center")
        else:
            if idx < 0 or idx >= len(self.scf_traces):
                return

            trace = self.scf_traces[idx]
            iterations = trace.get("iterations", [])

            iters = [d["iter"] for d in iterations]
            energies = [d["energy"] for d in iterations]

            if energies:
                self.ax.plot(
                    iters, energies, "o-", color="blue", linewidth=1.5, markersize=4
                )
                self.ax.set_title(f"SCF Trace: {trace.get('step', 'Step')}", pad=15)
                self.ax.set_xlabel("Iteration")
                self.ax.set_ylabel("Energy (Eh)")
                self.ax.grid(True, linestyle="--", alpha=0.7)
                self.ax.ticklabel_format(useOffset=False, style="plain")
            else:
                self.ax.text(
                    0.5, 0.5, "No energy data in this block", ha="center", va="center"
                )

        self.figure.tight_layout()
        self.canvas.draw_idle()

    def export_csv(self):
        """Prompt for a path and write the SCF trace (selected block or all) to CSV."""
        idx = self.combo_steps.currentData()
        if idx is None:
            return

        default_path = get_default_export_path(
            self.parent().file_path, suffix="_scf_trace", extension=".csv"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Export SCF Trace", default_path, "CSV Files (*.csv)"
        )
        if not path:
            return

        try:
            if idx == -1:
                # Export all
                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(
                        [
                            "Cumulative Iteration",
                            "Block",
                            "Internal Iter",
                            "Energy (Eh)",
                        ]
                    )
                    cum_idx = 1
                    for i, trace in enumerate(self.scf_traces):
                        label = trace.get("step") or f"Step {i + 1}"
                        for d in trace.get("iterations", []):
                            writer.writerow([cum_idx, label, d["iter"], d["energy"]])
                            cum_idx += 1
            else:
                trace = self.scf_traces[idx]
                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(["Iteration", "Energy (Eh)"])
                    for d in trace.get("iterations", []):
                        writer.writerow([d["iter"], d["energy"]])
            notify(self, f"Data exported to {path}", 5000)
        # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.warning("SCF: exporting the trace to %s failed: %s", path, e)
            QMessageBox.critical(self, "Export Error", f"Failed to export CSV:\n{e}")
