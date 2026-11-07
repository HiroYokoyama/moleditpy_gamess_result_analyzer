"""Atomic Charges dialog: table, color-scheme gradient mapping and CSV export."""

import csv
import os
import numpy as np
import pyvista as pv
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QWidget,
    QCheckBox,
    QInputDialog,
    QColorDialog,
    QMessageBox,
    QGroupBox,
    QAbstractItemView,
    QFileDialog,
)
from PySide6.QtGui import QColor, QPainter, QLinearGradient
from PySide6.QtCore import Qt
import matplotlib.colors as mcolors
from matplotlib.colors import LinearSegmentedColormap
from .utils import get_default_export_path, notify
from .settings import load_section, save_section
import logging


# GradientBar Widget
class GradientBar(QWidget):
    """Widget painting a horizontal color gradient bar for the charge scale."""

    def __init__(self, parent=None, colors=None):
        super().__init__(parent)
        self.colors = colors if colors is not None else ["red", "white", "blue"]
        self.setFixedHeight(30)

    def set_colors(self, colors):
        """Replace the gradient's color stops and repaint."""
        self.colors = colors
        self.update()

    def paintEvent(self, event):  # pylint: disable=unused-argument
        # Qt override signature requires the event argument.
        """Fill the widget with the gradient and draw a border around it."""
        painter = QPainter(self)
        grad = self.get_gradient()
        painter.fillRect(self.rect(), grad)

        # Draw border
        painter.setPen(Qt.GlobalColor.black)
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)

    def get_gradient(self):
        """Build a QLinearGradient spanning the widget from the stored colors."""
        grad = QLinearGradient(0, 0, self.width(), 0)

        n = len(self.colors)
        if n < 2:
            return grad

        for i, c in enumerate(self.colors):
            pos = i / (n - 1)
            grad.setColorAt(pos, QColor(c))
        return grad


class ChargeDialog(QDialog):
    """Dialog listing per-atom charges with 3D color-mapping and CSV export."""

    # pylint: disable=attribute-defined-outside-init,access-member-before-definition
    # Qt/PyVista pattern: label/actor/state attrs are created lazily, not in __init__.

    def __init__(self, parent, all_charges):
        super().__init__(parent)
        self.parent_dlg = parent  # GAMESSResultAnalyzerDialog
        self.setWindowTitle("Atomic Charges")
        self.resize(550, 750)
        self.all_charges = all_charges
        self.current_type = next(iter(self.all_charges)) if self.all_charges else ""

        # Color Schemes - default schemes
        self.schemes = {
            "Red(-) - White - Blue(+)": ["red", "white", "blue"],
            "Blue(-) - White - Red(+)": ["blue", "white", "red"],
            "Red(-) - Blue(+)": ["red", "blue"],
            "Green(-) - White - Purple(+)": ["green", "white", "purple"],
        }

        # Load custom schemes from settings.json
        self.settings_file = os.path.join(os.path.dirname(__file__), "settings.json")
        self.current_scheme = "Red(-) - White - Blue(+)"

        if os.path.exists(self.settings_file):
            try:
                # Load from "charge_settings" key
                settings_data = load_section(self.settings_file, "charge_settings")

                # Load custom schemes
                if "custom_color_schemes" in settings_data:
                    for scheme_data in settings_data["custom_color_schemes"]:
                        name = scheme_data.get("name", "")
                        colors = scheme_data.get("colors", [])
                        if name and colors:
                            self.schemes[f"Custom: {name}"] = colors

                # Load last used scheme
                if "last_charge_scheme" in settings_data:
                    self.current_scheme = settings_data["last_charge_scheme"]
            except (TypeError, AttributeError) as e:
                logging.warning("Error loading charge settings: %s", e)

        main_layout = QVBoxLayout(self)

        # 1. Type Selection Section
        type_group = QGroupBox("Charge Method")
        type_layout = QHBoxLayout(type_group)
        type_layout.addWidget(QLabel("Select Type:"))
        self.combo_type = QComboBox()
        self.combo_type.addItems(list(self.all_charges.keys()))
        self.combo_type.currentTextChanged.connect(self.on_type_change)
        type_layout.addWidget(self.combo_type)
        main_layout.addWidget(type_group)

        # 2. Data Table Section
        table_group = QGroupBox("Charge Data")
        table_layout = QVBoxLayout(table_group)
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Idx", "Atom", "Charge (e)"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.verticalHeader().setVisible(False)
        table_layout.addWidget(self.table)
        main_layout.addWidget(table_group)

        # 3. 3D Visualization Section
        view_group = QGroupBox("3D Visualization & Coloring")
        view_layout = QVBoxLayout(view_group)

        # Scheme row
        scheme_row = QHBoxLayout()
        scheme_row.addWidget(QLabel("Scheme:"))
        self.combo_scheme = QComboBox()
        self.combo_scheme.addItems(list(self.schemes.keys()))
        self.combo_scheme.setCurrentText(self.current_scheme)
        self.combo_scheme.currentTextChanged.connect(self.on_scheme_change)
        scheme_row.addWidget(self.combo_scheme)

        btn_custom = QPushButton("+ New Scheme")
        btn_custom.setFixedWidth(100)
        btn_custom.clicked.connect(self.edit_custom_scheme)
        scheme_row.addWidget(btn_custom)
        view_layout.addLayout(scheme_row)

        # A saved scheme that no longer exists (deleted or malformed custom
        # entry) used to KeyError here and abort opening the dialog.
        if self.current_scheme not in self.schemes:
            self.current_scheme = "Red(-) - White - Blue(+)"
            self.combo_scheme.setCurrentText(self.current_scheme)

        # Gradient Bar
        self.grad_bar = GradientBar(self, self.schemes[self.current_scheme])
        view_layout.addWidget(self.grad_bar)

        # Labels for gradient
        lbl_layout = QHBoxLayout()
        lbl_layout.addWidget(QLabel("<font color='gray'>Negative</font>"))
        lbl_mid = QLabel("<font color='gray'>Neutral</font>")
        lbl_mid.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_layout.addWidget(lbl_mid)
        lbl_max = QLabel("<font color='gray'>Positive</font>")
        lbl_max.setAlignment(Qt.AlignmentFlag.AlignRight)
        lbl_layout.addWidget(lbl_max)
        view_layout.addLayout(lbl_layout)

        # Labels checkbox
        self.chk_show_labels = QCheckBox("Show charge values as 3D labels")
        self.chk_show_labels.stateChanged.connect(self.toggle_labels)
        view_layout.addWidget(self.chk_show_labels)

        # Actions
        btn_row = QHBoxLayout()
        btn_colorize = QPushButton("Apply Coloring")
        btn_colorize.setFixedHeight(30)
        btn_colorize.setStyleSheet(
            "font-weight: bold; background-color: #2196F3; color: white;"
        )
        btn_colorize.clicked.connect(self.apply_colors)
        btn_row.addWidget(btn_colorize)

        btn_reset = QPushButton("Reset Colors")
        btn_reset.setFixedHeight(30)
        btn_reset.clicked.connect(self.reset_colors)
        btn_row.addWidget(btn_reset)
        view_layout.addLayout(btn_row)

        main_layout.addWidget(view_group)

        # 4. Actions
        bottom_row = QHBoxLayout()
        btn_clear = QPushButton("Clear Selection")
        btn_clear.clicked.connect(self.table.clearSelection)
        bottom_row.addWidget(btn_clear)

        btn_csv = QPushButton("Export CSV")
        btn_csv.clicked.connect(self.export_csv)
        bottom_row.addWidget(btn_csv)

        bottom_row.addStretch()

        btn_close = QPushButton("Close")
        btn_close.setFixedWidth(100)
        btn_close.clicked.connect(self.close)  # accept() skips closeEvent (Qt >= 6.3)
        bottom_row.addWidget(btn_close)

        main_layout.addLayout(bottom_row)

        self.update_table()

    def on_type_change(self, text):
        """Switch the displayed charge type when the combo box selection changes."""
        self.current_type = text
        self.update_table()

    def on_scheme_change(self, text):
        """Apply the newly-selected color scheme to the gradient bar and save it."""
        self.current_scheme = text
        colors = self.schemes.get(text, ["red", "white", "blue"])
        self.grad_bar.set_colors(colors)

        # Save last used scheme to settings.json
        self.save_settings()

    def edit_custom_scheme(self):
        """Create a custom color scheme"""

        # Get scheme name
        name, ok = QInputDialog.getText(self, "Custom Scheme", "Enter scheme name:")
        if not ok or not name:
            return

        # Get number of colors
        num_colors, ok = QInputDialog.getInt(
            self, "Custom Scheme", "Number of colors (2-5):", 3, 2, 5
        )
        if not ok:
            return

        # Collect colors
        colors = []
        for i in range(num_colors):
            label = (
                ["Negative", "Mid-Negative", "Neutral", "Mid-Positive", "Positive"][i]
                if num_colors <= 5
                else f"Color {i + 1}"
            )
            color = QColorDialog.getColor(
                QColor("white"), self, f"Select {label} color"
            )
            if not color.isValid():
                return
            colors.append(color.name())

        # Add to schemes
        scheme_name = f"Custom: {name}"
        self.schemes[scheme_name] = colors

        # Update combo box
        self.combo_scheme.addItem(scheme_name)
        self.combo_scheme.setCurrentText(scheme_name)

        # Save to settings
        self.save_custom_schemes()

    def save_custom_schemes(self):
        """Save custom schemes to settings.json"""
        self.save_settings()

    def save_settings(self):
        """Save all settings to settings.json"""
        # Prepare charge-specific data
        charge_data = {}

        # Save custom schemes
        custom_schemes = []
        for name, colors in self.schemes.items():
            if name.startswith("Custom: "):
                # Remove prefix
                clean_name = name.replace("Custom: ", "")
                custom_schemes.append({"name": clean_name, "colors": colors})

        charge_data["custom_color_schemes"] = custom_schemes
        charge_data["last_charge_scheme"] = self.current_scheme

        save_section(self.settings_file, "charge_settings", charge_data)

    def toggle_labels(self):
        """Toggle charge value labels in 3D view"""
        show = self.chk_show_labels.isChecked()

        # Remove old labels if they exist
        if getattr(self, "_charge_labels", None) is not None:
            for actor in self._charge_labels:
                try:
                    self.parent_dlg.mw.plotter.remove_actor(actor)
                except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                    logging.debug(
                        "Charge analysis: could not remove an old charge label actor: %s",
                        e,
                    )
            self._charge_labels = []

        if not show:
            if hasattr(self.parent_dlg.mw, "plotter"):
                self.parent_dlg.mw.plotter.render()
            return

        # Add labels
        data = self.all_charges.get(self.current_type, [])

        coords = []
        if hasattr(self.parent_dlg, "parser") and self.parent_dlg.parser:
            coords = self.parent_dlg.parser.data.get("coords", [])

        if not coords or len(coords) != len(data):
            QMessageBox.warning(self, "Error", "No coordinates available for labels.")
            self.chk_show_labels.setChecked(False)
            return

        self._charge_labels = []
        try:
            for item in data:
                idx = item["atom_idx"]
                pos = coords[idx]
                label_pos = [pos[0], pos[1], pos[2] + 0.3]

                actor = self.parent_dlg.mw.plotter.add_point_labels(
                    [label_pos],
                    [f"{item['charge']:.2f}"],
                    font_size=10,
                    text_color="yellow",
                    point_size=0,
                    always_visible=True,
                )
                self._charge_labels.append(actor)

            self.parent_dlg.mw.plotter.render()
        # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
        except Exception as e:  # pylint: disable=broad-exception-caught
            QMessageBox.warning(self, "Error", f"Could not add labels: {e}")
            self.chk_show_labels.setChecked(False)

    def reset_colors(self):
        """Reset atom colors to default CPK colors"""
        try:
            mw = self.parent_dlg.mw
            v3d = getattr(mw, "view_3d_manager", None)
            if v3d is not None:
                data = self.all_charges.get(self.current_type, [])
                overrides = getattr(v3d, "_plugin_color_overrides", None)
                if overrides is not None:
                    # Clear entries directly instead of calling
                    # update_atom_color_override() per atom -- that helper
                    # redraws the whole molecule on every single call.
                    for item in data:
                        overrides.pop(item["atom_idx"], None)
                else:
                    for item in data:
                        v3d.update_atom_color_override(item["atom_idx"], None)

            # Remove scalar bar if exists
            if getattr(self, "_charge_scalar_bar", None) is not None:
                try:
                    self.parent_dlg.mw.plotter.remove_actor(self._charge_scalar_bar)
                    delattr(self, "_charge_scalar_bar")
                except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                    logging.debug(
                        "Charge analysis: could not remove the charge scalar bar actor: %s",
                        e,
                    )

            # Remove labels if exist
            if getattr(self, "_charge_labels", None) is not None:
                for actor in self._charge_labels:
                    try:
                        self.parent_dlg.mw.plotter.remove_actor(actor)
                    except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                        logging.debug(
                            "Charge analysis: could not remove an old charge label actor: %s",
                            e,
                        )
                self._charge_labels = []
                self.chk_show_labels.setChecked(False)

            # Redraw molecule with default CPK colors
            if self.parent_dlg.mw.current_mol:
                self.parent_dlg.context.draw_molecule_3d(self.parent_dlg.mw.current_mol)

            # Render
            if hasattr(self.parent_dlg.mw, "plotter"):
                self.parent_dlg.mw.plotter.render()

            notify(self, "Colors reset to CPK default.", 5000)
        # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
        except Exception as e:  # pylint: disable=broad-exception-caught
            QMessageBox.critical(self, "Error", f"Failed to reset colors:\n{e}")

    def update_table(self):
        """Rebuild the charge table's columns and rows for the current type."""
        data = self.all_charges.get(self.current_type, [])
        if not data:
            self.table.setRowCount(0)
            return

        # Always "atom_idx", "atom_sym", "charge"
        # Others might be "spin", "valency", "bonded_valency", "free_valency", "population"

        headers = ["Idx", "Atom", "Charge"]
        keys = ["atom_idx", "atom_sym", "charge"]

        # Check for extras
        special_map = {
            "spin": "Spin",
            "valency": "Valency (VA)",
            "bonded_valency": "Bonded (BVA)",
            "free_valency": "Free (FA)",
            "population": "Pop",
            "core": "Core",
            "valence": "Valence",
            "rydberg": "Rydberg",
            "total": "Total",
            "homo_mulliken": "HOMO(M)",
            "homo_loewdin": "HOMO(L)",
            "lumo_mulliken": "LUMO(M)",
            "lumo_loewdin": "LUMO(L)",
        }

        # Collect other keys present in the data. Union over every row, not
        # just the first: GAMESS omits trailing columns for some atoms, and
        # keying off row 0 alone dropped whole properties from the table.
        other_keys = []
        for item in data:
            for k in item.keys():
                if k not in keys and k not in other_keys:
                    other_keys.append(k)

        # Sort or prioritize
        # Make sure standard Mayer order if present: VA, BVA, FA
        if "valency" in other_keys:
            other_keys.remove("valency")
            if "bonded_valency" in other_keys:
                other_keys.remove("bonded_valency")
            if "free_valency" in other_keys:
                other_keys.remove("free_valency")
            keys.extend(["valency", "bonded_valency", "free_valency"])
            headers.extend(["Valency (VA)", "Bonded (BVA)", "Free (FA)"])

        # Prioritized Sort for remaining keys
        def sort_key(k):
            order = [
                "core",
                "valence",
                "rydberg",
                "total",  # NBO
                "homo_mulliken",
                "homo_loewdin",  # FMO
                "lumo_mulliken",
                "lumo_loewdin",
                "spin",
                "population",
            ]
            if k in order:
                return order.index(k)
            return 999

        other_keys.sort(key=sort_key)

        # Add remaining (like spin)
        for k in other_keys:
            keys.append(k)
            headers.append(special_map.get(k, k.capitalize()))

        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)

        self.table.setRowCount(len(data))
        for r, item in enumerate(data):
            for c, k in enumerate(keys):
                val = item.get(k, "")
                if isinstance(val, float):
                    val_str = f"{val:.4f}"
                else:
                    val_str = str(val)
                self.table.setItem(r, c, QTableWidgetItem(val_str))

    def apply_colors(self):
        """Recolor the 3D atoms by charge using the current gradient scheme."""
        data = self.all_charges.get(self.current_type, [])
        if not data:
            return

        charges = [d["charge"] for d in data]
        if not charges:
            return

        max_c = max(abs(min(charges)), abs(max(charges)))
        if max_c == 0:
            max_c = 1.0

        # Get current color scheme
        colors = self.schemes.get(self.current_scheme, ["red", "white", "blue"])

        # Helper to interpolate color between multiple colors
        def get_color(q):
            # Normalize charge to 0.0 (negative) to 1.0 (positive)
            norm = (q + max_c) / (2 * max_c)
            norm = max(0.0, min(1.0, norm))

            n = len(colors)
            if n < 2:
                return QColor(colors[0]).name()

            # Find which segment we're in
            seg_len = 1.0 / (n - 1)
            idx = int(norm / seg_len)
            if idx >= n - 1:
                return QColor(colors[-1]).name()

            # Interpolate within segment
            local_t = (norm - (idx * seg_len)) / seg_len

            c1 = QColor(colors[idx])
            c2 = QColor(colors[idx + 1])

            r = int(c1.red() + (c2.red() - c1.red()) * local_t)
            g = int(c1.green() + (c2.green() - c1.green()) * local_t)
            b = int(c1.blue() + (c2.blue() - c1.blue()) * local_t)

            return f"#{r:02x}{g:02x}{b:02x}"

        try:
            mw = self.parent_dlg.mw
            v3d = getattr(mw, "view_3d_manager", None)
            if v3d is not None:
                overrides = getattr(v3d, "_plugin_color_overrides", None)
                if overrides is not None:
                    # Write overrides directly instead of calling
                    # update_atom_color_override() per atom -- that helper
                    # triggers a full molecule redraw on every call, which is
                    # O(n_atoms) redraws for a single "Apply Coloring" click.
                    for item in data:
                        overrides[item["atom_idx"]] = get_color(item["charge"])
                else:
                    for item in data:
                        v3d.update_atom_color_override(
                            item["atom_idx"], get_color(item["charge"])
                        )

            # Redraw molecule once after all colors are set
            if self.parent_dlg.mw.current_mol:
                self.parent_dlg.context.draw_molecule_3d(self.parent_dlg.mw.current_mol)

            # Add charge labels if checkbox is enabled
            if self.chk_show_labels.isChecked():
                coords = []
                if hasattr(self.parent_dlg, "parser") and self.parent_dlg.parser:
                    coords = self.parent_dlg.parser.data.get("coords", [])

                if coords and len(coords) == len(data):
                    # Remove old labels if exist
                    if getattr(self, "_charge_labels", None) is not None:
                        for actor in self._charge_labels:
                            try:
                                self.parent_dlg.mw.plotter.remove_actor(actor)
                            except (
                                RuntimeError,
                                AttributeError,
                                KeyError,
                                ValueError,
                            ) as e:
                                logging.debug(
                                    "Charge analysis: could not remove an old charge label actor: %s",
                                    e,
                                )

                    self._charge_labels = []
                    for i, item in enumerate(data):
                        pos = coords[item["atom_idx"]]
                        label_pos = [pos[0], pos[1], pos[2] + 0.3]
                        actor = self.parent_dlg.mw.plotter.add_point_labels(
                            [label_pos],
                            [f"{item['charge']:.2f}"],
                            font_size=10,
                            text_color="yellow",
                            point_size=0,
                            always_visible=True,
                        )
                        self._charge_labels.append(actor)

            # Add scalar bar legend showing charge scale with color gradient
            try:
                # Create a mesh with charge values to display scalar bar
                vmin = -max_c
                vmax = max_c

                # Convert scheme colors to colormap
                cmap_colors = [mcolors.to_rgb(QColor(c).name()) for c in colors]
                cmap = LinearSegmentedColormap.from_list(
                    "charge_cmap", cmap_colors, N=256
                )

                # Remove old scalar bar if exists
                if getattr(self, "_charge_scalar_bar", None) is not None:
                    try:
                        self.parent_dlg.mw.plotter.remove_actor(self._charge_scalar_bar)
                    except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                        logging.debug(
                            "Charge analysis: could not remove the old charge scalar bar actor: %s",
                            e,
                        )

                # Create dummy mesh for scalar bar
                dummy = pv.Box()
                dummy.point_data["charges"] = np.linspace(vmin, vmax, dummy.n_points)

                # Add mesh with scalar bar (invisible mesh, visible bar)
                self._charge_scalar_bar = self.parent_dlg.mw.plotter.add_mesh(
                    dummy,
                    scalars="charges",
                    cmap=cmap,
                    clim=[vmin, vmax],
                    opacity=0.0,  # Invisible mesh
                    show_scalar_bar=True,
                    scalar_bar_args={
                        "title": f"{self.current_type}",
                        "title_font_size": 14,
                        "label_font_size": 12,
                        "n_labels": 5,
                        "vertical": True,
                        "height": 0.3,
                        "width": 0.08,
                        "position_x": 0.88,
                        "position_y": 0.35,
                        "color": "white",
                    },
                )
            # C++ library boundary: RDKit/VTK/pyvista exceptions do not map to Python types
            except Exception as e:  # pylint: disable=broad-exception-caught
                logging.warning("Error adding scalar bar: %s", e)

            # Trigger update
            if hasattr(self.parent_dlg.mw, "plotter"):
                self.parent_dlg.mw.plotter.render()

            notify(self, f"Applied '{self.current_scheme}' coloring to 3D view.", 5000)

        # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
        except Exception as e:  # pylint: disable=broad-exception-caught
            QMessageBox.critical(self, "Error", f"Failed to color atoms:\n{e}")

    def export_csv(self):
        """Prompt for a path and write the current charge table to CSV."""
        if getattr(self, "_csv_exporting", False):
            return
        self._csv_exporting = True
        default_path = get_default_export_path(
            self.parent_dlg.file_path, suffix="_charges_list", extension=".csv"
        )
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Charge Data", default_path, "CSV Files (*.csv)"
        )
        if not filename:
            self._csv_exporting = False
            return

        try:
            data = self.all_charges.get(self.current_type, [])
            if not data:
                return

            # Determine headers dynamically, unioned over every row — GAMESS
            # omits trailing columns for some atoms, and keying off row 0
            # alone dropped whole properties from the export.
            all_keys = []
            for item in data:
                for k in item.keys():
                    if k not in all_keys:
                        all_keys.append(k)

            # Priorities: atom_idx, atom_sym, charge
            prio = ["atom_idx", "atom_sym", "charge"]

            headers = [k for k in prio if k in all_keys]
            other_keys = [k for k in all_keys if k not in prio]

            # Sorting logic for common extra fields
            def sort_key(k):
                order = [
                    "valency",
                    "bonded_valency",
                    "free_valency",
                    "core",
                    "valence",
                    "rydberg",
                    "total",
                    "spin",
                    "population",
                ]
                if k in order:
                    return order.index(k)
                return 999

            other_keys.sort(key=sort_key)
            headers.extend(other_keys)

            # Formatted Headers maps
            header_map = {
                "atom_idx": "Index",
                "atom_sym": "Atom",
                "charge": "Charge",
                "valency": "Valency (VA)",
                "bonded_valency": "Bonded (BVA)",
                "free_valency": "Free (FA)",
                "core": "Core",
                "valence": "Valence (NBO)",
                "rydberg": "Rydberg",
                "total": "Total Pop",
                "homo_mulliken": "HOMO(Mulliken)",
                "homo_loewdin": "HOMO(Loewdin)",
                "lumo_mulliken": "LUMO(Mulliken)",
                "lumo_loewdin": "LUMO(Loewdin)",
            }
            display_headers = [header_map.get(k, k.capitalize()) for k in headers]

            with open(filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(display_headers)

                for item in data:
                    row = []
                    for k in headers:
                        row.append(item.get(k, ""))
                    writer.writerow(row)
            # QMessageBox.information(self, "Success", f"Data exported to {filename}")
            notify(self, f"Data exported to {filename}", 5000)
        # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
        except Exception as e:  # pylint: disable=broad-exception-caught
            QMessageBox.critical(self, "Error", f"Failed to export CSV: {e}")
        finally:
            self._csv_exporting = False

    def reject(self):
        """Route Esc through close() so closeEvent cleanup always runs."""
        # Esc must run closeEvent cleanup (QDialog.reject only hides)
        self.close()

    def closeEvent(self, event):
        """Clean up labels and scalar bar on close"""
        # Remove scalar bar
        if getattr(self, "_charge_scalar_bar", None) is not None:
            try:
                self.parent_dlg.mw.plotter.remove_actor(self._charge_scalar_bar)
            except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                logging.debug(
                    "Charge analysis: could not remove the charge scalar bar actor on close: %s",
                    e,
                )

        # Remove labels
        if getattr(self, "_charge_labels", None) is not None:
            for actor in self._charge_labels:
                try:
                    self.parent_dlg.mw.plotter.remove_actor(actor)
                except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                    logging.debug(
                        "Charge analysis: could not remove a charge label actor on close: %s",
                        e,
                    )

        if hasattr(self.parent_dlg.mw, "plotter"):
            self.parent_dlg.mw.plotter.render()

        # Save settings on close
        self.save_settings()

        # accept() not super().closeEvent(): QDialog.closeEvent calls reject(),
        # which is routed back through close() and would recurse.
        event.accept()
