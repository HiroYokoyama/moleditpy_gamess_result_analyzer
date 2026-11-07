"""Dipole Moment dialog: magnitude/vector display plus a 3D arrow overlay."""

import numpy as np
import os
import pyvista as pv
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QCheckBox,
    QDoubleSpinBox,
    QColorDialog,
    QSpinBox,
    QGroupBox,
)
from PySide6.QtGui import QColor
from .settings import load_section, save_section
import logging


class DipoleDialog(QDialog):
    """Dialog showing the dipole magnitude/vector with a 3D arrow overlay."""

    def __init__(self, parent_dlg, dipole_data):
        super().__init__(parent_dlg)
        self.setWindowTitle("Dipole Moment")
        self.resize(350, 300)
        self.parent_dlg = parent_dlg
        self.dipole_data = dipole_data
        self.arrow_actor = None
        self.arrow_color = "cyan"
        self.arrow_res = 20
        self.settings_file = os.path.join(os.path.dirname(__file__), "settings.json")

        main_layout = QVBoxLayout(self)

        # 1. Info Section
        info_group = QGroupBox("Dipole Data")
        info_layout = QVBoxLayout(info_group)
        vec = dipole_data.get(
            "vector_debye", dipole_data.get("vector", [0.0, 0.0, 0.0])
        )
        mag = dipole_data.get("magnitude_debye", dipole_data.get("magnitude", 0.0))
        vec_au = dipole_data.get("vector_au", None)
        mag_au = dipole_data.get("magnitude_au", None)

        info_text = (
            f"Vector (X, Y, Z):<br>"
            f"<b>{vec[0]:.4f}, {vec[1]:.4f}, {vec[2]:.4f}</b> Debye<br><br>"
            f"Magnitude: <b>{mag:.4f} Debye</b>"
        )
        if vec_au is not None and mag_au is not None:
            info_text += (
                f"<br><br><span style='color:#666;'>"
                f"a.u.: {vec_au[0]:.5f}, {vec_au[1]:.5f}, {vec_au[2]:.5f}"
                f" &nbsp;(|μ| = {mag_au:.5f})</span>"
            )
        lbl_info = QLabel(info_text)
        lbl_info.setWordWrap(True)
        info_layout.addWidget(lbl_info)
        main_layout.addWidget(info_group)

        # 2. 3D Appearance Section
        view_group = QGroupBox("3D Visualization")
        view_layout = QVBoxLayout(view_group)

        self.chk_show = QCheckBox("Show in 3D Viewer")
        self.chk_show.setChecked(False)
        self.chk_show.stateChanged.connect(self.update_view)
        view_layout.addWidget(self.chk_show)

        self.chk_reverse = QCheckBox("Reverse Vector")
        self.chk_reverse.setChecked(True)
        self.chk_reverse.stateChanged.connect(self.update_view)
        view_layout.addWidget(self.chk_reverse)

        # Scale and Resolution in one row
        res_row = QHBoxLayout()
        res_row.addWidget(QLabel("Scale:"))
        self.spin_scale = QDoubleSpinBox()
        self.spin_scale.setRange(0.1, 10.0)
        self.spin_scale.setSingleStep(0.1)
        self.spin_scale.setValue(2.0)
        self.spin_scale.valueChanged.connect(self.update_view)
        res_row.addWidget(self.spin_scale)

        res_row.addWidget(QLabel(" Res:"))
        self.spin_res = QSpinBox()
        self.spin_res.setRange(3, 100)
        self.spin_res.setValue(20)
        self.spin_res.valueChanged.connect(self.on_res_changed)
        res_row.addWidget(self.spin_res)
        view_layout.addLayout(res_row)

        # Color picker row
        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Color:"))
        self.btn_color = QPushButton()
        self.btn_color.setFixedWidth(60)
        self.btn_color.setStyleSheet(
            f"background-color: {self.arrow_color}; border: 1px solid gray; height: 20px;"
        )
        self.btn_color.clicked.connect(self.pick_color)
        color_row.addWidget(self.btn_color)

        color_row.addWidget(QLabel(" Alpha:"))
        self.spin_alpha = QDoubleSpinBox()
        self.spin_alpha.setRange(0.05, 1.0)
        self.spin_alpha.setSingleStep(0.05)
        self.spin_alpha.setValue(1.0)
        self.spin_alpha.setToolTip(
            "Arrow opacity. Below 1.0 the structure stays visible through the "
            "arrow, which helps when it points into the molecule."
        )
        self.spin_alpha.valueChanged.connect(self.update_view)
        color_row.addWidget(self.spin_alpha)
        color_row.addStretch()
        view_layout.addLayout(color_row)

        main_layout.addWidget(view_group)

        main_layout.addStretch()

        # 3. Actions
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        main_layout.addWidget(btn_close)

        self.load_settings()

    def update_view(self):
        """Redraw the dipole arrow in 3D from the current settings, or hide it."""
        # Clear old
        if self.arrow_actor:
            try:
                self.parent_dlg.mw.plotter.remove_actor(self.arrow_actor)
            except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                logging.debug(
                    "Dipole analysis: could not remove the old dipole arrow actor: %s",
                    e,
                )
            self.arrow_actor = None

        if not self.chk_show.isChecked():
            self.parent_dlg.mw.plotter.render()
            return

        try:
            mw = self.parent_dlg.mw
            # Geometric centroid — only the arrow's anchor point. For a neutral
            # molecule the dipole is origin-independent, so this is cosmetic.
            coords = self.parent_dlg.parser.data.get("coords", [])
            if coords:
                center = np.mean(coords, axis=0)
            else:
                center = np.array([0.0, 0.0, 0.0])

            # Debye throughout, so the arrow length matches the number shown.
            vec = np.array(
                self.dipole_data.get(
                    "vector_debye", self.dipole_data.get("vector", [0.0, 0.0, 0.0])
                )
            )
            mag = np.linalg.norm(vec)

            if mag < 1e-6:
                return

            direction = vec / mag
            if self.chk_reverse.isChecked():
                direction = -direction

            scale = self.spin_scale.value()
            length = mag * scale

            # Add Arrow using add_mesh and pv.Arrow for robustness
            arrow = pv.Arrow(
                start=center,
                direction=direction,
                scale=length,
                shaft_resolution=self.arrow_res,
                tip_resolution=self.arrow_res,
            )
            self.arrow_actor = mw.plotter.add_mesh(
                arrow,
                color=self.arrow_color,
                opacity=self.spin_alpha.value(),
                name="dipole_vector",
            )
            mw.plotter.render()

        # C++ library boundary: RDKit/VTK/pyvista exceptions do not map to Python types
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.warning("Error drawing dipole: %s", e)

    def pick_color(self):
        """Open a color picker and apply the chosen color to the arrow."""
        color = QColorDialog.getColor(
            QColor(self.arrow_color), self, "Select Arrow Color"
        )
        if color.isValid():
            self.arrow_color = color.name()
            self.btn_color.setStyleSheet(f"background-color: {self.arrow_color};")
            self.update_view()

    def on_res_changed(self, val):
        """Update the arrow's tessellation resolution and redraw it."""
        self.arrow_res = val
        self.update_view()

    def reject(self):
        """Route Esc through close() so closeEvent cleanup always runs."""
        # Esc must run closeEvent cleanup (QDialog.reject only hides)
        self.close()

    def closeEvent(self, event):
        """Remove the 3D arrow, clear the parent's reference and save settings."""
        if self.arrow_actor:
            try:
                self.parent_dlg.mw.plotter.remove_actor(self.arrow_actor)
                self.parent_dlg.mw.plotter.render()
            except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                logging.debug(
                    "Dipole analysis: could not remove the dipole arrow actor on close: %s",
                    e,
                )
        # Clean up reference in parent
        if hasattr(self.parent_dlg, "dipole_dlg"):
            self.parent_dlg.dipole_dlg = None

        self.save_settings()
        event.accept()

    def load_settings(self):
        """Restore the arrow resolution, color, opacity and toggle state."""
        if os.path.exists(self.settings_file):
            settings = load_section(self.settings_file, "dipole_settings")
            try:
                if "res" in settings:
                    self.spin_res.setValue(int(settings["res"]))

                if "color" in settings:
                    self.arrow_color = settings["color"]
                    self.btn_color.setStyleSheet(
                        f"background-color: {self.arrow_color}; border: 1px solid gray; height: 20px;"
                    )

                if "alpha" in settings:
                    self.spin_alpha.setValue(float(settings["alpha"]))

                if "show" in settings:
                    self.chk_show.setChecked(bool(settings["show"]))

                if "reverse" in settings:
                    self.chk_reverse.setChecked(bool(settings["reverse"]))

            except (TypeError, ValueError) as e:
                logging.warning("Error loading dipole settings: %s", e)

    def save_settings(self):
        """Persist the arrow resolution, color, opacity and toggle state."""
        dipole_settings = {
            # "scale": self.spin_scale.value(),
            "res": self.spin_res.value(),
            "color": self.arrow_color,
            "alpha": self.spin_alpha.value(),
            "show": self.chk_show.isChecked(),
            "reverse": self.chk_reverse.isChecked(),
        }

        save_section(self.settings_file, "dipole_settings", dipole_settings)
