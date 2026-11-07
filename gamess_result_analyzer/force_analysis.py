"""Force Analysis dialog: gradient/force table, 3D vector overlay and convergence graph."""

import os
import math
import numpy as np
import pyvista as pv
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QDoubleSpinBox,
    QGroupBox,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QCheckBox,
    QAbstractItemView,
    QMessageBox,
    QComboBox,
)
from PySide6.QtCore import Qt, QTimer
from .loading import load_gamess_parser
from .settings import load_section, save_section
import logging

try:
    from matplotlib.backends.backend_qtagg import (
        FigureCanvasQTAgg,
        NavigationToolbar2QT,
    )
    from matplotlib.figure import Figure

    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


class ConvergenceGraphDialog(QDialog):
    """Dialog plotting optimization convergence metrics against their thresholds."""

    def __init__(self, parent, traj_steps, current_idx=None):
        super().__init__(parent)
        self.setWindowTitle("Convergence Thresholds")
        self.resize(1100, 600)

        self.traj_steps = traj_steps
        self.current_idx = current_idx

        layout = QVBoxLayout(self)

        if not HAS_MATPLOTLIB:
            layout.addWidget(QLabel("Matplotlib is required to view the graph."))
            return

        controls_layout = QHBoxLayout()
        controls_layout.addWidget(QLabel("Display Metric:"))
        self.metric_combo = QComboBox()
        self.metric_combo.addItem("All")
        self.metric_combo.addItems(
            ["RMS Grad", "MAX Grad", "RMS Step", "MAX Step", "Energy Change"]
        )
        self.metric_combo.currentTextChanged.connect(self.redraw_graph)
        controls_layout.addWidget(self.metric_combo)
        controls_layout.addStretch()
        btn_export = QPushButton("Export CSV")
        btn_export.clicked.connect(self.export_csv)
        controls_layout.addWidget(btn_export)
        layout.addLayout(controls_layout)

        self.figure = Figure()
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas)

        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        layout.addWidget(self.toolbar)

        self.redraw_graph()

    def redraw_graph(self):
        """Replot the convergence graph for the currently selected metric."""
        self.figure.clear()
        selection = self.metric_combo.currentText()
        if not isinstance(selection, str) or not selection:
            selection = "All"
        self.plot_data(self.traj_steps, self.current_idx, selection)
        self.canvas.draw()

    def export_csv(self):
        """Export convergence data to CSV."""
        from PySide6.QtWidgets import QFileDialog
        import csv

        display_keys = {
            "rms gradient": "RMS Grad",
            "max gradient": "MAX Grad",
            "rms step": "RMS Step",
            "max step": "MAX Step",
            "energy change": "Energy Change",
        }

        def safe_float(v):
            try:
                return float(v)
            except (ValueError, TypeError):
                return ""

        # Gather all data
        rows = []
        for i, step in enumerate(self.traj_steps):
            conv = step.get("convergence")
            if not conv:
                continue
            row = {"Step": i + 1}
            for k, label in display_keys.items():
                val_obj = conv.get(k)
                if val_obj and isinstance(val_obj, dict):
                    row[f"{label} Value"] = safe_float(val_obj.get("value", ""))
                    tol = val_obj.get("tolerance") or val_obj.get("target", "")
                    row[f"{label} Tolerance"] = safe_float(tol) if tol else ""
                    row[f"{label} Converged"] = val_obj.get("converged", "")
                else:
                    row[f"{label} Value"] = ""
                    row[f"{label} Tolerance"] = ""
                    row[f"{label} Converged"] = ""
            rows.append(row)

        if not rows:
            QMessageBox.warning(self, "No Data", "No convergence data to export.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Convergence CSV", "convergence.csv", "CSV Files (*.csv)"
        )
        if not path:
            return

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            QMessageBox.information(self, "Exported", f"Saved to:\n{path}")
        except (ImportError, OSError, IndexError, ValueError) as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def plot_data(self, traj_steps, current_idx, selection="All"):  # pylint: disable=unused-argument
        # current_idx is unused: the graph has no per-step highlight, unlike the trajectory plot.
        """Draw the selected convergence metric(s) with threshold lines and markers."""
        display_keys = {
            "rms gradient": "RMS Grad",
            "max gradient": "MAX Grad",
            "rms step": "RMS Step",
            "max step": "MAX Step",
            "energy change": "Energy Change",
        }

        if selection != "All":
            display_keys = {k: v for k, v in display_keys.items() if v == selection}

        # Gather data
        data = {k: [] for k in display_keys.keys()}
        targets = {k: None for k in display_keys.keys()}
        steps = []

        def safe_float(v):
            try:
                return float(v)
            except (ValueError, TypeError):
                return np.nan

        for i, step in enumerate(traj_steps):
            conv = step.get("convergence")
            if not conv:
                continue
            steps.append(i + 1)
            for k in display_keys.keys():
                val_obj = conv.get(k)
                if val_obj and isinstance(val_obj, dict):
                    val = safe_float(val_obj.get("value", np.nan))
                    if k == "energy change":
                        val = abs(val)
                    data[k].append(val)
                    if targets[k] is None:
                        # parser uses "tolerance" key; also accept "target" for compat
                        tol = val_obj.get("tolerance") or val_obj.get("target")
                        if tol not in (None, ""):
                            targets[k] = safe_float(tol)
                elif val_obj is not None:
                    val = safe_float(val_obj)
                    if k == "energy change":
                        val = abs(val)
                    data[k].append(val)
                else:
                    data[k].append(np.nan)

        keys_with_data = [
            k for k in display_keys.keys() if any(not np.isnan(v) for v in data[k])
        ]

        if not steps or not keys_with_data:
            ax = self.figure.add_subplot(111)
            ax.text(
                0.5,
                0.5,
                f"No convergence data available for {selection}",
                ha="center",
                va="center",
            )
            return

        # Fixed color per metric — stable no matter which dropdown selection is active
        ALL_KEYS = [
            "rms gradient",
            "max gradient",
            "rms step",
            "max step",
            "energy change",
        ]
        COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]
        color_map = {k: COLORS[i % len(COLORS)] for i, k in enumerate(ALL_KEYS)}

        is_multi = selection == "All" and len(keys_with_data) > 1

        ax1 = self.figure.add_subplot(111)
        axes = [ax1]

        if is_multi:
            for _ in range(1, len(keys_with_data)):
                axes.append(ax1.twinx())

            # Hide ALL spines on every axis first, then selectively show each one
            for ax in axes:
                for spine in ax.spines.values():
                    spine.set_visible(False)

            # ax[0]: left + bottom + top (upper line)
            axes[0].spines["left"].set_visible(True)
            axes[0].spines["bottom"].set_visible(True)
            axes[0].spines["top"].set_visible(True)
            # ax[1]: right at 1.0
            if len(axes) > 1:
                axes[1].spines["right"].set_visible(True)
            # ax[2]: right at 1.18
            if len(axes) > 2:
                axes[2].spines["right"].set_position(("axes", 1.18))
                axes[2].spines["right"].set_visible(True)
            # ax[3]: left at -0.18
            if len(axes) > 3:
                axes[3].spines["left"].set_position(("axes", -0.18))
                axes[3].spines["left"].set_visible(True)
                axes[3].yaxis.set_label_position("left")
                axes[3].yaxis.set_ticks_position("left")
            # ax[4]: right at 1.36
            if len(axes) > 4:
                axes[4].spines["right"].set_position(("axes", 1.36))
                axes[4].spines["right"].set_visible(True)

            self.figure.subplots_adjust(left=0.15, right=0.65, bottom=0.10, top=0.96)
        else:
            self.figure.subplots_adjust(left=0.12, right=0.78, bottom=0.12, top=0.95)

        lines = []
        labels = []
        spine_positions = ["left", "right", "right", "left", "right"]
        axes_x_coords = [0.0, 1.0, 1.18, -0.18, 1.36]

        for idx, (ax, k) in enumerate(zip(axes, keys_with_data)):
            name = display_keys[k]
            color = color_map[k]

            plot_result = ax.plot(
                steps,
                data[k],
                marker="o",
                markersize=4,
                color=color,
                label=name,
                linewidth=1.5,
            )
            if not plot_result:
                continue
            line = plot_result[0]
            lines.append(line)
            labels.append(name)

            # Threshold dashed line and Y-axis triangle marker
            if targets[k] is not None:
                ax.axhline(
                    y=targets[k], color=color, linestyle="--", linewidth=1.5, alpha=0.8
                )
                # Add threshold value to y-ticks
                try:
                    yticks = list(ax.get_yticks())
                    # Filter out ticks that are too close to avoid overlapping labels in log space
                    yticks = [
                        t
                        for t in yticks
                        if abs(
                            math.log10(max(1e-10, abs(t)))
                            - math.log10(max(1e-10, abs(targets[k])))
                        )
                        > 0.3
                    ]
                    yticks.append(targets[k])
                    ax.set_yticks(yticks)
                except (IndexError, TypeError, ValueError) as e:
                    logging.warning("Failed to add threshold tick: %s", e)

                # Draw a triangle marker on the side of the Y-axis
                try:
                    import matplotlib.transforms as mtransforms

                    trans = mtransforms.blended_transform_factory(
                        ax.transAxes, ax.transData
                    )
                    x_axes_val = (
                        axes_x_coords[idx % len(axes_x_coords)] if is_multi else 0.0
                    )
                    if x_axes_val <= 0.0:
                        marker_shape = ">"
                        x_marker_val = x_axes_val - 0.015
                    else:
                        marker_shape = "<"
                        x_marker_val = x_axes_val + 0.015

                    ax.plot(
                        x_marker_val,
                        targets[k],
                        marker=marker_shape,
                        color=color,
                        transform=trans,
                        clip_on=False,
                        markersize=7,
                        zorder=5,
                    )
                # C++ library boundary: matplotlib rendering exceptions do not map to Python types
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logging.warning("Failed to draw threshold marker on axis: %s", e)

            ax.set_ylabel(name, color=color, fontsize=9)
            ax.tick_params(axis="y", colors=color, labelsize=8)
            ax.set_yscale("symlog", linthresh=1e-6)

            # Set Y-axis spine color to match the metric color
            if is_multi:
                pos = spine_positions[idx % len(spine_positions)]
                ax.spines[pos].set_color(color)
            else:
                ax.spines["left"].set_color(color)

        ax1.set_xlabel("Optimization Step", fontsize=9)
        ax1.tick_params(axis="x", labelsize=8)
        ax1.grid(True, which="both", ls="--", alpha=0.15)

        # Legend outside to the right, never overlapping
        ax1.legend(
            lines,
            labels,
            loc="upper left",
            bbox_to_anchor=(1.05 if not is_multi else 1.45, 1.0),
            borderaxespad=0,
            fontsize=8,
            framealpha=0.9,
        )


class ForceViewerDialog(QDialog):
    """Dialog showing gradients/forces per atom with an optional 3D vector overlay."""

    # pylint: disable=attribute-defined-outside-init
    # Qt/PyVista pattern: label/actor/state attrs are created lazily, not in __init__.

    def __init__(self, parent_dlg, gradients, parser=None):
        super().__init__(parent_dlg)
        self.setWindowTitle("Force Analysis")
        self.resize(700, 750)
        self.parent_dlg = parent_dlg
        self.gradients = gradients  # Current gradients from .out file
        self.parser = parser
        self.actors = []
        self.force_color = "red"
        self.settings_file = os.path.join(os.path.dirname(__file__), "settings.json")
        self.graph_dlg = None

        # Get trajectory steps if available
        self.traj_steps = []
        self.current_step_idx = -1  # -1 means current/final structure
        if self.parser and "scan_steps" in self.parser.data:
            self.traj_steps = self.parser.data["scan_steps"]

        main_layout = QVBoxLayout(self)

        # Visualization Controls (Restoring deleted code)
        view_group = QGroupBox("Visualization Settings")
        view_layout = QVBoxLayout(view_group)

        # Row 1: Visualize Button and Scale
        row1 = QHBoxLayout()

        self.btn_visualize = QPushButton("Visualize Forces")
        self.btn_visualize.setCheckable(True)  # Use checkable for toggle state
        self.btn_visualize.clicked.connect(self.toggle_visualization)
        row1.addWidget(self.btn_visualize)

        row1.addSpacing(20)
        row1.addWidget(QLabel("Vector Scale:"))
        self.spin_scale = QDoubleSpinBox()
        self.spin_scale.setRange(0.01, 100000.0)  # Increased range for small gradients
        self.spin_scale.setValue(1.0)
        self.spin_scale.setSingleStep(0.1)
        self.spin_scale.valueChanged.connect(self.update_vectors)
        row1.addWidget(self.spin_scale)

        # Auto Scale Button
        btn_autoscale = QPushButton("Auto Scale")
        btn_autoscale.clicked.connect(self.auto_scale)
        row1.addWidget(btn_autoscale)

        self.chk_auto_scale = QCheckBox("Auto Apply")
        self.chk_auto_scale.setToolTip(
            "Automatically auto-scale vectors when step changes"
        )
        row1.addWidget(self.chk_auto_scale)

        row1.addStretch()

        view_layout.addLayout(row1)
        main_layout.addWidget(view_group)

        # Force and Gradient Table
        table_label = QLabel("Force and Gradient Data (Eh/Bohr):")
        main_layout.addWidget(table_label)

        self.force_table = QTableWidget()
        self.force_table.setColumnCount(8)
        self.force_table.setHorizontalHeaderLabels(
            [
                "Atom",
                "Grad X",
                "Grad Y",
                "Grad Z",
                "Force X",
                "Force Y",
                "Force Z",
                "Force Mag",
            ]
        )
        self.force_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.force_table.horizontalHeader()
        header.setStretchLastSection(True)
        main_layout.addWidget(self.force_table)

        # Trajectory Navigation (if available) - Moved AFTER table creation
        # (becuase controls trigger update which updates table)
        if self.traj_steps:
            self._setup_trajectory_controls(main_layout)

        # Buttons row
        buttons_layout = QHBoxLayout()

        btn_reload = QPushButton("Reload from File")
        btn_reload.setToolTip("Reload data from the GAMESS output file")
        btn_reload.clicked.connect(self.reload_data)
        buttons_layout.addWidget(btn_reload)

        buttons_layout.addStretch()

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        buttons_layout.addWidget(btn_close)

        main_layout.addLayout(buttons_layout)

        # Initialize
        self.populate_force_table()

        self.load_settings()

        # Vector visualization is OFF by default as per user request
        self.btn_visualize.setChecked(False)

    def toggle_visualization(self):
        """Toggle force vector visualization"""
        if self.btn_visualize.isChecked():
            self.btn_visualize.setText("Clear Forces")
            self.update_vectors()
        else:
            self.btn_visualize.setText("Visualize Forces")
            self.clear_vectors()

    def auto_scale(self):
        """Calculate and set an appropriate scale factor"""
        if not self.gradients:
            return

        try:
            # Find max magnitude
            max_mag = 0.0
            for g in self.gradients:
                vec = g.get("grad", g.get("vector", None))
                if vec:
                    mag = np.linalg.norm(vec)
                    if mag > max_mag:
                        max_mag = mag

            if max_mag > 1e-12:
                # Target max vector length ~ 1.5 - 2.0 units (Angstrom)
                target = 2.0
                new_scale = target / max_mag

                # Round nicely
                if new_scale > 100:
                    new_scale = round(new_scale, -int(np.log10(new_scale)) + 1)
                else:
                    new_scale = round(new_scale, 2)

                self.spin_scale.blockSignals(True)
                self.spin_scale.setValue(new_scale)
                self.spin_scale.blockSignals(False)

                # If currently visualizing, update
                if self.btn_visualize.isChecked():
                    self.update_vectors()
        # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.warning(
                "Force analysis: could not auto-scale the gradient vector display: %s",
                e,
            )

    def _setup_trajectory_controls(self, layout):
        """Setup trajectory navigation controls"""
        traj_group = QGroupBox(
            f"Optimization Trajectory ({len(self.traj_steps)} steps)"
        )
        traj_layout = QVBoxLayout(traj_group)

        # Slider
        slider_layout = QHBoxLayout()
        slider_layout.addWidget(QLabel("Step:"))

        self.traj_slider = QSlider(Qt.Orientation.Horizontal)
        self.traj_slider.setRange(0, len(self.traj_steps))  # 0 to N-1 (traj), N = final

        self.traj_label = QLabel("Current (Final)")

        # Info labels (define early)
        self.traj_info = QLabel("Showing final optimized structure")
        self.traj_conv = QLabel("")
        self.traj_conv.setStyleSheet("color: #444; font-size: 9pt;")
        self.traj_conv.setWordWrap(True)

        # Navigation buttons
        btn_prev = QPushButton("<")
        btn_prev.setFixedWidth(30)
        btn_prev.clicked.connect(
            lambda: self.traj_slider.setValue(max(0, self.traj_slider.value() - 1))
        )

        btn_next = QPushButton(">")
        btn_next.setFixedWidth(30)
        btn_next.clicked.connect(
            lambda: self.traj_slider.setValue(
                min(self.traj_slider.maximum(), self.traj_slider.value() + 1)
            )
        )

        slider_layout.addWidget(btn_prev)
        slider_layout.addWidget(self.traj_slider)
        slider_layout.addWidget(btn_next)
        slider_layout.addWidget(self.traj_label)

        # Jump to Final button
        btn_final = QPushButton("Jump to Final")
        btn_final.setToolTip("Jump to final structure with force data")
        btn_final.clicked.connect(
            lambda: self.traj_slider.setValue(len(self.traj_steps))
        )
        slider_layout.addWidget(btn_final)

        traj_layout.addLayout(slider_layout)

        traj_layout.addWidget(self.traj_info)
        traj_layout.addWidget(self.traj_conv)

        # Show Convergence Graph Button
        btn_graph = QPushButton("Show Threshold Graph")
        btn_graph.setToolTip(
            "Show convergence thresholds plotted across all optimization steps"
        )
        btn_graph.clicked.connect(self.show_convergence_graph)
        traj_layout.addWidget(btn_graph)

        # Connect signals AFTER UI construction
        self.traj_slider.valueChanged.connect(self.on_trajectory_change)

        # Block signals while setting initial value to avoid firing before data is ready
        self.traj_slider.blockSignals(True)

        is_running = False
        if self.parser:
            status = self.parser.data.get("termination_status", "Running")
            if "Running" in status:
                is_running = True

        if is_running:
            initial_val = self.get_last_force_containing_step_idx()
        else:
            initial_val = len(self.traj_steps)
            if (
                self.parser
                and not self.parser.data.get("converged", False)
                and self.traj_steps
            ):
                # Prefer last step that has convergence data
                found = False
                for idx in range(len(self.traj_steps) - 1, -1, -1):
                    step = self.traj_steps[idx]
                    if step.get("convergence"):
                        initial_val = idx
                        found = True
                        break

                # Fall back to last step with coords (very early in calc)
                if not found:
                    for idx in range(len(self.traj_steps) - 1, -1, -1):
                        if self.traj_steps[idx].get("atoms"):
                            initial_val = idx
                            break

        self.traj_slider.setValue(initial_val)
        self.traj_slider.blockSignals(False)
        # Manually fire the handler since setValue was called while signals were blocked
        self.on_trajectory_change(initial_val)

        # Insert at the top (index 0) so it appears above other controls
        # even though it is initialized last.
        layout.insertWidget(0, traj_group)

    def show_convergence_graph(self):
        """Open (or refresh) the convergence-threshold graph window."""
        if getattr(self, "traj_steps", None) is None or not self.traj_steps:
            QMessageBox.warning(
                self, "No Data", "No trajectory convergence data available."
            )
            return

        # Close existing graph dialog if open
        if getattr(self, "graph_dlg", None) is not None:
            try:
                self.graph_dlg.close()
            except (RuntimeError, AttributeError) as e:
                logging.warning(
                    "Force analysis: could not close the existing convergence graph window: %s",
                    e,
                )

        # pass current_step_idx so it can draw a vertical line for the current frame
        current_idx = getattr(self, "current_step_idx", None)
        self.graph_dlg = ConvergenceGraphDialog(self, self.traj_steps, current_idx)
        self.graph_dlg.show()
        self.graph_dlg.raise_()
        self.graph_dlg.activateWindow()

    def get_last_force_containing_step_idx(self):
        """Find the index of the last step/frame in traj_steps that contains force data.
        If no forces are found anywhere, returns len(self.traj_steps).
        """
        # Check trajectory steps from last to first
        for idx in range(len(self.traj_steps) - 1, -1, -1):
            step = self.traj_steps[idx]
            if step.get("gradients"):
                return idx

        # Fallback to len(self.traj_steps) if none found
        return len(self.traj_steps)

    def on_trajectory_change(self, val):
        """Handle trajectory step change"""
        self.current_step_idx = val
        num_steps = len(self.traj_steps)

        if val == num_steps:
            # Final/current structure
            self.traj_label.setText("Current (Final)")
            self.traj_info.setText("Showing final optimized structure")

            # Show convergence from the LAST trajectory step if it represents the same state
            # (which it usually does in a completed optimization)
            last_conv = {}
            if self.traj_steps:
                last_conv = self.traj_steps[-1].get("convergence", {})

            if last_conv:
                self._update_conv_label(last_conv)
            else:
                self.traj_conv.setText("")

            # Use current gradients
            self.gradients = (
                self.parser.data.get("gradients", []) if self.parser else []
            )

            # Auto-Scale if requested
            if (
                getattr(self, "chk_auto_scale", None) is not None
                and self.chk_auto_scale.isChecked()
            ):
                self.auto_scale()

            # Populate table
            self.populate_force_table()

            # Update structure first
            if (
                self.parser
                and "atoms" in self.parser.data
                and "coords" in self.parser.data
            ):
                self.update_structure(
                    self.parser.data["atoms"], self.parser.data["coords"]
                )

            # Then update vectors only if visualization is active
            if self.btn_visualize.isChecked():
                QTimer.singleShot(100, self.update_vectors)
        else:
            # Historical step
            step = self.traj_steps[val]
            energy = step.get("energy")
            self.traj_label.setText(f"Step {val + 1}/{num_steps}")
            self.traj_info.setText(
                f"Energy: {energy:.8f} Eh" if energy is not None
                else "Energy: not available"
            )

            # Update convergence info
            self._update_conv_label(step.get("convergence", {}))

            # Update gradients for this step
            self.gradients = step.get("gradients", [])

            # Auto-Scale if requested
            if (
                getattr(self, "chk_auto_scale", None) is not None
                and self.chk_auto_scale.isChecked()
            ):
                self.auto_scale()

            # Populate table
            self.populate_force_table()

            # Update structure first
            atoms = step.get("atoms", [])
            coords = step.get("coords", [])
            if atoms and coords:
                self.update_structure(atoms, coords)

            # Then update vectors only if visualization is active
            if self.btn_visualize.isChecked():
                QTimer.singleShot(100, self.update_vectors)

    def _update_conv_label(self, conv):
        """Helper to update the convergence info label with rich text and normalization"""
        if not conv:
            self.traj_conv.setText("No convergence data available")
            return

        # Map specific keys to display names (keys are now lowercased from parser)
        display_keys = {
            "rms gradient": "RMS Grad",
            "max gradient": "MAX Grad",
            "rms step": "RMS Step",
            "max step": "MAX Step",
            "energy change": "Energy Change",
        }

        items = []
        for k, v in conv.items():
            if isinstance(v, dict):
                dn = display_keys.get(k.lower(), k.title())
                status = v.get("converged", "??").upper()
                # Status determines color of the whole line
                if status == "YES":
                    color = "#28a745"  # Green
                    status_text = f" ({status})"
                elif status == "NO":
                    color = "#dc3545"  # Red
                    status_text = f" ({status})"
                elif status == "INFO":
                    color = "#000000"  # Black
                    status_text = ""
                else:
                    color = "#000000"
                    status_text = f" ({status})"

                items.append(
                    f"<span style='color: {color};'><b>{dn}:</b> {v.get('value')}{status_text}</span>"
                )
            else:
                items.append(f"<b>{k}:</b> {v:.6f}")

        num_items = len(items)
        if num_items == 0:
            self.traj_conv.setText("")
            return

        # Split into 2 columns
        half = math.ceil(num_items / 2)
        col1_items = items[:half]
        col2_items = items[half:]

        html = "<table width='100%'><tr>"
        html += "<td valign='top'>" + "<br>".join(col1_items) + "</td>"
        html += "<td valign='top'>" + "<br>".join(col2_items) + "</td>"
        html += "</tr></table>"

        self.traj_conv.setText(html)

    def reload_data(self):
        """Reload data from the output file"""
        if getattr(self, "_reloading", False):
            return
        self._reloading = True
        if not self.parser or not self.parser.filename:
            QMessageBox.warning(self, "Error", "No file associated with this parser.")
            self._reloading = False
            return

        if not os.path.exists(self.parser.filename):
            QMessageBox.warning(
                self, "Error", f"File not found: {self.parser.filename}"
            )
            self._reloading = False
            return

        try:
            fresh = load_gamess_parser(self.parser.filename, self)
            if fresh is None:  # cancelled by the user
                return

            # Refresh in place — the other dialogs hold this parser object.
            self.parser.data.clear()
            self.parser.data.update(fresh.data)
            self.parser.lines = fresh.lines
            self.parser.raw_content = fresh.raw_content

            # Update local state
            self.gradients = self.parser.data.get("gradients", [])
            self.traj_steps = self.parser.data.get("scan_steps", [])

            # Update slider if it exists
            if getattr(self, "traj_slider", None) is not None:
                self.traj_slider.blockSignals(True)
                self.traj_slider.setRange(0, len(self.traj_steps))

                # Check if job is running
                is_running = False
                if self.parser:
                    status = self.parser.data.get("termination_status", "Running")
                    if "Running" in status:
                        is_running = True

                if is_running:
                    # Switch to the last force containing frame
                    self.current_step_idx = self.get_last_force_containing_step_idx()
                else:
                    # Keep current selection within bounds
                    if self.current_step_idx < 0 or self.current_step_idx > len(
                        self.traj_steps
                    ):
                        self.current_step_idx = len(self.traj_steps)

                self.traj_slider.setValue(self.current_step_idx)
                self.traj_slider.blockSignals(False)

                # Update label/text
                self.on_trajectory_change(self.current_step_idx)

            # Update force table and vectors
            self.populate_force_table()
            if self.btn_visualize.isChecked():
                self.update_vectors()

        # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
        except Exception as e:  # pylint: disable=broad-exception-caught
            QMessageBox.critical(self, "Error", f"Failed to reload data: {e}")
        finally:
            self._reloading = False

    def update_structure(self, atoms, coords):
        """Update the 3D structure visualization using RDKit"""
        try:
            from rdkit import Chem
            from rdkit.Geometry import Point3D
            from .utils import normalize_atom_symbol, determine_bonds_without_dummies
        except ImportError:
            return

        if not hasattr(self, "_pt"):
            self._pt = Chem.GetPeriodicTable()

        mol = Chem.RWMol()  # pylint: disable=no-member
        conf = Chem.Conformer()  # pylint: disable=no-member
        pt = self._pt

        try:
            for i, sym in enumerate(atoms):
                sym = normalize_atom_symbol(sym)
                an = pt.GetAtomicNumber(sym)
                mol.AddAtom(Chem.Atom(an))
                conf.SetAtomPosition(
                    i, Point3D(coords[i][0], coords[i][1], coords[i][2])
                )
        except (RuntimeError, AttributeError, IndexError, ValueError):
            return

        mol.AddConformer(conf)

        # Determine bonds and bond orders on every load (no animation in this view).
        try:
            charge = self.parser.data.get("charge", 0) if self.parser else 0
            determine_bonds_without_dummies(mol, charge=charge, bond_orders=True)
        except (RuntimeError, AttributeError, ValueError) as e:
            logging.warning(
                "Force analysis: could not determine bonds/bond orders for the trajectory frame: %s",
                e,
            )

        final_mol = mol.GetMol()

        # Update in main window
        if self.parent_dlg.context:
            self.parent_dlg.context.draw_molecule_3d(final_mol)

    def populate_force_table(self):
        """Populate the force and gradient table from current gradient data"""
        self.force_table.setRowCount(0)

        # Get atoms from parser
        atoms = []
        if self.parser and "atoms" in self.parser.data:
            atoms = self.parser.data["atoms"]

        if not atoms or not self.gradients:
            # Show "No Data" message if gradients are missing but we are supposed to show something
            self.force_table.setRowCount(1)
            msg_item = QTableWidgetItem(
                "No gradient data available for this step in output file"
            )
            msg_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.force_table.setItem(0, 0, msg_item)
            self.force_table.setSpan(0, 0, 1, 8)
            return

        # Populate with gradient data (force = -gradient)
        for grad_item in self.gradients:
            atom_idx = grad_item.get("atom_idx", None)
            if atom_idx is None or atom_idx >= len(atoms):
                continue

            vec = grad_item.get("grad", grad_item.get("vector", None))
            if not vec or len(vec) != 3:
                continue

            # Gradient components
            gx, gy, gz = vec[0], vec[1], vec[2]

            # Force is negative gradient
            fx, fy, fz = -gx, -gy, -gz
            magnitude = np.sqrt(fx**2 + fy**2 + fz**2)

            atom_label = f"{atoms[atom_idx]}{atom_idx + 1}"  # 1-based

            row = self.force_table.rowCount()
            self.force_table.insertRow(row)
            self.force_table.setItem(row, 0, QTableWidgetItem(atom_label))
            self.force_table.setItem(row, 1, QTableWidgetItem(f"{gx:.8f}"))
            self.force_table.setItem(row, 2, QTableWidgetItem(f"{gy:.8f}"))
            self.force_table.setItem(row, 3, QTableWidgetItem(f"{gz:.8f}"))
            self.force_table.setItem(row, 4, QTableWidgetItem(f"{fx:.8f}"))
            self.force_table.setItem(row, 5, QTableWidgetItem(f"{fy:.8f}"))
            self.force_table.setItem(row, 6, QTableWidgetItem(f"{fz:.8f}"))
            self.force_table.setItem(row, 7, QTableWidgetItem(f"{magnitude:.8f}"))

        self.force_table.resizeColumnsToContents()

    def update_vectors(self):
        """Update the force vectors in the 3D visualizer"""
        if getattr(self, "_is_closing", False):
            return
        try:
            mw = None
            if hasattr(self.parent_dlg, "context") and self.parent_dlg.context:
                mw = self.parent_dlg.context.get_main_window()
            elif hasattr(self.parent_dlg, "mw"):
                mw = self.parent_dlg.mw

            if not mw or not hasattr(mw, "plotter"):
                return

            # Clear old vectors
            self.clear_vectors()

            # Only show vectors if checked (visualizing)
            if not self.btn_visualize.isChecked():
                return

            # Get coordinates for the current view
            current_coords = []
            if self.current_step_idx < 0 or self.current_step_idx == len(
                self.traj_steps
            ):
                # Final / current structure (sentinel -1 or slider at max)
                if self.parser and "coords" in self.parser.data:
                    current_coords = self.parser.data["coords"]
            else:
                # Trajectory step
                current_coords = self.traj_steps[self.current_step_idx].get(
                    "coords", []
                )

            # Get settings
            scale = self.spin_scale.value()

            if not current_coords or not self.gradients:
                return

            # Draw force vectors
            for grad_item in self.gradients:
                atom_idx = grad_item.get("atom_idx", None)
                if atom_idx is None or atom_idx >= len(current_coords):
                    continue

                vec = grad_item.get("grad", grad_item.get("vector", None))
                if not vec or len(vec) != 3:
                    continue

                # Force = -Gradient
                force = np.array([-vec[0], -vec[1], -vec[2]])

                magnitude = np.linalg.norm(force)

                if magnitude < 1e-12:
                    continue

                # Scale the vector
                scaled_force = force * scale
                scaled_mag = np.linalg.norm(scaled_force)

                if scaled_mag < 1e-12:
                    continue

                direction = scaled_force / scaled_mag

                # Create arrow
                arrow = pv.Arrow(
                    start=current_coords[atom_idx],
                    direction=direction,
                    scale=scaled_mag,
                    shaft_resolution=20,
                    tip_resolution=20,
                )

                actor = mw.plotter.add_mesh(
                    arrow, color=self.force_color, name=f"force_{atom_idx}"
                )
                self.actors.append(actor)

            mw.plotter.render()

        # C++ library boundary: RDKit/VTK/pyvista exceptions do not map to Python types
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.warning("Error drawing force vectors: %s", e)

    def clear_vectors(self):
        """Clear all force vector actors"""
        mw = None
        if hasattr(self.parent_dlg, "context") and self.parent_dlg.context:
            mw = self.parent_dlg.context.get_main_window()
        elif hasattr(self.parent_dlg, "mw"):
            mw = self.parent_dlg.mw

        if not mw or not hasattr(mw, "plotter"):
            return

        for actor in self.actors:
            try:
                mw.plotter.remove_actor(actor)
            except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                logging.debug(
                    "Force analysis: could not remove a force-vector actor: %s", e
                )

        self.actors = []
        mw.plotter.render()

    def reject(self):
        """Route Esc through close() so closeEvent cleanup always runs."""
        # Esc must run closeEvent cleanup (QDialog.reject only hides)
        self.close()

    def closeEvent(self, event):
        """Clean up when dialog closes"""
        self._is_closing = True
        self.clear_vectors()
        self.save_settings()
        if getattr(self, "graph_dlg", None) is not None:
            try:
                self.graph_dlg.close()
            except (RuntimeError, AttributeError) as e:
                logging.warning(
                    "Force analysis: could not close the convergence graph window on close: %s",
                    e,
                )
            self.graph_dlg = None
        # accept() not super().closeEvent(): QDialog.closeEvent calls reject(),
        # which is routed back through close() and would recurse.
        event.accept()

    def load_settings(self):
        """Restore the saved force-vector color."""
        if os.path.exists(self.settings_file):
            settings = load_section(self.settings_file, "force_settings")
            try:
                # Note: 'scale' is intentionally NOT loaded to allow auto-scaling based on specific molecule data

                if "force_color" in settings:
                    self.force_color = settings["force_color"]

            except (KeyError, IndexError, ValueError) as e:
                logging.warning("Error loading force settings: %s", e)

    def save_settings(self):
        """Persist the force-vector color."""
        force_settings = {
            # "scale": self.spin_scale.value(), # Do not save scale
            # "reverse_vector": self.chk_reverse.isChecked() if hasattr(self, 'chk_reverse') else True,
            "force_color": self.force_color
        }

        save_section(self.settings_file, "force_settings", force_settings)
