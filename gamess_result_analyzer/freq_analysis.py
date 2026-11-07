"""Vibrational frequency dialog: mode list, 3D vectors, animation and IR/Raman spectrum window."""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QCheckBox,
    QDoubleSpinBox,
    QWidget,
    QRadioButton,
    QFileDialog,
    QFormLayout,
    QDialogButtonBox,
    QSpinBox,
    QMessageBox,
    QApplication,
    QColorDialog,
    QGroupBox,
    QAbstractItemView,
    QTreeWidgetItemIterator,
    QComboBox,
    QSlider,
    QTabWidget,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
import math
import numpy as np
import os
import pyvista as pv
from .spectrum_widget import SpectrumWidget
from .gif_export import Image, HAS_PIL, encode_frames_to_gif
from .utils import get_default_export_path, notify
from .settings import load_section, save_section
import logging

try:
    from rdkit.Geometry import Point3D
except ImportError:
    Point3D = None


class ResetSlider(QSlider):
    """QSlider that resets to 0 when double-clicked."""

    def mouseDoubleClickEvent(self, event):
        """Reset the slider value to 0 on double-click."""
        self.setValue(0)
        if hasattr(super(), "mouseDoubleClickEvent"):
            super().mouseDoubleClickEvent(event)


class FreqSpectrumWindow(QWidget):
    """
    Separate window for displaying the spectrum.
    """

    def __init__(self, parent_dialog, frequencies):
        super().__init__(parent_dialog)
        # We don't verify parent strictly to allow independent testing if needed,
        # but logically it belongs to FrequencyDialog
        self.freq_dialog = parent_dialog
        self.frequencies = frequencies
        self.scaling_a = 1.0
        self.scaling_b = 0.0

        self.setWindowTitle("IR/Raman Spectrum")
        # Ensure it stays on top of the parent window
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Window)
        self.resize(800, 550)

        self.init_ui()
        self.load_settings()

    def load_settings(self):
        """Restore the spectrum's sigma/sticks/markers/auto-range settings from the shared settings file."""
        if not self.freq_dialog:
            return
        settings_file = self.freq_dialog.settings_file
        if os.path.exists(settings_file):
            settings = load_section(settings_file, "freq_settings")
            try:
                if "spec_sigma" in settings:
                    self.spin_sigma.setValue(float(settings["spec_sigma"]))
                if "spec_sticks" in settings:
                    self.chk_sticks.setChecked(bool(settings["spec_sticks"]))
                if "spec_markers" in settings:
                    self.chk_markers.setChecked(bool(settings["spec_markers"]))
                if "spec_auto_x" in settings:
                    self.chk_auto_x.setChecked(bool(settings["spec_auto_x"]))
                if "spec_auto_y" in settings:
                    self.chk_auto_y.setChecked(bool(settings["spec_auto_y"]))
            except (KeyError, TypeError, ValueError) as e:
                logging.warning(
                    "Frequency analysis: could not load spectrum display settings: %s",
                    e,
                )

    def init_ui(self):
        """Build the spectrum widget, range/type controls and PNG/CSV/sticks export buttons."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Spectrum Widget
        self.spectrum = SpectrumWidget(
            self.frequencies,
            x_key="freq",
            y_key="ir",
            x_unit="Frequency (cm-1)",
            y_unit="Intensity (km/mol)",
            sigma=20.0,
        )
        self.spectrum.show_legend = False
        layout.addWidget(self.spectrum)

        # 1. Main Controls (Sigma, Sticks, Inversion, Type)
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setSpacing(15)

        ctrl_layout.addWidget(QLabel("Sigma:"))
        self.spin_sigma = QDoubleSpinBox()
        self.spin_sigma.setRange(1.0, 100.0)
        self.spin_sigma.setValue(20.0)
        self.spin_sigma.setDecimals(1)
        self.spin_sigma.valueChanged.connect(self.spectrum.set_sigma)
        ctrl_layout.addWidget(self.spin_sigma)

        self.chk_sticks = QCheckBox("Sticks")
        self.chk_sticks.setChecked(True)
        self.chk_sticks.stateChanged.connect(self.spectrum.set_sticks)
        ctrl_layout.addWidget(self.chk_sticks)

        self.chk_markers = QCheckBox("Markers")
        self.chk_markers.setChecked(True)
        self.chk_markers.stateChanged.connect(self.spectrum.set_markers)
        ctrl_layout.addWidget(self.chk_markers)

        self.chk_invert_x = QCheckBox("Rev. X")
        self.chk_invert_x.setChecked(True)
        self.chk_invert_x.stateChanged.connect(self.toggle_invert)
        ctrl_layout.addWidget(self.chk_invert_x)

        self.chk_invert_y = QCheckBox("Rev. Y")
        self.chk_invert_y.setChecked(True)
        self.chk_invert_y.stateChanged.connect(self.toggle_invert)
        ctrl_layout.addWidget(self.chk_invert_y)

        ctrl_layout.addStretch()

        # Spectrum Type Group
        self.radio_ir = QRadioButton("IR")
        self.radio_ir.setChecked(True)
        self.radio_ir.toggled.connect(self.switch_spectrum_type)
        ctrl_layout.addWidget(self.radio_ir)

        self.radio_raman = QRadioButton("Raman")
        self.radio_raman.toggled.connect(self.switch_spectrum_type)
        ctrl_layout.addWidget(self.radio_raman)

        layout.addLayout(ctrl_layout)

        # 2. Combined Range Controls (X and Y side-by-side)
        range_layout = QHBoxLayout()
        range_layout.setSpacing(10)

        # X Group
        range_layout.addWidget(QLabel("<b>X Range:</b>"))
        self.chk_auto_x = QCheckBox("Auto")
        self.chk_auto_x.setChecked(False)
        self.chk_auto_x.stateChanged.connect(self.toggle_auto_x)
        range_layout.addWidget(self.chk_auto_x)

        self.spin_x_min = QDoubleSpinBox()
        self.spin_x_min.setRange(0, 10000)
        self.spin_x_min.setDecimals(2)
        self.spin_x_min.setValue(400)
        self.spin_x_min.setSuffix(" cm⁻¹")
        self.spin_x_min.setFixedWidth(110)
        self.spin_x_min.setEnabled(False)
        self.spin_x_min.valueChanged.connect(self.update_x_range)
        range_layout.addWidget(self.spin_x_min)

        range_layout.addWidget(QLabel("-"))

        self.spin_x_max = QDoubleSpinBox()
        self.spin_x_max.setRange(0, 10000)
        self.spin_x_max.setDecimals(2)
        self.spin_x_max.setValue(4000)
        self.spin_x_max.setSuffix(" cm⁻¹")
        self.spin_x_max.setFixedWidth(110)
        self.spin_x_max.setEnabled(False)
        self.spin_x_max.valueChanged.connect(self.update_x_range)
        range_layout.addWidget(self.spin_x_max)

        range_layout.addSpacing(20)
        range_layout.addWidget(QLabel("|"))
        range_layout.addSpacing(20)

        # Y Group
        range_layout.addWidget(QLabel("<b>Y Range:</b>"))
        self.chk_auto_y = QCheckBox("Auto")
        self.chk_auto_y.setChecked(True)
        self.chk_auto_y.stateChanged.connect(self.toggle_auto_y)
        range_layout.addWidget(self.chk_auto_y)

        self.spin_y_min = QDoubleSpinBox()
        self.spin_y_min.setRange(-1e5, 1e5)
        self.spin_y_min.setDecimals(2)
        self.spin_y_min.setValue(0)
        self.spin_y_min.setFixedWidth(90)
        self.spin_y_min.setEnabled(False)
        self.spin_y_min.valueChanged.connect(self.update_range)
        range_layout.addWidget(self.spin_y_min)

        range_layout.addWidget(QLabel("-"))

        self.spin_y_max = QDoubleSpinBox()
        self.spin_y_max.setRange(-1e5, 1e5)
        self.spin_y_max.setDecimals(2)
        self.spin_y_max.setValue(1.0)
        self.spin_y_max.setFixedWidth(90)
        self.spin_y_max.setEnabled(False)
        self.spin_y_max.valueChanged.connect(self.update_range)
        range_layout.addWidget(self.spin_y_max)

        range_layout.addStretch()
        layout.addLayout(range_layout)

        # 3. Action Buttons
        btn_layout = QHBoxLayout()

        btn_png = QPushButton("Save PNG")
        btn_png.clicked.connect(self.save_png)
        btn_layout.addWidget(btn_png)

        btn_csv = QPushButton("Save CSV")
        btn_csv.clicked.connect(self.save_csv)
        btn_layout.addWidget(btn_csv)

        btn_sticks = QPushButton("Export Sticks")
        btn_sticks.clicked.connect(self.save_sticks)
        btn_layout.addWidget(btn_sticks)

        btn_layout.addStretch()

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        btn_layout.addWidget(btn_close)

        layout.addLayout(btn_layout)

        # Connections (connected now that all variables are initialized)
        self.spectrum.clicked.connect(self.on_spectrum_clicked)
        self.spectrum.range_changed.connect(self.on_spectrum_range_changed)

        # Initial Update
        self.toggle_auto_x()  # Initialize state
        self.switch_spectrum_type()

    def on_spectrum_clicked(self, item):
        """Forward a clicked spectrum peak to the parent dialog to select the matching mode."""
        if self.freq_dialog:
            self.freq_dialog.select_mode_by_item(item)

    def set_scaling_params(self, a, b):
        """Store the frequency scaling factors and rebuild the plotted data."""
        self.scaling_a = a
        self.scaling_b = b
        self.update_data()

    def update_data(self):
        """Rebuild the spectrum's plotted data by re-applying the current scaling factors."""
        # Re-calc scaled data
        scaled_data = []
        for i, f in enumerate(self.frequencies):
            d = f.copy()
            if "freq" in d:
                d["freq"] = d["freq"] * self.scaling_a + self.scaling_b
            # Store original index for robust identification
            d["_orig_idx"] = i
            scaled_data.append(d)
        self.spectrum.set_data(scaled_data)

    def toggle_invert(self):
        """Apply the Rev. X / Rev. Y checkbox states to the spectrum widget's axis inversion."""
        self.spectrum.invert_x = self.chk_invert_x.isChecked()
        self.spectrum.invert_y = self.chk_invert_y.isChecked()
        self.spectrum.update()

    def switch_spectrum_type(self):
        """Switch the plotted axis between IR and Raman, resetting axis units and default inversion."""
        # Trigger update data to ensure scaling is applied
        is_raman = self.radio_raman.isChecked()
        if is_raman:
            self.spectrum.y_key = "raman"
            self.spectrum.x_unit = "Raman Shift (cm-1)"
            self.spectrum.y_unit = "Activity (Å⁴/amu)"
            # Default Raman: Reversed X (standard), Normal Y
            self.chk_invert_x.setChecked(True)
            self.chk_invert_y.setChecked(False)
        else:
            self.spectrum.y_key = "ir"
            self.spectrum.x_unit = "Frequency (cm-1)"
            self.spectrum.y_unit = "Intensity (km/mol)"
            # Default IR: Inverted X (High->Low) AND Inverted Y (Transmittance-style)
            self.chk_invert_x.setChecked(True)
            self.chk_invert_y.setChecked(True)

        self.toggle_invert()  # Force update of spectrum widget properties
        self.update_data()  # Will set data and update

    def toggle_auto_y(self):
        """Enable/disable the Y-range spin boxes and apply auto or manual Y range to the spectrum."""
        is_auto = self.chk_auto_y.isChecked()
        self.spin_y_min.setEnabled(not is_auto)
        self.spin_y_max.setEnabled(not is_auto)
        if is_auto:
            self.spectrum.set_auto_range()
        else:
            self.update_range()

    def update_range(self):
        """Apply the manual Y-range spin box values to the spectrum, unless Auto Y is checked."""
        if self.chk_auto_y.isChecked():
            return
        ymin = self.spin_y_min.value()
        ymax = self.spin_y_max.value()
        self.spectrum.set_y_range(ymin, ymax)

    def toggle_auto_x(self):
        """Enable/disable the X-range spin boxes and apply auto or manual X range to the spectrum."""
        is_auto = self.chk_auto_x.isChecked()
        self.spin_x_min.setEnabled(not is_auto)
        self.spin_x_max.setEnabled(not is_auto)

        if is_auto:
            self.spectrum.set_auto_x_range()
        else:
            # If frequency plot, default to standard range on uncheck
            is_freq = (
                "freq" in self.spectrum.x_unit.lower()
                or "cm" in self.spectrum.x_unit.lower()
            )
            if is_freq:
                self.spin_x_min.blockSignals(True)
                self.spin_x_max.blockSignals(True)
                self.spin_x_min.setValue(400)
                self.spin_x_max.setValue(4000)
                self.spin_x_min.blockSignals(False)
                self.spin_x_max.blockSignals(False)
            self.update_x_range()

    def update_x_range(self):
        """Apply the manual X-range spin box values to the spectrum, unless Auto X is checked."""
        if self.chk_auto_x.isChecked():
            return
        xmin = self.spin_x_min.value()
        xmax = self.spin_x_max.value()
        self.spectrum.set_x_range(xmin, xmax)

    def on_spectrum_range_changed(self, xmin, xmax, ymin, ymax, is_manual):
        """Sync the range spin boxes to a zoom/pan on the spectrum, unchecking Auto on manual interaction."""
        # Update spin boxes to match zoom
        self.spin_x_min.blockSignals(True)
        self.spin_x_max.blockSignals(True)
        self.spin_y_min.blockSignals(True)
        self.spin_y_max.blockSignals(True)

        self.spin_x_min.setValue(xmin)
        self.spin_x_max.setValue(xmax)
        self.spin_y_min.setValue(ymin)
        self.spin_y_max.setValue(ymax)

        if is_manual:
            # Only uncheck Auto if it was a manual zoom/pan interaction
            self.chk_auto_x.blockSignals(True)
            self.chk_auto_y.blockSignals(True)
            self.chk_auto_x.setChecked(False)
            self.chk_auto_y.setChecked(False)
            self.chk_auto_x.blockSignals(False)
            self.chk_auto_y.blockSignals(False)

            self.spin_x_min.setEnabled(True)
            self.spin_x_max.setEnabled(True)
            self.spin_y_min.setEnabled(True)
            self.spin_y_max.setEnabled(True)

        self.spin_x_min.blockSignals(False)
        self.spin_x_max.blockSignals(False)
        self.spin_y_min.blockSignals(False)
        self.spin_y_max.blockSignals(False)

    def save_png(self):
        """Prompt for a path and save the spectrum plot as a PNG image."""
        default_path = get_default_export_path(
            self.freq_dialog.mw.init_manager.current_file_path,
            suffix="_vib_spectrum",
            extension=".png",
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Graph", default_path, "Images (*.png)"
        )
        if path:
            try:
                self.spectrum.save_png(path)
            except (OSError, ValueError, RuntimeError) as exc:
                logging.warning("Frequency spectrum: image export failed: %s", exc)
                QMessageBox.critical(
                    self, "Export Error", f"Could not save image:\n{exc}"
                )

    def save_csv(self):
        """Prompt for a path and save the plotted spectrum curve as CSV."""
        default_path = get_default_export_path(
            self.freq_dialog.mw.init_manager.current_file_path,
            suffix="_vib_data",
            extension=".csv",
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Data", default_path, "CSV Files (*.csv)"
        )
        if path:
            success = self.spectrum.save_csv(path)
            if success:
                notify(self, f"Data saved to: {os.path.basename(path)}", 5000)
            else:
                QMessageBox.warning(self, "Error", "Failed to save CSV.")

    def save_sticks(self):
        """Prompt for a path and export the raw (unbroadened) stick data as CSV."""
        default_path = get_default_export_path(
            self.freq_dialog.mw.init_manager.current_file_path,
            suffix="_vib_sticks",
            extension=".csv",
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Sticks", default_path, "CSV Files (*.csv)"
        )
        if path:
            success = self.spectrum.save_sticks_csv(path)
            if success:
                notify(self, f"Stick data saved to: {os.path.basename(path)}", 5000)
            else:
                QMessageBox.warning(self, "Error", "Failed to export stick data.")

    def closeEvent(self, event):
        """Close the spectrum window (state is kept by the parent FrequencyDialog for reuse)."""
        # No cleanup of its own: the parent FrequencyDialog keeps the
        # reference and reuses this window, checking visibility to decide
        # whether to re-show it.
        super().closeEvent(event)


class FrequencyDialog(QDialog):
    """Vibrational frequency dialog: mode list, scaling, 3D vectors and animated displacement."""

    # pylint: disable=attribute-defined-outside-init
    # Qt pattern: widget attributes are set in init_ui(), called from __init__.

    def __init__(self, parent, frequencies, atoms, coords, context=None):
        super().__init__(parent)
        self.mw = parent
        self.context = context
        self.frequencies = frequencies  # List of dicts
        self.atoms = atoms
        self.base_coords = coords

        self.is_playing = False
        self.timer = QTimer()
        self.timer.timeout.connect(self.animate_frame)
        self.animation_step = 0
        self.current_mode_idx = -1
        self.vector_color = "orange"
        self.vector_res = 20

        # Scaling Params (ax + b)
        self.scaling_a = 1.0
        self.scaling_b = 0.0
        self.default_presets = {
            "Unscaled": {"a": 1.0, "b": 0.0},
            "B3LYP/6-31G*": {"a": 0.9614, "b": 0.0},
        }
        self.custom_presets = {}  # Only user-added ones

        self.spectrum_win = None

        self.setWindowTitle("Vibrational Frequencies")
        self.resize(570, 700)

        self.settings_file = os.path.join(os.path.dirname(__file__), "settings.json")
        self.init_ui()

    def init_ui(self):
        """Build the mode list, scaling controls, vector appearance tabs and animation controls."""
        main_layout = QVBoxLayout(self)

        # 1. Frequency List Section
        list_group = QGroupBox("Frequency Modes")
        list_layout = QVBoxLayout(list_group)

        # Preset row
        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel("Preset:"))
        self.combo_preset = QComboBox()
        self.update_preset_combo()
        self.combo_preset.activated.connect(self.apply_preset)
        preset_layout.addWidget(self.combo_preset)

        btn_save_preset = QPushButton("Save...")
        btn_save_preset.setFixedWidth(50)
        btn_save_preset.clicked.connect(self.save_custom_preset)
        preset_layout.addWidget(btn_save_preset)
        preset_layout.addStretch()
        list_layout.addLayout(preset_layout)

        # Scaling Params row (ax + b)
        sf_layout = QHBoxLayout()
        sf_layout.addWidget(QLabel("Scaling (<i>ax</i> + <i>b</i>): <i>a</i>"))
        self.spin_sf_a = QDoubleSpinBox()
        self.spin_sf_a.setRange(-2.0, 2.0)
        self.spin_sf_a.setSingleStep(0.001)
        self.spin_sf_a.setDecimals(4)
        self.spin_sf_a.setValue(1.0)
        self.spin_sf_a.valueChanged.connect(self.update_data)
        sf_layout.addWidget(self.spin_sf_a)

        sf_layout.addWidget(QLabel(" <i>b</i>"))
        self.spin_sf_b = QDoubleSpinBox()
        self.spin_sf_b.setRange(-1000, 1000)
        self.spin_sf_b.setSingleStep(0.1)
        self.spin_sf_b.setDecimals(2)
        self.spin_sf_b.setValue(0.0)
        self.spin_sf_b.valueChanged.connect(self.update_data)
        sf_layout.addWidget(self.spin_sf_b)

        sf_layout.addStretch()
        list_layout.addLayout(sf_layout)

        # List
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(
            ["Mode", "Freq (cm⁻¹)", "Unscaled (cm⁻¹)", "IR (km/mol)", "Raman (Å⁴/amu)"]
        )
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.currentItemChanged.connect(self.on_mode_selected)
        list_layout.addWidget(self.tree)

        # Spectrum Button
        btn_spectrum = QPushButton("Open IR/Raman Spectrum...")
        btn_spectrum.clicked.connect(self.open_spectrum)
        list_layout.addWidget(btn_spectrum)

        main_layout.addWidget(list_group)

        # 2. 3D Appearance (Vectors)
        # Two independent arrow overlays: the per-atom displacements of the
        # mode, and the single molecule-wide dipole derivative. They are drawn
        # at very different scales and are usually wanted one at a time, so
        # each keeps its own scale/colour rather than sharing one set.
        vec_group = QGroupBox("3D Vector Appearance")
        vec_group_layout = QVBoxLayout(vec_group)
        self.vec_tabs = QTabWidget()
        vec_group_layout.addWidget(self.vec_tabs)

        mode_tab = QWidget()
        vec_layout = QVBoxLayout(mode_tab)

        vec_top_row = QHBoxLayout()
        self.chk_vector = QCheckBox("Show Vectors")
        self.chk_vector.setChecked(False)
        self.chk_vector.stateChanged.connect(self.update_view)
        vec_top_row.addWidget(self.chk_vector)

        vec_top_row.addWidget(QLabel("Scale:"))
        self.spin_vec_scale = QDoubleSpinBox()
        self.spin_vec_scale.setRange(0.1, 50.0)
        self.spin_vec_scale.setValue(2.0)
        self.spin_vec_scale.valueChanged.connect(self.update_view)
        vec_top_row.addWidget(self.spin_vec_scale)
        vec_layout.addLayout(vec_top_row)

        vec_bot_row = QHBoxLayout()
        vec_bot_row.addWidget(QLabel("Resolution:"))
        self.spin_vec_res = QSpinBox()
        self.spin_vec_res.setRange(3, 100)
        self.spin_vec_res.setValue(20)
        self.spin_vec_res.valueChanged.connect(self.on_res_changed)
        vec_bot_row.addWidget(self.spin_vec_res)

        vec_bot_row.addWidget(QLabel(" Color:"))
        self.btn_vec_color = QPushButton()
        self.btn_vec_color.setFixedWidth(60)
        self.btn_vec_color.setStyleSheet(
            f"background-color: {self.vector_color}; border: 1px solid gray; height: 20px;"
        )
        self.btn_vec_color.clicked.connect(self.pick_color)
        vec_bot_row.addWidget(self.btn_vec_color)

        vec_bot_row.addWidget(QLabel(" Alpha:"))
        self.spin_vec_alpha = QDoubleSpinBox()
        self.spin_vec_alpha.setRange(0.05, 1.0)
        self.spin_vec_alpha.setSingleStep(0.05)
        self.spin_vec_alpha.setValue(1.0)
        self.spin_vec_alpha.setToolTip(
            "Arrow opacity. Below 1.0 the structure stays visible through the "
            "arrows, which helps when they cover the molecule."
        )
        self.spin_vec_alpha.valueChanged.connect(self.update_view)
        vec_bot_row.addWidget(self.spin_vec_alpha)
        vec_bot_row.addStretch()
        vec_layout.addLayout(vec_bot_row)
        vec_layout.addStretch()
        self.vec_tabs.addTab(mode_tab, "Mode Vectors")

        main_layout.addWidget(vec_group)

        # 3. Animation Section
        anim_group = QGroupBox("Animation Parameters")
        anim_layout = QVBoxLayout(anim_group)

        anim_row1 = QHBoxLayout()
        anim_row1.addWidget(QLabel("Displacement Scale:"))
        self.spin_amp = QDoubleSpinBox()
        self.spin_amp.setRange(0.1, 10.0)
        self.spin_amp.setSingleStep(0.1)
        self.spin_amp.setValue(1.0)
        self.spin_amp.valueChanged.connect(self.on_amp_changed)
        anim_row1.addWidget(self.spin_amp)

        anim_row1.addWidget(QLabel(" | FPS:"))
        self.spin_fps = QSpinBox()
        self.spin_fps.setRange(1, 120)
        self.spin_fps.setValue(30)
        self.spin_fps.valueChanged.connect(self.update_fps)
        anim_row1.addWidget(self.spin_fps)
        anim_layout.addLayout(anim_row1)

        # Manual Displacement Row
        manual_row = QHBoxLayout()
        self.chk_manual_displ = QCheckBox("Manual Displacement")
        self.chk_manual_displ.setChecked(False)
        self.chk_manual_displ.toggled.connect(self.toggle_manual_displacement)
        manual_row.addWidget(self.chk_manual_displ)

        self.slider_displ = ResetSlider(Qt.Orientation.Horizontal)
        self.slider_displ.setRange(-100, 100)
        self.slider_displ.setValue(0)
        self.slider_displ.setEnabled(False)
        self.slider_displ.valueChanged.connect(self.on_displacement_slider_changed)
        manual_row.addWidget(self.slider_displ)

        self.lbl_displ_val = QLabel("+0.00")
        self.lbl_displ_val.setFixedWidth(50)
        self.lbl_displ_val.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        manual_row.addWidget(self.lbl_displ_val)
        anim_layout.addLayout(manual_row)

        # Playback row
        action_row = QHBoxLayout()
        self.btn_play = QPushButton("Play")
        self.btn_play.clicked.connect(self.start_animation)
        action_row.addWidget(self.btn_play)

        self.btn_pause = QPushButton("Pause")
        self.btn_pause.clicked.connect(self.pause_animation)
        self.btn_pause.setEnabled(False)
        action_row.addWidget(self.btn_pause)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(self.stop_animation)
        self.btn_stop.setEnabled(False)
        action_row.addWidget(self.btn_stop)

        action_row.addStretch()

        self.btn_gif = QPushButton("GIF")
        self.btn_gif.clicked.connect(self.save_gif)
        self.btn_gif.setEnabled(HAS_PIL)
        action_row.addWidget(self.btn_gif)

        anim_layout.addLayout(action_row)
        main_layout.addWidget(anim_group)

        # Close
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close_clean)
        main_layout.addWidget(btn_close)

        self.vector_actor = None
        self.populate_list()

        self.load_settings()

    def populate_list(self):
        """Fill the mode tree, hiding leading translation/rotation modes and colouring imaginary ones red."""
        self.tree.clear()
        a = self.spin_sf_a.value()
        b = self.spin_sf_b.value()

        # Determine if we should hide first 6 (translation/rotation)
        # Usually they are 0.00 or very small imaginary.
        # Let's check the first few.
        start_idx = 0
        if len(self.frequencies) > 6:
            # Check if first 6 are "small" (e.g. < 20 cm-1) - Non-Linear
            is_trivial_6 = True
            for k in range(6):
                if abs(self.frequencies[k]["freq"]) > 20.0:
                    is_trivial_6 = False
                    break

            if is_trivial_6:
                start_idx = 6
            else:
                # Check if first 5 are "small" - Linear
                is_trivial_5 = True
                for k in range(5):
                    if abs(self.frequencies[k]["freq"]) > 20.0:
                        is_trivial_5 = False
                        break
                if is_trivial_5:
                    start_idx = 5

        _imaginary_color = QColor("#cc0000")  # red for imaginary modes

        for i, f in enumerate(self.frequencies):
            if i < start_idx:
                continue

            raw_freq = f["freq"]  # unscaled — used to decide imaginary
            freq_val = raw_freq * a + b  # scaled — shown in the column
            # Columns: Mode | Scaled | Unscaled | IR | Raman
            item = QTreeWidgetItem(
                [
                    str(i),
                    f"{freq_val:.2f}",
                    f"{raw_freq:.2f}",
                    f"{f.get('ir', 0.0):.2f}",
                    f"{f.get('raman', 0.0):.2f}",
                ]
            )
            # Colour imaginary modes red based on the unscaled frequency
            if raw_freq < 0:
                for col in range(5):
                    item.setForeground(col, _imaginary_color)
            self.tree.addTopLevelItem(item)

    def update_preset_combo(self):
        """Rebuild the preset combo box from the default and custom presets, preserving the current selection."""
        self.combo_preset.blockSignals(True)
        current = self.combo_preset.currentText()
        self.combo_preset.clear()

        # 1. Unscaled at top
        if "Unscaled" in self.default_presets:
            self.combo_preset.addItem("Unscaled")

        # 2. Manual
        self.combo_preset.addItem("Manual")

        # 3. Other defaults (except Unscaled if already added)
        for name in sorted(self.default_presets.keys()):
            if name != "Unscaled":
                self.combo_preset.addItem(name)

        # 4. Customs
        for name in sorted(self.custom_presets.keys()):
            self.combo_preset.addItem(name)

        if current in [
            self.combo_preset.itemText(i) for i in range(self.combo_preset.count())
        ]:
            self.combo_preset.setCurrentText(current)
        else:
            # Default to Unscaled if current is invalid
            self.combo_preset.setCurrentText("Unscaled")

        self.combo_preset.blockSignals(False)

    def save_custom_preset(self):
        """Prompt for a name and save the current scaling factors as a new custom preset."""
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "Save Scaling Preset", "Preset Name:")
        if ok and name:
            if name in self.default_presets:
                QMessageBox.warning(self, "Error", "Cannot overwrite default preset.")
                return
            self.custom_presets[name] = {
                "a": self.spin_sf_a.value(),
                "b": self.spin_sf_b.value(),
            }
            self.update_preset_combo()
            self.combo_preset.setCurrentText(name)
            self.save_settings()

    def apply_preset(self, _index=None):
        """Apply the selected scaling preset's a/b values, or enable manual editing for "Manual"."""
        # _index absorbs QComboBox.activated(int); the preset is read from the combo.
        preset = self.combo_preset.currentText()
        is_manual = preset == "Manual"
        self.spin_sf_a.setEnabled(is_manual)
        self.spin_sf_b.setEnabled(is_manual)

        if is_manual:
            return

        p = self.default_presets.get(preset, None) or self.custom_presets.get(
            preset, None
        )
        if p:
            self.spin_sf_a.blockSignals(True)
            self.spin_sf_b.blockSignals(True)
            self.spin_sf_a.setValue(p["a"])
            self.spin_sf_b.setValue(p["b"])
            self.spin_sf_a.blockSignals(False)
            self.spin_sf_b.blockSignals(False)
            self.update_data()

    def update_data(self):
        """Re-apply the current scaling factors to the mode list and the open spectrum window."""
        # Update list values (scaling); col 2 (Unscaled) is static — never touched here
        a = self.spin_sf_a.value()
        b = self.spin_sf_b.value()
        _imaginary_color = QColor("#cc0000")
        _default_color = QColor()  # invalid = palette default
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            idx = int(item.text(0))
            raw_freq = self.frequencies[idx]["freq"]  # unscaled
            item.setText(1, f"{raw_freq * a + b:.2f}")
            # Re-apply (or remove) imaginary colouring using unscaled value
            color = _imaginary_color if raw_freq < 0 else _default_color
            for col in range(5):
                item.setForeground(col, color)

        # Update spectrum window if it's open
        if self.spectrum_win is not None:
            self.spectrum_win.set_scaling_params(a, b)

    def open_spectrum(self):
        """Open the IR/Raman spectrum window, creating it on first use, and bring it to the front."""
        if self.spectrum_win is None:
            self.spectrum_win = FreqSpectrumWindow(self, self.frequencies)
            # Apply current scaling params
            self.spectrum_win.set_scaling_params(
                self.spin_sf_a.value(), self.spin_sf_b.value()
            )
            self.spectrum_win.show()
        else:
            self.spectrum_win.show()
            self.spectrum_win.activateWindow()
            self.spectrum_win.raise_()

    def select_mode_by_item(self, item):
        """Select the mode tree row matching a spectrum peak's original index, or clear the selection."""
        if item is None:
            if self.spectrum_win:
                self.spectrum_win.spectrum.set_selected_item(None)
            self.tree.clearSelection()
            return

        # Find corresponding tree item using original index
        target_idx = item.get("_orig_idx", -1)

        if target_idx < 0:
            return

        # Update highlight in spectrum window if exists
        if self.spectrum_win:
            self.spectrum_win.spectrum.set_selected_item(item)

        # Iterate tree to find the item with this index in column 0
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            tree_item = it.value()
            try:
                if int(tree_item.text(0)) == target_idx:
                    self.tree.setCurrentItem(tree_item)
                    self.tree.scrollToItem(
                        tree_item, QAbstractItemView.ScrollHint.PositionAtCenter
                    )
                    break
            except (RuntimeError, AttributeError, TypeError, ValueError) as e:
                logging.debug(
                    "Frequency analysis: could not match tree item to mode index %d: %s",
                    target_idx,
                    e,
                )
            it += 1

    def on_mode_selected(self, current, previous):  # pylint: disable=unused-argument
        # previous is unused: Qt's currentItemChanged signature, only the new item is needed.
        """Handle a mode tree selection change: sync the spectrum highlight and redraw the 3D view."""
        if not current:
            return
        idx = int(current.text(0))

        # Update spectrum highlight if window is open
        if self.spectrum_win:
            # Find the item dict
            # In FreqSpectrumWindow we store them in self.frequencies
            # But FreqSpectrumWindow uses its own copy with _orig_idx
            # Actually it uses parent's self.frequencies.
            # Wait, update_data creates new copies? No, it creates new scaled_data list.
            # Let's find the item by index in scaled_data.

            # SpectrumWidget's data_list is what we need to search.
            for item in self.spectrum_win.spectrum.data_list:
                if item.get("_orig_idx", None) == idx:
                    self.spectrum_win.spectrum.set_selected_item(item)
                    break

        was_playing = self.is_playing

        self.current_mode_idx = idx
        self.stop_animation()
        self.update_view()

        if was_playing:
            self.start_animation()

    def update_view(self):
        """Redraw the selected mode's displacement vectors and dipole arrow at the resting geometry."""
        if self.current_mode_idx < 0:
            return

        # 1. Clear old vectors
        if self.vector_actor:
            try:
                self.mw.plotter.remove_actor(self.vector_actor)
            except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                logging.debug(
                    "Frequency analysis: could not remove the old displacement vector actor: %s",
                    e,
                )
            self.vector_actor = None

        # 2. Reset geometry
        self.reset_geometry()

        # Drawn before the manual-displacement early return: the dipole arrow
        # describes the mode, not the current frame, so it stays valid there.

        if self.chk_manual_displ.isChecked():
            self.apply_manual_displacement()
            return

        if not self.chk_vector.isChecked():
            return

        # 3. Draw Vectors
        # frequency dict has 'vector': list of (dx, dy, dz)
        vecs = self.frequencies[self.current_mode_idx].get("vector", [])
        if not vecs:
            return

        try:
            points = np.array(self.base_coords)
            vectors = np.array(vecs)

            scale = self.spin_vec_scale.value()

            # Use add_mesh with glyphs to ensure resolution and custom appearance are respected
            poly = pv.PolyData(points)
            poly.point_data["vectors"] = vectors
            geom = pv.Arrow(
                shaft_resolution=self.vector_res, tip_resolution=self.vector_res
            )
            arrows = poly.glyph(orient=True, scale=True, factor=scale, geom=geom)
            self.vector_actor = self.mw.plotter.add_mesh(
                arrows,
                color=self.vector_color,
                opacity=self.spin_vec_alpha.value(),
                name="vib_vectors",
            )

            self.mw.plotter.render()
        # C++ library boundary: RDKit/VTK/pyvista exceptions do not map to Python types
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.warning("Error in FrequencyDialog.update_view: %s", e)

    def toggle_manual_displacement(self, checked):
        """Switch between animation playback and the manual displacement slider."""
        self.slider_displ.setEnabled(checked)
        # Disable/enable play buttons
        self.btn_play.setEnabled(not checked)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.btn_gif.setEnabled(HAS_PIL and not checked)

        if checked:
            if self.is_playing:
                self.stop_animation()
            self.apply_manual_displacement()
        else:
            self.slider_displ.setValue(0)
            self.reset_geometry()
            if self.chk_vector.isChecked():
                self.update_view()  # Restore vectors

    def on_displacement_slider_changed(self):
        """Re-apply the manual displacement when the slider moves."""
        self.apply_manual_displacement()

    def on_amp_changed(self):
        """Re-apply the manual displacement when the amplitude spin box changes."""
        if self.chk_manual_displ.isChecked():
            self.apply_manual_displacement()

    def apply_manual_displacement(self):
        """Move the 3D structure to the slider's fixed displacement along the selected mode."""
        if self.current_mode_idx < 0:
            return

        amp = self.spin_amp.value()
        val = self.slider_displ.value() / 100.0
        factor = val * amp

        self.lbl_displ_val.setText(f"{factor:+.2f}")

        vecs = self.frequencies[self.current_mode_idx].get("vector", [])
        if not vecs:
            return

        try:
            mol = self.mw.current_mol
            conf = mol.GetConformer()
            for i, (bx, by, bz) in enumerate(self.base_coords):
                vx, vy, vz = vecs[i]
                nx = bx + vx * factor
                ny = by + vy * factor
                nz = bz + vz * factor
                conf.SetAtomPosition(i, Point3D(nx, ny, nz))

            if self.context:
                self.context.draw_molecule_3d(mol)
            else:
                self.mw.view_3d_manager.draw_molecule_3d(mol)

            # Update vectors if enabled
            if self.chk_vector.isChecked():
                self.update_vectors_at_displaced_position()
            else:
                # Remove vectors if disabled
                if self.vector_actor:
                    try:
                        self.mw.plotter.remove_actor(self.vector_actor)
                    except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                        logging.debug(
                            "Frequency analysis: could not remove the vector actor when vectors were disabled: %s",
                            e,
                        )
                    self.vector_actor = None

        # C++ library boundary: RDKit/VTK/pyvista exceptions do not map to Python types
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.warning("Error in apply_manual_displacement: %s", e)

    def start_animation(self):
        """Start the mode's displacement animation timer at the current FPS."""
        if self.current_mode_idx < 0:
            return
        if self.is_playing:
            return

        self.is_playing = True
        self.btn_play.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)

        fps = self.spin_fps.value()
        self.timer.start(int(1000 / fps))

    def pause_animation(self):
        """Stop the animation timer but leave the structure at its current displaced frame."""
        self.is_playing = False
        self.timer.stop()
        self.btn_play.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(True)  # Can still stop to reset

    def stop_animation(self):
        """Stop the animation timer and reset the structure to its resting geometry."""
        self.is_playing = False
        self.timer.stop()
        self.animation_step = 0  # Reset phase
        self.reset_geometry()
        if self.chk_vector.isChecked():
            self.update_view()  # Restore vectors

        self.btn_play.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)

    def update_fps(self):
        """Apply a changed FPS spin box value to the running animation timer."""
        if self.is_playing:
            self.timer.setInterval(int(1000 / self.spin_fps.value()))

    def animate_frame(self):
        """Advance the animation by one timer tick and redraw the structure at the new sinusoidal phase."""
        if self.current_mode_idx < 0:
            return

        self.animation_step += 1
        phase = self.animation_step * 0.2

        amp = self.spin_amp.value()
        factor = math.sin(phase) * amp

        vecs = self.frequencies[self.current_mode_idx].get("vector", [])
        if not vecs:
            return

        try:
            mol = self.mw.current_mol
            conf = mol.GetConformer()
            for i, (bx, by, bz) in enumerate(self.base_coords):
                vx, vy, vz = vecs[i]
                nx = bx + vx * factor
                ny = by + vy * factor
                nz = bz + vz * factor
                conf.SetAtomPosition(i, Point3D(nx, ny, nz))

            if self.context:
                self.context.draw_molecule_3d(mol)
            else:
                self.mw.view_3d_manager.draw_molecule_3d(mol)

            # Update vectors if enabled
            if self.chk_vector.isChecked():
                self.update_vectors_at_displaced_position()

        # C++ library boundary: RDKit/VTK/pyvista exceptions do not map to Python types
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.warning("Error in animate_frame: %s", e)

    def update_vectors_at_displaced_position(self):
        """Redraw vectors at current displaced atomic positions"""
        if self.vector_actor:
            try:
                self.mw.plotter.remove_actor(self.vector_actor)
            except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                logging.debug(
                    "Frequency analysis: could not remove the vector actor before redraw: %s",
                    e,
                )
            self.vector_actor = None

        vecs = self.frequencies[self.current_mode_idx].get("vector", [])
        if not vecs or not hasattr(self.mw, "current_mol"):
            return

        try:
            mol = self.mw.current_mol
            conf = mol.GetConformer()

            # Get current (displaced) coordinates
            coords = []
            for i in range(conf.GetNumAtoms()):
                pos = conf.GetAtomPosition(i)
                coords.append([pos.x, pos.y, pos.z])

            coords_array = np.array(coords)
            vecs_array = np.array(vecs)
            scale = self.spin_vec_scale.value()

            # Use add_arrows if available, otherwise fall back to glyph method
            if hasattr(self.mw.plotter, "add_arrows"):
                self.vector_actor = self.mw.plotter.add_arrows(
                    coords_array,
                    vecs_array,
                    mag=scale,
                    color=self.vector_color,
                    opacity=self.spin_vec_alpha.value(),
                    show_scalar_bar=False,
                )
            else:
                # Fallback to glyph method
                poly = pv.PolyData(coords_array)
                poly.point_data["vectors"] = vecs_array
                geom = pv.Arrow(
                    shaft_resolution=self.vector_res, tip_resolution=self.vector_res
                )
                arrows = poly.glyph(orient=True, scale=True, factor=scale, geom=geom)
                self.vector_actor = self.mw.plotter.add_mesh(
                    arrows,
                    color=self.vector_color,
                    opacity=self.spin_vec_alpha.value(),
                    name="vib_vectors",
                )
        # C++ library boundary: RDKit/VTK/pyvista exceptions do not map to Python types
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.warning("Error updating vectors: %s", e)

    def reset_geometry(self):
        """Restore the 3D structure to the original (unscaled/undisplaced) equilibrium geometry."""
        try:
            mol = self.mw.current_mol
            conf = mol.GetConformer()
            for i, (bx, by, bz) in enumerate(self.base_coords):
                conf.SetAtomPosition(i, Point3D(bx, by, bz))
            if self.context:
                self.context.draw_molecule_3d(mol)
            else:
                self.mw.view_3d_manager.draw_molecule_3d(mol)
        except (RuntimeError, AttributeError, TypeError, ValueError) as e:
            logging.warning("Error in reset_geometry: %s", e)

    # pylint: disable=duplicate-code  # Qt setup mirrors traj_analysis.save_gif; gif_export.py stays PySide6-free
    def save_gif(self):
        """Prompt for GIF settings and export one animation cycle of the selected mode as an animated GIF."""
        if not HAS_PIL:
            QMessageBox.warning(self, "Error", "PIL (Pillow) not installed.")
            return

        if getattr(self, "_gif_saving", False):
            return

        if self.current_mode_idx < 0:
            QMessageBox.warning(
                self, "Select Mode", "Please select a frequency mode first."
            )
            return

        # Settings
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
            self.mw.init_manager.current_file_path, suffix="_vib_anim", extension=".gif"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save GIF", default_path, "GIF Files (*.gif)"
        )
        if not path:
            return
        if not path.lower().endswith(".gif"):
            path += ".gif"

        # Stop playback and reset
        was_playing = self.is_playing
        if self.is_playing:
            self.stop_animation()
        else:
            self.reset_geometry()

        images = []
        mw = self.mw

        if not hasattr(mw, "plotter"):
            QMessageBox.warning(self, "Error", "Cannot access 3D plotter.")
            return

        self._gif_saving = True
        self.btn_gif.setEnabled(False)
        try:
            self.setCursor(Qt.CursorShape.WaitCursor)

            # Generate 20 frames (1 cycle)
            vecs = self.frequencies[self.current_mode_idx].get("vector", [])
            if not vecs:
                raise ValueError("No vectors for this mode")

            from rdkit.Geometry import Point3D  # pylint: disable=no-name-in-module

            mol = self.mw.current_mol
            conf = mol.GetConformer()

            for i in range(20):
                cycle_pos = i / 20.0
                phase = cycle_pos * 2 * np.pi

                amp = self.spin_amp.value()
                factor = math.sin(phase) * amp

                for j, (bx, by, bz) in enumerate(self.base_coords):
                    vx, vy, vz = vecs[j]
                    nx = bx + vx * factor
                    ny = by + vy * factor
                    nz = bz + vz * factor
                    conf.SetAtomPosition(j, Point3D(nx, ny, nz))

                if self.context:
                    self.context.draw_molecule_3d(mol)
                elif hasattr(mw, "view_3d_manager"):
                    mw.view_3d_manager.draw_molecule_3d(mol)
                QApplication.processEvents()
                mw.plotter.render()

                img_array = mw.plotter.screenshot(
                    transparent_background=transparent, return_img=True
                )
                if img_array is not None:
                    img = Image.fromarray(img_array)
                    images.append(img)

            if images:
                encode_frames_to_gif(images, path, fps, transparent, use_hq)
                notify(self, f"GIF saved to: {os.path.basename(path)}", 5000)

        # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
        except Exception as e:  # pylint: disable=broad-exception-caught
            QMessageBox.critical(self, "Error", f"Failed to save GIF:\n{e}")
        finally:
            self._gif_saving = False
            # Manual displacement mode keeps GIF disabled (toggle_manual_displacement).
            self.btn_gif.setEnabled(HAS_PIL and not self.chk_manual_displ.isChecked())
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.reset_geometry()
            if was_playing:
                self.start_animation()

    # pylint: enable=duplicate-code

    def pick_color(self):
        """Prompt for a colour and apply it to the mode displacement vectors."""
        color = QColorDialog.getColor(
            QColor(self.vector_color), self, "Select Vector Color"
        )
        if color.isValid():
            self.vector_color = color.name()
            self.btn_vec_color.setStyleSheet(f"background-color: {self.vector_color};")
            self.update_view()

    def on_res_changed(self, val):
        """Apply a changed arrow resolution spin box value and redraw the vectors."""
        self.vector_res = val
        self.update_view()

    def reject(self):
        """Route Esc through close() so closeEvent cleanup always runs."""
        # Esc must run closeEvent cleanup (QDialog.reject only hides)
        self.close()

    def closeEvent(self, event):
        """Stop animation, remove 3D actors, close the spectrum window and persist settings."""
        if self.chk_manual_displ.isChecked():
            self.is_playing = False
            self.timer.stop()
            self.animation_step = 0
        else:
            self.stop_animation()

        if self.vector_actor:
            try:
                self.mw.plotter.remove_actor(self.vector_actor)
            except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                logging.debug(
                    "Frequency analysis: could not remove the vector actor on close: %s",
                    e,
                )
        # The dipole arrow is a separate actor and outlives the dialog if it
        # is not removed here -- it would stay in the 3D view after closing.
        if self.spectrum_win:
            self.spectrum_win.close()

        self.save_settings()
        event.accept()

    def close_clean(self):
        """Close the dialog via the Close button, routing through closeEvent cleanup."""
        self.close()

    def load_settings(self):
        """Restore scaling, presets and vector/animation appearance from the shared settings file."""
        if os.path.exists(self.settings_file):
            settings = load_section(self.settings_file, "freq_settings")
            try:
                if "sf_a" in settings:
                    self.spin_sf_a.setValue(float(settings["sf_a"]))
                elif "sf" in settings:
                    self.spin_sf_a.setValue(float(settings["sf"]))  # backward compat

                if "sf_b" in settings:
                    self.spin_sf_b.setValue(float(settings["sf_b"]))

                # Load custom presets
                if "custom_presets" in settings:
                    self.custom_presets.update(settings["custom_presets"])
                self.update_preset_combo()

                if "preset" in settings:
                    self.combo_preset.setCurrentText(settings["preset"])
                    self.apply_preset()

                if "show_vec" in settings:
                    self.chk_vector.setChecked(bool(settings["show_vec"]))
                if "vec_res" in settings:
                    self.spin_vec_res.setValue(int(settings["vec_res"]))
                if "vec_color" in settings:
                    self.vector_color = settings["vec_color"]
                    self.btn_vec_color.setStyleSheet(
                        f"background-color: {self.vector_color}; border: 1px solid gray; height: 20px;"
                    )
                if "vec_alpha" in settings:
                    self.spin_vec_alpha.setValue(float(settings["vec_alpha"]))
                if "amp" in settings:
                    self.spin_amp.setValue(float(settings["amp"]))
                if "fps" in settings:
                    self.spin_fps.setValue(int(settings["fps"]))

            # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
            except Exception as e:  # pylint: disable=broad-exception-caught
                logging.warning("Error loading freq settings: %s", e)

    def save_settings(self):
        """Persist scaling, presets and vector/animation appearance (and spectrum settings, if open) to disk."""
        freq_settings = {
            "sf_a": self.spin_sf_a.value(),
            "sf_b": self.spin_sf_b.value(),
            "preset": self.combo_preset.currentText(),
            "custom_presets": self.custom_presets,
            "show_vec": self.chk_vector.isChecked(),
            "vec_res": self.spin_vec_res.value(),
            "vec_color": self.vector_color,
            "vec_alpha": self.spin_vec_alpha.value(),
            "amp": self.spin_amp.value(),
            "fps": self.spin_fps.value(),
        }

        # Save spectrum window settings if it was ever opened
        if self.spectrum_win:
            freq_settings.update(
                {
                    "spec_sigma": self.spectrum_win.spin_sigma.value(),
                    "spec_sticks": self.spectrum_win.chk_sticks.isChecked(),
                    "spec_markers": self.spectrum_win.chk_markers.isChecked(),
                    "spec_auto_x": self.spectrum_win.chk_auto_x.isChecked(),
                    "spec_auto_y": self.spectrum_win.chk_auto_y.isChecked(),
                }
            )
        else:
            # Preserve spectrum settings if window is not currently open
            prev = load_section(self.settings_file, "freq_settings")
            prev_spec = {k: v for k, v in prev.items() if k.startswith("spec_")}
            freq_settings.update(prev_spec)

        save_section(self.settings_file, "freq_settings", freq_settings)
