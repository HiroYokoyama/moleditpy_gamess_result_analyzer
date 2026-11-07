"""Trajectory/scan dialog: energy profile plot, structure playback and export."""

import csv
import os
import matplotlib

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QRadioButton,
    QComboBox,
    QPushButton,
    QSpinBox,
    QFormLayout,
    QDialogButtonBox,
    QCheckBox,
    QFileDialog,
    QMessageBox,
    QApplication,
    QButtonGroup,
)
from PySide6.QtCore import Qt, QTimer
from .parser import GAMESSParser
from .gif_export import Image, HAS_PIL, encode_frames_to_gif
from .utils import (
    get_default_export_path,
    normalize_atom_symbol,
    determine_bonds_without_dummies,
    notify,
)
import logging

try:
    from rdkit import Chem
    from rdkit.Geometry import Point3D
except ImportError:
    Chem = None
    Point3D = None


class MplCanvas(FigureCanvasQTAgg):
    """Matplotlib canvas embedding a single figure/axes for the energy profile plot."""

    def __init__(self, parent=None, width=5, height=4, dpi=100):  # pylint: disable=unused-argument
        # parent is unused: layout.addWidget() reparents the canvas once it is added.
        """Create the figure and single subplot backing this canvas."""
        self.fig = Figure(figsize=(width, height), dpi=dpi)
        self.axes = self.fig.add_subplot(111)
        super(MplCanvas, self).__init__(self.fig)


class TrajectoryResultDialog(QDialog):
    """Trajectory/scan dialog: energy profile plot, structure playback and export."""

    # pylint: disable=attribute-defined-outside-init
    # Qt pattern: widget attributes are set in init_ui(), called from __init__.

    def __init__(
        self,
        gl_widget,
        steps,
        charge=0,
        title="Trajectory Analysis",
        base_dir=None,
        output_path=None,
        context=None,
    ):
        super().__init__()
        self.setWindowTitle(title)
        self.resize(800, 600)
        self.base_dir = base_dir
        self.gl_widget = gl_widget
        self.context = context
        # Filter out steps with zero energy (incomplete cycles in running calculations)
        steps = [
            s for s in steps if s.get("energy") is not None and abs(s["energy"]) > 1e-9
        ]
        self.steps = steps  # Current steps to display
        self.charge = charge
        self.output_path = output_path

        # Filtering setup
        self.all_steps = steps
        self.scan_points = self.compute_scan_points(steps)
        self.showing_scan_points = False

        # Default logic: prefer scan points if available
        if len(self.scan_points) < len(self.all_steps) and len(self.scan_points) > 0:
            self.steps = self.scan_points
            self.showing_scan_points = True
        self.show_relative = self.showing_scan_points
        # The coordinate axis is the default when the scan reports one.
        has_coord_values = any(
            s.get("scan_coord", None) is not None or s.get("dist", None) is not None
            for s in self.all_steps
        )
        self.show_coord_x = has_coord_values
        self.use_log_scale = False
        self.is_playing = False
        self.timer = QTimer()
        self.timer.timeout.connect(self.next_frame)
        # pylint: disable-next=no-member
        self._periodic_table = Chem.GetPeriodicTable() if Chem else None

        self.current_unit = "kJ/mol"
        self.global_min_e = (
            min([s["energy"] for s in self.all_steps]) if self.all_steps else 0
        )

        self.recalc_energies()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        # 1. Matplotlib Canvas
        self.canvas = MplCanvas(self, width=5, height=4, dpi=100)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas)

        # Connect events
        self.canvas.mpl_connect("pick_event", self.on_pick)
        self.canvas.mpl_connect("motion_notify_event", self.on_hover)
        self.canvas.mpl_connect("scroll_event", self.on_scroll)

        # Tooltip annotation
        self.annot = self.canvas.axes.annotate(
            "",
            xy=(0, 0),
            xytext=(20, 20),
            textcoords="offset points",
            bbox=dict(boxstyle="round", fc="w", alpha=0.9),
            arrowprops=dict(arrowstyle="->"),
            zorder=1000,
        )
        self.annot.set_visible(False)

        self.init_ui()
        self.plot_data()
        # Load initial structure into the 3D view (slider starts at 0 and never
        # emits valueChanged, so on_step_changed would not fire otherwise).
        QTimer.singleShot(0, lambda: self.on_step_changed(self.slider.value()))


    def init_ui(self):
        """Build the step slider, view/energy/axis toggles and playback/export buttons."""
        # 2. Controls
        ctrl_layout = QHBoxLayout()

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, len(self.steps) - 1)
        self.slider.valueChanged.connect(self.on_step_changed)
        ctrl_layout.addWidget(QLabel("Step:"))
        ctrl_layout.addWidget(self.slider)

        self.lbl_info = QLabel(f"Step 1/{len(self.steps)}")
        ctrl_layout.addWidget(self.lbl_info)

        self.layout().addLayout(ctrl_layout)

        # Toggle Layout (Combined)
        toggle_layout = QHBoxLayout()

        # 1. View Group
        self.view_grp = QButtonGroup(self)
        self.radio_scan = QRadioButton("Scan Points")
        self.radio_full = QRadioButton("All Trajectory")
        self.view_grp.addButton(self.radio_scan)
        self.view_grp.addButton(self.radio_full)

        if self.showing_scan_points:
            self.radio_scan.setChecked(True)
        else:
            self.radio_full.setChecked(True)

        self.radio_full.toggled.connect(self.on_traj_mode_changed)
        self.radio_scan.toggled.connect(self.on_traj_mode_changed)

        # Only enable if distinction exists
        if len(self.scan_points) == len(self.all_steps):
            self.radio_full.setEnabled(False)
            self.radio_scan.setEnabled(False)

        # 2. Energy Group
        self.energy_grp = QButtonGroup(self)
        self.radio_abs = QRadioButton("Absolute")
        self.radio_rel = QRadioButton("Relative")
        self.energy_grp.addButton(self.radio_abs)
        self.energy_grp.addButton(self.radio_rel)

        self.radio_abs.setChecked(not self.show_relative)
        self.radio_rel.setChecked(self.show_relative)
        self.radio_rel.toggled.connect(self.on_toggle_mode)

        self.chk_log = QCheckBox("Log Scale")
        self.chk_log.toggled.connect(self.on_log_changed)
        self.chk_log.setChecked(self.use_log_scale)

        self.combo_unit = QComboBox()
        self.combo_unit.addItems(["kJ/mol", "kcal/mol", "eV", "Eh"])
        self.combo_unit.currentTextChanged.connect(self.on_unit_changed)

        # Add to layout
        toggle_layout.addWidget(QLabel("Mode:"))
        toggle_layout.addWidget(self.radio_abs)
        toggle_layout.addWidget(self.radio_rel)
        toggle_layout.addWidget(self.chk_log)
        toggle_layout.addWidget(QLabel("Unit:"))
        toggle_layout.addWidget(self.combo_unit)

        self.x_axis_grp = QButtonGroup(self)
        self.radio_idx = QRadioButton("Step")
        self.radio_coord = QRadioButton("Coordinate")
        self.x_axis_grp.addButton(self.radio_idx)
        self.x_axis_grp.addButton(self.radio_coord)

        if self.show_coord_x:
            self.radio_coord.setChecked(True)
        else:
            self.radio_idx.setChecked(True)
            self.radio_coord.setEnabled(False)

        self.radio_idx.toggled.connect(self.on_x_axis_mode_changed)
        self.radio_coord.toggled.connect(self.on_x_axis_mode_changed)

        toggle_layout.addStretch()
        self.layout().addLayout(toggle_layout)

        # Disable Log Scale by default (if not relative)
        self.chk_log.setEnabled(self.radio_rel.isChecked())

        # 3. Buttons (Playback & Export)
        btn_layout = QHBoxLayout()

        self.btn_play = QPushButton("Play")
        self.btn_play.clicked.connect(self.toggle_play)
        if self.steps and not self.steps[0].get("atoms", None):
            self.btn_play.setEnabled(False)
        btn_layout.addWidget(self.btn_play)

        self.btn_first = QPushButton("|<")
        self.btn_first.setFixedWidth(30)
        self.btn_first.setToolTip("Go to first step")
        self.btn_first.clicked.connect(self.go_to_first_frame)
        btn_layout.addWidget(self.btn_first)

        self.btn_prev = QPushButton("<")
        self.btn_prev.setFixedWidth(30)
        self.btn_prev.clicked.connect(self.prev_frame)
        btn_layout.addWidget(self.btn_prev)

        self.btn_next = QPushButton(">")
        self.btn_next.setFixedWidth(30)
        self.btn_next.clicked.connect(self.next_frame)
        btn_layout.addWidget(self.btn_next)

        self.btn_last = QPushButton(">|")
        self.btn_last.setFixedWidth(30)
        self.btn_last.setToolTip("Go to last step")
        self.btn_last.clicked.connect(self.go_to_last_frame)
        btn_layout.addWidget(self.btn_last)

        btn_layout.addWidget(QLabel(" | FPS:"))
        self.spin_fps = QSpinBox()
        self.spin_fps.setRange(1, 60)
        self.spin_fps.setValue(10)
        self.spin_fps.valueChanged.connect(self.on_fps_changed)
        btn_layout.addWidget(self.spin_fps)

        self.chk_loop = QCheckBox("Loop")
        self.chk_loop.setChecked(True)
        btn_layout.addWidget(self.chk_loop)

        btn_layout.addStretch()

        btn_save_graph = QPushButton("Save Graph")
        btn_save_graph.clicked.connect(self.save_graph)
        btn_layout.addWidget(btn_save_graph)

        btn_save_csv = QPushButton("Save CSV")
        btn_save_csv.clicked.connect(self.save_csv)
        btn_layout.addWidget(btn_save_csv)

        self.btn_save_gif = QPushButton("Save GIF")
        self.btn_save_gif.clicked.connect(self.save_gif)
        self.btn_save_gif.setEnabled(HAS_PIL)
        if self.steps and not self.steps[0].get("atoms", None):
            self.btn_save_gif.setEnabled(False)
        btn_layout.addWidget(self.btn_save_gif)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        btn_layout.addWidget(btn_close)

        self.layout().addLayout(btn_layout)

    def recalc_energies(self):
        """Recompute the raw and display energies (and their baseline) for the current step list."""
        # Extract energies
        self.energies = [s["energy"] for s in self.steps]
        # Absolute and Relative energies in current unit
        # Use context-aware baseline: minimum of currently displayed steps
        # This ensures perfect zero alignment in Scan Points view
        self.min_e = min(self.energies) if self.energies else 0

        self.display_energies = []
        self.update_display_values()

    def compute_scan_points(self, steps):
        """Filter steps to find key scan points (converged geometries)."""
        groups = {}
        has_scan_ids = False

        for s in steps:
            sid = s.get("scan_step_id", None)
            if sid is not None:
                has_scan_ids = True
                if sid not in groups:
                    groups[sid] = []
                groups[sid].append(s)

        if not has_scan_ids:
            return steps

        final_points = []
        sorted_ids = sorted(groups.keys())
        for sid in sorted_ids:
            g_steps = groups[sid]
            # Prefer opt_final (FINAL ENERGY EVALUATION) if present, else last opt_cycle
            opt_final = [x for x in g_steps if x.get("type") == "opt_final"]
            if opt_final:
                final_points.append(opt_final[-1])
            else:
                opt_cycles = [x for x in g_steps if x.get("type") == "opt_cycle"]
                if opt_cycles:
                    final_points.append(opt_cycles[-1])
                else:
                    final_points.append(g_steps[-1])

        return final_points

    def on_traj_mode_changed(self):
        """Switch between showing all trajectory steps and only the converged scan points."""
        # Only trigger on the newly checked button
        if not self.sender().isChecked():
            return

        self.showing_scan_points = self.radio_scan.isChecked()

        if self.is_playing:
            self.toggle_play()

        if self.showing_scan_points:
            self.steps = self.scan_points
            self.show_relative = True
        else:
            self.steps = self.all_steps
            self.show_relative = False

        # Update energy radio silently (no signal -> no double plot)
        self.radio_rel.blockSignals(True)
        self.radio_abs.blockSignals(True)
        self.radio_rel.setChecked(self.show_relative)
        self.radio_abs.setChecked(not self.show_relative)
        self.radio_rel.blockSignals(False)
        self.radio_abs.blockSignals(False)
        # Update log scale enable state
        self.chk_log.setEnabled(self.show_relative)
        if not self.show_relative:
            self.chk_log.setChecked(False)
            self.use_log_scale = False

        self.recalc_energies()
        self.slider.blockSignals(True)
        self.slider.setRange(0, len(self.steps) - 1)
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self.plot_data()
        self.canvas.axes.relim()
        self.canvas.axes.autoscale_view()
        self.canvas.draw()
        self.on_step_changed(0)

    def on_toggle_mode(self):
        """Switch the plotted energy between absolute and relative-to-minimum."""
        self.show_relative = self.radio_rel.isChecked()

        # Disable Log Scale for Absolute Mode (usually negative energies)
        if not self.show_relative:
            self.chk_log.setChecked(False)
            self.chk_log.setEnabled(False)
            self.use_log_scale = False
        else:
            self.chk_log.setEnabled(True)
            self.use_log_scale = self.chk_log.isChecked()

        self.update_display_values()
        self.plot_data()
        self.on_step_changed(self.slider.value())

    def on_log_changed(self):
        """Toggle log-scale plotting of the energy axis and redraw."""
        self.use_log_scale = self.chk_log.isChecked()
        self.plot_data()

    def on_x_axis_mode_changed(self):
        """Switch the plotted X axis between step index and scan coordinate/path distance."""
        # Trigger fires for both the newly-checked and newly-unchecked button;
        # only act on the newly-checked one.
        sender = self.sender()
        if sender and not sender.isChecked():
            return

        self.show_coord_x = self.radio_coord.isChecked()
        # plot_data handles relim / autoscale / draw / highlight_point internally.
        # on_step_changed is still needed to refresh the info label text.
        self.plot_data()
        self.on_step_changed(self.slider.value())

    def on_unit_changed(self, unit):
        """Switch the displayed energy unit and refresh the plot and step label."""
        self.current_unit = unit
        self.update_display_values()
        self.plot_data()
        self.on_step_changed(self.slider.value())

    def update_display_values(self):
        """Recompute the display-unit energies from the raw Hartree values and current unit/mode."""
        factor = 1.0  # default Eh
        # High precision factors (CODATA 2018 / GAMESS consistency)
        if self.current_unit == "kJ/mol":
            factor = 2625.4996395
        elif self.current_unit == "kcal/mol":
            factor = 627.50947406
        elif self.current_unit == "eV":
            factor = 27.211386246

        if self.show_relative:
            self.display_energies = [(e - self.min_e) * factor for e in self.energies]
        else:
            self.display_energies = [e * factor for e in self.energies]

    def plot_data(self):
        """Redraw the whole energy-profile plot from the current steps, axis mode and unit."""
        self.canvas.axes.clear()
        self._highlight_marker = None
        self._highlight_line = None

        if not self.steps or not self.display_energies:
            self.scatter = None
            self.canvas.axes.text(
                0.5,
                0.5,
                "No optimization/scan data found",
                ha="center",
                va="center",
                transform=self.canvas.axes.transAxes,
            )
            self.canvas.axes.set_xlabel("Step")
            self.canvas.axes.set_ylabel("Energy")
            self.canvas.axes.set_title("Energy Profile")
            self.canvas.draw_idle()
            return

        if self.show_coord_x:
            # Try to get scan values.
            # Use explicit None checks so that 0.0 is not skipped.
            x = []
            for i, s in enumerate(self.steps):
                v = s.get("scan_coord", None)
                if v is None:
                    v = s.get("dist", None)
                if v is None:
                    v = float(i)
                x.append(v)

            xlabel = "Scan Coordinate"
        else:
            x = list(range(len(self.steps)))
            xlabel = "Step"

        y = self.display_energies

        if self.show_relative:
            ylabel = f"Relative Energy ({self.current_unit})"
        else:
            ylabel = f"Absolute Energy ({self.current_unit})"

        # Draw Line and Scatter
        if self.use_log_scale:
            epsilon = 1e-7
            plot_y = [max(v, epsilon) for v in y]

            self.canvas.axes.semilogy(x, plot_y, "b-", label="Energy", picker=5)
            self.scatter = self.canvas.axes.scatter(
                x, plot_y, c="red", s=40, picker=5, zorder=5
            )
        else:
            # Force data arrays as lists to ensure matplotlib correctly interprets them
            x_arr = list(x)
            y_arr = list(y)
            self.canvas.axes.plot(x_arr, y_arr, "b-", label="Energy", picker=5)
            self.scatter = self.canvas.axes.scatter(
                x_arr, y_arr, c="red", s=40, picker=5, zorder=5
            )

        self.canvas.axes.set_xlabel(xlabel)
        self.canvas.axes.set_ylabel(ylabel)
        self.canvas.axes.set_title("Energy Profile")
        self.canvas.axes.grid(True, which="both", ls="-", alpha=0.3)

        # Only apply ticklabel_format for non-log scale (it only works with ScalarFormatter)
        if not self.use_log_scale:
            self.canvas.axes.ticklabel_format(useOffset=False, style="plain")

        # Re-add tooltip annotation to cleared axis
        self.annot = self.canvas.axes.annotate(
            "",
            xy=(0, 0),
            xytext=(20, 20),
            textcoords="offset points",
            bbox=dict(boxstyle="round", fc="w", alpha=0.9),
            arrowprops=dict(arrowstyle="->"),
            zorder=1000,
        )
        self.annot.set_visible(False)

        self.highlight_point(self.slider.value())
        self.canvas.fig.tight_layout()
        self.canvas.draw()

    def highlight_point(self, idx):
        """Draw the red marker and vertical guide line at the given step's position on the plot."""
        # Remove old markers
        if getattr(self, "_highlight_marker", None) is not None:
            try:
                self._highlight_marker.remove()
            except (AttributeError, ValueError, NotImplementedError) as e:
                logging.debug(
                    "Trajectory: could not remove the old highlight marker: %s", e
                )
        if getattr(self, "_highlight_line", None) is not None:
            try:
                self._highlight_line.remove()
            except (AttributeError, ValueError, NotImplementedError) as e:
                logging.debug(
                    "Trajectory: could not remove the old highlight line: %s", e
                )

        if not self.display_energies or idx < 0 or idx >= len(self.display_energies):
            return
        if not self.steps or idx >= len(self.steps):
            return

        y = self.display_energies[idx]

        if self.show_coord_x:
            # Explicit None checks so that 0.0 is not treated as missing.
            x_val = self.steps[idx].get("scan_coord", None)
            if x_val is None:
                x_val = self.steps[idx].get("dist", None)
            if x_val is None:
                x_val = float(idx)
        else:
            x_val = idx

        # Red circle
        (self._highlight_marker,) = self.canvas.axes.plot(
            x_val,
            y,
            "ro",
            markersize=12,
            markeredgecolor="black",
            markeredgewidth=2,
            zorder=10,
        )
        # Vertical Line
        self._highlight_line = self.canvas.axes.axvline(
            x=x_val, color="gray", linestyle="--", alpha=0.6
        )

        self.canvas.draw_idle()

    def on_step_changed(self, idx):
        """Update the highlight, info label and 3D structure for a newly selected step index."""
        if getattr(self, "_is_closing", False):
            return
        # Bounds check to prevent IndexError during mode transitions
        if idx < 0 or idx >= len(self.steps) or idx >= len(self.display_energies):
            return

        self.highlight_point(idx)
        step = self.steps[idx]
        val = self.display_energies[idx]
        abs_h = step["energy"]

        coord_info = ""
        cv = step.get("scan_coord", None)
        if cv is None:
            cv = step.get("dist", None)
        if cv is not None:
            coord_info = f" | Coord: {cv:.6f}"

        if self.show_relative:
            self.lbl_info.setText(
                f"Step {idx + 1}/{len(self.steps)}{coord_info} | {val:.8f} {self.current_unit} (Abs: {abs_h:.10f} Eh)"
            )
        else:
            self.lbl_info.setText(
                f"Step {idx + 1}/{len(self.steps)}{coord_info} | {val:.8f} {self.current_unit}"
            )

        # Without atoms there is no structure to update
        if not step.get("atoms", None):
            return

        self.update_structure(step["atoms"], step["coords"])

    def update_structure(self, atoms, coords):
        """Build an RDKit molecule from a step's atoms/coordinates and push it to the 3D view."""
        if not atoms:
            return
        # RDKit build
        if not Chem:
            return
        mol = Chem.RWMol()  # pylint: disable=no-member
        conf = Chem.Conformer()  # pylint: disable=no-member
        try:
            for i, sym in enumerate(atoms):
                rdkit_sym = normalize_atom_symbol(sym)
                mol.AddAtom(Chem.Atom(rdkit_sym))
                conf.SetAtomPosition(
                    i, Point3D(coords[i][0], coords[i][1], coords[i][2])
                )
        except (RuntimeError, AttributeError, IndexError, TypeError, ValueError):
            return

        mol.AddConformer(conf)

        # Determine bonds — skip dummy atoms to avoid RDKit crashes.
        # Skip DetermineBondOrders during animation playback to avoid per-frame
        # latency; connectivity is sufficient while playing.
        determine_bonds_without_dummies(
            mol, charge=self.charge, bond_orders=not self.is_playing
        )

        final_mol = mol.GetMol()

        # Update current molecule in Main Window context (V3 manager architecture)
        if hasattr(self.gl_widget, "current_mol"):
            self.gl_widget.current_mol = final_mol

        if self.context:
            self.context.draw_molecule_3d(final_mol)
        elif hasattr(self.gl_widget, "view_3d_manager"):
            self.gl_widget.view_3d_manager.draw_molecule_3d(final_mol)
        elif hasattr(self.gl_widget, "draw_molecule_3d"):
            self.gl_widget.draw_molecule_3d(final_mol)

    def on_scroll(self, event):
        """Step the slider one frame per mouse-wheel notch over the plot."""
        if event.button == "up":
            self.slider.setValue(max(self.slider.value() - 1, 0))
        elif event.button == "down":
            self.slider.setValue(min(self.slider.value() + 1, len(self.steps) - 1))

    def on_pick(self, event):
        """Jump the slider to the plot point clicked by the user."""
        # Disable pick if current steps have no atoms
        if self.steps and not self.steps[0].get("atoms", None):
            return

        # Only allow left-click
        mouseevent = getattr(event, "mouseevent", None)
        if mouseevent is None or mouseevent.button != 1:
            return

        if event.artist and hasattr(event, "ind"):
            idx = event.ind[0]  # Index of point
            self.slider.setValue(idx)

    def on_hover(self, event):
        """Show or hide the tooltip annotation as the mouse moves over a plotted point."""
        if not self.scatter:
            return
        vis = self.annot.get_visible()
        if event.inaxes == self.canvas.axes:
            cont, ind = self.scatter.contains(event)
            if cont:
                idx = ind["ind"][0]
                if (
                    idx < 0
                    or idx >= len(self.display_energies)
                    or idx >= len(self.steps)
                ):
                    return
                pos = self.scatter.get_offsets()[idx]
                self.annot.xy = pos
                val = self.display_energies[idx]
                abs_h = self.steps[idx]["energy"]
                cv = self.steps[idx].get("scan_coord", None)
                if cv is None:
                    cv = self.steps[idx].get("dist", None)

                coord_txt = f"Coord: {cv:.6f}\n" if cv is not None else ""

                if self.show_relative:
                    text = f"Step {idx}\n{coord_txt}{val:.8f} {self.current_unit}\n(Abs: {abs_h:.10f} Eh)"
                else:
                    text = f"Step {idx}\n{coord_txt}{val:.8f} {self.current_unit}"
                self.annot.set_text(text)
                self.annot.set_visible(True)
                self.canvas.draw_idle()
            else:
                if vis:
                    self.annot.set_visible(False)
                    self.canvas.draw_idle()

    def toggle_play(self):
        """Start or stop step-by-step playback of the trajectory."""
        # Disable play if no atoms
        if self.steps and not self.steps[0].get("atoms", None):
            return

        if self.is_playing:
            self.timer.stop()
            self.btn_play.setText("Play")
            self.is_playing = False
            # Re-render current frame with full bond orders now that playback stopped
            idx = self.slider.value()
            if self.steps and 0 <= idx < len(self.steps):
                step = self.steps[idx]
                if step.get("atoms"):
                    self.update_structure(step["atoms"], step["coords"])
        else:
            # If loop is off and we are at the last frame, start from the beginning
            if (
                not self.chk_loop.isChecked()
                and self.slider.value() >= len(self.steps) - 1
            ):
                self.slider.setValue(0)

            fps = self.spin_fps.value()
            self.timer.start(int(1000 / fps))
            self.btn_play.setText("Pause")
            self.is_playing = True

    def on_fps_changed(self):
        """Apply a changed FPS spin box value to the running playback timer."""
        if self.is_playing:
            fps = self.spin_fps.value()
            self.timer.start(int(1000 / fps))

    def next_frame(self):
        """Advance the slider one step, looping or stopping playback at the end per the Loop checkbox."""
        idx = self.slider.value() + 1
        if idx >= len(self.steps):
            if self.chk_loop.isChecked():
                idx = 0
            else:
                idx = len(self.steps) - 1
                if self.is_playing:
                    self.toggle_play()
        self.slider.setValue(idx)

    def prev_frame(self):
        """Step the slider back one frame, wrapping to the last step if Loop is checked."""
        idx = self.slider.value() - 1
        if idx < 0:
            if self.chk_loop.isChecked():
                idx = len(self.steps) - 1
            else:
                idx = 0
        self.slider.setValue(idx)

    def go_to_first_frame(self):
        """Stop playback and jump the slider to the first step."""
        if self.is_playing:
            self.toggle_play()
        self.slider.setValue(0)

    def go_to_last_frame(self):
        """Stop playback and jump the slider to the last step."""
        if self.is_playing:
            self.toggle_play()
        self.slider.setValue(len(self.steps) - 1)

    def save_graph(self):
        """Prompt for a path and save the energy profile plot as an image."""
        # Hide annotation before saving
        was_visible = self.annot.get_visible()
        self.annot.set_visible(False)
        self.canvas.draw()

        default_path = get_default_export_path(
            self.output_path, suffix="_traj_graph", extension=".png"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Graph", default_path, "Images (*.png *.jpg *.svg)"
        )
        try:
            if path:
                self.canvas.fig.savefig(path, dpi=300)
                notify(self, f"Graph saved to: {os.path.basename(path)}", 5000)
        except (OSError, ValueError, RuntimeError) as exc:
            logging.warning("Trajectory: graph export failed: %s", exc)
            QMessageBox.critical(self, "Export Error", f"Could not save image:\n{exc}")
        finally:
            self.annot.set_visible(was_visible)
            self.canvas.draw()

    def clear_selection(self):
        """Remove the plot's highlight marker/line and clear the step info label."""
        # Remove highlight markers and line
        if getattr(self, "_highlight_marker", None) is not None:
            try:
                self._highlight_marker.remove()
            except (AttributeError, ValueError, NotImplementedError) as e:
                logging.debug(
                    "Trajectory: could not remove the highlight marker on selection clear: %s",
                    e,
                )
            del self._highlight_marker
        if getattr(self, "_highlight_line", None) is not None:
            try:
                self._highlight_line.remove()
            except (AttributeError, ValueError, NotImplementedError) as e:
                logging.debug(
                    "Trajectory: could not remove the highlight line on selection clear: %s",
                    e,
                )
            del self._highlight_line

        self.lbl_info.setText("Selection Cleared")
        self.canvas.draw()

    def save_csv(self):
        """Prompt for a path and export the step/energy/coordinate data as CSV."""
        default_path = get_default_export_path(
            self.output_path, suffix="_traj_data", extension=".csv"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save CSV", default_path, "CSV Files (*.csv)"
        )
        if path:
            try:
                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    has_coord = any(
                        s.get("scan_coord") is not None or s.get("dist") is not None
                        for s in self.steps
                    )
                    header = [
                        "Step",
                        f"Energy_{self.current_unit.replace('/', '_')}",
                        "Mode",
                    ]
                    if has_coord:
                        header.insert(1, "Coordinate")
                    writer.writerow(header)
                    mode = "Relative" if self.show_relative else "Absolute"
                    for i, step in enumerate(self.steps):
                        if i >= len(self.display_energies):
                            break
                        row = [i + 1, self.display_energies[i], mode]
                        if has_coord:
                            cv = step.get("scan_coord")
                            if cv is None:
                                cv = step.get("dist")
                            row.insert(1, cv if cv is not None else "")
                        writer.writerow(row)
                notify(self, f"Data saved to: {os.path.basename(path)}", 5000)
            # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
            except Exception as e:  # pylint: disable=broad-exception-caught
                QMessageBox.critical(self, "Error", str(e))

    # pylint: disable=duplicate-code  # Qt setup mirrors freq_analysis.save_gif; gif_export.py stays PySide6-free
    def save_gif(self):
        """Prompt for GIF settings and export a playback loop of all steps as an animated GIF."""
        if not HAS_PIL:
            QMessageBox.warning(self, "Error", "PIL (Pillow) not installed.")
            return
        if getattr(self, "_gif_saving", False):
            return

        # Settings Dialog
        dialog = QDialog(self)
        dialog.setWindowTitle("GIF Settings")
        form = QFormLayout(dialog)

        spin_fps = QSpinBox()
        spin_fps.setRange(1, 60)
        spin_fps.setValue(self.spin_fps.value())
        form.addRow("FPS:", spin_fps)

        chk_trans = QCheckBox()
        chk_trans.setChecked(True)
        form.addRow("Transparent:", chk_trans)

        chk_hq = QCheckBox()
        chk_hq.setChecked(True)
        form.addRow("High Quality (Adaptive):", chk_hq)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        form.addRow(btns)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        fps = spin_fps.value()
        transparent = chk_trans.isChecked()
        use_hq = chk_hq.isChecked()

        default_path = get_default_export_path(
            self.output_path, suffix="_traj_anim", extension=".gif"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save GIF", default_path, "GIF Files (*.gif)"
        )
        if not path:
            return
        if not path.lower().endswith(".gif"):
            path += ".gif"

        # Stop playback if running
        was_playing = self.is_playing
        if self.is_playing:
            self.toggle_play()

        # Capture logic
        images = []
        original_idx = self.slider.value()

        # Access Plotter from gl_widget?
        # Ideally gl_widget IS the main window or has controller.
        # The constructor passed 'gl_widget' which in show_scan is 'self.mw'.
        mw = self.gl_widget

        if not hasattr(mw, "plotter"):
            QMessageBox.warning(self, "Error", "Cannot access 3D plotter for capture.")
            return

        self._gif_saving = True
        self.btn_save_gif.setEnabled(False)
        try:
            self.setCursor(Qt.CursorShape.WaitCursor)
            for i in range(len(self.steps)):
                self.slider.setValue(i)
                # Force update
                QApplication.processEvents()
                mw.plotter.render()

                img_array = mw.plotter.screenshot(
                    transparent_background=transparent, return_img=True
                )
                if img_array is not None:
                    img = Image.fromarray(img_array)
                    images.append(img)

            # Save GIF
            if images:
                encode_frames_to_gif(images, path, fps, transparent, use_hq)
                notify(self, f"GIF saved to: {os.path.basename(path)}", 5000)

        # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
        except Exception as e:  # pylint: disable=broad-exception-caught
            QMessageBox.critical(self, "Error", f"Failed to save GIF:\n{e}")
        finally:
            self._gif_saving = False
            self.btn_save_gif.setEnabled(True)
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.slider.setValue(original_idx)
            if was_playing:
                self.toggle_play()

    # pylint: enable=duplicate-code

    def reject(self):
        """Route Esc through close() so closeEvent cleanup always runs."""
        # Esc must run closeEvent cleanup (QDialog.reject only hides)
        self.close()

    def closeEvent(self, event):
        """Stop animation, push final structure with full bond orders, then clean up."""
        self._is_closing = True
        if getattr(self, "timer", None) is not None and self.timer.isActive():
            self.timer.stop()
        self.is_playing = False  # ensure bond orders run in update_structure below
        # Re-render current step so main window gets accurate bond types on close
        idx = self.slider.value() if hasattr(self, "slider") else 0
        if self.steps and 0 <= idx < len(self.steps):
            step = self.steps[idx]
            if step.get("atoms"):
                self.update_structure(step["atoms"], step["coords"])
        # accept() not super().closeEvent(): QDialog.closeEvent calls reject(),
        # which is routed back through close() and would recurse.
        event.accept()
